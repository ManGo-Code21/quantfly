# -*- encoding: utf-8 -*-
"""
QuantFly 策略API路由

POST /strategy/signal    — 获取V13买卖信号（同步）
GET  /strategy/status    — 策略状态（模式/温度/仓位/下次调仓日）
GET  /strategy/positions — 当前持仓（来自 xtquant query_stock_positions）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("API.Strategy")

router = APIRouter(prefix='/strategy')

# ── 缓存最近一次信号结果 ───────────────────────────────────
_last_signal: Optional[dict] = None


# ── 请求模型 ──────────────────────────────────────────────
class SignalRequest(BaseModel):
    """信号请求体"""
    current_position: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="当前仓位比例 (0.0~1.0)"
    )


# ── 端点 ──────────────────────────────────────────────────

@router.post("/signal")
async def get_signal(req: SignalRequest):
    """
    获取V13策略买卖信号（同步执行）

    调用 v13_live.get_v13_signals() 计算板块动量→质量筛选→选股评分，
    返回完整的信号字典（含选股结果、市场温度、目标仓位等）。

    Args:
        req: SignalRequest，current_position 当前仓位比例

    Returns:
        dict: V13信号（date/is_bearish/temperature/target_position/picks 等）
    """
    try:
        from v13_live import get_v13_signals

        signals = get_v13_signals(current_position=req.current_position)

        if signals.get('error'):
            raise HTTPException(status_code=500, detail=signals['error'])

        # 缓存到模块级变量，供 /status 读取
        global _last_signal
        _last_signal = signals

        logger.info(
            f"V13信号: {signals.get('mode')} | "
            f"仓位{signals.get('target_position', 0) * 100:.0f}% | "
            f"温度{signals.get('temperature', 0)} | "
            f"选股:{[p.get('code') for p in signals.get('picks', [])]}"
        )

        return signals

    except HTTPException:
        raise
    except ImportError as e:
        logger.error(f"导入v13_live失败: {e}")
        raise HTTPException(status_code=500, detail="v13_live模块未找到，请确认项目根目录在PYTHONPATH中")
    except Exception as e:
        logger.error(f"获取V13信号失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status():
    """
    获取策略状态快照

    返回最近一次信号的摘要信息：市场模式、温度、目标仓位、下次调仓日。
    若尚未运行过信号，返回空状态提示。

    Returns:
        dict: {mode, temperature, position, next_rebalance, is_bearish, volatility}
    """
    global _last_signal

    if _last_signal is None:
        return {
            "mode": "未知",
            "temperature": 0.0,
            "position": 0.0,
            "next_rebalance": None,
            "is_bearish": False,
            "volatility": 0.0,
            "message": "尚未运行策略，请先调用 POST /strategy/signal",
        }

    # 推算下次调仓日（信号日期 + 跳过周末）
    signal_date = _last_signal.get('date', '')
    next_rebalance = None
    if signal_date:
        try:
            dt = datetime.strptime(signal_date, '%Y-%m-%d')
            dt += timedelta(days=1)
            while dt.weekday() >= 5:  # 5=周六, 6=周日
                dt += timedelta(days=1)
            next_rebalance = dt.strftime('%Y-%m-%d')
        except ValueError:
            logger.warning(f"无法解析信号日期: {signal_date}")

    return {
        "mode": _last_signal.get('mode', '未知'),
        "temperature": _last_signal.get('temperature', 0.0),
        "position": _last_signal.get('target_position', 0.0),
        "next_rebalance": next_rebalance,
        "is_bearish": _last_signal.get('is_bearish', False),
        "volatility": _last_signal.get('volatility', 0.0),
    }


@router.get("/positions")
async def get_positions():
    """
    获取当前持仓（来自 xtquant query_stock_positions）

    返回 QMT/MiniQMT 中的实际持仓列表，每条包含：
    code / volume / cost / price / pnl_pct

    若 xtquant 不可用，降级尝试 QMT Connector，再失败返回空列表。

    Returns:
        dict: {positions: [...], count: int, source: str}
    """
    positions = []
    source = "unknown"

    # ── 优先：xtquant 直接查询 ──
    try:
        from xtquant import xtdata
        xtdata.connect()

        # query_stock_positions 在 xttrader 中，需要 trader 连接
        from xtquant.xttrader import XtQuantTrader

        # 尝试从 xtdata 获取账户持仓（MiniQMT 环境下可能通过 data 层可查）
        # 实际使用中，query_stock_positions 需要在 MiniQMT 内调用
        try:
            from xtquant import xttrader
            # xttrader.query_stock_positions() 是可用接口
            positions_raw = xttrader.query_stock_positions()
            source = "xtquant"

            for pos in positions_raw:
                positions.append({
                    "code": str(pos.get('stock_code', '') or pos.get('code', '')),
                    "volume": int(pos.get('volume', 0) or pos.get('持仓数量', 0)),
                    "cost": float(pos.get('cost', 0) or pos.get('成本价', 0) or pos.get('avg_price', 0)),
                    "price": float(pos.get('price', 0) or pos.get('最新价', 0) or pos.get('last_price', 0)),
                    "pnl_pct": float(pos.get('pnl_pct', 0) or pos.get('浮动盈亏比例', 0) or pos.get('profit_ratio', 0)),
                })
        except (AttributeError, ImportError, TypeError) as e:
            logger.debug(f"xtquant.xttrader 不可用: {e}")
            # xtquant 在非 MiniQMT 环境下 xttrader 可能不可用，抛给外层降级
            raise

    except Exception as e:
        logger.warning(f"xtquant 查询持仓失败: {e}，尝试 QMT Connector 降级")
        source = "qmt_connector"

        # ── 降级：QMT Connector ──
        try:
            from quantfly.trading.qmt_connector import get_qmt_connector
            qmt = get_qmt_connector()
            raw_positions = qmt.get_positions() if qmt._connected else []
            for pos in raw_positions:
                positions.append({
                    "code": str(pos.get('code', '')),
                    "volume": int(pos.get('volume', 0)),
                    "cost": float(pos.get('cost', 0)),
                    "price": float(pos.get('price', 0)),
                    "pnl_pct": float(pos.get('pnl_pct', 0)),
                })
        except Exception as e2:
            logger.error(f"QMT Connector 降级也失败: {e2}")

    return {
        "positions": positions,
        "count": len(positions),
        "source": source,
    }
