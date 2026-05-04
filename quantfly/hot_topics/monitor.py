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
        items.extend(self._fetch_eastmoney_boards())
        items.extend(self._fetch_cls())
        return items

    def _fetch_eastmoney_boards(self) -> list[dict]:
        """东方财富概念板块涨幅榜"""
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": 1, "pz": 20,
                "po": 1, "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2,
                "fid": "f3",
                "fs": "m:90+t:3",
                "fields": "f12,f14,f3,f6,f8",
            }
            r = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
            data = r.json().get("data", {}).get("diff", [])
            result = []
            for item in data[:15]:
                name = item.get("f14", "")
                result.append({
                    "source": "eastmoney_board",
                    "title": name,
                    "code": str(item.get("f12", "")),
                    "change_pct": item.get("f3", 0),
                    "amount": item.get("f6", 0),
                    "topic": self._map_board_to_industry(name),
                    "timestamp": datetime.now().isoformat(),
                })
            logger.info(f"东方财富板块采集 {len(result)} 条")
            return result
        except Exception as e:
            logger.warning(f"东方财富板块采集失败: {e}")
            return []

    def _fetch_cls(self) -> list[dict]:
        """财联社电报（需要签名，暂时降级为空）"""
        return []

    def _map_board_to_industry(self, board_name: str) -> str:
        """板块名 → 产业名映射"""
        mapping = {
            "AI": "AI大模型", "人形机器人": "机器人", "机器人": "机器人",
            "半导体": "半导体", "芯片": "半导体",
            "稀土": "稀土永磁", "永磁": "稀土永磁",
            "商业航天": "商业航天", "低空经济": "商业航天",
            "量子": "量子计算",
            "固态电池": "新能源车", "新能源车": "新能源车",
            "脑机": "脑机接口",
        }
        for kw, industry in mapping.items():
            if kw in board_name:
                return industry
        return "其他"
