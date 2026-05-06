# -*- encoding: utf-8 -*-
"""
QMT 全数据 API — Mac 按需调用

行情:
  GET  /data/quote?codes=000001,000002          → 实时行情（五档+财务摘要）
  GET  /data/kline?code=000001&period=1d&count=100 → K线
  GET  /data/minute?code=000001&period=5m&count=240  → 分钟K线
  GET  /data/tick?code=000001&count=100          → 逐笔成交

市场:
  GET  /data/sectors                               → 板块列表
  GET  /data/sector/stocks?sector=沪深300           → 板块成分股
  GET  /data/index_weight?index=000300&date=20260506 → 指数权重

财务:
  GET  /data/financial?codes=000001,000002          → 财务数据(PE/PB/ROE...)

日历:
  GET  /data/calendar?start=20260101&end=20260506   → 交易日历
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import time
import json
import math

logger = logging.getLogger("API.Data")
router = APIRouter()

def _safe_float(v, default=0.0):
    """JSON-safe float，NaN/Inf → default"""
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default

# ── 工具函数 ──────────────────────────────────────────────
def _fmt(code: str) -> str:
    code = code.strip()
    if code.endswith((".SH", ".SZ")):
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"

def _ensure_connected():
    from xtquant import xtdata
    xtdata.connect()

# ══════════════════════════════════════════════════════════
# 行情
# ══════════════════════════════════════════════════════════

@router.get("/data/quote")
async def get_quotes(codes: str = Query(..., description="逗号分隔，如 000001,600000")):
    """
    实时行情 — 最新价、涨跌幅、成交量等
    
    注: 返回最近一根日K作为"最新行情"，盘中使用分钟K获取实时数据。
    """
    try:
        from xtquant import xtdata
        import pandas as pd
        _ensure_connected()

        code_list = [_fmt(c) for c in codes.split(",") if c.strip()]
        if len(code_list) > 100:
            raise HTTPException(400, "最多100只")

        fields = ["open", "high", "low", "close", "volume", "amount"]

        # 批量获取最新2根日K
        raw = xtdata.get_market_data_ex(
            stock_list=code_list,
            period="1d",
            start_time="",
            end_time="",
            count=2,
            field_list=fields,
            dividend_type="front",
        )

        result = {}
        # get_market_data_ex 返回 {stock_code: DataFrame(index=date, columns=fields)}
        for c in code_list:
            raw_code = c.replace(".SH", "").replace(".SZ", "")
            df = raw.get(c)
            if df is None or df.empty or "close" not in df.columns:
                continue
            try:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                cur_close = float(last["close"])
                prev_close = float(prev["close"])
                pct = (cur_close / prev_close - 1) * 100 if prev_close else 0

                result[raw_code] = {
                    "date": str(df.index[-1]),
                    "price": _safe_float(last.get("close")),
                    "open": _safe_float(last.get("open")),
                    "high": _safe_float(last.get("high")),
                    "low": _safe_float(last.get("low")),
                    "volume": _safe_int(last.get("volume")),
                    "amount": _safe_float(last.get("amount")),
                    "pre_close": _safe_float(prev.get("close")),
                    "pct_chg": round(_safe_float(pct), 2),
                }
            except Exception:
                continue

        return {"quotes": result, "count": len(result)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Quote failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/data/kline")
async def get_kline(
    code: str = Query(...),
    period: str = Query("1d", description="1d / 1m / 5m / 15m / 30m / 60m"),
    count: int = Query(100, ge=1, le=1000),
):
    """K线数据（日线/分钟）"""
    try:
        from xtquant import xtdata
        import pandas as pd
        _ensure_connected()

        mqcode = _fmt(code)
        fields = ["open", "high", "low", "close", "volume", "amount"]

        # 先下载
        xtdata.download_history_data(mqcode, period, "", "")

        raw = xtdata.get_market_data(
            stock_list=[mqcode],
            period=period,
            count=count,
            field_list=fields,
            dividend_type="front",
        )

        if not raw or all(v.empty for v in raw.values()):
            return {"code": code, "candles": [], "count": 0}

        # 构建 candles
        import numpy as np
        dates = None
        for field_df in raw.values():
            if not field_df.empty and mqcode in field_df.index:
                dates = field_df.columns
                break

        if dates is None:
            return {"code": code, "candles": [], "count": 0}

        candles = []
        for dt in dates:
            row = {
                "date": str(dt),
                "open": float(raw["open"].loc[mqcode, dt]) if "open" in raw else 0,
                "high": float(raw["high"].loc[mqcode, dt]) if "high" in raw else 0,
                "low": float(raw["low"].loc[mqcode, dt]) if "low" in raw else 0,
                "close": float(raw["close"].loc[mqcode, dt]) if "close" in raw else 0,
                "volume": int(raw["volume"].loc[mqcode, dt]) if "volume" in raw else 0,
                "amount": float(raw["amount"].loc[mqcode, dt]) if "amount" in raw else 0,
            }
            candles.append(row)

        return {"code": code, "period": period, "candles": candles[-count:], "count": len(candles)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Kline failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/data/minute")
async def get_minute_kline(
    code: str = Query(...),
    period: str = Query("5m"),
    count: int = Query(240, ge=1, le=2000),
):
    """分钟K线快捷接口（＝/data/kline with period=1m/5m/15m/30m/60m）"""
    return await get_kline(code=code, period=period, count=count)


@router.get("/data/tick")
async def get_ticks(
    code: str = Query(...),
    count: int = Query(100, ge=1, le=5000),
):
    """逐笔成交数据"""
    try:
        from xtquant import xtdata
        import pandas as pd
        _ensure_connected()

        mqcode = _fmt(code)
        raw = xtdata.get_full_tick([mqcode])

        if not raw or mqcode not in raw:
            return {"code": code, "ticks": [], "count": 0}

        tick_data = raw[mqcode]
        if isinstance(tick_data, pd.DataFrame):
            df = tick_data.tail(count)
        else:
            return {"code": code, "ticks": [], "count": 0, "error": "tick format unknown"}

        ticks = []
        for _, row in df.iterrows():
            ticks.append({
                "time": str(row.get("time", "")),
                "price": float(row.get("price", row.get("lastPrice", 0)) or 0),
                "volume": int(row.get("volume", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
                "type": str(row.get("type", "")),  # 买/卖
            })

        return {"code": code, "ticks": ticks[-count:], "count": len(ticks)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Tick failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 市场
# ══════════════════════════════════════════════════════════

@router.get("/data/sectors")
async def get_sectors():
    """所有板块列表"""
    try:
        from xtquant import xtdata
        _ensure_connected()
        xtdata.download_sector_data()
        sectors = xtdata.get_sector_list()
        return {"sectors": sectors, "count": len(sectors)}
    except Exception as e:
        logger.error(f"Sectors failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/data/sector/stocks")
async def get_sector_stocks(sector: str = Query(..., description="板块名，如 沪深300、中证500、AI大模型")):
    """板块成分股"""
    try:
        from xtquant import xtdata
        _ensure_connected()
        xtdata.download_sector_data()
        stocks = xtdata.get_stock_list_in_sector(sector)
        return {"sector": sector, "stocks": stocks, "count": len(stocks)}
    except Exception as e:
        logger.error(f"Sector stocks failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/data/index_weight")
async def get_index_weight(
    index: str = Query("000300", description="指数代码"),
    date: str = Query("", description="日期 YYYYMMDD，空=最新"),
):
    """指数成分股权重"""
    try:
        from xtquant import xtdata
        _ensure_connected()

        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%Y%m%d")

        mqindex = _fmt(index)
        weights = xtdata.get_index_weight(mqindex, date)
        return {"index": index, "date": date, "weights": weights or {}, "count": len(weights or {})}
    except Exception as e:
        logger.error(f"Index weight failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 财务
# ══════════════════════════════════════════════════════════

FINANCIAL_FIELDS = [
    "pe", "pe_ttm", "pb", "ps", "pcf",
    "roe", "roa", "grossProfitMargin", "netProfitMargin",
    "totalMarketCap", "floatMarketCap",
    "operatingRevenue", "netProfit",
    "operatingRevenueYOY", "netProfitYOY",
    "goodwill", "totalShares", "floatShares",
]


@router.get("/data/financial")
async def get_financial(
    codes: str = Query(..., description="逗号分隔"),
    fields: str = Query("", description="逗号分隔，空=全部"),
):
    """财务数据"""
    try:
        from xtquant import xtdata
        _ensure_connected()

        code_list = [_fmt(c) for c in codes.split(",") if c.strip()]
        if len(code_list) > 50:
            raise HTTPException(400, "最多50只")

        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else FINANCIAL_FIELDS

        # 下载财务数据
        xtdata.download_financial_data(code_list)

        result = {}
        for mqcode in code_list:
            raw_code = mqcode.replace(".SH", "").replace(".SZ", "")
            fin = xtdata.get_financial_data([mqcode], field_list)
            if fin and mqcode in fin:
                result[raw_code] = {}
                for f in field_list:
                    val = fin[mqcode].get(f)
                    if val is not None:
                        try:
                            result[raw_code][f] = round(float(val), 4)
                        except (ValueError, TypeError):
                            result[raw_code][f] = str(val)

        return {"financial": result, "count": len(result)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Financial failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 日历
# ══════════════════════════════════════════════════════════

@router.get("/data/calendar")
async def get_calendar(
    start: str = Query("", description="起始日期 YYYYMMDD"),
    end: str = Query("", description="截止日期 YYYYMMDD"),
):
    """交易日历"""
    try:
        from xtquant import xtdata
        from datetime import datetime
        _ensure_connected()

        if not start:
            start = (datetime.now().replace(year=datetime.now().year - 1)).strftime("%Y%m%d")
        if not end:
            end = datetime.now().strftime("%Y%m%d")

        # 下载假日数据
        xtdata.download_holiday_data()

        dates = xtdata.get_trading_dates("SH", start, end)
        holidays = xtdata.get_holidays()

        return {
            "trading_days": len(dates),
            "dates": [str(d) for d in dates[-60:]],  # 最近60天
            "holidays": [str(h) for h in (holidays or [])],
            "range": f"{start} ~ {end}",
        }

    except Exception as e:
        logger.error(f"Calendar failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))
