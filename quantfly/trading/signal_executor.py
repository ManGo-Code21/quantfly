# -*- encoding: utf-8 -*-
"""
信号执行器 — 选股信号 → QMT下单
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("Trading.Executor")


def scan_and_signal(industry: str, dry_run: bool = True) -> list[dict]:
    """
    扫描指定产业，输出选股信号
    """
    try:
        from quantfly.screener.stock_picker import TopicDrivenScreener
        screener = TopicDrivenScreener()
        results = screener.screen(industry, top_n=10)
        buyable = [r for r in results if r.get("is_buyable")]
        logger.info(f"[{industry}] 扫描完成: {len(results)}只候选, {len(buyable)}只可买")
        return results
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        return []


def execute_buy(codes: list[dict], dry_run: bool = True) -> dict:
    """
    对给定的股票执行买入（只有is_buyable=True的才能买）
    """
    if dry_run:
        logger.info("[DRY-RUN] 跳过实际下单")
        return {}

    from quantfly.trading.qmt_connector import get_qmt_connector
    qmt = get_qmt_connector()
    if not qmt._connected:
        logger.error("QMT未连接，无法下单")
        return {}

    results = {}
    for stock in codes:
        code = stock["code"]
        name = stock["name"]
        result = qmt.buy(stock_code=code, amount=0, price=None,
                         strategy="HotTopicScreener",
                         order_remark=f"热点选股 score={stock.get('total_score', 0)}")
        results[code] = result

    return results
