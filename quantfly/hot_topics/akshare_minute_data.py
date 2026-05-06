# -*- encoding: utf-8 -*-
"""
akshare分钟级ETF数据 — 备选方案
==================================
当QMT不可用时，使用akshare获取分钟级ETF数据

注意：akshare分钟数据只能在交易时间内获取，非交易时间返回空
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger("HotTopics.AkshareMinute")

# 行业ETF映射
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


class AkshareMinuteData:
    """
    akshare分钟级ETF数据获取器

    限制：
    - 只能在交易时间内获取
    - 分钟数据量较大，建议用5分钟或15分钟
    """

    def __init__(self, cache_ttl: int = 60):
        """
        Args:
            cache_ttl: 缓存有效期（秒），默认60秒
        """
        self._cache: dict = {}
        self._cache_time: dict = {}
        self._cache_ttl = cache_ttl

    def _is_cache_valid(self, key: str) -> bool:
        """检查缓存是否有效"""
        if key not in self._cache_time:
            return False
        return time.time() - self._cache_time[key] < self._cache_ttl

    def _set_cache(self, key: str, data):
        """设置缓存"""
        self._cache[key] = data
        self._cache_time[key] = time.time()

    def _get_cache(self, key: str):
        """获取缓存"""
        if self._is_cache_valid(key):
            return self._cache.get(key)
        return None

    def get_5min_bars(self, code: str, count: int = 100) -> list[dict]:
        """
        获取5分钟K线数据

        Args:
            code: ETF代码，如 "512760"
            count: 获取数量

        Returns:
            list of {time, open, high, low, close, volume, amount}
        """
        cache_key = f"{code}_5m_{count}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        import akshare as ak

        # 判断市场
        if code.startswith(("51", "15")):
            market = "XSHG"
        else:
            market = "XSHE"

        full_code = f"{code}.{market}"
        end_date = datetime.now().strftime("%Y%m%d %H:%M:%S")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")

        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period="5",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )

            if df is None or df.empty:
                logger.warning(f"[{code}] akshare 5分钟数据为空（非交易时间？）")
                return []

            # 标准化列名
            df.columns = [c.strip() for c in df.columns]
            rename = {
                "时间": "time",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }
            df.rename(columns=rename, inplace=True)

            # 取最近count条
            df = df.tail(count)
            result = df[["time", "open", "high", "low", "close", "volume", "amount"]].to_dict("records")

            self._set_cache(cache_key, result)
            logger.debug(f"[{code}] 获取 {len(result)} 条5分钟数据")
            return result

        except Exception as e:
            logger.warning(f"[{code}] akshare 5分钟数据获取失败: {e}")
            return []

    def get_today_minute_bars(self, code: str) -> list[dict]:
        """
        获取今日分钟数据（适合盘中监控）

        Returns:
            list of {time, volume, amount}
        """
        cache_key = f"{code}_today"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        import akshare as ak

        today = datetime.now().strftime("%Y%m%d")

        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period="5",
                start_date=today,
                end_date=today,
                adjust="",
            )

            if df is None or df.empty:
                return []

            df.columns = [c.strip() for c in df.columns]
            df.rename(columns={
                "时间": "time",
                "成交量": "volume",
                "成交额": "amount",
            }, inplace=True)

            result = df[["time", "volume", "amount"]].to_dict("records")
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.warning(f"[{code}] 今日分钟数据失败: {e}")
            return []

    def get_volume_ratio(self, code: str, n_history: int = 20) -> dict:
        """
        计算今日量比 vs 历史同时段均值

        Args:
            code: ETF代码
            n_history: 历史天数

        Returns:
            {current_vol, avg_vol, ratio, signal}
        """
        # 获取今日数据
        today_bars = self.get_today_minute_bars(code)
        if not today_bars:
            return {"current_vol": 0, "avg_vol": 0, "ratio": 1.0, "signal": "no_data_today"}

        # 获取历史5分钟数据
        hist_bars = self.get_5min_bars(code, count=n_history * 50)
        if not hist_bars:
            return {"current_vol": today_bars[-1]["volume"], "avg_vol": 0, "ratio": 1.0, "signal": "no_history"}

        # 当前最后一条
        current_vol = today_bars[-1]["volume"]

        # 从历史数据中提取同时段（hour:minute）
        if len(today_bars) >= 2:
            # 取最近2条的平均作为"当前"
            current_vol = sum(b["volume"] for b in today_bars[-2:]) / 2
            current_slot = today_bars[-1]["time"][-5:]  # 如 "10:30"
        else:
            current_vol = today_bars[-1]["volume"]
            current_slot = today_bars[-1]["time"][-5:]

        # 筛选同时段
        same_slot = [b for b in hist_bars if b.get("time", "")[-5:] == current_slot]
        if same_slot:
            avg_vol = sum(b["volume"] for b in same_slot) / len(same_slot)
        else:
            avg_vol = sum(b["volume"] for b in hist_bars) / len(hist_bars)

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
            "current_vol": int(current_vol),
            "avg_vol": int(avg_vol),
            "ratio": round(ratio, 2),
            "signal": signal,
        }

    def monitor_all_etfs(self) -> dict[str, dict]:
        """监控所有行业ETF"""
        results = {}
        for code in INDUSTRY_ETFS:
            ratio_data = self.get_volume_ratio(code)
            results[code] = {
                "name": INDUSTRY_ETFS[code],
                **ratio_data,
            }
        return results

    def is_trading_time(self) -> bool:
        """判断是否在交易时间内"""
        now = datetime.now()
        weekday = now.weekday()

        # 周末非交易
        if weekday >= 5:
            return False

        time_str = now.strftime("%H%M")
        # 9:15-11:30, 13:00-15:05
        is_am = 915 <= int(time_str) <= 1130
        is_pm = 1300 <= int(time_str) <= 1505
        return is_am or is_pm


_akshare_instance: Optional[AkshareMinuteData] = None


def get_akshare_minute_data() -> AkshareMinuteData:
    """获取akshare分钟数据实例"""
    global _akshare_instance
    if _akshare_instance is None:
        _akshare_instance = AkshareMinuteData()
    return _akshare_instance
