# -*- encoding: utf-8 -*-
"""
双数据源分钟级选股 — QuantFly 核心脚本
=======================================
支持: QMT Mini (xtquant) + akshare 双数据源
用法: python qmt_live_rank.py [--mode live|backtest] [--top 30]

运行方式:
  Windows: python qmt_live_rank.py
  Mac (测试): python qmt_live_rank.py --source akshare
"""
import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ============================================================
# 配置
# ============================================================
FEISHU_APP_ID = "cli_a97e6559e9b8dbd5"
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "vWE2WoYyHu3JepDj0gOjUdDnLvAwwsgk")
FEISHU_CHAT_ID = "oc_588a05c5a177864a9bc6635a05ddf4ba"
FEISHU_ENABLED = True  # False则跳过推送

MODEL_PATH = Path(__file__).parent / "model" / "ranker_model.pkl"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("LiveRank")


# ============================================================
# 数据源层
# ============================================================

def get_xtquant_minute_data(codes: list[str], count: int = 240) -> dict[str, pd.DataFrame]:
    """
    通过 xtquant 获取分钟K线（QMT Mini 实盘数据）
    codes: ["000001.XSHG", "600256.XSHG", ...]
    返回: {code: DataFrame(index=datetime, columns=[open,high,low,close,volume,amount])}
    """
    try:
        import xtquant.xtdatacenter as dc
        import xtquant.xtconstant as xtc
    except ImportError:
        logger.warning("xtquant 未安装，无法使用 QMT 数据")
        return {}

    try:
        # 连接本地QMT Mini数据服务（默认端口5860）
        dc.set_data_back_addr("127.0.0.1:5860")

        result = {}
        # xtquant 需要不带后缀的代码
        clean_codes = [c.split(".")[0] for c in codes]

        data = dc.get_market_data(
            stock_list=clean_codes,
            start_time=None,
            end_time=None,
            count=count,
            period="1m",
            fields=["open", "high", "low", "close", "volume", "amount"],
            dividend_type="none",
        )

        if data is None or data.empty:
            logger.warning("QMT返回空数据")
            return {}

        # data是DataFrame，index是时间
        for code in codes:
            clean = code.split(".")[0]
            if clean in data.columns.get_level_values(1):
                df = data[clean].droplevel(1, axis=1) if isinstance(data.columns, pd.MultiIndex) else data
                result[code] = df.copy()
            elif code in data.columns:
                result[code] = data[code].copy()

        logger.info(f"QMT数据获取成功: {len(result)}/{len(codes)} 只")
        return result

    except Exception as e:
        logger.warning(f"QMT数据获取失败: {e}")
        return {}


def get_akshare_minute_data(codes: list[str], count: int = 240) -> dict[str, pd.DataFrame]:
    """
    akshare 获取分钟K线（备用/回测数据）
    注意：akshare盘中数据有15分钟延迟，收盘后更新
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare 未安装")
        return {}

    import concurrent.futures
    from typing import Optional, Tuple

    result = {}

    def _fetch_one(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
        try:
            mkt = "sh" if code.startswith(("6", "9")) else "sz"
            symbol = f"{mkt}{code.split('.')[0]}"
            df = ak.stock_zh_a_minute(symbol=symbol, period="1", adjust="qfq")
            if df is not None and len(df) > 0:
                df = df.tail(count).copy()
                if "date" not in df.columns and "时间" in df.columns:
                    df = df.rename(columns={"时间": "date"})
                if "open" not in df.columns and "开盘" in df.columns:
                    df = df.rename(columns={"开盘": "open", "收盘": "close",
                                           "最高": "high", "最低": "low",
                                           "成交量": "volume"})
                return (code, df)
        except Exception as e:
            logger.debug(f"akshare {code} 失败: {e}")
        return (code, None)

    # 并发拉取，每只股票5s超时
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch_one, c): c for c in codes}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=30):
                try:
                    code, df = future.result(timeout=5)
                    if df is not None:
                        result[code] = df
                except Exception:
                    pass
        except concurrent.futures.TimeoutError:
            pass  # 未完成的future直接丢弃

    logger.info(f"akshare数据获取: {len(result)}/{len(codes)} 只")
    return result


def get_sina_realtime(codes: list[str]) -> pd.DataFrame:
    """
    新浪实时行情（涨幅/量比/价格）— 替代东方财富
    """
    _sina_session = requests.Session()
    _sina_session.trust_env = False
    SINA_QUOTE = "https://hq.sinajs.cn/list="

    df_list = []
    # 每批20只
    for i in range(0, len(codes), 20):
        batch = codes[i:i + 20]
        # 拼装sina代码
        symbols = ",".join(
            f"sh{c}" if c.startswith(("6", "9")) else f"sz{c}"
            for c in batch
        )
        try:
            r = _sina_session.get(
                SINA_QUOTE + symbols,
                headers={
                    "Referer": "https://finance.sina.com.cn",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=8,
            )
            r.encoding = "gbk"
            for line in r.text.strip().split("\n"):
                if "hq_str_" not in line:
                    continue
                # var hq_str_sh000001="name,open,yclose,close,high,low,...,...
                try:
                    content = line.split('="')[1].rstrip('";')
                    parts = content.split(",")
                    if len(parts) < 10:
                        continue
                    code = line.split('hq_str_')[1].split("=")[0]
                    code = code.replace("sh", "").replace("sz", "")
                    df_list.append({
                        "code": code,
                        "name": parts[0],
                        "open": float(parts[1]) if parts[1] else 0,
                        "yclose": float(parts[2]) if parts[2] else 0,
                        "close": float(parts[3]) if parts[3] else 0,
                        "high": float(parts[4]) if parts[4] else 0,
                        "low": float(parts[5]) if parts[5] else 0,
                        "pct_chg": float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                        "volume": float(parts[8]) if parts[8] else 0,
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"新浪实时行情批次{i}失败: {e}")
        time.sleep(0.05)

    return pd.DataFrame(df_list) if df_list else pd.DataFrame()


# ============================================================
# 因子计算层（20因子）
# ============================================================

def calc_factors(df: pd.DataFrame) -> dict:
    """
    计算20个因子
    df要求: columns=[date,open,high,low,close,volume,amount]
    """
    if df is None or len(df) < 20:
        return {}

    close = df["close"].astype(float).values
    volume = df["volume"].astype(float).values if "volume" in df.columns else np.zeros(len(df))
    high = df["high"].astype(float).values if "high" in df.columns else close
    low = df["low"].astype(float).values if "low" in df.columns else close
    open_ = df["open"].astype(float).values if "open" in df.columns else close

    ret5 = (close[-1] / close[-6] - 1) if len(close) > 5 else 0
    ret10 = (close[-1] / close[-11] - 1) if len(close) > 10 else 0
    ret20 = (close[-1] / close[-21] - 1) if len(close) > 20 else 0

    # 1-10 量价波动
    factors = {
        # 量价波动 (10)
        "ret5": ret5,
        "ret10": ret10,
        "ret20": ret20,
        "vol_std20": np.std(volume[-20:]) / (np.mean(volume[-20:]) + 1),
        "vol_ratio": volume[-1] / (np.mean(volume[-5:]) + 1),
        "price_std20": np.std(close[-20:]) / (np.mean(close[-20:]) + 1),
        "high_low_ratio": (high[-1] - low[-1]) / (close[-1] + 0.01),
        "turn_rate": (volume[-1] / (volume[-20:].sum() / 20 + 1)) if volume[-20:].sum() > 0 else 0,
        "ret_skew": float(pd.Series(close[-20:]).skew()) if len(close) >= 20 else 0,
        "vol_skew": float(pd.Series(volume[-20:]).skew()) if len(volume) >= 20 else 0,

        # 情绪 (2)
        "pct_chg": (close[-1] / close[-2] - 1) * 100 if len(close) > 1 else 0,
        "amplitude": ((high[-1] - low[-1]) / (close[-1] + 0.01)) * 100,

        # 价值 (1)
        "close_ma20_ratio": close[-1] / (np.mean(close[-20:]) + 0.01),

        # 盈利 (2)
        "ret_vs_ma5": close[-1] / (np.mean(close[-5:]) + 0.01),
        "ret_vs_ma10": close[-1] / (np.mean(close[-10:]) + 0.01),

        # 分红 (1)
        "vol_stability": np.std(volume[-10:]) / (np.mean(volume[-10:]) + 1),

        # 成长 (3)
        "ret_accel": ret5 - ret10,
        "vol_growth": np.mean(volume[-5:]) / (np.mean(volume[-20:]) + 1),
        "vol_momentum": np.mean(volume[-3:]) / (np.mean(volume[-10:-3]) + 1),

        # 技术 (1)
        "rsi14": _calc_rsi(close, 14),
    }

    # 清理NaN
    for k, v in factors.items():
        if not isinstance(v, (int, float)) or math.isnan(v):
            factors[k] = 0.0

    return factors


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
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


# ============================================================
# 排序层（机器学习Ranker）
# ============================================================

def load_ranker():
    """加载预训练排序模型"""
    import pickle
    if not MODEL_PATH.exists():
        logger.warning(f"模型不存在 {MODEL_PATH}，使用规则排序替代")
        return None
    with open(MODEL_PATH, "rb") as f:
        model = pickle.load(f)
    logger.info("Ranker模型加载成功")
    return model


def rank_stocks(factor_df: pd.DataFrame, model) -> pd.DataFrame:
    """
    用Ranker对股票排序
    factor_df: index=code, columns=20个因子值
    """
    FEATURE_COLS = [
        "ret5", "ret10", "ret20", "vol_std20", "vol_ratio", "price_std20",
        "high_low_ratio", "turn_rate", "ret_skew", "vol_skew",
        "pct_chg", "amplitude", "close_ma20_ratio",
        "ret_vs_ma5", "ret_vs_ma10", "vol_stability",
        "ret_accel", "vol_growth", "vol_momentum", "rsi14",
    ]

    # 过滤有效因子行
    df = factor_df.dropna(subset=FEATURE_COLS, thresh=15).copy()
    if df.empty:
        return pd.DataFrame()

    # 缺失值填充
    for col in FEATURE_COLS:
        if col in df.columns:
            df[col] = df[col].fillna(0).replace([np.inf, -np.inf], 0)

    if model is None:
        # 规则排序：综合因子
        df["score"] = (
            df["pct_chg"].rank(pct=True) * 0.3 +
            df["vol_ratio"].rank(pct=True) * 0.25 +
            df["ret5"].rank(pct=True) * 0.2 +
            df["rsi14"].rank(pct=True) * 0.15 +
            df["vol_growth"].rank(pct=True) * 0.1
        )
    else:
        X = df[FEATURE_COLS].values
        df["score"] = model.predict(X)

    df = df.sort_values("score", ascending=False)
    return df


# ============================================================
# 选股范围（中证全指成分 + 热门行业）
# ============================================================

def get_universe() -> list[str]:
    """
    返回候选股票池（A股全市场 ≈ 5000只）
    使用 akshare 全量股票列表，排除科创板(688)
    """
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        # 排除科创板（分钟数据质量差）
        df = df[~df["code"].str.startswith("688")]
        codes = df["code"].tolist()[:100]
        logger.info(f"全市场股票数量: {len(df)}，候选前100只")
        return codes
    except Exception as e:
        logger.warning(f"获取股票列表失败: {e}")
        return []


# ============================================================
# 飞书推送
# ============================================================

def push_to_feishu(text: str) -> bool:
    """推送文本到飞书群"""
    if not FEISHU_ENABLED:
        return False
    try:
        # 获取token
        req = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        token = req.json().get("tenant_access_token", "")
        if not token:
            logger.warning("飞书token获取失败")
            return False

        # 发消息
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json={"receive_id": FEISHU_CHAT_ID, "msg_type": "text",
                  "content": json.dumps({"text": text})},
            timeout=10,
        )
        result = resp.json()
        if result.get("code") == 0:
            logger.info(f"飞书推送成功")
            return True
        else:
            logger.warning(f"飞书推送失败: {result.get('msg')}")
            return False
    except Exception as e:
        logger.warning(f"飞书推送异常: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def build_factor_df(codes: list[str], minute_data: dict[str, pd.DataFrame],
                    realtime: pd.DataFrame) -> pd.DataFrame:
    """计算所有股票的20因子"""
    rows = []
    for code in codes:
        df = minute_data.get(code)
        factors = calc_factors(df) if df is not None else {}

        # 补充实时行情字段
        r = realtime[realtime["code"] == code.split(".")[0]] if not realtime.empty else pd.DataFrame()
        if not r.empty:
            row = r.iloc[0].to_dict()
            row.update(factors)
            row["code"] = code
            rows.append(row)
        elif factors:
            factors["code"] = code
            rows.append(factors)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("code")
    return df


def format_top_stocks(df: pd.DataFrame, top_n: int = 30) -> str:
    """格式化Top股票输出"""
    if df.empty:
        return "今日无信号（市场无异动）"

    top = df.head(top_n)
    lines = [f"📊 QuantFly Top{int(top_n)}信号 {datetime.now().strftime('%m-%d %H:%M')}\n"]

    for i, (code, row) in enumerate(top.iterrows(), 1):
        name = row.get("name", code)
        pct = row.get("pct_chg", 0)
        score = row.get("score", 0)
        vol_r = row.get("vol_ratio", 0)
        rsi = row.get("rsi14", 50)

        # 标签
        tags = []
        if pct > 5:
            tags.append("🔥强势")
        if vol_r > 1.5:
            tags.append("💥放量")
        if rsi < 40:
            tags.append("📉超卖")
        elif rsi > 70:
            tags.append("📈RSI高")
        tag_str = " ".join(tags) if tags else ""

        lines.append(
            f"{i:2d}. {name}({code}) "
            f"涨{pct:+.1f}% 分{score:.2f} 量比{vol_r:.1f}x "
            f"RSI{rsi:.0f} {tag_str}"
        )

    lines.append(f"\n⏰ {datetime.now().strftime('%H:%M:%S')}")
    return "\n".join(lines)


def run(source: str = "qmt", top_n: int = 30, push: bool = True):
    """
    主运行函数

    Args:
        source: 数据源 "qmt", "akshare", "auto"(双数据源)
        top_n: 返回Top N
        push: 是否推送飞书
    """
    logger.info(f"=== QuantFly 选股启动 (source={source}) ===")

    # Step 1: 获取候选股票池
    codes = get_universe()
    if not codes:
        logger.error("候选股票池为空，退出")
        return
    logger.info(f"候选股票: {len(codes)} 只")

    # Step 2: 获取分钟K线数据（双数据源）
    minute_data = {}
    source_used = "none"

    if source in ("qmt", "auto"):
        minute_data = get_xtquant_minute_data(codes, count=240)
        if minute_data:
            source_used = "QMT"
        elif source == "qmt":
            logger.error("QMT数据获取失败，退出")
            return

    if not minute_data and source in ("akshare", "auto"):
        logger.info("切换到 akshare 数据源")
        minute_data = get_akshare_minute_data(codes, count=240)
        if minute_data:
            source_used = "akshare"

    if not minute_data:
        logger.error("所有数据源均失败，退出")
        return

    logger.info(f"数据获取完成 ({source_used}): {len(minute_data)} 只")

    # Step 3: 获取实时行情
    realtime = get_sina_realtime(list(minute_data.keys()))
    logger.info(f"实时行情: {len(realtime)} 只")

    # Step 4: 计算20因子
    factor_df = build_factor_df(list(minute_data.keys()), minute_data, realtime)
    if factor_df.empty:
        logger.error("因子计算为空")
        return
    logger.info(f"因子计算完成: {len(factor_df)} 只")

    # Step 5: Ranker排序
    model = load_ranker()
    ranked = rank_stocks(factor_df, model)
    if ranked.empty:
        logger.error("排序结果为空")
        return

    # Step 6: 输出结果
    result_text = format_top_stocks(ranked, top_n=top_n)
    print("\n" + result_text + "\n")

    # 保存结果
    out_file = OUTPUT_DIR / f"rank_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    ranked.reset_index().to_csv(out_file, index=False)
    logger.info(f"结果已保存: {out_file}")

    # Step 7: 飞书推送
    if push:
        push_to_feishu(result_text)

    return ranked


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuantFly 双数据源分钟级选股")
    parser.add_argument("--source", choices=["qmt", "akshare", "auto"], default="auto",
                        help="数据源: qmt(迅投)|akshare(备用)|auto(自动切换)")
    parser.add_argument("--top", type=int, default=30, help="输出Top N")
    parser.add_argument("--no-push", action="store_true", help="跳过飞书推送")
    args = parser.parse_args()

    run(source=args.source, top_n=args.top, push=not args.no_push)
