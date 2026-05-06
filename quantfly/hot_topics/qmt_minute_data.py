# -*- encoding: utf-8 -*-
"""
QMT分钟级数据获取 — 用于成交量监控验证
==========================================
Gate2: 成交量验证模块

核心逻辑：
1. 获取ETF分钟级成交量
2. 计算同时段历史均值（20日同时段）
3. 监控量比是否异常放大

QMT API 参考：
  - xtquant.xtdatacenter.iptz_data
  - xtquant.xtdatacenter.get_market_data
  - xtquant.xtdatacenter.get_quote
"""
import logging
from typing import Optional

logger = logging.getLogger("HotTopics.QMTMinute")

# 行业ETF映射（用于分钟级监控）
INDUSTRY_ETFS = {
    "512760": "芯片半导体",
    "515790": "光伏产业",
    "512660": "国防军工",
    "159928": "主要消费",
    "512180": "生物医药",
    "515050": "5G通信",
    "159995": "人工智能",
    "515980": "云计算",
    "512800": "中证银行",
    "512690": "中证酒",
    "516110": "房地产开发",
    "515220": "煤炭燃料",
}


class QMTMinuteData:
    """QMT分钟级数据获取器"""

    def __init__(self):
        self._session = None
        self._connected = False
        self._benchmark_vol: dict[str, dict] = {}  # 行业ETF同时段历史均值

    def connect(self) -> bool:
        """连接QMT"""
        try:
            import xtquant
            import xtquant.xtdatacenter as dc

            # 连接数据服务
            dc.set_data_back_addr("172.17.0.1:5860")  # 默认地址，可能需要调整
            self._session = dc
            self._connected = True
            logger.info("QMT分钟数据服务已连接")
            return True
        except ImportError:
            logger.warning("xtquant未安装，使用stub模式")
            self._connected = False
            return False
        except Exception as e:
            logger.warning(f"QMT连接失败: {e}，使用stub模式")
            self._connected = False
            return False

    def get_minute_bars(self, code: str, count: int = 240) -> list[dict]:
        """
        获取分钟K线数据

        Args:
            code: 证券代码，如 "512760.XSHG" 或 "512760.XSHE"
            count: 获取数量（默认240=4小时）

        Returns:
            list of dict with keys: time, open, high, low, close, volume, amount
        """
        if not self._connected:
            return self._stub_minute_data(code, count)

        try:
            import xtquant.xtdatacenter as dc
            from xtquant import xtconstant

            # 获取分钟数据
            # period: "1m", "5m", "15m", "30m", "1h"
            data = dc.get_market_data(
                stock_list=[code],
                start_time=None,
                end_time=None,
                count=count,
                period="1m",
                fields=["open", "high", "low", "close", "volume", "amount"],
                dividend_type="none",
            )

            if data is None or data.empty:
                return []

            # 转换为list[dict]
            result = []
            for _, row in data.iterrows():
                result.append({
                    "time": str(row.name),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "amount": float(row["amount"]),
                })
            return result

        except Exception as e:
            logger.warning(f"QMT获取分钟数据失败 [{code}]: {e}")
            return self._stub_minute_data(code, count)

    def _stub_minute_data(self, code: str, count: int) -> list[dict]:
        """Stub模式：返回模拟数据用于开发测试"""
        import random
        from datetime import datetime, timedelta

        now = datetime.now()
        result = []
        base_vol = 1000000  # 基础成交量

        for i in range(count):
            t = now - timedelta(minutes=count - i)
            # 模拟每分钟成交量
            vol = int(base_vol * random.uniform(0.5, 2.0))
            result.append({
                "time": t.strftime("%H:%M"),
                "volume": vol,
                "amount": vol * random.uniform(3.0, 5.0),
            })
        return result

    def get_volume_ratio(self, code: str, window: int = 20) -> dict:
        """
        计算成交量比值

        Args:
            code: ETF代码，如 "512760"
            window: 历史同时段均值窗口（默认20日）

        Returns:
            dict with keys:
                - current_vol: 当前5分钟成交量
                - avg_vol: 20日同时段平均成交量
                - ratio: 量比 (current_vol / avg_vol)
                - signal: "strong" / "medium" / "weak" / "normal"
        """
        bars = self.get_minute_bars(f"{code}.XSHG", count=window * 240)
        if not bars:
            return {"current_vol": 0, "avg_vol": 0, "ratio": 1.0, "signal": "no_data"}

        import pandas as pd

        df = pd.DataFrame(bars)

        # 获取当前时刻（如 10:05）
        if len(df) > 0:
            current_time = df["time"].iloc[-1]
            # 提取小时:分钟
            current_slot = current_time[-5:] if len(current_time) > 4 else current_time

            # 筛选同时段数据
            same_slot = df[df["time"].str.contains(current_slot[-5:])]
            if len(same_slot) > 0:
                avg_vol = same_slot["volume"].mean()
            else:
                avg_vol = df["volume"].mean()

            current_vol = df["volume"].iloc[-1]
            ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

            # 信号判断
            if ratio > 2.0:
                signal = "strong"
            elif ratio > 1.5:
                signal = "medium"
            elif ratio > 1.2:
                signal = "weak"
            else:
                signal = "normal"

            return {
                "current_vol": current_vol,
                "avg_vol": avg_vol,
                "ratio": ratio,
                "signal": signal,
            }

        return {"current_vol": 0, "avg_vol": 0, "ratio": 1.0, "signal": "no_data"}

    def monitor_all_etfs(self) -> dict[str, dict]:
        """
        监控所有行业ETF的量比

        Returns:
            dict: {etf_code: volume_ratio_result}
        """
        results = {}
        for code in INDUSTRY_ETFS:
            try:
                ratio_data = self.get_volume_ratio(code)
                results[code] = {
                    "name": INDUSTRY_ETFS[code],
                    **ratio_data,
                }
            except Exception as e:
                logger.warning(f"监控ETF {code} 失败: {e}")
                results[code] = {
                    "name": INDUSTRY_ETFS[code],
                    "current_vol": 0,
                    "avg_vol": 0,
                    "ratio": 1.0,
                    "signal": "error",
                }
        return results


# ============================================================
# 异步监控器（用于Gate2实时监控）
# ============================================================

import threading
import time
from collections import defaultdict


class VolumeMonitor:
    """
    异步成交量监控器

    使用方式：
        monitor = VolumeMonitor(qmt_data)
        monitor.start()
        # 每次新闻触发时检查
        result = monitor.check_industry("512760")
        monitor.stop()
    """

    def __init__(self, qmt_data: QMTMinuteData, check_interval: int = 60):
        """
        Args:
            qmt_data: QMTMinuteData实例
            check_interval: 检查间隔（秒）
        """
        self._qmt = qmt_data
        self._interval = check_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._last_check: dict[str, float] = {}

    def start(self):
        """启动监控线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("成交量监控已启动")

    def stop(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("成交量监控已停止")

    def _run(self):
        """监控循环"""
        while self._running:
            try:
                results = self._qmt.monitor_all_etfs()
                with self._lock:
                    self._latest = results
                    now = time.time()
                    for code in results:
                        self._last_check[code] = now
            except Exception as e:
                logger.warning(f"监控循环异常: {e}")
            time.sleep(self._interval)

    def check_industry(self, code: str) -> dict:
        """
        检查指定行业的成交量状态

        Args:
            code: ETF代码，如 "512760"

        Returns:
            dict: {ratio, signal, age_seconds}
        """
        with self._lock:
            data = self._latest.get(code, {})

        if not data:
            return {"ratio": 1.0, "signal": "no_data", "age_seconds": 999}

        age = 0.0
        if code in self._last_check:
            age = time.time() - self._last_check[code]

        return {
            "ratio": data.get("ratio", 1.0),
            "signal": data.get("signal", "unknown"),
            "current_vol": data.get("current_vol", 0),
            "avg_vol": data.get("avg_vol", 0),
            "age_seconds": age,
        }

    def get_top_signals(self, min_ratio: float = 1.2) -> list[dict]:
        """
        获取所有异常信号，按量比排序

        Args:
            min_ratio: 最小量比阈值

        Returns:
            list of {code, name, ratio, signal}
        """
        with self._lock:
            items = [
                {**v, "code": code}
                for code, v in self._latest.items()
                if v.get("ratio", 1.0) >= min_ratio
            ]
        return sorted(items, key=lambda x: x.get("ratio", 1.0), reverse=True)


_qmt_instance: Optional[QMTMinuteData] = None


def get_qmt_minute_data() -> QMTMinuteData:
    """获取QMT分钟数据实例"""
    global _qmt_instance
    if _qmt_instance is None:
        _qmt_instance = QMTMinuteData()
        _qmt_instance.connect()
    return _qmt_instance
