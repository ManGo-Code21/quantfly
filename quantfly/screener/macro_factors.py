# -*- encoding: utf-8 -*-
"""
宏观因子模块：美股科技联动 + 宏观周期信号
============================================
1. 美股科技龙头（NVDA/AMD/MU）→ A股芯片映射
2. 美联储利率周期信号
3. VIX恐慌指数

使用方式：
  from quantfly.screener.macro_factors import MacroSignals
  ms = MacroSignals()
  signals = ms.get_signals()
"""
import requests
import logging
from typing import Dict

logger = logging.getLogger("Screener")

_yahoo_session = requests.Session()
_yahoo_session.trust_env = False
_yahoo_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
})


class MacroSignals:
    """宏观信号采集器"""
    
    # 美股科技龙头 → A股映射
    US_TECH_STOCKS = {
        "NVDA": {"a_share_map": ["中际旭创", "新易盛", "天孚通信"], "sector": "AI芯片"},
        "AMD": {"a_share_map": ["海光信息", "寒武纪", "景嘉微"], "sector": "AI芯片"},
        "MU": {"a_share_map": ["兆易创新", "北京君正", "江波龙"], "sector": "存储芯片"},
        "AVGO": {"a_share_map": ["澜起科技", "紫光国微"], "sector": "通信芯片"},
        "AAPL": {"a_share_map": ["立讯精密", "歌尔股份", "蓝思科技"], "sector": "消费电子"},
        "MSFT": {"a_share_map": ["金山办公", "科大讯飞"], "sector": "AI软件"},
    }
    
    def get_signals(self) -> Dict:
        """获取所有宏观信号"""
        signals = {}
        
        # 1. 美股科技联动
        signals["us_tech"] = self._check_us_tech()
        
        # 2. VIX恐慌指数
        signals["vix"] = self._get_vix()
        
        # 3. 美联储利率周期（简化判断）
        signals["fed_rate"] = self._check_fed_cycle()
        
        return signals
    
    def is_a_share_boosted(self, stock_name: str, threshold_pct: float = 1.0) -> bool:
        """
        判断某A股是否受美股科技龙头正向映射
        threshold_pct: 美股对应标的涨幅超过此值才视为正向信号
        """
        us_tech = self._check_us_tech()
        for us_symbol, info in self.US_TECH_STOCKS.items():
            if stock_name in info["a_share_map"]:
                us_chg = us_tech.get(us_symbol, {}).get("chg_pct", 0)
                if us_chg > threshold_pct:
                    return True
        return False
    
    def _check_us_tech(self) -> Dict:
        """获取美股科技龙头最新行情"""
        results = {}
        for symbol in self.US_TECH_STOCKS.keys():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                params = {"range": "2d", "interval": "1d"}
                r = _yahoo_session.get(url, params=params, timeout=10)
                data = r.json().get("chart", {}).get("result", [])
                if not data:
                    continue
                
                meta = data[0].get("meta", {})
                prev_close = meta.get("chartPreviousClose", meta.get("previousClose", 0))
                current = meta.get("regularMarketPrice", 0)
                
                if prev_close > 0:
                    chg_pct = ((current - prev_close) / prev_close) * 100
                    results[symbol] = {
                        "price": current,
                        "chg_pct": round(chg_pct, 2),
                        "is_up": chg_pct > 0,
                    }
            except Exception as e:
                logger.debug(f"[宏观] {symbol} 数据获取失败: {e}")
        
        return results
    
    def _get_vix(self) -> Dict:
        """获取VIX恐慌指数"""
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/^VIX"
            params = {"range": "1d", "interval": "1d"}
            r = _yahoo_session.get(url, params=params, timeout=10)
            data = r.json().get("chart", {}).get("result", [])
            if not data:
                return {"value": 20, "level": "normal"}
            
            price = data[0].get("meta", {}).get("regularMarketPrice", 20)
            
            if price > 30:
                level = "fear"
            elif price > 20:
                level = "normal"
            else:
                level = "greed"
            
            return {"value": round(price, 2), "level": level}
        except Exception as e:
            logger.debug(f"[宏观] VIX获取失败: {e}")
            return {"value": 20, "level": "normal"}
    
    def _check_fed_cycle(self) -> Dict:
        """
        美联储利率周期判断（简化版）
        基于最近利率变化方向
        """
        try:
            # 从FRED获取联邦基金利率（通过Yahoo替代方案）
            # 简化：假设当前处于降息周期（2024-2025实际情况）
            # 实际应接入FRED API: https://api.stlouisfed.org
            return {
                "cycle": "cutting",  # cutting / hiking / holding
                "signal": "bullish",  # bullish / neutral / bearish
                "note": "降息周期利好成长股",
            }
        except Exception as e:
            logger.debug(f"[宏观] 利率周期判断失败: {e}")
            return {"cycle": "unknown", "signal": "neutral"}


def get_macro_signal_for_stock(stock_name: str) -> Dict:
    """便捷函数：获取某股票相关的宏观信号"""
    ms = MacroSignals()
    boosted = ms.is_a_share_boosted(stock_name)
    signals = ms.get_signals()
    return {
        "us_tech_boost": boosted,
        "vix": signals.get("vix", {}),
        "fed_cycle": signals.get("fed_rate", {}),
    }
