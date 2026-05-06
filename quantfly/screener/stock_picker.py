# -*- encoding: utf-8 -*-
"""
选股三原则 — 题材 + 筹码 + 分时强度
"""
import numpy as np
import pandas as pd
import requests
import logging
from typing import Optional

from quantfly.screener.principle_filter import analyze_stock
from quantfly.screener.risk_filter import RiskFilter

logger = logging.getLogger("Screener")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
EM_HIST_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def get_kline_em(code: str, count: int = 100) -> pd.DataFrame:
    """东方财富K线"""
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": count,
    }
    try:
        r = requests.get(EM_HIST_URL, params=params, headers=EM_HEADERS, timeout=10)
        klines = r.json().get("data", {}).get("klines", [])
        records = []
        for k in klines:
            p = k.split(",")
            records.append({
                "date": pd.to_datetime(p[0]),
                "open": float(p[1]),
                "high": float(p[2]),
                "low": float(p[3]),
                "close": float(p[4]),
                "volume": int(p[5]),
            })
        return pd.DataFrame(records).set_index("date").sort_index()
    except Exception as e:
        logger.warning(f"获取K线失败 {code}: {e}")
        return pd.DataFrame()


class TopicDrivenScreener:
    """
    题材驱动选股器

    完整流水线：
      1. 产业 → 板块 → 成分股（industry_mapper）
      2. 风险过滤（RiskFilter）— ST/亏损/流动性
      3. 选股三原则（题材+筹码+分时）

    选股三原则：
    1. 题材：涨幅3%~9.8%（有赚钱效应但未涨停）
    2. 筹码：位置0.1~0.7，上方空间>15%，未大幅上涨
    3. 分时：量比≥1.5，在均价线上，主动买入强度≥0.7
    """

    def __init__(self, enable_risk_filter: bool = True):
        self._industry_stocks_cache = {}
        self.risk_filter = RiskFilter() if enable_risk_filter else None

    def screen(self, industry: str, top_n: int = 10) -> list[dict]:
        """
        对产业相关板块执行完整选股流水线

        Args:
            industry: 产业名
            top_n: 返回最多top_n只

        Returns:
            [{code, name, total_score, is_buyable, signals, ...}]
        """
        from quantfly.hot_topics.industry_mapper import get_eastmoney_sector_stocks

        # Step 1: 获取成分股
        raw_stocks = get_eastmoney_sector_stocks(industry)
        if not raw_stocks:
            logger.warning(f"[{industry}] 未找到成分股")
            return []

        # Step 2: 风险过滤（ST/亏损/流动性）
        if self.risk_filter:
            safe_stocks = self.risk_filter.filter(raw_stocks)
            logger.info(f"[{industry}] 风险过滤: {len(safe_stocks)}/{len(raw_stocks)} 通过")
        else:
            safe_stocks = raw_stocks

        if not safe_stocks:
            return []

        logger.info(f"[{industry}] 开始扫描 {len(safe_stocks[:30])} 只成分股...")
        results = []
        for code, name in safe_stocks[:30]:  # 取前30只扫描
            df = get_kline_em(code, count=100)
            if df.empty or len(df) < 25:
                continue

            analysis = analyze_stock(df)
            if not analysis:
                continue

            result = {
                "code": code,
                "name": name,
                "industry": industry,
                "total_score": analysis.get("score", 0),
                "is_buyable": analysis.get("is_buyable", False),
                "is_sellable": analysis.get("is_sellable", False),
                "close": analysis.get("close", 0),
                "chg_pct": analysis.get("chg_pct", 0),
                "vol_ratio": analysis.get("vol_ratio", 0),
                "rel_pos": analysis.get("rel_pos", 0),
                "theme_score": 1.5 if analysis.get("signals", {}).get("题材_涨幅符合") else 0,
                "chips_score": sum([
                    3.0 if analysis.get("signals", {}).get("筹码_位置合适") else 0,
                    2.0 if analysis.get("signals", {}).get("筹码_上方有空间") else 0,
                    1.0 if analysis.get("signals", {}).get("筹码_未大幅上涨") else 0,
                ]),
                "momentum_score": sum([
                    1.5 if analysis.get("signals", {}).get("分时_量比充足") else 0,
                    1.0 if analysis.get("signals", {}).get("分时_在均价线上") else 0,
                    1.0 if analysis.get("signals", {}).get("分时_主动买入强") else 0,
                ]),
                "signals": [k for k, v in analysis.get("signals", {}).items() if v],
            }
            results.append(result)

        # 按综合评分排序
        results.sort(key=lambda x: x["total_score"], reverse=True)
        buyable = [r for r in results if r["is_buyable"]]
        logger.info(f"[{industry}] 扫描完成: {len(results)}只候选, {len(buyable)}只可买")

        return results[:top_n]
