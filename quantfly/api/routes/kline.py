# -*- encoding: utf-8 -*-
"""
K线数据API
GET /api/backtest/kline/{code}
"""
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger("API.Kline")

router = APIRouter()


@router.get("/kline/{code}")
async def get_kline(code: str):
    """获取股票K线数据"""
    try:
        from quantfly.backtest.data_provider import get_kline_em
        import pandas as pd

        df = get_kline_em(code, count=365)
        if df.empty:
            return {"code": code, "candles": [], "volumes": []}

        candles = []
        volumes = []
        for idx, row in df.iterrows():
            ts = int(pd.Timestamp(idx).timestamp())
            candles.append({
                "time": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
            vol_color = "rgba(63,185,80,0.3)" if row["close"] >= row["open"] else "rgba(248,81,73,0.3)"
            volumes.append({
                "time": ts,
                "value": int(row["volume"]),
                "color": vol_color,
            })

        return {"code": code, "candles": candles, "volumes": volumes}
    except Exception as e:
        logger.error(f"获取K线失败 {code}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
