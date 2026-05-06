# -*- encoding: utf-8 -*-
"""
Walk-Forward 回测（预计算优化版）
==================================
策略：一次性算完所有日期的因子，滚动窗口直接查表
速度：90只×8窗口 全量计算 约 3-5 分钟（原来 >10 分钟）

用法: python backtest_ranker_fast.py
"""
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import HistGradientBoostingRegressor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("BacktestRankerFast")

MODEL_DIR = Path(__file__).parent / "model"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ============================================================
# 配置
# ============================================================
TRAIN_DAYS = 200
PREDICT_DAYS = 10
TOTAL_WINDOWS = 8
TOP_N = 5
UNIVERSE_SIZE = 100

FEATURE_COLS = [
    "ret5", "ret10", "ret20", "vol_std20", "vol_ratio", "price_std20",
    "high_low_ratio", "turn_rate", "ret_skew", "vol_skew",
    "pct_chg", "amplitude", "close_ma20_ratio",
    "ret_vs_ma5", "ret_vs_ma10", "vol_stability",
    "ret_accel", "vol_growth", "vol_momentum", "rsi14",
]

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}


# ============================================================
# 数据获取
# ============================================================

def get_sample_stocks(n: int = 100) -> list[dict]:
    """获取各行业龙头（按市值降序，排除科创板）"""
    try:
        params = {
            "pn": 1, "pz": n * 3,
            "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2,
            "fid": "f20",  # 市值降序
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14",
        }
        r = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                          params=params, headers=EM_HEADERS, timeout=10)
        data = r.json().get("data", {}).get("diff", [])
        stocks = [{"code": str(x["f12"]), "name": x.get("f14", "")} for x in data
                  if not str(x["f12"]).startswith("688")][:n]
        return stocks
    except Exception as e:
        logger.warning(f"获取股票列表失败: {e}")
        return []


def get_daily_em(code: str, days: int = 600) -> pd.DataFrame:
    """东方财富日K线（快速接口）"""
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": days,
    }
    try:
        r = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                          params=params, headers=EM_HEADERS, timeout=8)
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
        return pd.DataFrame(records).set_index("date").sort_index()
    except:
        return pd.DataFrame()


# ============================================================
# 因子预计算（一次性算完整个历史）
# ============================================================

def precompute_factors(df: pd.DataFrame, future_n: int = 5) -> pd.DataFrame:
    """
    预计算一只股票所有日期的因子。
    返回包含 date, 所有FEATURE_COLS, label 的 DataFrame。
    每个日期只用当日前的数据（无未来函数）。
    """
    if df is None or len(df) < 60:
        return pd.DataFrame()

    close = df["close"].astype(float).values
    volume = df["volume"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    n = len(df)

    # 预分配（避免append）
    rows = []

    for i in range(30, n - future_n):
        c = close[:i]
        v = volume[:i]
        h = high[:i]
        l = low[:i]

        # ---- 收益率 ----
        ret5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0.0
        ret10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0.0
        ret20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0.0

        # ---- 标签 ----
        future_ret = close[i + future_n] / c[-1] - 1 if i + future_n < n else 0.0
        if np.isnan(future_ret) or np.isinf(future_ret):
            future_ret = 0.0

        # ---- RSI ----
        deltas = np.diff(c)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else 0.0
        avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else 0.0
        rsi14 = 100.0 - 100.0 / (1.0 + avg_gain / (avg_loss + 1e-10)) if avg_loss > 0 else 100.0

        # ---- 波动率 ----
        vol_std20 = np.std(v[-20:]) / (np.mean(v[-20:]) + 1.0)
        vol_ratio = v[-1] / (np.mean(v[-5:]) + 1.0)
        price_std20 = np.std(c[-20:]) / (np.mean(c[-20:]) + 1.0)
        vol_stability = np.std(v[-10:]) / (np.mean(v[-10:]) + 1.0)

        # ---- 偏度 ----
        ret_skew = float(pd.Series(c[-20:]).skew()) if len(c) >= 20 else 0.0
        vol_skew = float(pd.Series(v[-20:]).skew()) if len(v) >= 20 else 0.0

        # ---- 动量 ----
        vol_growth = np.mean(v[-5:]) / (np.mean(v[-20:]) + 1.0)
        vol_momentum = (np.mean(v[-3:]) / (np.mean(v[-10:-3]) + 1.0)) if len(v) > 10 else 1.0
        ret_accel = ret5 - ret10

        # ---- 价格结构 ----
        pct_chg = (c[-1] / c[-2] - 1) * 100.0 if len(c) > 1 else 0.0
        amplitude = ((h[-1] - l[-1]) / (c[-1] + 0.01)) * 100.0
        high_low_ratio = (h[-1] - l[-1]) / (c[-1] + 0.01)
        close_ma20_ratio = c[-1] / (np.mean(c[-20:]) + 0.01)
        ret_vs_ma5 = c[-1] / (np.mean(c[-5:]) + 0.01)
        ret_vs_ma10 = c[-1] / (np.mean(c[-10:]) + 0.01)

        # ---- 换手率 ----
        turn_rate = v[-1] / (np.sum(v[-20:]) / 20.0 + 1.0) if np.sum(v[-20:]) > 0 else 0.0

        rows.append({
            "date": df.index[i],
            "ret5": ret5,
            "ret10": ret10,
            "ret20": ret20,
            "vol_std20": float(vol_std20),
            "vol_ratio": float(vol_ratio),
            "price_std20": float(price_std20),
            "high_low_ratio": float(high_low_ratio),
            "turn_rate": float(turn_rate),
            "ret_skew": ret_skew,
            "vol_skew": vol_skew,
            "pct_chg": float(pct_chg),
            "amplitude": float(amplitude),
            "close_ma20_ratio": float(close_ma20_ratio),
            "ret_vs_ma5": float(ret_vs_ma5),
            "ret_vs_ma10": float(ret_vs_ma10),
            "vol_stability": float(vol_stability),
            "ret_accel": float(ret_accel),
            "vol_growth": float(vol_growth),
            "vol_momentum": float(vol_momentum),
            "rsi14": float(rsi14),
            "label": float(future_ret),
        })

    return pd.DataFrame(rows)


def precompute_all_factors(all_data: dict, future_n: int = 5) -> dict:
    """
    对所有股票预计算因子（一次性）。
    返回 {code: factor_df}，每个df包含该股所有日期的因子。
    """
    factor_cache = {}
    codes = list(all_data.keys())
    for i, code in enumerate(codes):
        df = all_data[code]
        feats = precompute_factors(df, future_n=future_n)
        if feats is not None and len(feats) > 0:
            feats = feats.copy()
            feats["code"] = code
            factor_cache[code] = feats
        if (i + 1) % 20 == 0:
            logger.info(f"因子预计算: {i+1}/{len(codes)} ({len(factor_cache)} 只有效)")
    return factor_cache


# ============================================================
# Walk-Forward 回测（查表版）
# ============================================================

def run_backtest():
    logger.info(f"=== Walk-Forward 回测（预计算版）===")
    logger.info(f"配置: 训练{TRAIN_DAYS}天 | 持有{PREDICT_DAYS}天 | {TOTAL_WINDOWS}窗口 | Top{TOP_N} | 候选{UNIVERSE_SIZE}只")

    # Step 1: 获取候选股票
    stocks = get_sample_stocks(UNIVERSE_SIZE)
    if not stocks:
        logger.error("获取股票列表失败")
        return
    codes = [s["code"] for s in stocks]
    logger.info(f"候选股票: {len(codes)} 只")

    # Step 2: 下载日线数据（只下载一次）
    t0 = time.time()
    all_data = {}
    for i, code in enumerate(codes):
        df = get_daily_em(code, days=TRAIN_DAYS + PREDICT_DAYS * TOTAL_WINDOWS + 120)
        if len(df) >= 60:
            all_data[code] = df
        if (i + 1) % 20 == 0:
            logger.info(f"数据下载: {i+1}/{len(codes)} ({len(all_data)} 只有效)")
        time.sleep(0.08)
    download_time = time.time() - t0
    logger.info(f"数据下载完成: {len(all_data)}只, 耗时: {download_time:.1f}s")

    if len(all_data) < 10:
        logger.error(f"有效股票太少: {len(all_data)}")
        return

    # Step 3: 一次性预计算所有因子（核心优化）
    t1 = time.time()
    factor_cache = precompute_all_factors(all_data, future_n=PREDICT_DAYS)
    precompute_time = time.time() - t1
    logger.info(f"因子预计算完成: {len(factor_cache)}只, 耗时: {precompute_time:.1f}s")

    # Step 4: 获取有效交集日期（只用有足够多股票交易的日期）
    all_stock_dates = {}
    for code, df in all_data.items():
        all_stock_dates[code] = set(df.index)

    # 统计每天有多少只股票有数据
    date_counts = {}
    for code, dates in all_stock_dates.items():
        for d in dates:
            date_counts[d] = date_counts.get(d, 0) + 1

    # 至少60%的股票有数据才算有效
    threshold = max(int(len(all_data) * 0.6), 30)
    common_dates = sorted({d for d, cnt in date_counts.items() if cnt >= threshold})
    logger.info(f"有效交集日期: {len(common_dates)} 天（阈值≥{threshold}只, {common_dates[0].date() if common_dates else 'N/A'} ~ {common_dates[-1].date() if common_dates else 'N/A'}）")

    if len(common_dates) < TRAIN_DAYS + PREDICT_DAYS:
        logger.error(f"日期范围不足: {len(common_dates)} 天（需要{TRAIN_DAYS + PREDICT_DAYS}）")
        return

    all_dates = common_dates

    # Step 5: 滚动窗口（直接查表，不重复算因子）
    portfolio_returns = []
    benchmark_returns = []
    window_details = []

    start_idx = TRAIN_DAYS
    max_windows = min(TOTAL_WINDOWS, (len(all_dates) - start_idx) // PREDICT_DAYS)

    for w in range(max_windows):
        train_end_idx = start_idx + w * PREDICT_DAYS
        train_end_date = all_dates[min(train_end_idx, len(all_dates) - 1)]
        pred_end_idx = train_end_idx + PREDICT_DAYS
        pred_end_date = all_dates[min(pred_end_idx, len(all_dates) - 1)]

        logger.info(f"\n--- 窗口 {w+1}/{max_windows}: "
                    f"训练截止 {train_end_date.date()} | 预测 {pred_end_date.date()} ---")

        # ---- 构建训练集（从缓存查表，每只股票先tail再合并）----
        train_rows = []
        for code, feats_df in factor_cache.items():
            # 先按日期过滤，再每只股票取最多50条
            window_feats = feats_df[feats_df["date"] <= train_end_date]
            if len(window_feats) > 0:
                train_rows.append(window_feats.tail(30))  # 每只股最多30条

        if not train_rows:
            logger.warning(f"  窗口{w+1}: 无训练数据")
            continue

        train_df = pd.concat(train_rows, ignore_index=True)
        train_df = train_df.dropna(subset=FEATURE_COLS + ["label"], thresh=len(FEATURE_COLS))
        for col in FEATURE_COLS:
            train_df[col] = train_df[col].fillna(0).replace([np.inf, -np.inf], 0)
        train_df["label"] = train_df["label"].fillna(0).replace([np.inf, -np.inf], 0)
        train_df["label_rank"] = train_df["label"].rank(pct=True).fillna(0.5)

        if len(train_df) < 80:
            logger.warning(f"  窗口{w+1}: 训练样本不足({len(train_df)})")
            continue

        logger.info(f"  训练样本: {len(train_df)}")

        # ---- 训练模型 ----
        X_train = train_df[FEATURE_COLS].values
        y_train = train_df["label_rank"].values

        model = HistGradientBoostingRegressor(
            max_iter=150, max_depth=4, learning_rate=0.05,
            min_samples_leaf=20, random_state=42,
        )
        model.fit(X_train, y_train)

        # ---- 预测（从缓存查表，取预测窗口第一天）----
        pred_rows = []
        for code, feats_df in factor_cache.items():
            # 取预测窗口第一天的因子
            window_feats = feats_df[feats_df["date"] > train_end_date].head(1)
            if len(window_feats) > 0:
                pred_rows.append(window_feats)

        if not pred_rows:
            logger.warning(f"  窗口{w+1}: 无预测数据")
            continue

        pred_df = pd.concat(pred_rows, ignore_index=True)
        for col in FEATURE_COLS:
            pred_df[col] = pred_df[col].fillna(0).replace([np.inf, -np.inf], 0)

        X_pred = pred_df[FEATURE_COLS].values
        pred_df["score"] = model.predict(X_pred)
        pred_df = pred_df.sort_values("score", ascending=False)

        # ---- 统计 ----
        top = pred_df.head(TOP_N)
        strategy_ret = float(top["label"].mean()) if "label" in top.columns and len(top) > 0 else 0.0
        benchmark_ret = float(pred_df["label"].mean()) if "label" in pred_df.columns and len(pred_df) > 0 else 0.0

        portfolio_returns.append(strategy_ret)
        benchmark_returns.append(benchmark_ret)

        window_details.append({
            "window": w + 1,
            "train_end": str(train_end_date.date()),
            "pred_end": str(pred_end_date.date()),
            "top_stocks": top["code"].tolist() if "code" in top.columns else [],
            "strategy_ret": round(strategy_ret * 100, 2),
            "benchmark_ret": round(benchmark_ret * 100, 2),
            "excess": round((strategy_ret - benchmark_ret) * 100, 2),
        })

        logger.info(f"  策略: {strategy_ret*100:+.2f}% | 基准: {benchmark_ret*100:+.2f}% | "
                    f"超额: {(strategy_ret-benchmark_ret)*100:+.2f}%")
        logger.info(f"  Top{TOP_N}: {top['code'].tolist() if 'code' in top.columns else []}")

    # Step 6: 汇总
    if not portfolio_returns:
        logger.error("无有效回测窗口")
        return

    portfolio_returns = np.array(portfolio_returns)
    benchmark_returns = np.array(benchmark_returns)
    excess_returns = portfolio_returns - benchmark_returns

    cum_portfolio = np.cumprod(1 + portfolio_returns)
    cum_benchmark = np.cumprod(1 + benchmark_returns)
    total_return = cum_portfolio[-1] - 1
    annual_return = (1 + total_return) ** (252 / (len(portfolio_returns) * PREDICT_DAYS)) - 1
    annual_vol = portfolio_returns.std() * np.sqrt(252 / PREDICT_DAYS)
    sharpe = annual_return / (annual_vol + 1e-10)
    # numpy数组用 np.maximum.accumulate
    cummax_portfolio = np.maximum.accumulate(cum_portfolio)
    max_drawdown = float(np.nanmax((cummax_portfolio - cum_portfolio) / cummax_portfolio))
    win_rate = float((excess_returns > 0).mean())

    results = {
        "config": {
            "train_days": TRAIN_DAYS, "predict_days": PREDICT_DAYS,
            "total_windows": TOTAL_WINDOWS, "top_n": TOP_N, "universe": UNIVERSE_SIZE,
            "download_time_s": round(download_time, 1),
            "precompute_time_s": round(precompute_time, 1),
        },
        "metrics": {
            "total_return_pct": round(total_return * 100, 2),
            "annual_return_pct": round(annual_return * 100, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "annual_vol_pct": round(annual_vol * 100, 2),
            "win_rate": round(win_rate * 100, 1),
            "avg_excess_pct": round(excess_returns.mean() * 100, 2),
        },
        "windows": window_details,
    }

    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"backtest_fast_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    total_time = download_time + precompute_time

    logger.info("\n" + "=" * 50)
    logger.info("📊 Walk-Forward 回测结果（预计算版）")
    logger.info("=" * 50)
    logger.info(f"总耗时: {total_time:.0f}s（下载{download_time:.0f}s + 预计算{precompute_time:.0f}s）")
    logger.info(f"候选股票: {UNIVERSE_SIZE}只 | 有效: {len(all_data)}只 | 因子缓存: {len(factor_cache)}只")
    logger.info(f"总收益率:   {results['metrics']['total_return_pct']:+.2f}%")
    logger.info(f"年化收益率: {results['metrics']['annual_return_pct']:+.2f}%")
    logger.info(f"夏普比率:   {results['metrics']['sharpe_ratio']:.2f}")
    logger.info(f"最大回撤:   {results['metrics']['max_drawdown_pct']:.2f}%")
    logger.info(f"年化波动率: {results['metrics']['annual_vol_pct']:.2f}%")
    logger.info(f"超额胜率:   {results['metrics']['win_rate']:.1f}%")
    logger.info(f"平均超额:   {results['metrics']['avg_excess_pct']:+.2f}%")
    logger.info(f"结果已保存: {out_path}")

    return results


if __name__ == "__main__":
    run_backtest()
