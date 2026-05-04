# -*- encoding: utf-8 -*-
"""
QMT连接器 — MiniQMT xtquant对接
"""
import logging
from typing import Optional

logger = logging.getLogger("Trading.QMT")


class QMTConnector:
    """
    QMT连接器（stub实现）
    完整实现参考 QuantDragon/connectors/qmt_connector.py
    """

    def __init__(self):
        self._connected = False
        self._positions = {}

    def connect(self) -> bool:
        """连接QMT"""
        try:
            # 实际应调用 xtquant
            logger.info("QMT连接（stub）")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"QMT连接失败: {e}")
            return False

    def get_positions(self) -> list[dict]:
        """获取持仓"""
        return list(self._positions.values())

    def get_quote(self, code: str) -> dict:
        """获取实时行情"""
        return {"last_price": 0.0}

    def buy(self, stock_code: str, amount: int, price: Optional[float], **kwargs) -> dict:
        """买入"""
        logger.info(f"[STUB] 买入 {stock_code} x {amount}")
        return {"success": True, "message": "stub"}

    def sell(self, stock_code: str, amount: int, price: Optional[float], **kwargs) -> dict:
        """卖出"""
        logger.info(f"[STUB] 卖出 {stock_code} x {amount}")
        return {"success": True, "message": "stub"}


_qmt_instance: Optional[QMTConnector] = None


def get_qmt_connector() -> QMTConnector:
    global _qmt_instance
    if _qmt_instance is None:
        _qmt_instance = QMTConnector()
        _qmt_instance.connect()
    return _qmt_instance
