# -*- encoding: utf-8 -*-
"""
板块动量轮动模块：RSRS阻力支撑相对强度
============================================
RSRS > 200：强势板块（5G/AI/芯片等）
RSRS < 0：弱势板块（消费/酒/银行等）

使用方式：
  from quantfly.screener.sector_momentum import SectorMomentum
  sm = SectorMomentum()
  score = sm.get_sector_score("半导体")
  is_strong = sm.is_strong_sector("AI大模型")
"""
import requests
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional, List

logger = logging.getLogger("Screener")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_em_sector_session = requests.Session()
_em_sector_session.trust_env = False


class SectorMomentum:
    """板块动量评估器（基于RSRS）"""
    
    # 行业动量缓存
    _sector_cache: Dict[str, Dict] = {}
    
    def __init__(self, lookback_days: int = 60):
        self.lookback = lookback_days
    
    def is_strong_sector(self, industry: str, threshold: float = 100) -> bool:
        """判断是否为强势板块"""
        score = self.get_sector_score(industry)
        return score > threshold
    
    def get_sector_score(self, industry: str) -> float:
        """
        获取板块RSRS评分
        RSRS > 200: 极强
        RSRS 100-200: 强势
        RSRS 0-100: 震荡
        RSRS < 0: 弱势
        """
        if industry in self._sector_cache:
            return self._sector_cache[industry].get("rsrs", 0)
        
        try:
            # 获取板块指数K线
            sector_index = self._get_sector_index_code(industry)
            if not sector_index:
                self._sector_cache[industry] = {"rsrs": 50}
                return 50
            
            kline = self._get_sector_kline(sector_index, self.lookback)
            if kline.empty or len(kline) < 20:
                self._sector_cache[industry] = {"rsrs": 50}
                return 50
            
            rsrs = self._calc_rsrs(kline)
            self._sector_cache[industry] = {"rsrs": rsrs}
            return rsrs
        except Exception as e:
            logger.warning(f"[板块动量] {industry} 计算失败: {e}")
            return 50
    
    def rank_sectors(self, industries: List[str]) -> List[Dict]:
        """对多个板块按RSRS排名"""
        results = []
        for ind in industries:
            rsrs = self.get_sector_score(ind)
            results.append({"industry": ind, "rsrs": rsrs, "is_strong": rsrs > 100})
        
        results.sort(key=lambda x: x["rsrs"], reverse=True)
        return results
    
    def _get_sector_index_code(self, industry: str) -> Optional[str]:
        """获取板块对应的指数代码"""
        # 简化的行业-指数映射表
        mapping = {
            "AI大模型": "BK1121",
            "人工智能": "BK1121",
            "半导体": "BK0917",
            "芯片": "BK1121",
            "储能": "BK1096",
            "商业航天": "BK1162",
            "机器人": "BK1106",
            "新能源汽车": "BK0900",
            "光伏": "BK1056",
            "消费": "BK0478",
            "白酒": "BK0504",
            "银行": "BK0475",
            "医药": "BK0505",
        }
        return mapping.get(industry)
    
    def _get_sector_kline(self, index_code: str, days: int) -> pd.DataFrame:
        """获取板块指数K线"""
        secid = f"90.{index_code}"
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": days,
        }
        try:
            r = _em_sector_session.get(url, params=params, headers=EM_HEADERS, timeout=10)
            klines = r.json().get("data", {}).get("klines", [])
            records = []
            for k in klines:
                p = k.split(",")
                records.append({
                    "date": pd.to_datetime(p[0]),
                    "close": float(p[4]),
                    "high": float(p[2]),
                    "low": float(p[3]),
                    "volume": int(p[5]) if len(p) > 5 else 0,
                })
            return pd.DataFrame(records).set_index("date").sort_index()
        except Exception as e:
            logger.debug(f"[板块动量] K线获取失败: {e}")
            return pd.DataFrame()
    
    def _calc_rsrs(self, df: pd.DataFrame) -> float:
        """
        计算RSRS指标（阻力支撑相对强度）
        基于最高价对最低价的线性回归斜率
        """
        if len(df) < 20:
            return 50
        
        highs = df["high"].values
        lows = df["low"].values
        
        # 线性回归: high = beta * low + alpha
        n = len(highs)
        sum_x = np.sum(lows)
        sum_y = np.sum(highs)
        sum_xy = np.sum(lows * highs)
        sum_x2 = np.sum(lows ** 2)
        
        denom = n * sum_x2 - sum_x ** 2
        if abs(denom) < 1e-10:
            return 50
        
        beta = (n * sum_xy - sum_x * sum_y) / denom
        
        # RSRS标准化：beta > 1 表示强势，< 1 表示弱势
        # 映射到 0-300 区间
        rsrs = (beta - 0.8) / 0.4 * 100
        return min(max(rsrs, -100), 300)


def get_sector_momentum_rank(industries: list) -> list:
    """便捷函数：获取板块动量排名"""
    sm = SectorMomentum()
    return sm.rank_sectors(industries)
