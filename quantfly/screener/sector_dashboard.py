# -*- encoding: utf-8 -*-
"""板块仪表盘 — 37行业热力图数据"""
import requests
import logging
from typing import Dict, List, Optional
import time

logger = logging.getLogger("Screener.Dashboard")

EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


class SectorDashboard:
    """板块仪表盘"""

    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: float = 0
        self._ttl: float = 60

    def get_heatmap(self, force: bool = False) -> dict:
        """获取板块热力图数据"""
        now = time.time()
        if not force and (now - self._cache_time) < self._ttl:
            return self._cache

        sectors = self._fetch_sector_data()

        # 分类统计
        up_count = sum(1 for s in sectors if s["pct_chg"] > 0)
        down_count = sum(1 for s in sectors if s["pct_chg"] < 0)
        strong = sum(1 for s in sectors if s.get("rsrs", 0) > 100)

        result = {
            "sectors": sorted(sectors, key=lambda x: x["pct_chg"], reverse=True),
            "summary": {
                "total": len(sectors),
                "up": up_count,
                "down": down_count,
                "strong": strong,
                "avg_pct": round(sum(s["pct_chg"] for s in sectors) / max(len(sectors), 1), 2),
                "breadth": round(up_count / max(len(sectors), 1) * 100, 1),
            },
            "timestamp": now,
        }
        self._cache = result
        self._cache_time = now
        return result

    def _fetch_sector_data(self) -> list:
        """获取全板块行情+动量数据"""
        # 东方财富板块行情
        try:
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1", "pz": "200", "po": "0", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f3",
                    "fs": "m:90+t2",
                    "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f62,f184,f69",
                },
                headers=EM_HEADERS, timeout=10
            )
            items = r.json().get("data", {}).get("diff", [])

            # RSRS 动量
            from quantfly.screener.sector_momentum import SectorMomentum
            sm = SectorMomentum()

            result = []
            for it in items:
                name = it.get("f14", "")
                result.append({
                    "code": it.get("f12", ""),
                    "name": name,
                    "price": float(it.get("f2", 0)),
                    "pct_chg": float(it.get("f3", 0)),
                    "volume": int(float(it.get("f5", 0)) / 1e4),
                    "amount": float(it.get("f6", 0)) / 1e8,
                    "turnover": float(it.get("f8", 0)),
                    "qrr": float(it.get("f10", 1)),  # 量比
                    "high": float(it.get("f15", 0)),
                    "low": float(it.get("f16", 0)),
                    "main_net_inflow": float(it.get("f62", 0)),
                    "rsrs": sm.get_sector_score(name) if name else 50,
                })
            return result
        except Exception as e:
            logger.warning(f"板块数据获取失败: {e}")
            return []


# 单例
_dashboard = SectorDashboard()


def get_sector_dashboard() -> SectorDashboard:
    return _dashboard
