# -*- encoding: utf-8 -*-
"""市场情绪指数"""
import requests
import logging
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger("Sentiment.Index")

EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


class MarketSentiment:
    """市场情绪指数 — 综合多维度评分 0-100"""

    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: float = 0
        self._ttl: float = 60  # 缓存60秒

    def get_index(self, force: bool = False) -> dict:
        """获取综合情绪指数"""
        now = time.time()
        if not force and (now - self._cache_time) < self._ttl:
            return self._cache

        components = {}
        details = {}

        # 1. 涨跌比 (40%权重)
        up, down, flat = self._get_market_breadth()
        if up + down > 0:
            bread_ratio = up / (up + down + flat) * 100
            components["breadth"] = min(100, bread_ratio * 1.5)  # 涨跌比映射到0-100
            details["up_count"] = up
            details["down_count"] = down
            details["flat_count"] = flat
        else:
            components["breadth"] = 50

        # 2. 量比 (20%权重) — 沪深300成交额 vs 20日均量
        vol_ratio = self._get_volume_ratio()
        components["volume"] = min(100, max(0, vol_ratio * 40))
        details["volume_ratio"] = round(vol_ratio, 2)

        # 3. 北向资金 (20%权重)
        north_flow = self._get_north_flow()
        components["north"] = min(100, max(0, 50 + north_flow * 2))
        details["north_flow"] = north_flow

        # 4. 涨停数 (20%权重)
        limit_up = self._get_limit_up_count()
        components["limit_up"] = min(100, limit_up * 2)
        details["limit_up_count"] = limit_up

        # 综合评分
        weights = {"breadth": 0.4, "volume": 0.2, "north": 0.2, "limit_up": 0.2}
        score = sum(components[k] * weights[k] for k in weights)
        score = round(score, 1)

        # 情绪标签
        if score >= 70:
            label = "🔥 极度乐观"
        elif score >= 55:
            label = "😊 偏乐观"
        elif score >= 45:
            label = "😐 中性"
        elif score >= 30:
            label = "😟 偏悲观"
        else:
            label = "❄️ 极度悲观"

        result = {
            "score": score,
            "label": label,
            "components": components,
            "details": details,
            "timestamp": time.time(),
        }
        self._cache = result
        self._cache_time = now
        return result

    def get_history(self, days: int = 20) -> list:
        """获取历史情绪曲线（基于最近N天涨跌比和量比）"""
        try:
            import akshare as ak
            import pandas as pd
            df = ak.stock_zh_index_daily(symbol="sh000001")
            df = df.tail(days + 5)
            history = []
            for _, row in df.tail(days).iterrows():
                pct = float(row.get("pct_chg", 0))
                vol = float(row.get("volume", 0))
                vol_avg = float(df["volume"].tail(20).mean()) if "volume" in df.columns else vol
                vr = vol / max(vol_avg, 1)
                score = min(100, max(0, 50 + pct * 5 + (vr - 1) * 20))
                history.append({
                    "date": str(row["date"])[:10],
                    "score": round(score, 1),
                    "pct_chg": pct,
                })
            return history
        except Exception as e:
            logger.warning(f"历史情绪获取失败: {e}")
            return []

    def _get_market_breadth(self) -> tuple:
        """获取市场涨跌家数"""
        try:
            import akshare as ak
            df = ak.stock_zh_index_daily_tx()
            if df is not None and not df.empty:
                latest = df.iloc[-1] if len(df) > 0 else None
                if latest is not None:
                    return (int(latest.get("上涨家数", 0)), int(latest.get("下跌家数", 0)),
                            int(latest.get("平盘家数", 0)))
        except:
            pass

        # 回退：东方财富行情中心
        try:
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            r = requests.get(url, params={
                "fltt": "2", "secids": "1.000001",
                "fields": "f170,f171,f172"
            }, headers=EM_HEADERS, timeout=5)
            d = r.json().get("data", {}).get("diff", [{}])[0]
            return (d.get("f170", 0), d.get("f171", 0), d.get("f172", 0))
        except:
            return (0, 0, 0)

    def _get_volume_ratio(self) -> float:
        """沪深300 量比"""
        try:
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": "1.000300", "fields": "f43,f47,f48,f168,f170"},
                headers=EM_HEADERS, timeout=5
            )
            d = r.json().get("data", {})
            vol = d.get("f47", 0)  # 成交量
            vol5 = d.get("f168", vol)  # 5日均量
            return vol / max(vol5, 1) if vol5 else 1.0
        except:
            return 1.0

    def _get_north_flow(self) -> float:
        """北向资金净流入（亿元）"""
        try:
            import akshare as ak
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            return float(df["value"].iloc[-1]) if not df.empty else 0
        except:
            return 0

    def _get_limit_up_count(self) -> int:
        """涨停数"""
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            r = requests.get(url, params={
                "pn": "1", "pz": "500", "po": "0", "np": "1",
                "fltt": "2", "invt": "2",
                "fid": "f3", "fs": "m:0+t6,m:0+t80,m:1+t2,m:0+t13",
                "fields": "f2,f3,f12,f14",
            }, headers=EM_HEADERS, timeout=5)
            items = r.json().get("data", {}).get("diff", [])
            return sum(1 for it in items if float(it.get("f3", 0)) >= 9.9)
        except:
            return 0


# 单例
_sentiment = MarketSentiment()


def get_market_sentiment() -> dict:
    return _sentiment.get_index()
