# -*- encoding: utf-8 -*-
"""
热点新闻监控 — 从多个数据源采集热点
"""
import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("HotTopics.Monitor")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.eastmoney.com/",
}


class HotTopicMonitor:
    """热点新闻采集器"""

    def fetch_all(self) -> list[dict]:
        """从所有数据源采集热点新闻"""
        items = []
        items.extend(self._fetch_eastmoney())
        items.extend(self._fetch_cls())
        return items

    def _fetch_eastmoney(self) -> list[dict]:
        """东方财富资金流"""
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": 1, "pz": 20,
                "po": 1, "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2,
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f12,f14,f3,f6,f8",
                "filter": "f3>0",
            }
            r = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
            data = r.json().get("data", {}).get("diff", [])
            result = []
            for item in data:
                result.append({
                    "source": "eastmoney",
                    "title": item.get("f14", ""),
                    "code": str(item.get("f12", "")),
                    "change_pct": item.get("f3", 0),
                    "amount": item.get("f6", 0),
                    "topic": "板块",
                    "timestamp": datetime.now().isoformat(),
                })
            return result
        except Exception as e:
            logger.warning(f"东方财富采集失败: {e}")
            return []

    def _fetch_cls(self) -> list[dict]:
        """财联社电报"""
        try:
            url = "https://www.cls.cn/api/sw?app=CLS&os=web&sv=7.7.5"
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cls.cn/",
            }
            r = requests.get(url, headers=headers, timeout=8)
            # 财联社接口可能有变化，简化为空列表
            return []
        except Exception as e:
            logger.warning(f"财联社采集失败: {e}")
            return []
