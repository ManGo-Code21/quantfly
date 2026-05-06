# -*- encoding: utf-8 -*-
"""
Ranker模型训练 — 用历史数据训练LightGBM排序模型
================================================
训练数据: 东方财富K线 + 未来N日收益率作为标签
用法:
  Windows: python train_ranker.py
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

FEATURE_COLS = [
    "ret5", "ret10", "ret20", "vol_std20", "vol_ratio", "price_std20",
    "high_low_ratio", "turn_rate", "ret_skew", "vol_skew",
    "pct_chg", "amplitude", "close_ma20_ratio",
    "ret_vs_ma5", "ret_vs_ma10", "vol_stability",
    "ret_accel", "vol_growth", "vol_momentum", "rsi14",
]

N_STOCKS = 200      # 训练股票数
N_DAYS = 300         # 历史天数
FUTURE_N = 5        # 未来5日收益作为标签


def get_kline_em(code: str, count: int = 300) -> pd.DataFrame:
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


def get_sample_stocks(n: int = 200) -> list[str]:
    """获取样本股票（各行业龙头）"""
    try:
        params = {
            "pn": 1, "pz": n,
            "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12",
        }
        r = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                          params=params, headers=EM_HEADERS, timeout=10)
        data = r.json().get("data", {}).get("diff", [])
        return [str(x["f12"]) for x in data[:n]]
    except:
        return []


def calc_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算因子特征"""
    import math
    if df is None or len(df) < 30:
        return pd.DataFrame()

    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    low = df["low"].values

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

        # 未来N日收益（标签）
        future_ret = (df.iloc[i + FUTURE_N]["close"] / c[-1] - 1) if i + FUTURE_N < len(df) else 0

        row = {
            "date": df.index[i],
            "code": df.index[i],
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


def main():
    logger.info(f"=== Ranker 训练开始: {N_STOCKS}只股票 x {N_DAYS}天历史 ===")
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Step 1: 获取股票列表
    stocks = get_sample_stocks(N_STOCKS)
    if not stocks:
        logger.error("获取股票列表失败")
        return
    logger.info(f"训练样本: {len(stocks)} 只")

    # Step 2: 逐只获取K线并计算特征
    all_features = []
    for i, code in enumerate(stocks):
        df = get_kline_em(code, count=N_DAYS + FUTURE_N + 10)
        if df.empty:
            continue
        feats = calc_features(df)
        if not feats.empty:
            feats["code"] = code
            all_features.append(feats)
        if (i + 1) % 20 == 0:
            logger.info(f"进度: {i + 1}/{len(stocks)} ({len(all_features)} 只有效)")
        time.sleep(0.15)

    if not all_features:
        logger.error("没有有效的特征数据")
        return

    # Step 3: 合并并清理
    df_all = pd.concat(all_features, ignore_index=True)
    df_all = df_all.dropna(subset=FEATURE_COLS + ["label"], thresh=len(FEATURE_COLS))
    for col in FEATURE_COLS:
        df_all[col] = df_all[col].fillna(0).replace([np.inf, -np.inf], 0)
    logger.info(f"训练样本数: {len(df_all)} 条")

    # Step 4: 训练模型（用label_rank作为排序目标）
    X = df_all[FEATURE_COLS].values
    y = df_all["label_rank"].values

    model = HistGradientBoostingRegressor(
        max_iter=200,
        max_depth=5,
        learning_rate=0.05,
        min_samples_leaf=20,
        random_state=42,
    )
    model.fit(X, y)
    logger.info("模型训练完成")

    # Step 5: 保存
    with open(MODEL_OUT, "wb") as f:
        pickle.dump(model, f)
    logger.info(f"模型已保存: {MODEL_OUT}")

    # Step 6: 验证（按日期分组，留出最后1/4做测试）
    dates = sorted(df_all["date"].unique())
    split_idx = int(len(dates) * 0.75)
    train_dates = set(dates[:split_idx])
    test_df = df_all[df_all["date"].isin(dates[split_idx:])]
    if len(test_df) > 50:
        X_test = test_df[FEATURE_COLS].values
        y_pred = model.predict(X_test)
        y_true = test_df["label_rank"].values
        corr = np.corrcoef(y_pred, y_true)[0, 1]
        logger.info(f"测试集相关性: {corr:.3f} (越高越好)")
    else:
        logger.info("测试样本不足，跳过验证")


if __name__ == "__main__":
    main()
