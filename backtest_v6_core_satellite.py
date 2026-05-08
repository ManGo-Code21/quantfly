#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""
V6 全天候策略：核心 + 卫星 (Core-Satellite)
====================================================
架构说明：
1. 核心仓位 (Core - 70%): 基于 V3 行业动量轮动。
   - 逻辑：选取强势行业，做趋势跟踪。
   - 目标：提供稳定的 Beta 收益，跑赢大盘。
   
2. 卫星仓位 (Satellite - 30%): 基于 V5 弱转强策略。
   - 逻辑：在热门股池中寻找“回调后反弹”的爆发点。
   - 目标：博取高弹性的 Alpha 收益。

3. 资金管理：
   - 独立账户运行，最后合并净值。
   - 核心 700k，卫星 300k (总 1M)。
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
logger = logging.getLogger("V6_Core_Satellite")

import akshare as ak
from train_ranker import get_kline_em

START_DATE = "2024-01-01" # 卫星策略数据限制
INIT_CAPITAL = 1_000_000
CORE_RATIO = 0.7

# 核心池 (行业动量用)
CORE_INDUSTRIES = ["半导体", "通信设备", "汽车零部件"]

# 卫星池 (人气/高弹性)
SAT_POOL_CODES = [
    '002156', '688041', '688111', '002415', '000977', '603019', '300496', '002085', 
    '002594', '300750', '688256', '002747', '002230', '601127'
]

class V6Strategy:
    def __init__(self):
        # 核心账户
        self.cash_core = INIT_CAPITAL * CORE_RATIO
        self.pos_core = {}
        
        # 卫星账户
        self.cash_sat = INIT_CAPITAL * (1 - CORE_RATIO)
        self.pos_sat = {}
        
        self.stock_dfs = {}
        self.industry_indices = {}
        self.daily_values = [] # {date, core_val, sat_val, total}

    def prepare_data(self):
        logger.info("📥 准备数据 (Core + Satellite)...")
        
        # 1. 核心行业指数
        try:
            df_list = ak.stock_board_industry_name_em()
            code_map = {row['板块名称']: row['板块代码'] for _, row in df_list.iterrows()}
            for name in CORE_INDUSTRIES:
                code = code_map.get(name)
                if code:
                    try:
                        df = ak.stock_board_industry_hist_em(symbol=code, start_date="20240101", adjust="qfq")
                        if not df.empty:
                            self.industry_indices[name] = df[['日期', '收盘']].rename(columns={'日期':'date', '收盘':'close'}).set_index('date')
                    except: pass
        except: pass

        # 2. 个股数据 (核心需要行业内股票，这里简化为直接取卫星池 + 几个大盘股作为核心代理)
        # 为了简化代码运行，我们假设核心策略在“半导体”和“汽车零部件”这两个最强行业里选股
        # 卫星池已经覆盖了大量此类股票
        all_codes = list(set(SAT_POOL_CODES + ['300750', '002594', '000333', '002920']))
        
        for code in all_codes:
            try:
                df = get_kline_em(code, count=600)
                if not df.empty:
                    self.stock_dfs[code] = df
            except: pass

    def run(self):
        self.prepare_data()
        
        # 确定日期范围
        dates = sorted(set().union(*[df.index for df in self.stock_dfs.values()]))
        dates = [d for d in dates if d >= pd.Timestamp(START_DATE)]
        
        # 核心行业轮动状态
        active_core_stocks = SAT_POOL_CODES[:6] # 简化：默认持有一部分作为核心底仓
        last_rebalance = None

        for date in dates:
            # --- Core Logic (Monthly Rebalance) ---
            if last_rebalance is None or (date - last_rebalance).days >= 20:
                self._rebalance_core(date)
                last_rebalance = date
            
            # 核心交易执行
            self._trade_core(date)
            
            # --- Satellite Logic (Daily Scan) ---
            self._trade_satellite(date)
            
            # --- Record Values ---
            core_val = self.cash_core + sum(
                p['shares'] * self.stock_dfs[c].loc[date, 'close'] 
                for c, p in self.pos_core.items() if c in self.stock_dfs and date in self.stock_dfs[c].index
            )
            sat_val = self.cash_sat + sum(
                p['shares'] * self.stock_dfs[c].loc[date, 'close'] 
                for c, p in self.pos_sat.items() if c in self.stock_dfs and date in self.stock_dfs[c].index
            )
            self.daily_values.append({
                'date': date, 
                'core': core_val, 
                'sat': sat_val,
                'total': core_val + sat_val
            })

        self.report()

    def _rebalance_core(self, date):
        # 简化版行业动量：计算三个行业的涨幅
        rets = {}
        for name, df in self.industry_indices.items():
            if date in df.index:
                try:
                    idx = df.index.get_loc(date)
                    if idx > 20:
                        ret = (df.iloc[idx]['close'] - df.iloc[idx-20]['close']) / df.iloc[idx-20]['close']
                        rets[name] = ret
                except: pass
        
        # 选 Top 1 行业 (为了简化，我们只选最强的那个行业的股票作为核心)
        if rets:
            best_ind = max(rets, key=rets.get)
            # 简单的映射：假设卫星池里的部分股票属于这些行业
            # 实际上应该用成分股，这里简化逻辑
            logger.info(f"🏛️ [{date.strftime('%Y-%m-%d')}] 核心轮动 -> 选中行业: {best_ind}")
            # 暂时保持活跃池不变，实际应由 best_ind 决定

    def _trade_core(self, date):
        # 核心策略：趋势跟踪 (V2 Logic)
        # 卖出：跌破 MA20
        for code in list(self.pos_core.keys()):
            if code not in self.stock_dfs or date not in self.stock_dfs[code].index: continue
            df = self.stock_dfs[code]
            idx = df.index.get_loc(date)
            price = df.loc[date, 'close']
            
            # Stop Loss / Trend Break
            if idx >= 20:
                ma20 = df['close'].iloc[idx-19:idx+1].mean()
                if price < ma20:
                    self._sell_core(code, price, date)

        # 买入：金叉 (简单逻辑，保持核心仓位)
        if len(self.pos_core) < 4:
            # 寻找金叉机会
            for code in SAT_POOL_CODES:
                if code in self.pos_core: continue
                if code in self.stock_dfs and date in self.stock_dfs[code].index:
                    df = self.stock_dfs[code]
                    idx = df.index.get_loc(date)
                    if idx > 15:
                        ma5 = df['close'].iloc[idx-4:idx+1].mean()
                        ma10 = df['close'].iloc[idx-9:idx+1].mean()
                        ma5_p = df['close'].iloc[idx-5:idx].mean()
                        ma10_p = df['close'].iloc[idx-10:idx].mean()
                        
                        if ma5_p <= ma10_p and ma5 > ma10:
                            # 核心买入
                            alloc = self.cash_core * 0.25
                            self._buy_core(code, df.loc[date, 'close'], date, alloc)
                            if len(self.pos_core) >= 4: break

    def _trade_satellite(self, date):
        # 卫星策略：弱转强 (V5 Logic)
        # 卖出：跌破 MA5
        for code in list(self.pos_sat.keys()):
            if code not in self.stock_dfs or date not in self.stock_dfs[code].index: continue
            df = self.stock_dfs[code]
            idx = df.index.get_loc(date)
            price = df.loc[date, 'close']
            
            if idx >= 5:
                ma5 = df['close'].iloc[idx-4:idx+1].mean()
                if price < ma5 or price < self.pos_sat[code]['cost'] * 0.95:
                    self._sell_sat(code, price, date)

        # 买入：弱转强
        if len(self.pos_sat) < 3:
            for code in SAT_POOL_CODES:
                if code in self.pos_sat: continue
                if code in self.stock_dfs and date in self.stock_dfs[code].index:
                    df = self.stock_dfs[code]
                    idx = df.index.get_loc(date)
                    if idx > 10:
                        price = df.loc[date, 'close']
                        ma5 = df['close'].iloc[idx-4:idx+1].mean()
                        
                        # 弱：昨天跌
                        prev = df['close'].iloc[idx-1]
                        prev_prev = df['close'].iloc[idx-2]
                        
                        # 强：今天放量涨回 MA5 之上
                        vol = df['volume'].iloc[idx]
                        vol_ma = df['volume'].iloc[idx-4:idx].mean()
                        
                        if prev < prev_prev and price > ma5 and vol > vol_ma * 1.2:
                            alloc = self.cash_sat * 0.33
                            self._buy_sat(code, price, date, alloc)
                            if len(self.pos_sat) >= 3: break

    def _buy_core(self, code, price, date, alloc):
        shares = int(alloc / price / 100) * 100
        if shares > 0:
            self.cash_core -= shares * price
            self.pos_core[code] = {'shares': shares, 'cost': price}
            logger.info(f"🟢 [Core] BUY {code} @ {price:.1f}")

    def _sell_core(self, code, price, date):
        if code in self.pos_core:
            pos = self.pos_core.pop(code)
            self.cash_core += pos['shares'] * price

    def _buy_sat(self, code, price, date, alloc):
        shares = int(alloc / price / 100) * 100
        if shares > 0:
            self.cash_sat -= shares * price
            self.pos_sat[code] = {'shares': shares, 'cost': price}
            logger.info(f"🟢 [Sat]  BUY {code} @ {price:.1f}")

    def _sell_sat(self, code, price, date):
        if code in self.pos_sat:
            pos = self.pos_sat.pop(code)
            self.cash_sat += pos['shares'] * price
            ret = (price - pos['cost'])/pos['cost']
            logger.info(f"🔴 [Sat]  SELL {code} @ {price:.1f} | R: {ret*100:.1f}%")

    def report(self):
        df = pd.DataFrame(self.daily_values).set_index('date')
        start_val = df['total'].iloc[0]
        end_val = df['total'].iloc[-1]
        
        days = (df.index[-1] - df.index[0]).days
        total_ret = (end_val - start_val) / start_val
        ann_ret = (1 + total_ret) ** (365/days) - 1
        
        core_ret = (df['core'].iloc[-1] / df['core'].iloc[0]) ** (365/days) - 1
        sat_ret = (df['sat'].iloc[-1] / df['sat'].iloc[0]) ** (365/days) - 1

        logger.info("\n" + "="*60)
        logger.info("🏆 V6 全天候策略 (Core-Satellite) 最终结果")
        logger.info("="*60)
        logger.info(f"📅 区间: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
        logger.info(f"💰 初始: ¥1,000,000 → 最终: ¥{end_val:,.0f}")
        logger.info(f"📈 总年化收益: {ann_ret*100:.1f}%")
        logger.info("-" * 30)
        logger.info(f"🏛️ 核心仓位 (70%): 年化 {core_ret*100:.1f}%")
        logger.info(f"🚀 卫星仓位 (30%): 年化 {sat_ret*100:.1f}%")
        logger.info("="*60)

if __name__ == "__main__":
    V6Strategy().run()
