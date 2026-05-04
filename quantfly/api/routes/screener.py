# -*- encoding: utf-8 -*-
"""
选股API路由
POST /api/screener/screen — 执行选股扫描
GET  /api/screener/principles — 获取选股三原则说明
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger("API.Screener")

router = APIRouter()


class ScreenRequest(BaseModel):
    industry: str = "AI大模型"
    top_n: int = 10


class StockSignal(BaseModel):
    code: str
    name: str
    industry: str
    total_score: float
    is_buyable: bool
    theme_score: float
    chips_score: float
    momentum_score: float
    signals: list[str]


@router.post("/screen")
async def screen_stocks(req: ScreenRequest):
    """对指定产业执行选股扫描"""
    try:
        from quantfly.screener.stock_picker import TopicDrivenScreener

        screener = TopicDrivenScreener()
        results = screener.screen(req.industry, top_n=req.top_n)
        return {
            "industry": req.industry,
            "total": len(results),
            "buyable": len([r for r in results if r.get('is_buyable')]),
            "stocks": results,
        }
    except Exception as e:
        logger.error(f"选股扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/principles")
async def get_principles():
    """选股三原则说明"""
    return {
        "principles": [
            {
                "name": "题材原则",
                "description": "涨幅3%~9.8%之间，有赚钱效应但未涨停",
                "weight": 1.5,
            },
            {
                "name": "筹码原则",
                "description": "位置0.1~0.7，上方空间>15%，未大幅上涨",
                "weight": 6.0,
            },
            {
                "name": "分时原则",
                "description": "量比≥1.5，在均价线上，主动买入强度≥0.7",
                "weight": 3.5,
            },
        ]
    }
