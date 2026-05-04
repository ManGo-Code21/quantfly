# -*- encoding: utf-8 -*-
"""FastAPI 路由 — 热点监控"""
from fastapi import APIRouter, Query
from quantfly.hot_topics.monitor import HotTopicMonitor
from quantfly.hot_topics.industry_mapper import get_sector_list

router = APIRouter(prefix="/api/hot", tags=["热点监控"])
_monitor = HotTopicMonitor()


@router.get("/topics")
async def get_hot_topics():
    """获取当前热点板块"""
    items = _monitor.fetch_all()
    return {"count": len(items), "items": items}


@router.get("/industries")
async def get_industries():
    """获取支持的产业列表"""
    return {"industries": get_sector_list()}
