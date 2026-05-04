# -*- encoding: utf-8 -*-
"""
选股三原则核心分析逻辑
"""
import numpy as np
import pandas as pd
from typing import Optional


def analyze_stock(df: pd.DataFrame) -> dict:
    """
    综合分析单只股票：选股三原则 + 量价关系

    Args:
        df: 日K线（包含 open/high/low/close/volume）

    Returns:
        dict with scores and signals
    """
    n = len(df)
    if n < 25:
        return {}

    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    open_ = df["open"].values.astype(float)
    vol = df["volume"].values.astype(float)

    today = n - 1
    today_close = close[today]
    today_prev = close[today - 1] if today > 0 else today_close
    today_high = high[today]
    today_low = low[today]
    today_vol = vol[today]

    # 基础指标
    chg_pct = (today_close - today_prev) / today_prev * 100 if today_prev > 0 else 0
    ma5_vol = np.mean(vol[-5:]) if n >= 5 else np.mean(vol)
    vol_ratio = today_vol / ma5_vol if ma5_vol > 0 else 0

    # 筹码分析
    lookback = min(20, today)
    period_low = np.min(close[today - lookback:today + 1])
    period_high = np.max(close[today - lookback:today + 1])
    rel_pos = (today_close - period_low) / (period_high - period_low) if period_high > period_low else 0.5
    dist_from_high = (period_high - today_close) / period_high if period_high > 0 else 0
    recent_5d_return = (close[today] - close[today - 5]) / close[today - 5] if today >= 5 and close[today - 5] > 0 else 0

    # 量价分析
    vwap = (today_high + today_low + today_close) / 3
    above_vwap_pct = (today_close - vwap) / vwap * 100 if vwap > 0 else 0
    attack_strength = (today_close - today_low) / (today_high - today_low) if today_high > today_low else 0.5

    vol_5d_ma = np.mean(vol[today - 4:today]) if today >= 4 else np.mean(vol)
    vol_driven = today_vol / vol_5d_ma if vol_5d_ma > 0 else 1.0
    price_driven = (today_close - close[today - 1]) / close[today - 1] if close[today - 1] > 0 else 0
    vol_price_divergence = (price_driven > 0 and vol_driven < 0.8)

    # 选股信号
    signals = {}

    # 原则1：题材（涨幅3%~9.8%）
    if 3.0 <= chg_pct <= 9.8:
        signals["题材_涨幅符合"] = True

    # 原则2：筹码干净
    if 0.10 <= rel_pos <= 0.70:
        signals["筹码_位置合适"] = True
    if dist_from_high >= 0.15:
        signals["筹码_上方有空间"] = True
    if abs(recent_5d_return) <= 0.20:
        signals["筹码_未大幅上涨"] = True

    # 原则3：分时强劲
    if vol_ratio >= 1.5:
        signals["分时_量比充足"] = True
    if above_vwap_pct > 0:
        signals["分时_在均价线上"] = True
    if attack_strength >= 0.70:
        signals["分时_主动买入强"] = True

    # 量价异常
    if vol_driven > 2.0 and chg_pct < 1.0:
        signals["量价_放量滞涨"] = True
    if vol_driven < 0.6 and chg_pct > 2.0:
        signals["量价_缩量上涨"] = True
    if attack_strength < 0.3:
        signals["量价_主动弱"] = True
    if vol_price_divergence:
        signals["量价_背离"] = True

    # 综合评分
    score = 0.0
    if signals.get("筹码_位置合适"):
        score += 3.0
    if signals.get("筹码_上方有空间"):
        score += 2.0
    if signals.get("筹码_未大幅上涨"):
        score += 1.0
    if signals.get("分时_量比充足"):
        score += 1.5
    if signals.get("分时_在均价线上"):
        score += 1.0
    if signals.get("分时_主动买入强"):
        score += 1.0
    if signals.get("题材_涨幅符合"):
        score += 1.5
    if signals.get("量价_放量滞涨"):
        score -= 3.0
    if signals.get("量价_缩量上涨"):
        score += 0.5
    if signals.get("量价_背离"):
        score -= 1.0

    # 交易信号
    is_buyable = (
        score >= 6.0
        and not signals.get("量价_放量滞涨")
        and not signals.get("量价_背离")
        and not signals.get("量价_主动弱")
    )
    is_sellable = signals.get("量价_主动弱")

    return {
        "close": today_close,
        "chg_pct": chg_pct,
        "vol_ratio": vol_ratio,
        "rel_pos": rel_pos,
        "dist_from_high": dist_from_high,
        "recent_5d_return": recent_5d_return,
        "above_vwap_pct": above_vwap_pct,
        "attack_strength": attack_strength,
        "vol_driven": vol_driven,
        "signals": signals,
        "score": score,
        "is_buyable": is_buyable,
        "is_sellable": is_sellable,
    }
