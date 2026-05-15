# -*- encoding: utf-8 -*-
"""
盘中监测 API
=============
GET /monitor/summary  — 盘面概览（指数+涨跌家数）
GET /monitor/alerts   — 异动告警（涨停/放量/急跌）
"""
from fastapi import APIRouter, Query
import logging

logger = logging.getLogger("API.Monitor")
router = APIRouter(prefix="/monitor")


@router.get("/summary")
async def monitor_summary():
    """盘面概览"""
    from quantfly.monitor.intraday import get_monitor
    return get_monitor().get_summary()


@router.get("/alerts")
async def monitor_alerts():
    """异动告警"""
    from quantfly.monitor.intraday import get_monitor
    return get_monitor().get_alerts()
