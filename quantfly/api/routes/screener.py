# -*- encoding: utf-8 -*-
"""FastAPI 路由 — 选股扫描"""
from fastapi import APIRouter, Query
from quantfly.screener.stock_picker import TopicDrivenScreener

router = APIRouter(prefix="/api/screener", tags=["选股扫描"])
_screener = TopicDrivenScreener()


@router.get("/screen/{industry}")
async def screen_industry(
    industry: str,
    top_n: int = Query(10, ge=1, le=20)
):
    """对指定产业执行选股扫描"""
    results = _screener.screen(industry, top_n=top_n)
    buyable = [r for r in results if r.get("is_buyable")]
    return {
        "industry": industry,
        "total": len(results),
        "buyable": len(buyable),
        "stocks": results,
    }
