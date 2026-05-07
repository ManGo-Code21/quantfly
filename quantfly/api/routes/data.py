# -*- encoding: utf-8 -*-
"""
QMT 全数据 API — Mac 按需调用 (线程池版，不再卡死事件循环)

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
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import time
import json
import math

logger = logging.getLogger("API.Data")
router = APIRouter()

# 共享线程池 — QMT xtdata 所有调用都走这里，不阻塞事件循环
_qmt_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="qmt")

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


def _fmt(code: str) -> str:
    code = code.strip()
    if code.endswith((".SH", ".SZ")):
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


async def _run_qmt(fn, *args, timeout=30, **kwargs):
    """在独立线程中执行 QMT 同步调用，不阻塞事件循环"""
    future = asyncio.get_running_loop().run_in_executor(_qmt_executor, fn, *args, **kwargs)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"QMT调用超时({timeout}s)，数据源可能正在初始化")


# ══════════════════════════════════════════════════════════
# QMT 同步调用（在线程池中执行）
# ══════════════════════════════════════════════════════════

def _qmt_connect():
    from xtquant import xtdata
    xtdata.connect()


def _qmt_get_quotes(code_list: list[str]) -> dict:
    """在子线程中调用 QMT 获取行情"""
    from xtquant import xtdata
    import numpy as np
    _qmt_connect()
    fields = ["open", "high", "low", "close", "volume", "amount"]
    raw = xtdata.get_market_data_ex(
        stock_list=code_list, period="1d", start_time="", end_time="",
        count=2, field_list=fields, dividend_type="front",
    )
    result = {}
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
    return result


def _qmt_get_kline(mqcode: str, period: str, count: int) -> list[dict]:
    """在子线程中调用 QMT 获取 K 线"""
    from xtquant import xtdata
    import numpy as np
    _qmt_connect()
    fields = ["open", "high", "low", "close", "volume", "amount"]
    xtdata.download_history_data(mqcode, period, "", "")
    raw = xtdata.get_market_data(
        stock_list=[mqcode], period=period, count=count,
        field_list=fields, dividend_type="front",
    )
    if not raw or all(v.empty for v in raw.values()):
        return []
    dates = None
    for field_df in raw.values():
        if not field_df.empty and mqcode in field_df.index:
            dates = field_df.columns
            break
    if dates is None:
        return []
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
    return candles[-count:]


def _qmt_get_ticks(mqcode: str, count: int) -> list[dict]:
    """在子线程中调用 QMT 获取逐笔成交"""
    from xtquant import xtdata
    import pandas as pd
    _qmt_connect()
    raw = xtdata.get_full_tick([mqcode])
    if not raw or mqcode not in raw:
        return []
    tick_data = raw[mqcode]
    if not isinstance(tick_data, pd.DataFrame):
        return []
    df = tick_data.tail(count)
    ticks = []
    for _, row in df.iterrows():
        ticks.append({
            "time": str(row.get("time", "")),
            "price": float(row.get("price", row.get("lastPrice", 0)) or 0),
            "volume": int(row.get("volume", 0) or 0),
            "amount": float(row.get("amount", 0) or 0),
            "type": str(row.get("type", "")),
        })
    return ticks[-count:]


def _qmt_get_sectors() -> list[str]:
    """在子线程中调用 QMT 获取板块列表"""
    from xtquant import xtdata
    _qmt_connect()
    xtdata.download_sector_data()
    return xtdata.get_sector_list()


def _qmt_get_sector_stocks(sector: str) -> list[str]:
    """在子线程中调用 QMT 获取板块成分股"""
    from xtquant import xtdata
    _qmt_connect()
    xtdata.download_sector_data()
    return xtdata.get_stock_list_in_sector(sector)


def _qmt_get_index_weight(mqindex: str, date: str) -> dict:
    """在子线程中调用 QMT 获取指数权重"""
    from xtquant import xtdata
    _qmt_connect()
    return xtdata.get_index_weight(mqindex, date) or {}


def _qmt_get_financial(code_list: list[str], field_list: list[str]) -> dict:
    """在子线程中调用 QMT 获取财务数据"""
    from xtquant import xtdata
    _qmt_connect()
    xtdata.download_financial_data(code_list)
    result = {}
    for mqcode in code_list:
        fin = xtdata.get_financial_data([mqcode], field_list)
        if fin and mqcode in fin:
            raw_code = mqcode.replace(".SH", "").replace(".SZ", "")
            result[raw_code] = {}
            for f in field_list:
                val = fin[mqcode].get(f)
                if val is not None:
                    try:
                        result[raw_code][f] = round(float(val), 4)
                    except (ValueError, TypeError):
                        result[raw_code][f] = str(val)
    return result


def _qmt_get_calendar(start: str, end: str) -> dict:
    """在子线程中调用 QMT 获取交易日历"""
    from xtquant import xtdata
    _qmt_connect()
    xtdata.download_holiday_data()
    dates = xtdata.get_trading_dates("SH", start, end)
    holidays = xtdata.get_holidays()
    return {
        "trading_days": len(dates),
        "dates": [str(d) for d in dates[-60:]],
        "holidays": [str(h) for h in (holidays or [])],
        "range": f"{start} ~ {end}",
    }


# ══════════════════════════════════════════════════════════
# 行情 API
# ══════════════════════════════════════════════════════════

@router.get("/data/quote")
async def get_quotes(codes: str = Query(..., description="逗号分隔，如 000001,600000")):
    """实时行情 — 最新价、涨跌幅、成交量等"""
    try:
        code_list = [_fmt(c) for c in codes.split(",") if c.strip()]
        if len(code_list) > 100:
            raise HTTPException(400, "最多100只")
        result = await _run_qmt(_qmt_get_quotes, code_list)
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
        mqcode = _fmt(code)
        candles = await _run_qmt(_qmt_get_kline, mqcode, period, count)
        return {"code": code, "period": period, "candles": candles, "count": len(candles)}
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
    """分钟K线快捷接口"""
    return await get_kline(code=code, period=period, count=count)


@router.get("/data/tick")
async def get_ticks(
    code: str = Query(...),
    count: int = Query(100, ge=1, le=5000),
):
    """逐笔成交数据"""
    try:
        mqcode = _fmt(code)
        ticks = await _run_qmt(_qmt_get_ticks, mqcode, count)
        return {"code": code, "ticks": ticks, "count": len(ticks)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Tick failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 市场 API
# ══════════════════════════════════════════════════════════

@router.get("/data/sectors")
async def get_sectors():
    """所有板块列表"""
    try:
        sectors = await _run_qmt(_qmt_get_sectors, timeout=90)
        return {"sectors": sectors, "count": len(sectors)}
    except Exception as e:
        logger.error(f"Sectors failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/data/sector/stocks")
async def get_sector_stocks(sector: str = Query(..., description="板块名，如 沪深300、中证500")):
    """板块成分股"""
    try:
        stocks = await _run_qmt(_qmt_get_sector_stocks, sector, timeout=90)
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
        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%Y%m%d")
        mqindex = _fmt(index)
        weights = await _run_qmt(_qmt_get_index_weight, mqindex, date)
        return {"index": index, "date": date, "weights": weights, "count": len(weights)}
    except Exception as e:
        logger.error(f"Index weight failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 财务 API
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
        code_list = [_fmt(c) for c in codes.split(",") if c.strip()]
        if len(code_list) > 50:
            raise HTTPException(400, "最多50只")
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else FINANCIAL_FIELDS
        result = await _run_qmt(_qmt_get_financial, code_list, field_list)
        return {"financial": result, "count": len(result)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Financial failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 日历 API
# ══════════════════════════════════════════════════════════

@router.get("/data/calendar")
async def get_calendar(
    start: str = Query("", description="起始日期 YYYYMMDD"),
    end: str = Query("", description="截止日期 YYYYMMDD"),
):
    """交易日历"""
    try:
        from datetime import datetime
        if not start:
            start = (datetime.now().replace(year=datetime.now().year - 1)).strftime("%Y%m%d")
        if not end:
            end = datetime.now().strftime("%Y%m%d")
        return await _run_qmt(_qmt_get_calendar, start, end)
    except Exception as e:
        logger.error(f"Calendar failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════
# 资金流向（多数据源 fallback — 不涉及 xtdata，不受影响）
# ══════════════════════════════════════════════════════════

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}

EM_MF_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
EM_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


def _fetch_money_flow_em(code: str, days: int) -> list[dict] | None:
    """东方财富 HTTP API → 资金流向日线"""
    import requests as req
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    r = req.get(EM_MF_URL, params={
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": EM_FIELDS,
        "lmt": days,
        "klt": "101",
    }, headers=EM_HEADERS, timeout=8,
       proxies={"http": None, "https": None})
    klines = r.json().get("data", {}).get("klines", [])
    if not klines:
        return None
    flows = []
    for k in klines:
        p = k.split(",")
        flows.append({
            "date": p[0],
            "main_net": float(p[1]),
            "small": float(p[2]),
            "medium": float(p[3]),
            "large": float(p[4]),
            "super_large": float(p[5]),
            "main_net_ratio": float(p[6]),
            "small_ratio": float(p[7]),
            "medium_ratio": float(p[8]),
            "large_ratio": float(p[9]),
            "super_large_ratio": float(p[10]),
            "close": float(p[11]),
            "pct_chg": float(p[12]),
        })
    return flows


def _fetch_money_flow_ak(code: str, days: int) -> list[dict] | None:
    """akshare fallback → 资金流向日线"""
    try:
        import akshare as ak
        mkt = "sh" if code.startswith(("6", "9")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=mkt)
        if df is None or df.empty:
            return None
        df = df.tail(days)
        flows = []
        for _, row in df.iterrows():
            flows.append({
                "date": str(row["日期"]),
                "main_net": float(row.get("主力净流入-净额", 0)),
                "small": float(row.get("小单净流入-净额", 0)),
                "medium": float(row.get("中单净流入-净额", 0)),
                "large": float(row.get("大单净流入-净额", 0)),
                "super_large": float(row.get("超大单净流入-净额", 0)),
                "close": float(row.get("收盘价", 0)) or None,
                "pct_chg": float(row.get("涨跌幅", 0)) or None,
                "main_net_ratio": float(row.get("主力净流入-净占比", 0)),
                "large_net_ratio": float(row.get("大单净流入-净占比", 0)),
                "medium_net_ratio": float(row.get("中单净流入-净占比", 0)),
                "small_net_ratio": float(row.get("小单净流入-净占比", 0)),
            })
        return flows
    except Exception:
        return None


def _get_money_flow_one(code: str, days: int) -> tuple[list[dict] | None, str]:
    """单只股票资金流向: 东方财富 → akshare，返回 (data, source)"""
    try:
        data = _fetch_money_flow_em(code, days)
        if data:
            return data, "eastmoney"
    except Exception:
        pass
    try:
        data = _fetch_money_flow_ak(code, days)
        if data:
            return data, "akshare"
    except Exception:
        pass
    return None, "none"


@router.get("/data/money_flow")
async def get_money_flow(
    codes: str = Query(..., description="逗号分隔，如 000001,600000"),
    days: int = Query(5, ge=1, le=250),
):
    """
    资金流向 — 主力/超大单/大单/中单/小单净流入（多数据源 fallback）
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if len(code_list) > 20:
        raise HTTPException(400, "最多20只")

    result = {}
    source_count = {"eastmoney": 0, "akshare": 0, "none": 0}
    for code in code_list:
        data, src = _get_money_flow_one(code, days)
        source_count[src] += 1
        if data:
            result[code] = data

    return {
        "money_flow": result,
        "source": source_count,
        "count": len(result),
    }
