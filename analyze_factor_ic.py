# -*- encoding: utf-8 -*-
"""
因子IC分析 — analyze_factor_ic.py
==================================
测量每个因子与未来收益的相关性（IC = Information Coefficient）

用法:
  Mac测试:    python analyze_factor_ic.py --source akshare --days 300
  Windows生产: python analyze_factor_ic.py --source qmt --days 300 --output ic_report.json

IC解读:
  |IC| > 0.05   : 因子有效
  |IC| > 0.1    : 因子较强
  |IC| > 0.15   : 因子很强
  IR (IC均值/IC标准差) > 0.5: 因子稳定
"""
import argparse
import json
import logging
import math
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests  # 必须顶层导入

# ============================================================
# 配置
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("FactorIC")

FEATURE_COLS = [
    "ret5", "ret10", "ret20", "vol_std20", "vol_ratio", "price_std20",
    "high_low_ratio", "turn_rate", "ret_skew", "vol_skew",
    "pct_chg", "amplitude", "close_ma20_ratio",
    "ret_vs_ma5", "ret_vs_ma10", "vol_stability",
    "ret_accel", "vol_growth", "vol_momentum", "rsi14",
]


# ============================================================
# 数据获取
# ============================================================
def get_xtquant_daily(codes: list[str], count: int = 300) -> dict[str, pd.DataFrame]:
    """QMT日线数据（Windows用）"""
    try:
        from xtquant import xtdata
    except ImportError:
        logger.warning("xtquant 未安装")
        return {}

    def _fmt(c: str) -> str:
        c = c.strip()
        if c.startswith(("6", "9")):
            return f"{c}.SH"
        if c.endswith((".SH", ".SZ")):
            return c
        return f"{c}.SZ"

    try:
        # 连接 MiniQMT
        xtdata.connect()

        mqcodes = [_fmt(c) for c in codes]
        fields = ["open", "high", "low", "close", "volume", "amount"]

        # 批量下载历史数据（先下载才能获取）
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        # 估算起始日期：count根日K约需 count/250 + 1 年
        start_year = datetime.now().year - (count // 250 + 1)
        start = f"{start_year}0101"

        logger.info(f"下载 {len(mqcodes)} 只股票历史数据 ({start}~{today})...")
        for mqcode in mqcodes:
            try:
                xtdata.download_history_data(mqcode, "1d", start, today)
            except Exception:
                pass
            time.sleep(0.02)  # 避免太快

        # 批量获取 K 线（前复权，与东方财富 fqt=1 对齐）
        result_raw = xtdata.get_market_data(
            stock_list=mqcodes,
            period="1d",
            count=count,
            field_list=fields,
            dividend_type="front",
        )

        # 解析返回格式: {field: DataFrame(index=股票代码, columns=日期)}
        result = {}
        for mqcode in mqcodes:
            raw_code = mqcode.replace(".SH", "").replace(".SZ", "")
            rows = {}
            for field in fields:
                df = result_raw.get(field, pd.DataFrame())
                if not df.empty and mqcode in df.index:
                    rows[field] = df.loc[mqcode]

            if len(rows) >= 4:  # 至少 OHLC 四个字段
                # 跳过数据不足的股票（loc返回scalar = 只有1行数据）
                if any(not isinstance(v, pd.Series) for v in rows.values()):
                    continue
                out = pd.DataFrame(rows)
                out.index = pd.to_datetime(out.index.astype(str), format="%Y%m%d")
                out = out.sort_index().tail(count)
                if "amount" not in out.columns:
                    out["amount"] = 0
                result[raw_code] = out

        logger.info(f"QMT日线获取: {len(result)}/{len(codes)} 只")
        return result
    except Exception as e:
        logger.warning(f"QMT数据失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def get_eastmoney_daily(codes: list[str], count: int = 300) -> dict[str, pd.DataFrame]:
    """东方财富日线（Mac/测试用）"""
    EM_HIST = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    EM_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    }
    result = {}
    for code in codes:
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
            if records:
                result[code] = pd.DataFrame(records).set_index("date").sort_index()
        except Exception:
            pass
        time.sleep(0.12)

    logger.info(f"EM日线获取: {len(result)}/{len(codes)} 只")
    return result


def get_csi500_stocks() -> list[str]:
    """获取中证500成分股（akshare，固定股票池）"""
    try:
        import akshare as ak
        df = ak.index_stock_cons(symbol="000905")
        codes = df["品种代码"].tolist()
        logger.info(f"中证500成分股: {len(codes)} 只")
        return codes
    except Exception as e:
        logger.warning(f"中证500获取失败({e})，降级行业龙头")
        return _get_eastmoney_leaders(n=200)


def _get_eastmoney_leaders(n: int = 200) -> list[str]:
    """东方财富各行业龙头（备用）"""
    EM_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    }
    params = {
        "pn": 1, "pz": n, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12",
    }
    try:
        r = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                         params=params, headers=EM_HEADERS, timeout=10)
        data = r.json().get("data", {}).get("diff", [])
        return [str(x["f12"]) for x in data[:n]]
    except Exception:
        return []


def get_sample_stocks(n: int = 100) -> list[str]:
    """统一入口：优先中证500，失败则降级行业龙头"""
    return get_csi500_stocks()


# ============================================================
# 因子计算（与 train_ranker.py / qmt_live_rank.py 完全对齐）
# ============================================================
def calc_features(df: pd.DataFrame, future_n: int = 5) -> pd.DataFrame:
    """计算所有因子 + 未来收益标签"""
    if df is None or len(df) < 30:
        return pd.DataFrame()

    close = df["close"].astype(float).values
    volume = df["volume"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values

    rows = []
    for i in range(21, len(df) - future_n):
        c = close[:i+1]
        v = volume[:i+1]
        h = high[:i+1]
        l = low[:i+1]

        ret5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
        ret10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
        ret20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0
        future_ret = (close[i + future_n] / c[-1] - 1) if i + future_n < len(close) else 0

        rows.append({
            "date": df.index[i],
            "ret5": ret5,
            "ret10": ret10,
            "ret20": ret20,
            "vol_std20": float(np.std(v[-20:]) / (np.mean(v[-20:]) + 1)),
            "vol_ratio": v[-1] / (np.mean(v[-5:]) + 1),
            "price_std20": float(np.std(c[-20:]) / (np.mean(c[-20:]) + 1)),
            "high_low_ratio": (h[-1] - l[-1]) / (c[-1] + 0.01),
            "turn_rate": v[-1] / (v[-20:].sum() / 20 + 1) if v[-20:].sum() > 0 else 0,
            "ret_skew": float(pd.Series(c[-20:]).skew()) if len(c) >= 20 else 0,
            "vol_skew": float(pd.Series(v[-20:]).skew()) if len(v) >= 20 else 0,
            "pct_chg": (c[-1] / c[-2] - 1) * 100 if len(c) > 1 else 0,
            "amplitude": ((h[-1] - l[-1]) / (c[-1] + 0.01)) * 100,
            "close_ma20_ratio": c[-1] / (np.mean(c[-20:]) + 0.01),
            "ret_vs_ma5": c[-1] / (np.mean(c[-5:]) + 0.01) if len(c) >= 5 else 1,
            "ret_vs_ma10": c[-1] / (np.mean(c[-10:]) + 0.01) if len(c) >= 10 else 1,
            "vol_stability": float(np.std(v[-10:]) / (np.mean(v[-10:]) + 1)),
            "ret_accel": ret5 - ret10,
            "vol_growth": np.mean(v[-5:]) / (np.mean(v[-20:]) + 1),
            "vol_momentum": np.mean(v[-3:]) / (np.mean(v[-10:-3]) + 1) if len(v) >= 10 else 1,
            "rsi14": _calc_rsi(c, 14),
            "future_ret": future_ret,
        })

    return pd.DataFrame(rows)


def _calc_rsi(prices: np.ndarray, period: int = 14) -> float:
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
# IC计算
# ============================================================
def calc_cross_section_ic(df_all: pd.DataFrame) -> dict:
    """截面IC：每天计算因子与未来收益的Pearson相关系数"""
    daily_ics = {col: [] for col in FEATURE_COLS}

    for date, group in df_all.groupby("date"):
        if len(group) < 10:
            continue
        future = group["future_ret"].values
        for col in FEATURE_COLS:
            vals = group[col].values
            mask = np.isfinite(vals) & np.isfinite(future)
            if mask.sum() < 5:
                continue
            corr = np.corrcoef(vals[mask], future[mask])[0, 1]
            if np.isfinite(corr):
                daily_ics[col].append(corr)

    result = {}
    for col in FEATURE_COLS:
        ic_list = daily_ics[col]
        if ic_list:
            ic_arr = np.array(ic_list)
            result[col] = {
                "ic_mean": float(np.mean(ic_arr)),
                "ic_std": float(np.std(ic_arr)),
                "ic_ir": float(np.mean(ic_arr) / (np.std(ic_arr) + 1e-10)),
                "n_days": len(ic_list),
                "positive_rate": float(np.mean(ic_arr > 0)),
            }
        else:
            result[col] = {"ic_mean": 0, "ic_std": 0, "ic_ir": 0, "n_days": 0, "positive_rate": 0}

    return result


def rank_factors(ic_results: dict) -> list:
    """按|IC|排序因子"""
    return sorted(ic_results.items(), key=lambda x: abs(x[1]["ic_mean"]), reverse=True)


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="akshare", choices=["qmt", "akshare"])
    parser.add_argument("--days", type=int, default=300)
    parser.add_argument("--stocks", type=int, default=100)
    parser.add_argument("--output", default="output/factor_ic_report.json")
    args = parser.parse_args()

    logger.info(f"=== 因子IC分析 | {args.source} | {args.days}天 | {args.stocks}只 ===")

    # 1. 股票列表
    stocks = get_sample_stocks(args.stocks)
    if not stocks:
        logger.error("获取股票列表失败"); return
    logger.info(f"候选股票: {len(stocks)} 只")

    # 2. 数据
    t0 = time.time()
    if args.source == "qmt":
        data = get_xtquant_daily(stocks, count=args.days + 30)
    else:
        data = get_eastmoney_daily(stocks, count=args.days + 30)
    fetch_time = time.time() - t0
    if len(data) < 10:
        logger.error(f"有效数据不足: {len(data)} 只"); return
    logger.info(f"数据获取: {len(data)}只, {fetch_time:.1f}s")

    # 3. 因子
    t1 = time.time()
    all_features = []
    for code, df in data.items():
        feats = calc_features(df)
        if not feats.empty:
            feats["code"] = code
            all_features.append(feats)

    if not all_features:
        logger.error("无有效因子"); return

    df_all = pd.concat(all_features, ignore_index=True)
    for col in FEATURE_COLS + ["future_ret"]:
        if col in df_all.columns:
            df_all[col] = df_all[col].replace([np.inf, -np.inf], np.nan).fillna(0)
    feat_time = time.time() - t1
    logger.info(f"因子计算: {len(df_all)}条, {feat_time:.1f}s")

    # 4. IC分析
    ic_results = calc_cross_section_ic(df_all)
    ranked = rank_factors(ic_results)

    # 5. 打印
    logger.info("\n" + "=" * 62)
    logger.info("📊 因子IC排名（按|IC均值|排序）")
    logger.info("=" * 62)
    logger.info(f"{'因子':<18} {'IC均值':>8} {'IC标准差':>8} {'IR':>7} {'胜率':>6}  评价")
    logger.info("-" * 62)
    for name, d in ranked:
        strength = "★★★强" if abs(d["ic_mean"]) > 0.08 \
                   else "★★中" if abs(d["ic_mean"]) > 0.04 \
                   else "★弱" if abs(d["ic_mean"]) > 0.02 else "—无效"
        flag = "✅" if d["ic_mean"] > 0.04 else "🔻" if d["ic_mean"] < -0.04 else "  "
        logger.info(
            f"{flag}{name:<16} {d['ic_mean']:>+8.4f} {d['ic_std']:>8.4f} "
            f"{d['ic_ir']:>+7.3f} {d['positive_rate']*100:>5.0f}%  {strength}"
        )

    top_ics = [(n, d["ic_mean"], d["ic_ir"]) for n, d in ranked[:5]]
    logger.info(f"\n✅ Top5: {', '.join(f'{n}({v:+.3f})' for n,v,_ in top_ics)}")

    # 6. 保存
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    report = {
        "meta": {
            "source": args.source,
            "n_stocks": len(data), "n_records": len(df_all),
            "generated_at": datetime.now().isoformat(),
        },
        "ranked": [{"factor": n, **d} for n, d in ranked],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"报告: {args.output}")
    return report


if __name__ == "__main__":
    main()
