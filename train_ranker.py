# -*- encoding: utf-8 -*-
"""
Ranker模型训练 v2 — 加入基本面因子 + 因子筛选
================================================
改进点:
  1. 新增基本面因子: PE_TTM, PB, ROE, 营收增长, 净利增长 (akshare)
  2. 因子筛选: 只保留 |IC| > 0.02 的因子
  3. 训练周期扩展到 500 天
  4. 模型参数: max_iter=50, max_depth=2, min_samples_leaf=100 (防过拟合)

训练数据: 东方财富K线 + 未来N日收益率作为标签
用法:
  python train_ranker.py
"""
import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import HistGradientBoostingRegressor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("TrainRanker")

MODEL_DIR = Path(__file__).parent / "model"
MODEL_DIR.mkdir(exist_ok=True)
MODEL_OUT = MODEL_DIR / "ranker_model.pkl"

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}
EM_HIST = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_MF = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"

# ============================================================
# 因子定义
# ============================================================
# 技术因子 (19个)
TECH_COLS = [
    "ret5", "ret10", "ret20", "vol_std20", "vol_ratio", "price_std20",
    "high_low_ratio", "turn_rate", "ret_skew", "vol_skew",
    "pct_chg", "amplitude", "close_ma20_ratio",
    "ret_vs_ma5", "ret_vs_ma10", "vol_stability",
    "ret_accel", "vol_growth", "vol_momentum", "rsi14",
]
# 资金流向因子 (6个)
MF_COLS = [
    "mf_main_ratio", "mf_super_ratio", "mf_5d_cum",
    "mf_accel", "mf_price_divergence", "mf_trend_strength",
]
# 新闻情绪因子 (3个)
NEWS_COLS = ["news_sentiment", "news_breaking_count", "news_volume"]
# 产业动量因子 (4个)
INDUSTRY_COLS = ["industry_ret5", "industry_ret10", "industry_rank", "stock_vs_industry"]
# 基本面因子 (5个) — 新增
FUND_COLS = ["pe_ttm", "pb", "roe_ttm", "rev_growth_yoy", "np_growth_yoy"]

# 全量候选因子列表 (会在IC筛选后缩减)
ALL_CANDIDATE_COLS = TECH_COLS + MF_COLS + NEWS_COLS + INDUSTRY_COLS + FUND_COLS

# 经过IC筛选后的最终因子 (会在训练时动态确定)
FEATURE_COLS = list(ALL_CANDIDATE_COLS)  # 初始值，会被IC筛选覆盖

N_STOCKS = 200      # 训练股票数
N_DAYS = 500         # 历史天数 (扩展到500天)
FUTURE_N = 5        # 未来5日收益作为标签
IC_THRESHOLD = 0.02  # IC筛选阈值


def get_sample_stocks(n: int = 200) -> list[str]:
    """获取样本股票 — 通过akshare获取全市场，排除ST/科创板/北交所"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        mask = (
            ~df["name"].str.contains("ST") &
            ~df["code"].str.startswith("688") &
            ~df["code"].str.startswith(("8", "4"))
        )
        df = df[mask]
        codes = df["code"].tolist()
        # 按行业分散取，避免集中在某几个行业
        codes = codes[:n]
        logger.info(f"全市场候选: {len(df)}只, 实际取{len(codes)}只")
        return codes
    except Exception as e:
        logger.warning(f"akshare获取股票列表失败: {e}，使用硬编码列表")
        return ["000001", "600519", "000858", "601318", "000333",
                "600036", "002714", "300750", "601899", "002475",
                "600900", "000651", "601166", "002415", "000002",
                "600276", "000725", "601012", "300059", "603259",
                "600030", "601688", "002230", "002352", "000776",
                "600887", "002304", "601888", "000568", "600809",
                "601398", "000001", "600000", "002027", "000063",
                "600028", "002371", "300015", "000786", "601601"][:n]


def get_kline_em(code: str, count: int = 500) -> pd.DataFrame:
    """
    获取K线数据 — 优先通过 QMT HTTP API（Mac/Linux），直连东财作为备用（Windows）
    """
    # 尝试 QMT HTTP API
    try:
        clean = code.split(".")[0]
        r = requests.get(f"http://10.6.98.168:8765/data/kline",
                         params={"code": clean, "period": "1d", "count": count},
                         timeout=10)
        d = r.json()
        candles = d.get("candles", [])
        if candles:
            records = []
            for c in candles:
                records.append({
                    "date": pd.to_datetime(str(c["date"])[:10], format="%Y%m%d"),
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": int(c["volume"]),
                })
            return pd.DataFrame(records).set_index("date").sort_index()
    except Exception:
        pass

    # 备用：直连东财
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": count,
    }
    try:
        r = requests.get(EM_HIST, params=params, headers=EM_HEADERS, timeout=8)
        klines = r.json().get("data", {}).get("klines", [])
        records = []
        for k in klines:
            p = k.split(",")
            records.append({
                "date": pd.to_datetime(p[0]),
                "open": float(p[1]), "high": float(p[2]),
                "low": float(p[3]), "close": float(p[4]),
                "volume": int(p[5]),
            })
        df = pd.DataFrame(records).set_index("date").sort_index()
        return df
    except Exception as e:
        return pd.DataFrame()


def get_money_flow_em(code: str, days: int = 30) -> list[dict]:
    """
    获取资金流向数据 — 优先通过 QMT HTTP API，直连东财作为备用
    """
    # 尝试 QMT HTTP API
    try:
        clean = code.split(".")[0]
        r = requests.get(f"http://10.6.98.168:8765/data/money_flow",
                         params={"codes": clean, "days": days},
                         timeout=10)
        d = r.json()
        mf_list = d.get("money_flow", {}).get(clean, [])
        if mf_list:
            result = []
            for m in mf_list:
                result.append({
                    "date": m["date"],
                    "main_net": m.get("main_net", 0),
                    "super_large": m.get("super_large", 0),
                    "large": m.get("large", 0),
                    "medium": m.get("medium", 0),
                    "small": m.get("small", 0),
                    "main_net_ratio": m.get("main_net_ratio", 0),
                    "super_large_ratio": m.get("super_large_ratio", 0),
                })
            return result
    except Exception:
        pass

    # 备用：直连东财
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    params = {
        "lmt": days,
        "klt": "101",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "secid": secid,
    }
    try:
        r = requests.get(EM_MF, params=params, headers=EM_HEADERS, timeout=8)
        data = r.json().get("data", {})
        klines = data.get("klines", [])
        records = []
        for k in klines:
            p = k.split(",")
            if len(p) >= 15:
                records.append({
                    "date": p[0],
                    "main_net": float(p[2]) if p[2] != "-" else 0,
                    "super_large": float(p[3]) if p[3] != "-" else 0,
                    "large": float(p[4]) if p[4] != "-" else 0,
                    "medium": float(p[5]) if p[5] != "-" else 0,
                    "small": float(p[6]) if p[6] != "-" else 0,
                    "main_net_ratio": float(p[7]) if p[7] != "-" else 0,
                    "super_large_ratio": float(p[8]) if p[8] != "-" else 0,
                })
        return records
    except Exception as e:
        logger.debug(f"资金流向获取失败 [{code}]: {e}")
        return []


import sqlite3

# ============================================================
# 行业分类映射 (code -> industry) — 通过akshare动态构建
# ============================================================
def build_industry_map() -> dict:
    """
    通过akshare获取全市场行业分类
    返回: {code: industry_name}
    """
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        industry_map = {}

        for _, row in df.head(50).iterrows():
            board_name = row["板块名称"]
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
                for _, c in cons.iterrows():
                    code = c["代码"]
                    industry_map[code] = board_name
            except:
                pass
            time.sleep(0.1)

        logger.info(f"行业映射: {len(industry_map)} 只股票, {len(set(industry_map.values()))} 个行业")
        return industry_map
    except Exception as e:
        logger.warning(f"行业映射获取失败: {e}，使用硬编码映射")
        return STOCK_INDUSTRY.copy()

STOCK_INDUSTRY = {
    "000001": "银行", "600036": "银行", "601166": "银行", "601318": "银行",
    "000002": "房地产开发", "000776": "证券", "600030": "证券", "601688": "证券",
    "000333": "家电", "000651": "家电", "600276": "化学制药",
    "000858": "白酒", "000568": "白酒", "600809": "白酒",
    "002714": "农业", "300750": "电池", "601899": "贵金属", "002475": "消费电子",
    "600519": "白酒", "601012": "光伏设备", "002230": "消费电子", "002352": "物流",
    "002304": "白酒", "601888": "旅游", "603259": "化学制药", "600887": "食品加工",
    "600900": "水电",
}


# ============================================================
# 基本面因子获取 (新增)
# ============================================================
def fetch_fundamentals_single(code: str) -> pd.DataFrame:
    """
    获取单只股票的历史财务指标 (季度)
    返回: pd.DataFrame with columns:
        date, pe_ttm, pb, roe_ttm, rev_growth_yoy, np_growth_yoy
    """
    try:
        import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=code)
        if df.empty or len(df) == 0:
            return pd.DataFrame()

        # 过滤有效行 (排除'False'标记的无效数据)
        valid = df[df["每股净资产"] != "False"].copy()
        if valid.empty:
            return pd.DataFrame()

        # 转换为数值
        for col in ["每股净资产", "净资产收益率", "净利润同比增长率",
                     "营业总收入同比增长率", "基本每股收益"]:
            valid[col] = pd.to_numeric(valid[col], errors="coerce")

        valid["报告期"] = pd.to_datetime(valid["报告期"])

        result = pd.DataFrame()
        result["date"] = valid["报告期"]
        # PE_TTM = 股价 / 基本每股收益 (需要合并K线数据)
        result["eps"] = valid["基本每股收益"]
        result["bps"] = valid["每股净资产"]
        # ROE (百分比转小数)
        result["roe_ttm"] = valid["净资产收益率"] / 100.0
        # 营收同比增长
        result["rev_growth_yoy"] = valid["营业总收入同比增长率"] / 100.0
        # 净利同比增长
        result["np_growth_yoy"] = valid["净利润同比增长率"] / 100.0

        return result
    except Exception as e:
        logger.debug(f"基本面数据获取失败 [{code}]: {e}")
        return pd.DataFrame()


def build_fundamentals_daily(stocks: list[str], kline_cache: dict) -> dict:
    """
    批量获取基本面数据并向前填充到日频
    返回: {code: DataFrame(date, pe_ttm, pb, roe_ttm, rev_growth_yoy, np_growth_yoy)}
    """
    logger.info("获取基本面因子数据...")
    result = {}

    for i, code in enumerate(stocks):
        if code not in kline_cache or kline_cache[code].empty:
            continue

        fund_q = fetch_fundamentals_single(code)
        if fund_q.empty:
            continue

        kline = kline_cache[code]
        close_series = kline["close"]

        # 计算 PE_TTM 和 PB (需要每日收盘价)
        fund_q["date"] = pd.to_datetime(fund_q["date"])

        # 合并K线收盘价到财务数据
        # 向前填充: 对于每个交易日, 使用最新已知的财务数据
        dates = kline.index
        fund_daily = pd.DataFrame(index=dates)

        # 对每个财务指标, 按日期对齐并前向填充
        for col in ["roe_ttm", "rev_growth_yoy", "np_growth_yoy", "eps", "bps"]:
            if col in fund_q.columns:
                tmp = fund_q.set_index("date")[col].reindex(dates).ffill()
                fund_daily[col] = tmp

        # 计算 PE_TTM 和 PB
        fund_daily["pe_ttm"] = close_series / fund_daily["eps"].replace(0, np.nan)
        fund_daily["pb"] = close_series / fund_daily["bps"].replace(0, np.nan)

        # 基本面因子标准化处理:
        # PE: 取负倒数 (低PE=好), clip异常值
        fund_daily["pe_ttm"] = fund_daily["pe_ttm"].clip(0, 500)
        fund_daily["pe_ttm"] = -1.0 / (fund_daily["pe_ttm"] + 1)  # 负倒数, 低PE得高分

        # PB: 取负倒数
        fund_daily["pb"] = fund_daily["pb"].clip(0, 50)
        fund_daily["pb"] = -1.0 / (fund_daily["pb"] + 0.1)

        # ROE: 直接使用 (高ROE=好)
        fund_daily["roe_ttm"] = fund_daily["roe_ttm"].fillna(0).clip(-1, 2)

        # 营收增长: 直接使用
        fund_daily["rev_growth_yoy"] = fund_daily["rev_growth_yoy"].fillna(0).clip(-2, 5)

        # 净利增长: 直接使用
        fund_daily["np_growth_yoy"] = fund_daily["np_growth_yoy"].fillna(0).clip(-5, 10)

        result[code] = fund_daily[["pe_ttm", "pb", "roe_ttm", "rev_growth_yoy", "np_growth_yoy"]]

        if (i + 1) % 50 == 0:
            logger.info(f"  基本面数据进度: {i+1}/{len(stocks)} ({len(result)} 只有效)")
        time.sleep(0.15)  # API限流

    logger.info(f"基本面因子: {len(result)}/{len(stocks)} 只股票有效")
    return result


# ============================================================
# 新闻情绪 & 行业动量
# ============================================================
def get_news_sentiment_for_industry() -> dict:
    """从 .signals.db 读取新闻情绪，按行业聚合"""
    db_path = Path(__file__).parent / ".signals.db"
    if not db_path.exists():
        return {}

    SENTIMENT_MAP = {
        "long": 1.0, "watch": 0.3, "short": -0.5, "ignore": 0.0
    }

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT industry, trade_signal, is_breaking FROM signals ORDER BY timestamp DESC LIMIT 200"
        ).fetchall()
        conn.close()

        industry_data = {}
        for industry, signal, is_breaking in rows:
            if industry not in industry_data:
                industry_data[industry] = {"sentiments": [], "breaking": 0, "total": 0}
            industry_data[industry]["sentiments"].append(SENTIMENT_MAP.get(signal, 0))
            industry_data[industry]["total"] += 1
            if is_breaking:
                industry_data[industry]["breaking"] += 1

        result = {}
        for ind, data in industry_data.items():
            sents = data["sentiments"]
            result[ind] = {
                "avg_sentiment": sum(sents) / len(sents) if sents else 0,
                "breaking_count": data["breaking"],
                "total_count": data["total"],
            }
        return result
    except Exception as e:
        logger.debug(f"新闻情绪读取失败: {e}")
        return {}


def calc_industry_momentum_from_board() -> dict:
    """通过akshare获取行业板块涨跌幅，计算行业动量"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()

        result = {}
        for _, row in df.iterrows():
            name = row["板块名称"]
            pct = row.get("涨跌幅", 0)
            result[name] = {
                "ret5": pct / 100,
                "ret10": pct / 100 * 2,
                "rank": 0.5
            }

        sorted_items = sorted(result.items(), key=lambda x: -x[1]["ret5"])
        for rank, (name, _) in enumerate(sorted_items):
            result[name]["rank"] = rank / max(len(sorted_items) - 1, 1)

        return result
    except Exception as e:
        logger.warning(f"行业动量获取失败: {e}")
        return {}


# ============================================================
# 特征计算
# ============================================================
def calc_features(df: pd.DataFrame, mf_data: list[dict] = None,
                  code: str = None, news_data: dict = None,
                  industry_data: dict = None, industry_map: dict = None,
                  fund_data: pd.DataFrame = None) -> pd.DataFrame:
    """计算因子特征（含资金流向+新闻情绪+产业动量+基本面）"""
    import math
    if df is None or len(df) < 30:
        return pd.DataFrame()

    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    low = df["low"].values

    mf_by_date = {}
    if mf_data:
        for m in mf_data:
            mf_by_date[m["date"]] = m

    if industry_map and code:
        industry = industry_map.get(code, STOCK_INDUSTRY.get(code, "其他"))
    else:
        industry = STOCK_INDUSTRY.get(code, "其他")

    ind_news = news_data.get(industry, {}) if news_data else {}
    news_sentiment = ind_news.get("avg_sentiment", 0)
    news_breaking = ind_news.get("breaking_count", 0)
    news_vol = ind_news.get("total_count", 0)

    ind_momentum = industry_data.get(industry, {}) if industry_data else {}
    ind_ret5 = ind_momentum.get("ret5", 0)
    ind_ret10 = ind_momentum.get("ret10", 0)
    ind_rank = ind_momentum.get("rank", 0.5)

    rows = []
    for i in range(21, len(df) - FUTURE_N):
        window = df.iloc[max(0, i - 300):i].copy()
        c = window["close"].values
        v = window["volume"].values
        h = window["high"].values
        l = window["low"].values

        ret5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
        ret10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
        ret20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0

        future_ret = (df.iloc[i + FUTURE_N]["close"] / c[-1] - 1) if i + FUTURE_N < len(df) else 0

        # === 资金流向因子 ===
        current_date = df.index[i].strftime("%Y-%m-%d") if hasattr(df.index[i], 'strftime') else str(df.index[i])[:10]
        if len(current_date) == 8:
            current_date = f"{current_date[:4]}-{current_date[4:6]}-{current_date[6:]}"

        mf_today = mf_by_date.get(current_date, {})
        mf_main_ratio = mf_today.get("main_net_ratio", 0)
        mf_super_ratio = mf_today.get("super_large_ratio", 0)

        mf_5d_cum = 0
        if mf_data and len(mf_data) >= 5:
            for d in range(5):
                if d < len(mf_data):
                    date_key = mf_data[-(d+1)]["date"]
                    mf_5d_cum += mf_by_date.get(date_key, {}).get("main_net_ratio", 0)

        mf_accel = 0
        if len(mf_data) >= 4:
            recent_ratios = [mf_data[-(d+1)].get("main_net_ratio", 0) for d in range(3) if d < len(mf_data)]
            if len(recent_ratios) >= 2:
                mf_accel = recent_ratios[0] - recent_ratios[-1]

        mf_price_divergence = 0
        if mf_main_ratio > 2 and (c[-1] / c[-2] - 1) < -0.01:
            mf_price_divergence = 1.0
        elif mf_main_ratio < -2 and (c[-1] / c[-2] - 1) > 0.01:
            mf_price_divergence = -1.0

        mf_trend_strength = 0
        if mf_data and len(mf_data) >= 5:
            mf_trend_strength = sum(mf_data[-(d+1)].get("main_net_ratio", 0) for d in range(5) if d < len(mf_data)) / 5

        stock_vs_industry = ret5 - ind_ret5 if ind_ret5 != 0 else 0

        # === 基本面因子 ===
        pe_ttm = 0.0
        pb_val = 0.0
        roe_ttm = 0.0
        rev_growth = 0.0
        np_growth = 0.0

        if fund_data is not None and current_date in fund_data.index:
            row_fund = fund_data.loc[current_date]
            pe_ttm = float(row_fund.get("pe_ttm", 0) or 0)
            pb_val = float(row_fund.get("pb", 0) or 0)
            roe_ttm = float(row_fund.get("roe_ttm", 0) or 0)
            rev_growth = float(row_fund.get("rev_growth_yoy", 0) or 0)
            np_growth = float(row_fund.get("np_growth_yoy", 0) or 0)

        row = {
            "date": df.index[i],
            "ret5": ret5,
            "ret10": ret10,
            "ret20": ret20,
            "vol_std20": float(np.std(v[-20:]) / (np.mean(v[-20:]) + 1)),
            "vol_ratio": v[-1] / (np.mean(v[-5:]) + 1),
            "price_std20": float(np.std(c[-20:]) / (np.mean(c[-20:]) + 1)),
            "high_low_ratio": (h[-1] - l[-1]) / (c[-1] + 0.01),
            "turn_rate": v[-1] / (np.sum(v[-20:]) / 20 + 1) if np.sum(v[-20:]) > 0 else 0,
            "ret_skew": float(pd.Series(c[-20:]).skew()) if len(c) >= 20 else 0,
            "vol_skew": float(pd.Series(v[-20:]).skew()) if len(v) >= 20 else 0,
            "pct_chg": (c[-1] / c[-2] - 1) * 100 if len(c) > 1 else 0,
            "amplitude": ((h[-1] - l[-1]) / (c[-1] + 0.01)) * 100,
            "close_ma20_ratio": c[-1] / (np.mean(c[-20:]) + 0.01),
            "ret_vs_ma5": c[-1] / (np.mean(c[-5:]) + 0.01),
            "ret_vs_ma10": c[-1] / (np.mean(c[-10:]) + 0.01),
            "vol_stability": float(np.std(v[-10:]) / (np.mean(v[-10:]) + 1)),
            "ret_accel": ret5 - ret10,
            "vol_growth": np.mean(v[-5:]) / (np.mean(v[-20:]) + 1),
            "vol_momentum": np.mean(v[-3:]) / (np.mean(v[-10:-3]) + 1),
            "rsi14": _rsi(c, 14),
            # 资金流向因子
            "mf_main_ratio": mf_main_ratio,
            "mf_super_ratio": mf_super_ratio,
            "mf_5d_cum": mf_5d_cum,
            "mf_accel": mf_accel,
            "mf_price_divergence": mf_price_divergence,
            "mf_trend_strength": mf_trend_strength,
            # 新闻情绪因子
            "news_sentiment": news_sentiment,
            "news_breaking_count": news_breaking,
            "news_volume": news_vol,
            # 产业动量因子
            "industry_ret5": ind_ret5,
            "industry_ret10": ind_ret10,
            "industry_rank": ind_rank,
            "stock_vs_industry": stock_vs_industry,
            # 基本面因子 (新增)
            "pe_ttm": pe_ttm,
            "pb": pb_val,
            "roe_ttm": roe_ttm,
            "rev_growth_yoy": rev_growth,
            "np_growth_yoy": np_growth,
            "label": future_ret,
            "label_rank": 0.0,
        }
        rows.append(row)

    result = pd.DataFrame(rows)
    if len(result) > 0:
        result = result.dropna(subset=["label"])
        result["label"] = result["label"].replace([np.inf, -np.inf], np.nan).fillna(0)
        result["label_rank"] = result["label"].rank(pct=True, na_option="bottom")
        result["label_rank"] = result["label_rank"].fillna(0.5)
    return result


def _rsi(prices: np.ndarray, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return float(100 - 100 / (1 + avg_gain / avg_loss))


# ============================================================
# IC因子筛选
# ============================================================
def filter_factors_by_ic(df_all: pd.DataFrame, candidate_cols: list[str]) -> list[str]:
    """
    按IC筛选因子: 计算每个因子与未来收益的rank IC, 保留 |mean(IC)| > threshold
    按日期分组计算截面IC
    """
    logger.info(f"开始因子IC筛选 (候选{len(candidate_cols)}个因子, 阈值{IC_THRESHOLD})...")

    ic_results = {}
    dates = sorted(df_all["date"].unique())

    for col in candidate_cols:
        ics = []
        for date in dates:
            day_data = df_all[df_all["date"] == date].dropna(subset=[col, "label"])
            if len(day_data) < 10:
                continue
            # Rank IC
            rank_corr = day_data[col].rank().corr(day_data["label"].rank())
            if not np.isnan(rank_corr):
                ics.append(rank_corr)

        if len(ics) < 20:  # 至少20天数据
            ic_results[col] = {"mean_ic": 0, "std_ic": 1, "n_days": len(ics)}
            continue

        mean_ic = np.mean(ics)
        std_ic = np.std(ics) + 1e-10
        icir = mean_ic / std_ic  # ICIR

        ic_results[col] = {
            "mean_ic": mean_ic,
            "std_ic": std_ic,
            "icir": icir,
            "n_days": len(ics),
            "positive_pct": sum(1 for x in ics if x > 0) / len(ics),
        }

    # 筛选: |mean IC| > IC_THRESHOLD
    kept = []
    dropped = []
    for col in candidate_cols:
        info = ic_results[col]
        if abs(info["mean_ic"]) > IC_THRESHOLD:
            kept.append(col)
        else:
            dropped.append((col, info["mean_ic"]))

    # 打印筛选结果
    logger.info(f"\n{'='*60}")
    logger.info(f"因子IC筛选结果 (|IC| > {IC_THRESHOLD}):")
    logger.info(f"  保留 {len(kept)}/{len(candidate_cols)} 个因子")
    logger.info(f"{'='*60}")

    # 打印所有因子IC
    all_sorted = sorted(ic_results.items(), key=lambda x: -abs(x[1]["mean_ic"]))
    for col, info in all_sorted:
        status = "✅" if col in kept else "❌"
        logger.info(f"  {status} {col:25s} IC={info['mean_ic']:+.4f}  ICIR={info.get('icir',0):+.3f}  "
                     f"正比={info.get('positive_pct',0)*100:.0f}%  天数={info['n_days']}")

    logger.info(f"{'='*60}")

    # 保存IC结果
    ic_out = MODEL_DIR / "factor_ic.json"
    ic_save = {k: {kk: vv for kk, vv in v.items() if isinstance(vv, (int, float))}
               for k, v in ic_results.items()}
    with open(ic_out, "w") as f:
        json.dump(ic_save, f, indent=2, default=str)
    logger.info(f"IC结果已保存: {ic_out}")

    return kept


# ============================================================
# 主训练流程
# ============================================================
def main():
    global FEATURE_COLS

    logger.info(f"=== Ranker v2 训练开始: {N_STOCKS}只 x {N_DAYS}天 ===")
    logger.info(f"候选因子: {len(ALL_CANDIDATE_COLS)} 个 (技术{len(TECH_COLS)} + 资金流{len(MF_COLS)} "
                f"+ 新闻{len(NEWS_COLS)} + 产业{len(INDUSTRY_COLS)} + 基本面{len(FUND_COLS)})")
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Step 1: 获取全市场股票列表
    stocks = get_sample_stocks(N_STOCKS)
    if not stocks:
        logger.error("获取股票列表失败")
        return
    logger.info(f"候选股票: {len(stocks)} 只")

    # Step 2: 获取K线数据 (先缓存, 用于基本面计算)
    logger.info("获取K线数据...")
    kline_cache = {}
    for i, code in enumerate(stocks):
        df = get_kline_em(code, count=N_DAYS + FUTURE_N + 30)
        if not df.empty and len(df) >= 50:
            kline_cache[code] = df
        if (i + 1) % 50 == 0:
            logger.info(f"K线进度: {i+1}/{len(stocks)} ({len(kline_cache)} 只有效)")
        time.sleep(0.1)

    logger.info(f"有效K线: {len(kline_cache)} 只")

    # Step 3: 构建行业映射
    logger.info("构建行业映射...")
    industry_map = build_industry_map()

    # Step 4: 加载新闻情绪
    news_data = get_news_sentiment_for_industry()
    logger.info(f"新闻情绪: {len(news_data)} 个行业")

    # Step 5: 计算行业动量
    logger.info("计算行业动量...")
    industry_data = calc_industry_momentum_from_board()
    logger.info(f"行业动量: {len(industry_data)} 个行业")

    # Step 6: 获取基本面因子
    fund_cache = build_fundamentals_daily(stocks, kline_cache)

    # Step 7: 逐只计算特征
    logger.info("计算特征...")
    all_features = []
    for i, code in enumerate(stocks):
        if code not in kline_cache:
            continue

        df = kline_cache[code]
        mf_data = get_money_flow_em(code, days=30)
        fund_data = fund_cache.get(code, None)

        feats = calc_features(df, mf_data=mf_data, code=code,
                             news_data=news_data, industry_data=industry_data,
                             industry_map=industry_map, fund_data=fund_data)
        if not feats.empty:
            feats["code"] = code
            all_features.append(feats)
        if (i + 1) % 50 == 0:
            logger.info(f"特征进度: {i+1}/{len(stocks)} ({len(all_features)} 只有效)")
        time.sleep(0.1)

    if not all_features:
        logger.error("没有有效的特征数据")
        return

    # Step 8: 合并并清理
    df_all = pd.concat(all_features, ignore_index=True)
    df_all = df_all.dropna(subset=ALL_CANDIDATE_COLS + ["label"], thresh=len(ALL_CANDIDATE_COLS))
    for col in ALL_CANDIDATE_COLS:
        df_all[col] = df_all[col].fillna(0).replace([np.inf, -np.inf], 0)
    logger.info(f"总样本数: {len(df_all)} 条, {len(df_all['code'].unique())} 只股票")

    # Step 9: IC因子筛选
    FEATURE_COLS = filter_factors_by_ic(df_all, ALL_CANDIDATE_COLS)
    logger.info(f"筛选后因子数: {len(FEATURE_COLS)}")
    logger.info(f"因子列表: {FEATURE_COLS}")

    # Step 10: Walk-forward 训练 + 验证
    dates = sorted(df_all["date"].unique())
    if len(dates) < 60:
        logger.error(f"日期太少: {len(dates)}，无法做walk-forward")
        return

    train_end = int(len(dates) * 0.6)
    val_end = int(len(dates) * 0.8)
    train_dates = set(dates[:train_end])
    val_dates = set(dates[train_end:val_end])
    test_dates = set(dates[val_end:])

    df_train = df_all[df_all["date"].isin(train_dates)]
    df_val = df_all[df_all["date"].isin(val_dates)]
    df_test = df_all[df_all["date"].isin(test_dates)]

    logger.info(f"训练集: {len(df_train)} 条 ({len(train_dates)} 天)")
    logger.info(f"验证集: {len(df_val)} 条 ({len(val_dates)} 天)")
    logger.info(f"测试集: {len(df_test)} 条 ({len(test_dates)} 天) ← 完全样本外")

    # Step 11: 模型训练 — 防过拟合参数
    best_model = None
    best_corr = -1
    best_params = None

    param_grid = [
        {"max_iter": 50, "max_depth": 2, "learning_rate": 0.1, "min_samples_leaf": 100},
        {"max_iter": 50, "max_depth": 2, "learning_rate": 0.05, "min_samples_leaf": 100},
        {"max_iter": 80, "max_depth": 2, "learning_rate": 0.05, "min_samples_leaf": 80},
    ]

    X_train = df_train[FEATURE_COLS].values
    y_train = df_train["label_rank"].values
    X_val = df_val[FEATURE_COLS].values
    y_val = df_val["label_rank"].values
    X_test = df_test[FEATURE_COLS].values
    y_test = df_test["label_rank"].values

    for params in param_grid:
        m = HistGradientBoostingRegressor(random_state=42, **params)
        m.fit(X_train, y_train)

        y_val_pred = m.predict(X_val)
        val_corr = np.corrcoef(y_val_pred, y_val)[0, 1]

        y_test_pred = m.predict(X_test)
        test_corr = np.corrcoef(y_test_pred, y_test)[0, 1]

        tag = "✅ BEST" if val_corr > best_corr else ""
        logger.info(f"  参数: {params}")
        logger.info(f"  验证相关: {val_corr:.3f}, 测试相关: {test_corr:.3f} {tag}")

        if val_corr > best_corr:
            best_corr = val_corr
            best_model = m
            best_params = params

    logger.info(f"\n最佳参数: {best_params}")
    logger.info(f"验证集相关性: {best_corr:.3f}")
    logger.info(f"测试集相关性(样本外): {test_corr:.3f}")

    # Step 12: 保存模型 + 因子列表
    model_data = {
        "model": best_model,
        "feature_cols": FEATURE_COLS,
        "all_candidate_cols": ALL_CANDIDATE_COLS,
        "params": best_params,
    }
    with open(MODEL_OUT, "wb") as f:
        pickle.dump(model_data, f)
    logger.info(f"模型已保存: {MODEL_OUT}")

    # Step 13: 特征重要性
    logger.info("\n=== Top 10 重要因子 ===")
    from sklearn.inspection import permutation_importance
    result = permutation_importance(best_model, X_test, y_test, n_repeats=10, random_state=42)
    importance = result.importances_mean
    sorted_idx = np.argsort(importance)[::-1][:10]
    for i, idx in enumerate(sorted_idx, 1):
        logger.info(f"  #{i}: {FEATURE_COLS[idx]:25s} ({importance[idx]:.4f})")


if __name__ == "__main__":
    main()
