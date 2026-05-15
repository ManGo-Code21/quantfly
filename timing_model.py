# -*- encoding: utf-8 -*-
"""
择时模型 — 大盘量能+情绪+动量，输出仓位比例
=============================================
"""
import logging
import numpy as np
import pandas as pd
import pickle
import requests
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingRegressor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TimingModel")

MODEL_PATH = Path(__file__).parent / "model" / "timing_model.pkl"

TIMING_FEATURES = [
    "ret_5", "ret_10", "ret_20",
    "vol_ratio", "vol_growth",
    "vol_std", "price_ma_ratio",
    "ma_trend", "up_ratio",
    "fear_index"
]


def get_index_data(days: int = 500) -> pd.DataFrame:
    """获取上证指数日K线"""
    try:
        # 东方财富API
        params = {
            "secid": "1.000001",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": days,
        }
        r = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                         params=params, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        klines = r.json().get("data", {}).get("klines", [])
        records = []
        for k in klines:
            p = k.split(",")
            records.append({
                "date": pd.to_datetime(p[0]),
                "open": float(p[1]), "high": float(p[2]),
                "low": float(p[3]), "close": float(p[4]),
                "volume": float(p[5]),
            })
        df = pd.DataFrame(records).set_index("date").sort_index()
        return df
    except Exception as e:
        logger.warning(f"指数获取失败: {e}")
        return pd.DataFrame()


def calc_timing_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算择时特征"""
    if df.empty or len(df) < 60:
        return pd.DataFrame()
    
    close = df["close"].values
    volume = df["volume"].values
    features = []
    
    for i in range(60, len(df)):
        c = close[:i]
        v = volume[:i]
        
        ret_5 = c[-1] / c[-6] - 1
        ret_10 = c[-1] / c[-11] - 1
        ret_20 = c[-1] / c[-21] - 1
        
        vol_ma5 = np.mean(v[-5:])
        vol_ma20 = np.mean(v[-20:])
        vol_ratio = vol_ma5 / (vol_ma20 + 1)
        vol_growth = vol_ma5 / (np.mean(v[-10:-5]) + 1)
        
        vol_std = np.std(c[-20:]) / (np.mean(c[-20:]) + 1)
        
        ma5 = np.mean(c[-5:])
        ma20 = np.mean(c[-20:])
        ma60 = np.mean(c[-60:])
        price_ma_ratio = c[-1] / (ma20 + 1)
        ma_trend = 1.0 if ma5 > ma20 > ma60 else (0.0 if ma5 < ma20 < ma60 else 0.5)
        
        ret_series = pd.Series(c[-20:]).pct_change().dropna()
        up_ratio = (ret_series > 0).sum() / len(ret_series) if len(ret_series) > 0 else 0.5
        
        max_dd = (c[-1] / np.max(c[-20:]) - 1)
        fear_index = abs(max_dd) * vol_std * 100
        
        # 标签：未来5日收益
        if i + 5 < len(close):
            label = close[i + 5] / c[-1] - 1
        else:
            label = 0.0
        
        features.append({
            "date": df.index[i],
            "ret_5": ret_5, "ret_10": ret_10, "ret_20": ret_20,
            "vol_ratio": vol_ratio, "vol_growth": vol_growth,
            "vol_std": vol_std, "price_ma_ratio": price_ma_ratio,
            "ma_trend": ma_trend, "up_ratio": up_ratio,
            "fear_index": fear_index,
            "label": label,
        })
    
    return pd.DataFrame(features)


def _safe_corr(a, b):
    """相关系数计算，处理NaN"""
    if len(a) < 3 or np.std(a) < 1e-10 or np.std(b) < 1e-10:
        return 0.0
    c = np.corrcoef(a, b)[0, 1]
    return c if not np.isnan(c) else 0.0


def train_timing_model():
    logger.info("=== 训练择时模型 ===")
    df = get_index_data(days=500)
    if df.empty:
        logger.error("无指数数据")
        return None
    
    logger.info(f"指数数据: {len(df)} 天")
    features = calc_timing_features(df)
    if features.empty:
        return None
    
    # 去掉尾部无效标签（label=0的末尾行）
    valid_mask = features['label'] != 0
    last_valid = valid_mask[::-1].idxmax() if valid_mask.any() else None
    if last_valid is not None:
        features = features.loc[:last_valid]
    
    split = int(len(features) * 0.8)
    train = features.iloc[:split]
    test = features.iloc[split:]
    
    X_train = train[TIMING_FEATURES].values
    y_train = train["label"].values
    X_test = test[TIMING_FEATURES].values
    y_test = test["label"].values
    
    best_model = None
    best_corr = -1
    
    for params in [
        {"max_iter": 50, "max_depth": 3, "learning_rate": 0.05, "min_samples_leaf": 30},
        {"max_iter": 80, "max_depth": 2, "learning_rate": 0.03, "min_samples_leaf": 50},
    ]:
        m = HistGradientBoostingRegressor(random_state=42, **params)
        m.fit(X_train, y_train)
        pred = m.predict(X_test)
        corr = _safe_corr(pred, y_test)
        logger.info(f"  参数: {params} → 相关: {corr:.3f}")
        if corr > best_corr:
            best_corr = corr
            best_model = m
    
    logger.info(f"最佳相关: {best_corr:.3f}")
    
    MODEL_PATH.parent.mkdir(exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(best_model, f)
    logger.info(f"模型已保存: {MODEL_PATH}")
    
    return best_model


def predict_position(model=None) -> float:
    """预测当前仓位 0.0~1.0"""
    if model is None:
        if MODEL_PATH.exists():
            with open(MODEL_PATH, "rb") as f:
                model = pickle.load(f)
        else:
            return 0.5
    
    df = get_index_data(days=100)
    if df.empty:
        return 0.5
    
    features = calc_timing_features(df)
    if features.empty:
        return 0.5
    
    last = features.iloc[-1][TIMING_FEATURES].values.reshape(1, -1)
    pred_return = model.predict(last)[0]
    
    # 映射：-3% → 0%, 0% → 50%, +3% → 100%
    position = (pred_return + 0.03) / 0.06
    return max(0.0, min(1.0, position))


if __name__ == "__main__":
    model = train_timing_model()
    pos = predict_position(model)
    logger.info(f"当前建议仓位: {pos*100:.1f}%")
