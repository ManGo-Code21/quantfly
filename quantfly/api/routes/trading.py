# -*- encoding: utf-8 -*-
"""
交易API路由
POST /api/trading/scan — 扫描选股信号
POST /api/trading/buy — 执行买入
GET  /api/trading/positions — 获取当前持仓
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger("API.Trading")

router = APIRouter()


@router.get("/positions")
async def get_positions():
    """获取当前持仓"""
    try:
        from quantfly.trading.qmt_connector import get_qmt_connector
        qmt = get_qmt_connector()
        if not qmt._connected:
            return {"positions": [], "connected": False}
        positions = qmt.get_positions()
        return {"positions": positions, "connected": True}
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        return {"positions": [], "connected": False, "error": str(e)}


class ScanRequest(BaseModel):
    industry: str = "AI大模型"
    dry_run: bool = True


@router.post("/scan")
async def scan_signals(req: ScanRequest):
    """扫描指定产业的选股信号"""
    try:
        from quantfly.trading.signal_executor import scan_and_signal
        results = scan_and_signal(req.industry, dry_run=req.dry_run)
        buyable = [r for r in results if r.get('is_buyable')]
        return {
            "industry": req.industry,
            "total": len(results),
            "buyable": buyable,
            "dry_run": req.dry_run,
        }
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BuyRequest(BaseModel):
    code: str
    name: str
    amount: int
    price: Optional[float] = None
    dry_run: bool = True


@router.post("/buy")
async def execute_buy(req: BuyRequest):
    """执行买入"""
    try:
        from quantfly.trading.signal_executor import execute_buy as do_buy
        result = do_buy([{"code": req.code, "name": req.name, "total_score": 0}], dry_run=req.dry_run)
        return {"result": result, "dry_run": req.dry_run}
    except Exception as e:
        logger.error(f"买入失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
