# -*- encoding: utf-8 -*-
"""
Dashboard API路由 — 交易分析看板
==================================
实时持仓分析 / 板块热力图 / 热点板块

端点:
  GET  /dashboard/positions   → 实时持仓分析（QMT xtquant）
  GET  /dashboard/sectors      → 板块热力图（30+板块涨跌幅/成交额/RSRS）
  GET  /dashboard/hot          → 热点板块（涨幅TOP5 + 量比 + 涨停数）
"""
import asyncio
import logging
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("API.Dashboard")

router = APIRouter()

# ── 共享线程池（与 data.py 共用模式） ──────────────────────
_qmt_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="qmt")

QMT_BIN = r"D:\国金证券QMT交易端\bin.x64"


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


async def _run_qmt(fn, *args, timeout=30, **kwargs):
    """在独立线程中执行 QMT 同步调用，不阻塞事件循环"""
    future = asyncio.get_running_loop().run_in_executor(_qmt_executor, fn, *args, **kwargs)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"QMT调用超时({timeout}s)，数据源可能正在初始化")


# ═══════════════════════════════════════════════════════════════
# Dashboard 1: 实时持仓分析
# ═══════════════════════════════════════════════════════════════

def _qmt_get_positions_analysis() -> dict:
    """
    在子线程中调用 QMT，获取持仓 + 资产 + 盈亏分析。
    使用 xtquant.xttrader 的 query_stock_positions / query_stock_asset，
    回退到 QMT HTTP 服务（如果 xtquant 交易模块不可用）。
    """
    try:
        if QMT_BIN and QMT_BIN not in sys.path:
            sys.path.insert(0, QMT_BIN)

        from xtquant import xtdata
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        import time as _time

        xtdata.connect()

        # ── 尝试连接交易模块 ──
        session_id = int(_time.time()) % 10000 + 1000
        xt_trader = XtQuantTrader(QMT_BIN, session_id)
        xt_trader.start()

        # 尝试自动获取资金账号
        accounts = xt_trader.query_accounts()
        if not accounts:
            return _qmt_positions_via_http()

        acc = accounts[0] if hasattr(accounts[0], "m_strAccountID") else None
        if acc is None:
            return _qmt_positions_via_http()

        # 订阅账号
        conn_result = xt_trader.subscribe(acc)
        if conn_result != 0:
            logger.warning("QMT交易账号订阅失败，回退HTTP模式")
            return _qmt_positions_via_http()

        # ── query_stock_positions ──
        positions = xt_trader.query_stock_positions(acc)
        if positions is None:
            positions = []

        # ── query_stock_asset ──
        asset = xt_trader.query_stock_asset(acc)
        available_cash = asset.m_dCash if asset else 0.0
        total_asset = asset.m_dTotalAsset if asset else 0.0

        # ── 获取实时价格 ──
        codes_for_tick = []
        for p in positions:
            code = getattr(p, "stock_code", "")
            if code and getattr(p, "m_nCanUseVolume", 0) > 0:
                codes_for_tick.append(code)

        tick_data = {}
        if codes_for_tick:
            try:
                raw_ticks = xtdata.get_full_tick(codes_for_tick)
                for code in codes_for_tick:
                    if code in raw_ticks:
                        tick_data[code] = {
                            "lastPrice": float(raw_ticks[code].get("lastPrice", 0)),
                            "open": float(raw_ticks[code].get("open", 0)),
                            "preClose": float(raw_ticks[code].get("lastClose", 0)),
                        }
            except Exception as e:
                logger.warning(f"获取实时行情失败: {e}")

        # ── 计算每只盈亏 ──
        position_list = []
        total_market_value = 0.0
        total_profit = 0.0

        for p in positions:
            code = getattr(p, "stock_code", "")
            volume = getattr(p, "m_nCanUseVolume", 0)
            frozen = getattr(p, "m_nFrozenVolume", 0)
            total_vol = volume + frozen
            if total_vol <= 0:
                continue

            avg_price = getattr(p, "m_dOpenAvgPrice", 0)
            name = getattr(p, "stock_name", code)

            tick = tick_data.get(code, {})
            current_price = tick.get("lastPrice", avg_price)
            pre_close = tick.get("preClose", 0)

            market_value = total_vol * current_price
            cost_value = total_vol * avg_price
            profit = market_value - cost_value
            profit_pct = (profit / cost_value * 100) if cost_value > 0 else 0
            day_pct = ((current_price / pre_close - 1) * 100) if pre_close > 0 else 0

            total_market_value += market_value
            total_profit += profit

            position_list.append({
                "code": code,
                "name": name,
                "volume": total_vol,
                "available": volume,
                "frozen": frozen,
                "avg_price": round(avg_price, 3),
                "current_price": round(current_price, 3),
                "market_value": round(market_value, 2),
                "cost_value": round(cost_value, 2),
                "profit": round(profit, 2),
                "profit_pct": round(profit_pct, 2),
                "day_pct": round(day_pct, 2),
                "pre_close": round(pre_close, 3),
            })

        # ── 汇总 ──
        calculated_total = available_cash + total_market_value
        # 如果 QMT 返回了总资产，优先使用
        final_total = total_asset if total_asset > 0 else calculated_total
        position_ratio = (total_market_value / final_total * 100) if final_total > 0 else 0

        return {
            "connected": True,
            "source": "xtquant",
            "summary": {
                "total_asset": round(final_total, 2),
                "available_cash": round(available_cash, 2),
                "market_value": round(total_market_value, 2),
                "position_ratio": round(position_ratio, 2),
                "total_profit": round(total_profit, 2),
                "total_profit_pct": round(total_profit / (final_total - total_profit) * 100, 2) if (final_total - total_profit) > 0 else 0,
                "stock_count": len(position_list),
            },
            "positions": position_list,
            "timestamp": datetime.now().isoformat(),
        }

    except ImportError:
        logger.warning("xtquant 未安装，回退 HTTP 模式")
        return _qmt_positions_via_http()
    except Exception as e:
        logger.error(f"QMT持仓分析失败: {e}", exc_info=True)
        return _qmt_positions_via_http()


def _qmt_positions_via_http() -> dict:
    """通过 QMT HTTP 服务获取持仓（回退方案）"""
    try:
        from quantfly.trading.qmt_client import QMTClient
        client = QMTClient()
        result = client.get_positions()

        if result.get("error"):
            return {
                "connected": False,
                "source": "http_fallback",
                "error": result["error"],
                "summary": {},
                "positions": [],
                "timestamp": datetime.now().isoformat(),
            }

        positions = result.get("positions", result.get("data", []))
        return {
            "connected": True,
            "source": "http_fallback",
            "summary": {
                "stock_count": len(positions),
                "note": "HTTP fallback 模式，盈亏信息有限",
            },
            "positions": positions,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"HTTP持仓查询失败: {e}")
        return {
            "connected": False,
            "source": "none",
            "error": f"QMT不可用: {e}",
            "summary": {},
            "positions": [],
            "timestamp": datetime.now().isoformat(),
        }


@router.get("/dashboard/positions")
async def get_dashboard_positions():
    """
    实时持仓分析

    调用 QMT xtquant query_stock_positions + query_stock_asset，
    计算总资产、仓位占比、每只股票的浮动盈亏。

    Returns:
        {
            "connected": true,
            "source": "xtquant",
            "summary": {
                "total_asset": 100000.00,
                "available_cash": 30000.00,
                "market_value": 70000.00,
                "position_ratio": 70.00,
                "total_profit": 5000.00,
                "total_profit_pct": 5.26,
                "stock_count": 3
            },
            "positions": [
                {
                    "code": "000001",
                    "name": "平安银行",
                    "volume": 1000,
                    "available": 1000,
                    "avg_price": 12.50,
                    "current_price": 13.00,
                    "market_value": 13000.00,
                    "profit": 500.00,
                    "profit_pct": 4.00,
                    "day_pct": 1.50,
                },
                ...
            ]
        }
    """
    try:
        result = await _run_qmt(_qmt_get_positions_analysis, timeout=30)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard positions 失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════
# Dashboard 2: 板块热力图
# ═══════════════════════════════════════════════════════════════

# 30+ 行业板块及对应东方财富板块代码
SECTOR_INDEX_MAP = {
    "AI大模型": "BK1121",
    "人工智能": "BK1121",
    "半导体": "BK0917",
    "芯片": "BK1121",
    "机器人": "BK1106",
    "储能": "BK1096",
    "商业航天": "BK1162",
    "新能源汽车": "BK0900",
    "光伏": "BK1056",
    "风电": "BK1060",
    "氢能": "BK0997",
    "电池": "BK0996",
    "充电桩": "BK0954",
    "国防军工": "BK0611",
    "航天航空": "BK0613",
    "5G通信": "BK0902",
    "云计算": "BK0946",
    "大数据": "BK0944",
    "消费电子": "BK0981",
    "传媒娱乐": "BK0401",
    "游戏": "BK0998",
    "医药": "BK0505",
    "创新药": "BK1113",
    "医疗保健": "BK0507",
    "消费": "BK0478",
    "白酒": "BK0504",
    "食品饮料": "BK0503",
    "银行": "BK0475",
    "证券": "BK0395",
    "保险": "BK0394",
    "房地产": "BK0415",
    "建材": "BK0375",
    "化工": "BK0382",
    "有色金属": "BK1001",
    "煤炭": "BK0387",
    "钢铁": "BK0383",
    "电力": "BK0403",
    "交通运输": "BK0413",
    "建筑": "BK0376",
}


def _fetch_sector_heatmap_data() -> dict:
    """
    在线程中获取所有板块的涨跌幅、成交额、RSRS 动量。
    数据源：东方财富板块行情 + SectorMomentum RSRS。
    """
    import numpy as np
    import pandas as pd

    import requests

    session = requests.Session()
    session.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }

    sectors = []
    errors = 0

    # 去重：同一代码只查一次
    seen_codes = set()
    unique_sectors = []
    for name, code in SECTOR_INDEX_MAP.items():
        if code not in seen_codes:
            seen_codes.add(code)
            unique_sectors.append((name, code))

    # ── 1. 批量获取板块今日行情 ──
    secids = ",".join(f"90.{c}" for _, c in unique_sectors)
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "secids": secids,
            "fields": "f2,f3,f4,f5,f6,f15,f16,f17,f25",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2",
        }
        r = session.get(url, params=params, headers=headers, timeout=10)
        data = r.json().get("data", {}).get("diff", [])
    except Exception as e:
        logger.warning(f"板块行情批量请求失败: {e}")
        data = []

    quote_map = {}
    for item in data:
        code = item.get("f25", item.get("f12", ""))
        if code:
            quote_map[code] = {
                "price": _safe_float(item.get("f2")),
                "pct_chg": _safe_float(item.get("f3")),
                "volume": _safe_int(item.get("f5")),
                "amount": _safe_float(item.get("f6")),
                "turnover": _safe_float(item.get("f15")),
                "total_market_cap": _safe_float(item.get("f16")),
                "float_market_cap": _safe_float(item.get("f17")),
            }

    # ── 2. 计算 RSRS（使用 SectorMomentum） ──
    from quantfly.screener.sector_momentum import SectorMomentum

    sm = SectorMomentum(lookback_days=60)

    # ── 3. 组装结果 ──
    for name, code in unique_sectors:
        quote = quote_map.get(code, {})
        rsrs = sm.get_sector_score(name)

        # 量比：当天成交量 / 近5日均量
        vol_ratio = 1.0
        try:
            kline = sm._get_sector_kline(code, 10)
            if not kline.empty and len(kline) >= 6:
                today_vol = kline["volume"].iloc[-1] if "volume" in kline.columns else 0
                avg_vol = kline["volume"].iloc[-6:-1].mean() if "volume" in kline.columns else 1
                vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        except Exception:
            pass

        sectors.append({
            "name": name,
            "code": code,
            "price": quote.get("price", 0),
            "pct_chg": quote.get("pct_chg", 0),
            "volume": quote.get("volume", 0),
            "amount": quote.get("amount", 0),
            "turnover": quote.get("turnover", 0),
            "float_market_cap": quote.get("float_market_cap", 0),
            "rsrs": round(rsrs, 2),
            "vol_ratio": vol_ratio,
            "is_strong": rsrs > 100,
        })

    # 按涨跌幅排序
    sectors.sort(key=lambda x: x["pct_chg"], reverse=True)

    return {
        "sectors": sectors,
        "count": len(sectors),
        "errors": errors,
        "summary": {
            "up_count": sum(1 for s in sectors if s["pct_chg"] > 0),
            "down_count": sum(1 for s in sectors if s["pct_chg"] < 0),
            "flat_count": sum(1 for s in sectors if s["pct_chg"] == 0),
            "strong_count": sum(1 for s in sectors if s["is_strong"]),
            "avg_pct_chg": round(
                sum(s["pct_chg"] for s in sectors) / len(sectors), 2
            ) if sectors else 0,
        },
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/dashboard/sectors")
async def get_dashboard_sectors(
    sort_by: str = Query("pct_chg", description="排序字段：pct_chg/rsrs/volume/amount"),
    order: str = Query("desc", description="排序方向：asc/desc"),
    limit: int = Query(50, ge=1, le=100),
):
    """
    板块热力图

    获取 30+ 行业板块的涨跌幅、成交额、RSRS阻力支撑相对强度，
    返回排序后的 JSON，适用于前端热力图/排行榜展示。

    Query Params:
        sort_by  - 排序字段 (pct_chg / rsrs / volume / amount)
        order    - 排序方向 (asc / desc)
        limit    - 返回数量 (1~100)

    Returns:
        {
            "sectors": [...],
            "count": 37,
            "summary": { "up_count": 25, "down_count": 10, ... }
        }
    """
    try:
        result = await _run_qmt(_fetch_sector_heatmap_data, timeout=60)

        sectors = result.get("sectors", [])

        # 自定义排序
        reverse = order != "asc"
        if sort_by in ("pct_chg", "rsrs", "volume", "amount"):
            sectors.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)

        result["sectors"] = sectors[:limit]
        result["count"] = min(limit, len(sectors))
        result["sort_by"] = sort_by
        result["order"] = order

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard sectors 失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════
# Dashboard 3: 热点板块
# ═══════════════════════════════════════════════════════════════

def _get_zt_count(sector_code: str) -> int:
    """
    获取板块内涨停股数量（简化版）。
    从东方财富板块成分股接口获取，需涨停判断。
    """
    try:
        import requests

        session = requests.Session()
        session.trust_env = False
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        }

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "200",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": f"b:{sector_code}+f:!200",
            "fields": "f2,f3,f12,f14,f15",
            "fid": "f3",
        }
        r = session.get(url, params=params, headers=headers, timeout=10)
        stocks = r.json().get("data", {}).get("diff", [])
        zt_count = 0
        for s in stocks:
            pct = _safe_float(s.get("f3", 0))
            # A股涨停约 +9.9%（主板）/ +19.9%（创业板/科创板）
            if pct >= 9.8:
                zt_count += 1
        return zt_count
    except Exception:
        return 0


def _fetch_hot_sectors() -> dict:
    """
    在线程中计算热点板块：涨幅 TOP5 + 量比 + 涨停数。
    结合 SectorMomentum + 东方财富行情。
    """
    import requests

    from quantfly.screener.sector_momentum import SectorMomentum

    session = requests.Session()
    session.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }

    # ── 1. 获取所有板块行情 ──
    seen_codes = set()
    unique_sectors = []
    for name, code in SECTOR_INDEX_MAP.items():
        if code not in seen_codes:
            seen_codes.add(code)
            unique_sectors.append((name, code))

    secids = ",".join(f"90.{c}" for _, c in unique_sectors)
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "secids": secids,
            "fields": "f2,f3,f4,f5,f6,f15,f16,f17,f25",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2",
        }
        r = session.get(url, params=params, headers=headers, timeout=10)
        data = r.json().get("data", {}).get("diff", [])
    except Exception as e:
        logger.warning(f"热点板块行情请求失败: {e}")
        data = []

    # 构建行情映射
    sector_quotes = {}
    for item in data:
        code = item.get("f25", item.get("f12", ""))
        if code:
            sector_quotes[code] = {
                "price": _safe_float(item.get("f2")),
                "pct_chg": _safe_float(item.get("f3")),
                "volume": _safe_int(item.get("f5")),
                "amount": _safe_float(item.get("f6")),
            }

    # ── 2. 计算 RSRS + 量比 ──
    sm = SectorMomentum(lookback_days=60)

    all_sectors = []
    for name, code in unique_sectors:
        quote = sector_quotes.get(code, {})
        pct_chg = quote.get("pct_chg", 0)
        rsrs = sm.get_sector_score(name)

        # 量比
        vol_ratio = 1.0
        try:
            kline = sm._get_sector_kline(code, 10)
            if not kline.empty and len(kline) >= 6:
                today_vol = kline["volume"].iloc[-1] if "volume" in kline.columns else 0
                avg_vol = kline["volume"].iloc[-6:-1].mean() if "volume" in kline.columns else 1
                vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        except Exception:
            pass

        all_sectors.append({
            "name": name,
            "code": code,
            "pct_chg": pct_chg,
            "vol_ratio": vol_ratio,
            "rsrs": round(rsrs, 2),
            "price": quote.get("price", 0),
            "volume": quote.get("volume", 0),
            "amount": quote.get("amount", 0),
        })

    # ── 3. 按涨幅排序取 TOP5 ──
    all_sectors.sort(key=lambda x: x["pct_chg"], reverse=True)
    top5 = all_sectors[:5]

    # ── 4. 获取涨停数（并行请求每个板块） ──
    from concurrent.futures import ThreadPoolExecutor as TPE

    zt_results = {}
    with TPE(max_workers=5) as pool:
        futures = {pool.submit(_get_zt_count, s["code"]): s for s in top5}
        for fut in futures:
            s = futures[fut]
            try:
                zt_results[s["code"]] = fut.result(timeout=8)
            except Exception:
                zt_results[s["code"]] = 0

    # ── 5. 资金流向概览（简要） ──
    for s in top5:
        s["zt_count"] = zt_results.get(s["code"], 0)

    # ── 最热概览 ──
    up_sectors = [s for s in all_sectors if s["pct_chg"] > 0]
    strong_sectors = [s for s in all_sectors if s["rsrs"] > 100]

    return {
        "hot_sectors": top5,
        "all_sectors": all_sectors,
        "overview": {
            "total_sectors": len(all_sectors),
            "up_count": len(up_sectors),
            "down_count": len(all_sectors) - len(up_sectors),
            "strong_count": len(strong_sectors),
            "market_breadth": round(len(up_sectors) / len(all_sectors) * 100, 1) if all_sectors else 0,
            "avg_pct_chg": round(
                sum(s["pct_chg"] for s in all_sectors) / len(all_sectors), 2
            ) if all_sectors else 0,
        },
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/dashboard/hot")
async def get_dashboard_hot(
    include_all: bool = Query(False, description="是否返回全部板块数据（用于自定义展示）"),
):
    """
    热点板块

    返回涨幅 TOP5 板块，附带量比、涨停数、RSRS动量。
    同时提供市场广度概览（上涨/下跌板块数、强势板块数）。

    Query Params:
        include_all - 是否返回全部板块数据

    Returns:
        {
            "hot_sectors": [
                {
                    "name": "商业航天",
                    "code": "BK1162",
                    "pct_chg": 5.23,
                    "vol_ratio": 2.15,
                    "rsrs": 187.5,
                    "zt_count": 8
                },
                ...
            ],
            "overview": {
                "total_sectors": 37,
                "up_count": 25,
                "down_count": 10,
                "strong_count": 12,
                "market_breadth": 67.6
            }
        }
    """
    try:
        result = await _run_qmt(_fetch_hot_sectors, timeout=90)

        # 精简返回（除非要求全部）
        if not include_all:
            result.pop("all_sectors", None)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard hot 失败: {e}", exc_info=True)
        raise HTTPException(500, str(e))
