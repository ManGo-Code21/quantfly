#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""
V4 强势板块择时 + 动态风控 (Dynamic Risk Control)
====================================================
修复：
1. 移除代理干扰 (强制直连东方财富)。
2. 补充缺失的 START_DATE 定义。
"""
import os
import sys

# 1. 修复网络代理问题
for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    if var in os.environ:
        del os.environ[var]

import numpy as np
import pandas as pd
import logging
import time
import warnings
from datetime import timedelta

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("V4_Risk_Control")

# 定义全局变量
START_DATE = "2023-01-01"
INIT_CAPITAL = 1_000_000

# 手动引入必要的函数
sys.path.insert(0, '/Users/shj/quantfly')
from train_ranker import get_kline_em

class RiskModule:
    @staticmethod
    def calculate_risk(df: pd.DataFrame, current_idx: int) -> float:
        if current_idx < 25: return 0.0
        idx = current_idx - 1
        if idx < 20: return 0.0

        close = df['close'].iloc[:idx+1] 
        
        # 1. Bias Risk
        ma20 = close.iloc[-20:].mean()
        bias = (close.iloc[-1] - ma20) / ma20
        bias_risk = np.clip(bias * 5, 0, 1) 

        # 2. RSI Risk
        gain = close.diff().clip(lower=0)
        loss = -close.diff().clip(upper=0)
        avg_gain = gain.iloc[-14:].mean()
        avg_loss = loss.iloc[-14:].mean()
        rs = avg_gain / avg_loss if avg_loss != 0 else 100
        rsi = 100 - (100 / (1 + rs))
        rsi_risk = np.clip((rsi - 50) / 30, 0, 1)

        # 3. Vol Risk
        returns = close.pct_change().iloc[-5:]
        vol = returns.std() * np.sqrt(252) 
        vol_risk = np.clip(vol, 0, 1)

        return min(1.0, max(0.0, 0.4 * bias_risk + 0.3 * rsi_risk + 0.3 * vol_risk))

class V4Backtest:
    def __init__(self):
        self.cash = INIT_CAPITAL
        self.positions = {}
        self.daily_values = []
        self.stock_dfs = {}
        self.universe = [
            '002156', '688041', '688111', '002415', '000977', '603019', '300496', '002085', 
            '002594', '300750', '000333', '002920'
        ]

    def prepare_pool(self):
        logger.info("📥 下载历史 K 线 (强制直连模式)...")
        valid_count = 0
        for code in self.universe:
            try:
                df = get_kline_em(code, count=1200)
                if df is not None and not df.empty:
                    valid_len = len(df[df.index >= pd.Timestamp(START_DATE)])
                    if valid_len > 100:
                        self.stock_dfs[code] = df
                        valid_count += 1
                        logger.info(f"  ✅ {code} loaded ({valid_len} days)")
                    else:
                        logger.warning(f"  ⏭️ {code} skipped (too short: {valid_len})")
                else:
                    logger.warning(f"  ⏭️ {code} empty")
            except Exception as e:
                logger.error(f"  ❌ {code} error: {e}")
        logger.info(f"✅ 最终有效股票: {valid_count} 只")
        return valid_count > 0

    def run_comparison(self):
        if not self.prepare_pool():
            logger.error("无法获取数据，回测终止。")
            return

        # 1. V4 (Risk)
        logger.info("\n🚀 运行 V4 风控策略...")
        self._run_timing_with_risk()
        v4_final = self.daily_values[-1]['value'] if self.daily_values else INIT_CAPITAL
        
        # 2. V2 (Baseline)
        logger.info("\n🔄 运行 V2 原版 (无风控)...")
        self.cash = INIT_CAPITAL
        self.positions = {}
        self.daily_values = []
        self._run_timing()
        v2_final = self.daily_values[-1]['value']
        
        # 3. Result
        days = (pd.Timestamp("2026-05-08") - pd.Timestamp(START_DATE)).days
        v4_ann = ((v4_final / INIT_CAPITAL) ** (365/days) - 1) * 100
        v2_ann = ((v2_final / INIT_CAPITAL) ** (365/days) - 1) * 100
        
        logger.info("\n" + "="*60)
        logger.info("🏆 V4 风控策略验证结果")
        logger.info("="*60)
        logger.info(f"V2 原版 (无风控): 年化 {v2_ann:.1f}% | 终值 ¥{v2_final:,.0f}")
        logger.info(f"V4 风控版 (动态): 年化 {v4_ann:.1f}% | 终值 ¥{v4_final:,.0f}")
        
        diff = v4_ann - v2_ann
        if diff > 0: logger.info(f"✅ 风控跑赢 +{diff:.1f}%")
        else: logger.info(f"⚠️ 风控跑输 {diff:.1f}%")
        
        logger.info("\n📊 风控执行统计:")
        logger.info(f"  跳过高风险: {self.stats.get('risk_skip', 0)} 次")
        logger.info(f"  减仓买入: {self.stats.get('risk_half', 0)} 次")
        logger.info(f"  正常买入: {self.stats.get('risk_full', 0)} 次")

    def _run_timing_with_risk(self):
        self.stats = {'risk_skip': 0, 'risk_half': 0, 'risk_full': 0}
        all_dates = sorted(set().union(*[df.index for df in self.stock_dfs.values()]))
        dates = [d for d in all_dates if d >= pd.Timestamp(START_DATE)]
        
        for date in dates:
            # Sell
            for code in list(self.positions.keys()):
                if code in self.stock_dfs and date in self.stock_dfs[code].index:
                    self._check_exit(code, date)
            
            # Buy
            if len(self.positions) < 5:
                for code in self.stock_dfs:
                    if code in self.positions: continue
                    if date in self.stock_dfs[code].index:
                        self._check_entry_with_risk(code, date)
                        if len(self.positions) >= 5: break
            
            val = self.cash
            for c, pos in self.positions.items():
                if c in self.stock_dfs and date in self.stock_dfs[c].index:
                    val += pos['shares'] * self.stock_dfs[c].loc[date, 'close']
            self.daily_values.append({'date': date, 'value': val})

    def _run_timing(self):
        all_dates = sorted(set().union(*[df.index for df in self.stock_dfs.values()]))
        dates = [d for d in all_dates if d >= pd.Timestamp(START_DATE)]
        
        for date in dates:
            for code in list(self.positions.keys()):
                if code in self.stock_dfs and date in self.stock_dfs[code].index:
                    self._check_exit(code, date)
            
            if len(self.positions) < 5:
                for code in self.stock_dfs:
                    if code in self.positions: continue
                    if date in self.stock_dfs[code].index:
                        if self._check_entry(code, date):
                            if len(self.positions) >= 5: break
            
            val = self.cash
            for c, pos in self.positions.items():
                if c in self.stock_dfs and date in self.stock_dfs[c].index:
                    val += pos['shares'] * self.stock_dfs[c].loc[date, 'close']
            self.daily_values.append({'date': date, 'value': val})

    def _check_exit(self, code, date):
        df = self.stock_dfs[code]
        idx = df.index.get_loc(date)
        price = df.loc[date, 'close']
        pos = self.positions[code]
        
        if price < pos['cost'] * 0.92:
            self._sell(code, price); return

        if idx >= 20:
            ma20 = df['close'].iloc[idx-19:idx+1].mean()
            if price < ma20:
                self._sell(code, price); return

    def _check_entry(self, code, date):
        df = self.stock_dfs[code]
        idx = df.index.get_loc(date)
        if idx < 15: return False
        ma5 = df['close'].iloc[idx-4:idx+1].mean()
        ma10 = df['close'].iloc[idx-9:idx+1].mean()
        ma5_p = df['close'].iloc[idx-5:idx].mean()
        ma10_p = df['close'].iloc[idx-10:idx].mean()
        
        if ma5_p <= ma10_p and ma5 > ma10 and df.loc[date, 'close'] > 10:
            alloc = self.cash * 0.20
            shares = int(alloc / df.loc[date, 'close'] / 100) * 100
            if shares > 0:
                self.cash -= shares * df.loc[date, 'close']
                self.positions[code] = {'shares': shares, 'cost': df.loc[date, 'close']}
                return True
        return False

    def _check_entry_with_risk(self, code, date):
        df = self.stock_dfs[code]
        idx = df.index.get_loc(date)
        if idx < 15: return False
        
        ma5 = df['close'].iloc[idx-4:idx+1].mean()
        ma10 = df['close'].iloc[idx-9:idx+1].mean()
        ma5_p = df['close'].iloc[idx-5:idx].mean()
        ma10_p = df['close'].iloc[idx-10:idx].mean()
        
        if ma5_p <= ma10_p and ma5 > ma10 and df.loc[date, 'close'] > 10:
            price = df.loc[date, 'close']
            
            # RISK CHECK
            risk_score = RiskModule.calculate_risk(df, idx)
            base_alloc = self.cash * 0.20
            
            if risk_score > 0.7:
                self.stats['risk_skip'] += 1
                return False
            elif risk_score > 0.4:
                alloc = base_alloc * 0.5
                self.stats['risk_half'] += 1
            else:
                alloc = base_alloc
                self.stats['risk_full'] += 1
            
            shares = int(alloc / price / 100) * 100
            if shares > 0:
                self.cash -= shares * price
                self.positions[code] = {'shares': shares, 'cost': price}
                return True
        return False

    def _sell(self, code, price):
        pos = self.positions.pop(code)
        self.cash += pos['shares'] * price

if __name__ == "__main__":
    V4Backtest().run_comparison()
