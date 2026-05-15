# -*- encoding: utf-8 -*-
"""
资金流向 API — 北向/主力/行业/个股
=================================
GET /flow/overview       — 全市场资金总览
GET /flow/sectors        — 行业资金流向 TOP10
GET /flow/stock/{code}   — 个股资金流向
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import requests
import time
import math

logger = logging.getLogger("API.Flow")
router = APIRouter(prefix="/flow")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/",
}


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return default


# ═══════════════════════════════════════════
# 北向资金
# ═══════════════════════════════════════════

def _fetch_north_flow() -> dict:
    """获取北向资金（沪股通+深股通）"""
    try:
        import akshare as ak
        df_sh = ak.stock_hsgt_north_net_flow_in_em(symbol="沪股通")
        df_sz = ak.stock_hsgt_north_net_flow_in_em(symbol="深股通")
        sh = float(df_sh["value"].iloc[-1]) if not df_sh.empty else 0
        sz = float(df_sz["value"].iloc[-1]) if not df_sz.empty else 0
        return {"shanghai": round(sh, 2), "shenzhen": round(sz, 2), "total": round(sh + sz, 2),
                "date": str(df_sh["date"].iloc[-1]) if not df_sh.empty else ""}
    except Exception as e:
        logger.warning(f"北向资金获取失败: {e}")
        return {"shanghai": 0, "shenzhen": 0, "total": 0, "date": ""}


# ═══════════════════════════════════════════
# 行业资金流向（东方财富）
# ═══════════════════════════════════════════

SECTOR_FLOW_URL = "https://push2.eastmoney.com/api/qt/clist/get"

SECTOR_FLOW_PARAMS = {
    "pn": "1", "pz": "200", "po": "1", "np": "1",
    "fltt": "2", "invt": "2",
    "fid": "f62",  # 主力净流入
    "fs": "m:90+t2",  # 行业板块
    "fields": "f12,f14,f2,f3,f62,f66,f72,f184,f69",
}


def _fetch_sector_flow() -> list:
    """获取行业板块资金流向"""
    try:
        r = requests.get(SECTOR_FLOW_URL, params=SECTOR_FLOW_PARAMS, headers=EM_HEADERS, timeout=10)
        data = r.json()
        items = data.get("data", {}).get("diff", [])
        result = []
        for it in items:
            result.append({
                "code": it.get("f12", ""),
                "name": it.get("f14", ""),
                "pct_chg": _safe_float(it.get("f3")),
                "price": _safe_float(it.get("f2")),
                "main_net_inflow": _safe_float(it.get("f62")),  # 主力净流入(万)
                "super_large_net": _safe_float(it.get("f66")),  # 超大单净流入
                "large_net": _safe_float(it.get("f72")),       # 大单净流入
                "volume": _safe_float(it.get("f184")),          # 成交量
                "turnover_rate": _safe_float(it.get("f69")),    # 换手率
            })
        return result
    except Exception as e:
        logger.warning(f"行业资金流向获取失败: {e}")
        return []


# ═══════════════════════════════════════════
# 个股资金流向
# ═══════════════════════════════════════════

STOCK_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"


def _fetch_stock_flow(code: str, days: int = 5) -> dict:
    """获取个股资金流向"""
    clean = code.split(".")[0]
    market = 1 if code.endswith(".SH") or code.startswith(("6", "9")) else 0
    secid = f"{market}.{clean}"
    try:
        r = requests.get(STOCK_FLOW_URL, params={
            "lmt": days, "secid": secid,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        }, headers=EM_HEADERS, timeout=10)
        data = r.json()
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return {"code": code, "days": []}

        total_main = 0
        total_super = 0
        total_big = 0
        total_mid = 0
        total_small = 0
        daily = []

        for line in klines[-days:]:
            parts = line.split(",")
            if len(parts) >= 7:
                d = parts[0]
                main_val = _safe_float(parts[1])
                small_val = _safe_float(parts[2])
                mid_val = _safe_float(parts[3])
                big_val = _safe_float(parts[4])
                super_val = _safe_float(parts[5])
                total_main += main_val
                total_super += super_val
                total_big += big_val
                total_mid += mid_val
                total_small += small_val
                daily.append({"date": d, "main_net": main_val, "super_large": super_val,
                              "large": big_val, "mid": mid_val, "small": small_val})

        return {
            "code": code,
            "main_net_flow": round(total_main, 2),        # 主力净流入(万)
            "super_large_net": round(total_super, 2),     # 超大单
            "large_net": round(total_big, 2),             # 大单
            "mid_net": round(total_mid, 2),               # 中单
            "small_net": round(total_small, 2),           # 小单
            "daily": daily,
        }
    except Exception as e:
        logger.warning(f"个股资金流向失败 {code}: {e}")
        return {"code": code, "error": str(e)}


# ═══════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════

@router.get("/overview")
async def flow_overview():
    """全市场资金总览"""
    north = _fetch_north_flow()
    sectors = _fetch_sector_flow()

    total_main_in = sum(s["main_net_inflow"] for s in sectors if s["main_net_inflow"] > 0)
    total_main_out = sum(abs(s["main_net_inflow"]) for s in sectors if s["main_net_inflow"] < 0)

    inflow_top = sorted([s for s in sectors if s["main_net_inflow"] > 0],
                        key=lambda x: x["main_net_inflow"], reverse=True)[:10]
    outflow_top = sorted([s for s in sectors if s["main_net_inflow"] < 0],
                         key=lambda x: x["main_net_inflow"])[:10]

    return {
        "north_bound": north,
        "sector_summary": {
            "total_main_inflow": round(total_main_in, 2),
            "total_main_outflow": round(total_main_out, 2),
            "net_flow": round(total_main_in - total_main_out, 2),
        },
        "inflow_top10": inflow_top,
        "outflow_top10": outflow_top,
        "timestamp": time.time(),
    }


@router.get("/sectors")
async def flow_sectors(limit: int = Query(20, ge=1, le=200)):
    """行业资金流向"""
    sectors = _fetch_sector_flow()
    sectors.sort(key=lambda x: x["main_net_inflow"], reverse=True)
    return {"sectors": sectors[:limit], "total": len(sectors), "timestamp": time.time()}


@router.get("/stock/{code:path}")
async def flow_stock(code: str, days: int = Query(5, ge=1, le=30)):
    """个股资金流向"""
    result = _fetch_stock_flow(code, days)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result
