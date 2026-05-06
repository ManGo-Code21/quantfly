# -*- encoding: utf-8 -*-
"""
Walk-Forward 回测 — 验证20因子Ranker策略有效性
=================================================
用日线数据做滚动训练+回测，对比基准（创业板指数）
用法: python backtest_ranker.py
"""
import json
import logging
import pickle
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("BacktestRanker")

MODEL_DIR = Path(__file__).parent / "model"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ============================================================
# 配置
# ============================================================
TRAIN_DAYS = 200       # 每次训练用多少天历史
PREDICT_DAYS = 10      # 每次预测持有多少天
TOTAL_WINDOWS = 8       # 滚动多少次（约一年回测）
TOP_N = 5              # 每次选Top N只
UNIVERSE_SIZE = 100   # 候选股票池大小

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
    """获取各行业龙头（按市值降序）"""
    try:
        params = {
            "pn": 1, "pz": n * 3,  # 多取再过滤
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


def get_daily_em(code: str, days: int = 400) -> pd.DataFrame:
    """东方财富日K线（快速接口）"""
    mkt = "sh" if code.startswith(("6", "9")) else "sz"
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
        df = pd.DataFrame(records).set_index("date").sort_index()
        return df
    except:
        return pd.DataFrame()


# ============================================================
# 因子计算
# ============================================================

def calc_factors(df: pd.DataFrame, future_n: int = 5) -> pd.DataFrame:
    """计算日线因子和标签"""
    if df is None or len(df) < 60:
        return pd.DataFrame()

    close = df["close"].astype(float).values
    volume = df["volume"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values

    rows = []
    for i in range(30, len(df) - future_n):
        c = close[:i]
        v = volume[:i]
        h = high[:i]
        l = low[:i]

        ret5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
        ret10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
        ret20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0

        # 标签：未来N日收益率
        label = (close[i + future_n] / c[-1] - 1) if i + future_n < len(close) else 0
        label = 0 if (np.isnan(label) or np.isinf(label)) else label

        rows.append({
            "date": df.index[i],
            "ret5": ret5, "ret10": ret10, "ret20": ret20,
            "vol_std20": float(np.std(v[-20:]) / (np.mean(v[-20:]) + 1)),
            "vol_ratio": v[-1] / (np.mean(v[-5:]) + 1),
            "price_std20": float(np.std(c[-20:]) / (np.mean(c[-20:]) + 1)),
            "high_low_ratio": (h[-1] - l[-1]) / (c[-1] + 0.01),
            "turn_rate": v[-1] / (np.sum(v[-20:]) / 20 + 1) if np.sum(v[-20:]) > 0 else 0,
            "ret_skew": float(pd.Series(c[-20:]).skew()) if len(c) >= 20 else 0,
            "vol_skew": float(pd.Series(v[-20:]).skew()) if len(c) >= 20 else 0,
            "pct_chg": (c[-1] / c[-2] - 1) * 100 if len(c) > 1 else 0,
            "amplitude": ((h[-1] - l[-1]) / (c[-1] + 0.01)) * 100,
            "close_ma20_ratio": c[-1] / (np.mean(c[-20:]) + 0.01),
            "ret_vs_ma5": c[-1] / (np.mean(c[-5:]) + 0.01),
            "ret_vs_ma10": c[-1] / (np.mean(c[-10:]) + 0.01),
            "vol_stability": float(np.std(v[-10:]) / (np.mean(v[-10:]) + 1)),
            "ret_accel": ret5 - ret10,
            "vol_growth": np.mean(v[-5:]) / (np.mean(v[-20:]) + 1),
            "vol_momentum": np.mean(v[-3:]) / (np.mean(v[-10:-3]) + 1) if len(v) > 10 else 1.0,
            "rsi14": _rsi(c, 14),
            "label": label,
        })

    result = pd.DataFrame(rows)
    if len(result) > 0:
        result["label_rank"] = result["label"].rank(pct=True)
    return result


def _rsi(prices: np.ndarray, period: int = 14) -> float:
    if len(prices) < period + 2:
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
# Walk-Forward 回测
# ============================================================

def run_backtest():
    logger.info(f"=== Walk-Forward 回测开始: 训练{TRAIN_DAYS}天 → 持有{PREDICT_DAYS}天 × {TOTAL_WINDOWS}轮 ===")

    # Step 1: 获取候选股票
    stocks = get_sample_stocks(UNIVERSE_SIZE)
    if not stocks:
        logger.error("获取股票列表失败")
        return
    codes = [s["code"] for s in stocks]
    logger.info(f"候选股票: {len(codes)} 只")

    # Step 2: 下载日线数据
    all_data = {}
    for i, code in enumerate(codes):
        df = get_daily_em(code, days=TRAIN_DAYS + PREDICT_DAYS * TOTAL_WINDOWS + 100)
        if len(df) >= 60:
            all_data[code] = df
        if (i + 1) % 20 == 0:
            logger.info(f"数据下载: {i + 1}/{len(codes)} ({len(all_data)} 只有效)")
        time.sleep(0.1)

    if len(all_data) < 10:
        logger.error(f"有效股票太少: {len(all_data)}")
        return
    logger.info(f"有效股票数据: {len(all_data)} 只")

    # Step 3: 滚动窗口回测
    from sklearn.ensemble import HistGradientBoostingRegressor

    portfolio_returns = []   # 每期策略收益率
    benchmark_returns = []   # 每期基准收益率（等权平均）
    window_details = []

    dates = sorted({d for df in all_data.values() for d in df.index})
    if len(dates) < TRAIN_DAYS + PREDICT_DAYS:
        logger.error(f"日期范围不足: {len(dates)} 天")
        return

    start_idx = TRAIN_DAYS
    for w in range(TOTAL_WINDOWS):
        train_end_idx = start_idx + w * PREDICT_DAYS
        if train_end_idx + PREDICT_DAYS >= len(dates):
            break

        train_end_date = dates[train_end_idx]
        pred_end_date = dates[min(train_end_idx + PREDICT_DAYS, len(dates) - 1)]

        logger.info(f"\n--- 窗口 {w+1}/{min(TOTAL_WINDOWS, (len(dates)-start_idx)//PREDICT_DAYS)}: "
                    f"训练截止 {train_end_date.date()} | 预测区间 {pred_end_date.date()} ---")

        # 构建训练集
        train_rows = []
        for code, df in all_data.items():
            feats = calc_factors(df, future_n=PREDICT_DAYS)
            feats = feats[feats["date"] <= train_end_date].tail(50)
            if len(feats) > 0:
                feats["code"] = code
                train_rows.append(feats)

        if not train_rows:
            logger.warning(f"  窗口{w+1}: 无训练数据")
            continue

        train_df = pd.concat(train_rows, ignore_index=True)
        train_df = train_df.dropna(subset=FEATURE_COLS + ["label"], thresh=len(FEATURE_COLS))
        for col in FEATURE_COLS:
            train_df[col] = train_df[col].fillna(0).replace([np.inf, -np.inf], 0)
        train_df["label"] = train_df["label"].fillna(0).replace([np.inf, -np.inf], 0)
        train_df["label_rank"] = train_df["label"].rank(pct=True).fillna(0.5)

        if len(train_df) < 100:
            logger.warning(f"  窗口{w+1}: 训练样本不足({len(train_df)})")
            continue

        # 训练模型
        X_train = train_df[FEATURE_COLS].values
        y_train = train_df["label_rank"].values

        model = HistGradientBoostingRegressor(
            max_iter=150, max_depth=4, learning_rate=0.05,
            min_samples_leaf=20, random_state=42,
        )
        model.fit(X_train, y_train)

        # 预测打分
        pred_rows = []
        for code, df in all_data.items():
            feats = calc_factors(df, future_n=PREDICT_DAYS)
            feats = feats[feats["date"] > train_end_date].head(1)
            if len(feats) > 0:
                feats["code"] = code
                pred_rows.append(feats)

        if not pred_rows:
            continue

        pred_df = pd.concat(pred_rows, ignore_index=True)
        for col in FEATURE_COLS:
            pred_df[col] = pred_df[col].fillna(0).replace([np.inf, -np.inf], 0)

        X_pred = pred_df[FEATURE_COLS].values
        pred_df["score"] = model.predict(X_pred)
        pred_df = pred_df.sort_values("score", ascending=False)

        # Top N 等权组合
        top = pred_df.head(TOP_N)
        strategy_ret = top["label"].mean() if "label" in top.columns else 0
        benchmark_ret = pred_df["label"].mean() if "label" in pred_df.columns else 0

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

        logger.info(f"  策略收益: {strategy_ret*100:+.2f}% | 基准: {benchmark_ret*100:+.2f}% | 超额: {(strategy_ret-benchmark_ret)*100:+.2f}%")
        logger.info(f"  Top{TOP_N}: {top['code'].tolist() if 'code' in top.columns else []}")

    # Step 4: 汇总统计
    if not portfolio_returns:
        logger.error("无有效回测窗口")
        return

    portfolio_returns = np.array(portfolio_returns)
    benchmark_returns = np.array(benchmark_returns)
    excess_returns = portfolio_returns - benchmark_returns

    # 计算指标
    cum_portfolio = (1 + portfolio_returns).cumprod()
    cum_benchmark = (1 + benchmark_returns).cumprod()
    total_return = cum_portfolio[-1] - 1
    annual_return = (1 + total_return) ** (252 / (len(portfolio_returns) * PREDICT_DAYS)) - 1
    annual_vol = portfolio_returns.std() * np.sqrt(252 / PREDICT_DAYS)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0
    max_drawdown = ((cum_portfolio.cummax() - cum_portfolio) / cum_portfolio.cummax()).max()
    win_rate = (excess_returns > 0).mean()

    results = {
        "config": {"train_days": TRAIN_DAYS, "predict_days": PREDICT_DAYS,
                   "total_windows": TOTAL_WINDOWS, "top_n": TOP_N, "universe": UNIVERSE_SIZE},
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

    # 保存结果
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"backtest_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 打印汇总
    logger.info("\n" + "="*50)
    logger.info("📊 Walk-Forward 回测结果")
    logger.info("="*50)
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
