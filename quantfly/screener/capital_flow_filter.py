# -*- encoding: utf-8 -*-
"""
资金流向过滤模块：北向资金、主力资金、大单强度
============================================
数据源：东方财富资金流向接口

使用方式：
  from quantfly.screener.capital_flow_filter import CapitalFlowFilter
  cff = CapitalFlowFilter()
  result = cff.check("000001")  # {"net_main": xxx, "north_buy": xxx, ...}
"""
import requests
import logging
import time
from typing import Optional, Dict

logger = logging.getLogger("Screener")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}
_em_capital_session = requests.Session()
_em_capital_session.trust_env = False


class CapitalFlowFilter:
    """资金流向过滤器"""
    
    def __init__(self, min_main_netflow: float = 0, min_north_days: int = 3):
        """
        Args:
            min_main_netflow: 主力净流入最低阈值（万元），默认0表示净流入即可
            min_north_days: 北向资金连续净买入天数
        """
        self.min_main_netflow = min_main_netflow
        self.min_north_days = min_north_days
    
    def check(self, code: str) -> Optional[Dict]:
        """
        检查单只股票的资金流向
        
        Returns:
            {
                "passed": bool,
                "net_main": float,       # 主力净流入(万元)
                "net_super_large": float, # 超大单净流入
                "net_large": float,       # 大单净流入
                "buy_ratio": float,       # 主动买入占比
                "north_net_buy": float,   # 北向净流入(万元)
            }
        """
        try:
            # 1. 主力资金流向（今日）
            main_flow = self._get_main_flow(code)
            if main_flow is None:
                return {"passed": True, "net_main": 0, "buy_ratio": 0.5, "north_net_buy": 0}
            
            # 2. 北向资金（沪深港通）
            north_flow = self._get_north_flow(code)
            
            passed = main_flow.get("net_main", 0) > self.min_main_netflow
            
            return {
                "passed": passed,
                "net_main": main_flow.get("net_main", 0),
                "net_super_large": main_flow.get("net_super_large", 0),
                "net_large": main_flow.get("net_large", 0),
                "buy_ratio": main_flow.get("buy_ratio", 0.5),
                "north_net_buy": north_flow.get("net_buy", 0),
            }
        except Exception as e:
            logger.warning(f"[资金流向] {code} 检查失败: {e}")
            return {"passed": True, "net_main": 0, "buy_ratio": 0.5, "north_net_buy": 0}
    
    def _get_main_flow(self, code: str) -> Optional[Dict]:
        """获取主力资金流向"""
        try:
            secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
            # 东方财富资金流向接口
            url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
            params = {
                "secid": secid,
                "lmt": "1",
                "klt": "101",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": "b2884a393a59ad64002292a3e90d46a5",
            }
            r = _em_capital_session.get(url, params=params, headers=EM_HEADERS, timeout=5)
            data = r.json().get("data", {})
            klines = data.get("klines", [])
            if not klines:
                return None
            
            parts = klines[0].split(",")
            # f51=时间, f52=主力净流入, f53=小单, f54=中单, f55=大单, f56=超大单
            return {
                "net_main": float(parts[1]) if parts[1] != "-" else 0,
                "net_super_large": float(parts[5]) if parts[5] != "-" else 0,
                "net_large": float(parts[4]) if parts[4] != "-" else 0,
                "buy_ratio": float(parts[7]) / 100 if len(parts) > 7 and parts[7] != "-" else 0.5,
            }
        except Exception as e:
            logger.debug(f"[资金流向] 主力数据获取失败: {e}")
            return None
    
    def _get_north_flow(self, code: str) -> Dict:
        """获取北向资金持股变化（简化版）"""
        try:
            secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
            url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
            params = {
                "reportName": "RPT_MUTUAL_STOCK_NORTHSTA",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
                "filter": f'(MUTUAL_CODE="{secid}")',
                "pageNumber": "1",
                "pageSize": "5",
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
            }
            r = _em_capital_session.get(url, params=params, headers=EM_HEADERS, timeout=5)
            result = r.json().get("result", {})
            pages = result.get("pages", 0)
            if pages == 0:
                return {"net_buy": 0}
            
            data = result.get("data", [])
            if not data:
                return {"net_buy": 0}
            
            # 最近一期
            latest = data[0]
            hold_share = latest.get("HOLD_SHARES_QF", 0)
            market_value = latest.get("MARKET_CAP", 0)
            
            return {"net_buy": market_value if market_value else 0}
        except Exception as e:
            logger.debug(f"[资金流向] 北向数据获取失败: {e}")
            return {"net_buy": 0}


def check_capital_flow(code: str, min_main_netflow: float = 0) -> bool:
    """便捷函数：检查资金流向是否达标"""
    cff = CapitalFlowFilter(min_main_netflow=min_main_netflow)
    result = cff.check(code)
    return result.get("passed", True)
