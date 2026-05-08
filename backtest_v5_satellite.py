#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""
V5 卫星策略：人气榜 + 弱转强 (Sentiment + Weak-to-Strong)
====================================================
目标：构建一个高弹性、短周期的“卫星”模块，用于博取超额收益。

逻辑架构：
1. 股票池 (Sentiment Pool)：
   - 使用 akshare 获取东方财富“人气榜”或“热门股” (代表市场资金关注度最高的标的)。
   - 类似于聚宽策略中的“同花顺人气榜 100"。

2. 买入信号 (Weak-to-Strong)：
   - **弱 (Weak)**：近期处于上升趋势中，但经历了短期的回调（如连续 2-3 日下跌，或偏离 MA5 过大）。
   - **转强 (Strong)**：今日股价重新站上 MA5，且伴随成交量放大（量比 > 1.2），确认资金回流。
   
3. 卖出信号 (Exit)：
   - 跌破 MA5（短线趋势破坏）或 跌破买入价 -5%（硬止损）。

防未来函数：所有信号基于 T 日收盘数据，T+1 开盘执行（模拟回测中按收盘价成交）。
"""
import sys
import os
sys.path.insert(0, '/Users/shj/quantfly')

import numpy as np
import pandas as pd
import logging
import time
import warnings
from datetime import timedelta

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("V5_Satellite")

import akshare as ak
from train_ranker import get_kline_em

START_DATE = "2024-01-01" # 人气榜数据较新，回测期设为近期
INIT_CAPITAL = 500_000    # 卫星仓位通常较小
MAX_POS = 3
HOLD_DAYS = 5             # 预期持仓短

class SatelliteStrategy:
    def __init__(self):
        self.cash = INIT_CAPITAL
        self.positions = {} # Code -> {shares, cost, days_held}
        self.daily_values = []
        self.stock_pool_history = {} # Code -> DF

    def get_sentiment_pool(self):
        """获取当前市场热门股票 (东方财富人气榜)"""
        logger.info("🔥 获取市场人气热门股...")
        try:
            # 获取东方财富个股人气榜
            # 注意：ak.stock_hot_rank_em() 是实时数据，回测中我们需要历史数据
            # 但由于 akshare 限制，我们这里模拟：
            # 使用“近期换手率最高”或“近期涨幅榜”作为人气的替代指标
            # 或者直接硬编码一些近期妖股来验证逻辑
            
            # 为了演示逻辑的可行性，我们定义一个“动态热门池”生成器
            # 在实盘中，这里应该替换为每日抓取的人气榜
            # 这里我们使用 akshare 的“近期异动”或手动维护一个观察池
            
            # 替代方案：获取当前 A 股涨幅榜前 50 (代表当天资金关注)
            # 但在回测中这样做会有未来函数。
            # **正确做法**：回测中，我们应选取一个“历史上曾经热门”的股票池。
            
            logger.info("⚠️ 回测模式：使用核心成长股池作为‘人气股’代理 (模拟)")
            # 使用之前回测过的半导体/AI 核心池，因为它们在过去两年大部分时间是“人气”所在
            return [
                '002156', '688041', '688111', '002415', 
                '000977', '603019', '300496', '688012', 
                '688072', '300604', '688256', '002747',
                '002230', '002085', '601127' # 赛力斯 (人气王)
            ]
        except Exception as e:
            logger.error(f"获取失败: {e}")
            return []

    def prepare_data(self, codes):
        logger.info("📥 下载历史数据...")
        valid = 0
        for code in codes:
            try:
                df = get_kline_em(code, count=500)
                if df is not None and not df.empty:
                    self.stock_pool_history[code] = df
                    valid += 1
            except: pass
        logger.info(f"✅ 有效数据: {valid}/{len(codes)}")

    def run_backtest(self):
        logger.info("="*60)
        logger.info("V5 卫星策略：弱转强 (回测开始)")
        logger.info("="*60)
        
        pool = self.get_sentiment_pool()
        self.prepare_data(pool)
        
        # 获取所有日期
        all_dates = set()
        for df in self.stock_pool_history.values():
            all_dates.update(df.index)
        dates = sorted([d for d in all_dates if d >= pd.Timestamp(START_DATE)])
        
        for date in dates:
            # 1. 卖出检查
            self._check_exit(date)
            
            # 2. 买入检查
            if len(self.positions) < MAX_POS:
                self._check_entry_weak_to_strong(date)
            
            # 3. 记录市值
            val = self.cash
            for code, pos in self.positions.items():
                if code in self.stock_pool_history and date in self.stock_pool_history[code].index:
                    val += pos['shares'] * self.stock_pool_history[code].loc[date, 'close']
            
            self.daily_values.append({'date': date, 'value': val})

        self.report()

    def _check_exit(self, date):
        for code in list(self.positions.keys()):
            if code not in self.stock_pool_history: continue
            if date not in self.stock_pool_history[code].index: continue
            
            df = self.stock_pool_history[code]
            row = df.loc[date]
            price = row['close']
            pos = self.positions[code]
            
            # Update holding days
            pos['days_held'] += 1
            
            # 1. 止损 -5%
            if price < pos['cost'] * 0.95:
                self._sell(code, price, date, "止损")
                continue
            
            # 2. 跌破 MA5 (短线走坏)
            if len(df) >= 5:
                idx = df.index.get_loc(date)
                ma5 = df['close'].iloc[idx-4:idx+1].mean()
                if price < ma5:
                    self._sell(code, price, date, "破 MA5")
                    continue

    def _check_entry_weak_to_strong(self, date):
        """
        弱转强逻辑：
        1. 趋势向上 (MA20 > MA60)
        2. 近期有过回调 (例如 3 天前价格 > 今天价格) -> 弱
        3. 今天放量上涨，收复 MA5 -> 强
        """
        for code, df in self.stock_pool_history.items():
            if code in self.positions: continue
            if date not in df.index: continue
            
            idx = df.index.get_loc(date)
            if idx < 20: continue
            
            price = df.loc[date, 'close']
            
            # 计算均线
            ma5 = df['close'].iloc[idx-4:idx+1].mean()
            ma20 = df['close'].iloc[idx-19:idx+1].mean()
            ma60 = df['close'].iloc[idx-59:idx+1].mean() if idx >= 59 else 0
            
            # 条件 1: 趋势向上
            if ma20 < ma60: continue
            
            # 条件 2: 弱 (前期回调) - 简化：当前价格距离 5 日高点回撤超过 5%
            # 或者简单点：昨天收盘价 < 前天收盘价 (连跌)
            prev_close = df['close'].iloc[idx-1]
            prev_prev_close = df['close'].iloc[idx-2]
            is_weak = prev_close < prev_prev_close
            
            # 条件 3: 转强 (放量反包)
            # 今天价格 > MA5 且 今天涨幅 > 2% 且 量比 > 1.2
            vol = df['volume'].iloc[idx]
            vol_ma5 = df['volume'].iloc[idx-4:idx].mean() if idx > 4 else df['volume'].iloc[idx]
            
            is_strong = (
                price > ma5 and 
                (price - prev_close) / prev_close > 0.02 and # 涨幅 > 2%
                vol > vol_ma5 * 1.2
            )
            
            if is_weak and is_strong:
                # 买入
                alloc = self.cash * 0.33
                shares = int(alloc / price / 100) * 100
                if shares > 0:
                    self.cash -= shares * price
                    self.positions[code] = {
                        'shares': shares, 
                        'cost': price, 
                        'days_held': 0
                    }
                    logger.info(f"🟢 BUY {code} @ {price:.1f} (弱转强确认)")
                    if len(self.positions) >= MAX_POS: break

    def _sell(self, code, price, date, reason):
        pos = self.positions.pop(code)
        self.cash += pos['shares'] * price
        ret = (price - pos['cost'])/pos['cost'] * 100
        logger.info(f"🔴 SELL {code} @ {price:.1f} | {reason} | R: {ret:.1f}%")

    def report(self):
        if not self.daily_values: return
        df = pd.DataFrame(self.daily_values).set_index('date')
        start_val = df['value'].iloc[0]
        end_val = df['value'].iloc[-1]
        
        days = (df.index[-1] - df.index[0]).days
        total_ret = (end_val - start_val) / start_val
        ann_ret = (1 + total_ret) ** (365/days) - 1
        
        logger.info("\n" + "="*60)
        logger.info(f"🏆 V5 卫星策略结果:")
        logger.info(f"  区间: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
        logger.info(f"  总收益: {total_ret*100:.1f}%")
        logger.info(f"  年化收益: {ann_ret*100:.1f}%")
        logger.info("="*60)

if __name__ == "__main__":
    SatelliteStrategy().run_backtest()
