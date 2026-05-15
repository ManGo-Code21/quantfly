# -*- encoding: utf-8 -*-
"""盘中异动监测"""
import requests
import logging
import time
from typing import Dict, List
from datetime import datetime
import numpy as np

logger = logging.getLogger("Monitor.Intraday")

EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


class IntradayMonitor:
    """盘中异动监测器"""

    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: float = 0
        self._ttl: float = 30  # 30秒缓存

    def get_summary(self) -> dict:
        """盘面概览"""
        now = time.time()
        if (now - self._cache_time) < self._ttl and "summary" in self._cache:
            return self._cache["summary"]

        result = {
            "timestamp": now,
            "market": self._get_index_summary(),
            "breadth": self._get_breadth(),
        }
        self._cache["summary"] = result
        self._cache_time = now
        return result

    def get_alerts(self) -> dict:
        """异动告警"""
        now = time.time()
        if (now - self._cache_time) < self._ttl and "alerts" in self._cache:
            return self._cache["alerts"]

        alerts = []
        alerts.extend(self._check_limit_alerts())   # 涨跌停
        alerts.extend(self._check_volume_alerts())  # 放量
        result = {"alerts": alerts, "count": len(alerts), "timestamp": now}
        self._cache["alerts"] = result
        self._cache_time = now
        return result

    def _get_index_summary(self) -> list:
        """主要指数行情"""
        indices = {
            "1.000001": "上证指数", "1.000300": "沪深300",
            "0.399001": "深证成指", "0.399006": "创业板指",
        }
        try:
            ids = ",".join(indices.keys())
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={"secids": ids, "fields": "f2,f3,f4,f5,f6,f15,f16,f17", "fltt": "2"},
                headers=EM_HEADERS, timeout=5
            )
            items = r.json().get("data", {}).get("diff", [])
            return [
                {
                    "name": indices.get(it.get("f12", ""), it.get("f14", "")),
                    "code": it.get("f12", ""),
                    "price": float(it.get("f2", 0)),
                    "pct_chg": float(it.get("f3", 0)),
                    "high": float(it.get("f15", 0)),
                    "low": float(it.get("f16", 0)),
                    "volume": float(it.get("f5", 0)) / 1e4,
                }
                for it in items
            ]
        except Exception as e:
            logger.warning(f"指数行情获取失败: {e}")
            return []

    def _get_breadth(self) -> dict:
        """涨跌家数"""
        try:
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={"secids": "1.000001", "fields": "f170,f171,f172", "fltt": "2"},
                headers=EM_HEADERS, timeout=5
            )
            d = r.json().get("data", {}).get("diff", [{}])[0]
            up, down, flat = d.get("f170", 0), d.get("f171", 0), d.get("f172", 0)
            return {
                "up": up, "down": down, "flat": flat,
                "total": up + down + flat,
                "up_ratio": round(up / max(up + down + flat, 1) * 100, 1),
            }
        except:
            return {"up": 0, "down": 0, "flat": 0, "total": 0, "up_ratio": 0}

    def _check_limit_alerts(self) -> list:
        """涨跌停检测"""
        try:
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1", "pz": "50", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f3",
                    "fs": "m:0+t6,m:0+t80,m:1+t2,m:0+t13",
                    "fields": "f2,f3,f8,f12,f14,f20",
                },
                headers=EM_HEADERS, timeout=5
            )
            items = r.json().get("data", {}).get("diff", [])
            alerts = []
            limit_up_count = 0
            for it in items:
                pct = float(it.get("f3", 0))
                if pct >= 9.9:
                    limit_up_count += 1
                    alerts.append({
                        "type": "limit_up",
                        "code": it.get("f12", ""),
                        "name": it.get("f14", ""),
                        "price": float(it.get("f2", 0)),
                        "pct_chg": pct,
                        "turnover": float(it.get("f8", 0)),
                        "market_cap": float(it.get("f20", 0)) / 1e8,
                    })
            return alerts
        except Exception as e:
            logger.warning(f"涨跌停检测失败: {e}")
            return []

    def _check_volume_alerts(self) -> list:
        """放量检测"""
        try:
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1", "pz": "20", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f10",
                    "fs": "m:0+t6,m:0+t80,m:1+t2,m:0+t13",
                    "fields": "f2,f3,f5,f8,f10,f12,f14,f20",
                },
                headers=EM_HEADERS, timeout=5
            )
            items = r.json().get("data", {}).get("diff", [])
            alerts = []
            for it in items:
                qrr = float(it.get("f10", 1))  # 量比
                if qrr >= 3.0:
                    alerts.append({
                        "type": "volume_surge",
                        "code": it.get("f12", ""),
                        "name": it.get("f14", ""),
                        "pct_chg": float(it.get("f3", 0)),
                        "volume_ratio": round(qrr, 1),
                        "volume": float(it.get("f5", 0)) / 1e4,
                        "turnover": float(it.get("f8", 0)),
                    })
            return alerts
        except Exception as e:
            logger.warning(f"放量检测失败: {e}")
            return []


# 单例
_monitor = IntradayMonitor()


def get_monitor() -> IntradayMonitor:
    return _monitor
