# -*- encoding: utf-8 -*-
"""
增强版选股器 — 全模块集成
============================================
完整流水线：
  1. 热点板块 → 板块动量过滤（RSRS）
  2. 风险过滤（ST/亏损/流动性）
  3. 技术面扫描（题材+筹码+分时）
  4. 基本面过滤（净利润同比>0 + 净利率）
  5. 资金流向过滤（主力净流入 + 北向）
  6. 宏观因子加分（美股联动 + VIX）
  7. 情绪信号（新闻Gate1×Gate2）

使用方式：
  from quantfly.screener.enhanced_picker import EnhancedScreener
  es = EnhancedScreener()
  results = es.screen("AI大模型", top_n=5)
"""
import numpy as np
import pandas as pd
import requests
import logging
from typing import Optional, Dict, List

from quantfly.screener.principle_filter import analyze_stock
from quantfly.screener.risk_filter import RiskFilter
from quantfly.screener.fundamental_filter import check_profit_growth
from quantfly.screener.capital_flow_filter import CapitalFlowFilter
from quantfly.screener.sector_momentum import SectorMomentum
from quantfly.screener.macro_factors import MacroSignals

logger = logging.getLogger("Screener")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_em_session = requests.Session()
_em_session.trust_env = False


class EnhancedScreener:
    """增强版选股器"""
    
    def __init__(self, enable_risk_filter: bool = True,
                 min_rsrs: float = 0,
                 min_main_netflow: float = 0,
                 enable_macro: bool = True,
                 enable_emotion: bool = True):
        self.risk_filter = RiskFilter() if enable_risk_filter else None
        self.capital_flow = CapitalFlowFilter(min_main_netflow=min_main_netflow)
        self.sector_momentum = SectorMomentum()
        self.macro = MacroSignals() if enable_macro else None
        self.enable_emotion = enable_emotion
        self.min_rsrs = min_rsrs
    
    def screen(self, industry: str, top_n: int = 10) -> List[Dict]:
        """完整选股流水线"""
        from quantfly.hot_topics.industry_mapper import get_eastmoney_sector_stocks
        
        # Step 0: 板块动量过滤
        rsrs = self.sector_momentum.get_sector_score(industry)
        logger.info(f"[{industry}] RSRS={rsrs:.1f}")
        if rsrs < self.min_rsrs:
            logger.warning(f"[{industry}] 板块动量过弱(RSRS={rsrs})，跳过")
            return []
        
        # Step 1: 获取成分股
        raw_stocks = get_eastmoney_sector_stocks(industry)
        if not raw_stocks:
            return []
        
        # Step 2: 风险过滤
        if self.risk_filter:
            safe_stocks = self.risk_filter.filter(raw_stocks)
            logger.info(f"[{industry}] 风险过滤: {len(safe_stocks)}/{len(raw_stocks)} 通过")
        else:
            safe_stocks = raw_stocks
        
        if not safe_stocks:
            return []
        
        # Step 3-6: 综合扫描
        results = []
        for code, name in safe_stocks[:30]:
            # 3. 技术面
            df = get_kline_em(code, count=100)
            if df.empty or len(df) < 25:
                continue
            
            analysis = analyze_stock(df)
            if not analysis:
                continue
            
            # 技术面评分太低则跳过（放宽到≥3分）
            if analysis.get("score", 0) < 3.0:
                continue
            
            # 4. 基本面
            if not check_profit_growth(code):
                logger.info(f"[基本面] {code} {name} 业绩不达标")
                continue
            
            # 5. 资金流向
            capital = self.capital_flow.check(code)
            if not capital.get("passed"):
                logger.info(f"[资金流向] {code} {name} 主力净流出")
                continue
            
            # 6. 宏观因子加分
            macro_bonus = 0
            us_boost = False
            if self.macro:
                us_boost = self.macro.is_a_share_boosted(name, threshold_pct=1.0)
                if us_boost:
                    macro_bonus = 2.0
                
                # VIX恐慌时降低所有评分
                vix = self.macro.get_signals().get("vix", {})
                if vix.get("level") == "fear":
                    macro_bonus -= 1.0
            
            # 计算综合评分
            signals = analysis.get("signals", {})
            tech_score = analysis.get("score", 0)
            capital_score = min(capital.get("net_main", 0) / 10000, 3.0)  # 最多3分
            buy_ratio_bonus = (capital.get("buy_ratio", 0.5) - 0.5) * 4  # 最多2分
            
            total_score = tech_score + capital_score + buy_ratio_bonus + macro_bonus
            
            result = {
                "code": code,
                "name": name,
                "industry": industry,
                "total_score": round(total_score, 1),
                "is_buyable": True,
                "close": analysis.get("close", 0),
                "chg_pct": analysis.get("chg_pct", 0),
                "vol_ratio": analysis.get("vol_ratio", 0),
                "rel_pos": analysis.get("rel_pos", 0),
                # 各维度得分
                "tech_score": round(tech_score, 1),
                "capital_score": round(capital_score, 1),
                "macro_bonus": round(macro_bonus, 1),
                "us_tech_boost": us_boost,
                # 资金数据
                "net_main": capital.get("net_main", 0),
                "buy_ratio": capital.get("buy_ratio", 0.5),
                "north_net_buy": capital.get("north_net_buy", 0),
                # 板块数据
                "sector_rsrs": round(rsrs, 1),
                # 信号列表
                "signals": [k for k, v in signals.items() if v],
            }
            results.append(result)
        
        results.sort(key=lambda x: x["total_score"], reverse=True)
        logger.info(f"[{industry}] 增强扫描完成: {len(results)}只候选")
        
        return results[:top_n]


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
        r = _em_session.get(EM_HIST_URL, params=params, headers=EM_HEADERS, timeout=10)
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


EM_HIST_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
