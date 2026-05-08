# -*- encoding: utf-8 -*-
"""
基本面过滤模块：净利润同比/环比增长、净利率提升
"""
import logging
import re
import pandas as pd
from typing import Optional

logger = logging.getLogger("Screener")

def parse_pct(val):
    """解析百分比字符串，如 '719.47%' -> 719.47"""
    if not isinstance(val, str):
        try:
            return float(val)
        except:
            return None
    val = val.strip().replace('%', '')
    if val == 'False' or val == '':
        return None
    try:
        return float(val)
    except:
        return None

def parse_value(val):
    """解析带万/亿的字符串为数值"""
    if not isinstance(val, str):
        try:
            return float(val)
        except:
            return None
    val = val.strip()
    try:
        num = float(re.sub(r'[^0-9\-.]', '', val))
        if '亿' in val:
            return num * 1e8
        elif '万' in val:
            return num * 1e4
        return num
    except:
        return None

def check_profit_growth(code: str) -> bool:
    """
    检查股票基本面：
    1. 归母净利润同比增长 > 0
    2. 净利润未出现大幅下滑（基于同花顺财务摘要）
    """
    try:
        import akshare as ak
        import requests
        
        # 设置超时
        old_timeout = requests.Session.request
        def new_request(self, *args, **kwargs):
            kwargs.setdefault('timeout', 5)
            return old_timeout(self, *args, **kwargs)
        requests.Session.request = new_request
        
        df = ak.stock_financial_abstract_ths(symbol=code)
        requests.Session.request = old_timeout
        
        if df is None or df.empty:
            return True # 无数据默认放行

        # 取最新一期数据
        latest = df.tail(1)
        if latest.empty:
            return True

        # 寻找 净利润同比增长率 列
        yoy_col = None
        for c in df.columns:
            if '净利润同比增长' in c or '净利润同比' == c:
                yoy_col = c
                break
        
        if yoy_col:
            val = latest.iloc[0][yoy_col]
            yoy = parse_pct(str(val))
            if yoy is not None:
                # 只要同比是正的就算通过
                if yoy <= 0:
                    logger.info(f"[基本面] {code} 过滤: 净利润同比 {yoy}%")
                    return False
            else:
                logger.debug(f"[基本面] {code} 同比数据无效 ({val})")

        # 如果有营收列，检查净利率趋势
        rev_col = None
        for c in df.columns:
            if '营业收入' in c and '同比' not in c:
                rev_col = c
                break
        
        if rev_col:
            net_profit_col = next((c for c in df.columns if '净利润' in c and '同比' not in c), None)
            if net_profit_col:
                # 取最新两期
                recent = df.tail(2)
                profits = [parse_value(x) for x in recent[net_profit_col].values]
                revenues = [parse_value(x) for x in recent[rev_col].values]
                
                # 过滤 None
                valid_p = [p for p in profits if p is not None]
                valid_r = [r for r in revenues if r is not None]
                
                if len(valid_p) >= 2 and len(valid_r) >= 2:
                    # 检查净利率 (Profit / Revenue)
                    # 最近一期 vs 上一期
                    # 注意：tail(2) 可能是 Q1 和 年报，直接比可能不准确，但作为粗略过滤可以
                    # 更严谨的做法是对比去年同期，但需要日期解析
                    # 这里简化：如果营收增加且净利润增加，则 OK
                    
                    cur_p, prev_p = valid_p[-1], valid_p[-2]
                    cur_r, prev_r = valid_r[-1], valid_r[-2]
                    
                    if cur_r > 0 and prev_r > 0:
                        margin_diff = (cur_p/cur_r) - (prev_p/prev_r)
                        if margin_diff < -0.02: # 利润率下降超过 2个百分点
                             logger.info(f"[基本面] {code} 过滤: 净利率恶化")
                             return False
                else:
                    logger.debug(f"[基本面] {code} 数据不足")

        return True
        
    except Exception as e:
        logger.warning(f"[基本面] {code} 检查失败: {e}")
        return True # 出错放行
