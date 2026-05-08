#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""
均线择时策略 V2 — 趋势跟踪版 (拿住主升浪)
====================================================
改进点：
1. 移除敏感的 MA5/10 死叉卖出（防止卖飞大牛股）。
2. 卖出标准：跌破 MA20 或 MA60 (生命线破位)。
3. 止盈标准：高点回撤 15% (宽幅止盈，吃尽主升浪)。
4. 修正基准计算：与策略严格对齐同一时间窗口。
"""
import numpy as np
import pandas as pd
import logging
import time
import warnings
from datetime import timedelta

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Timing_V2")

from train_ranker import get_kline_em
from timing_model import get_index_data

# ============================================================
# 策略参数
# ============================================================
START_DATE = "2023-01-01"
INIT_CAPITAL = 1_000_000
MAX_POSITION_PCT = 0.25  
INITIAL_STOP_LOSS = -0.08  # 初始硬止损 -8%
TRAILING_STOP_PCT = -0.15  # 高点回撤 15% 止盈 (比 V1 的 5% 更宽)

# 股票池 (同上)
STOCK_POOL = {
    '002156': '通富微电', '688041': '海光信息', '688111': '澜起科技', '002415': '海康威视',
    '000977': '浪潮信息', '603019': '中科曙光', '300496': '中科创达', '688012': '中微公司',
    '688072': '拓荆科技', '300604': '长川科技', '688256': '寒武纪', '002747': '埃斯顿'
}
STOCK_CODES = list(STOCK_POOL.keys())

class V2SignalGenerator:
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # 均线
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA10'] = df['close'].rolling(10).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['MA60'] = df['close'].rolling(60).mean()
        
        # 量比
        df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        
        # 买入信号：金叉 + 放量 + 趋势向上
        df['buy_signal'] = (
            (df['MA5'].shift(1) <= df['MA10'].shift(1)) &
            (df['MA5'] > df['MA10']) &
            (df['vol_ratio'] > 1.5) &
            (df['close'] > df['MA20'])
        )
        
        return df

class V2Backtester:
    def __init__(self):
        self.cash = INIT_CAPITAL
        self.positions = {} # code -> {shares, cost, high}
        self.daily_values = []
    
    def run(self, stock_dfs: dict, index_df: pd.DataFrame):
        self.stock_dfs = stock_dfs
        # 获取所有交易日
        all_dates = sorted(set().union(*[df.index for df in stock_dfs.values()]))
        dates = [d for d in all_dates if d >= pd.Timestamp(START_DATE)]
        
        # 预热期跳过
        start_idx = min(len(dates), 60) 
        self.actual_start_date = dates[start_idx]
        dates = dates[start_idx:]
        
        for date in dates:
            portfolio_value = self.cash
            for code, pos in self.positions.items():
                if code in stock_dfs and date in stock_dfs[code].index:
                    current_price = stock_dfs[code].loc[date, 'close']
                    if current_price > pos['high']: pos['high'] = current_price
                    portfolio_value += pos['shares'] * current_price
            
            self._execute_trades(stock_dfs, date)
            self.daily_values.append({'date': date, 'value': portfolio_value})

    def _execute_trades(self, stock_dfs, date):
        # 1. 检查卖出
        for code in list(self.positions.keys()):
            if code not in stock_dfs or date not in stock_dfs[code].index: continue
            self._check_exit(code, stock_dfs[code], date)

        # 2. 检查买入
        for code, df in stock_dfs.items():
            if code in self.positions: continue
            if date not in df.index: continue
            self._check_entry(code, df, date)

    def _check_exit(self, code: str, df: pd.DataFrame, date: pd.Timestamp):
        pos = self.positions[code]
        row = df.loc[date]
        price = row['close']
        
        # 1. 初始止损
        if price < pos['cost'] * (1 + INITIAL_STOP_LOSS):
            self._sell(code, price, date, "初始止损")
            return

        # 2. 趋势破坏卖出 (跌破 MA20)
        if price < row['MA20']:
             self._sell(code, price, date, "跌破趋势线 (MA20)")
             return

        # 3. 移动止盈 (高点回撤 15%)
        high = pos['high']
        drawdown = (price - high) / high
        if drawdown < TRAILING_STOP_PCT:
             self._sell(code, price, date, f"移动止盈 (回撤{drawdown*100:.1f}%)")
             return

    def _check_entry(self, code: str, df: pd.DataFrame, date: pd.Timestamp):
        row = df.loc[date]
        if row.get('buy_signal', False):
            # 仓位计算
            portfolio_value = self.cash + sum(
                p['shares'] * self._get_price(c, date) 
                for c, p in self.positions.items()
            )
            target_val = min(portfolio_value * MAX_POSITION_PCT, self.cash * 0.95)
            
            price = row['close']
            if target_val > 10000 and price > 0:
                shares = int(target_val / price / 100) * 100
                if shares > 0:
                    self.cash -= shares * price
                    self.positions[code] = {'shares': shares, 'cost': price, 'high': price}

    def _get_price(self, code, date):
        try:
            return self.stock_dfs[code].loc[date, 'close']
        except:
            return 0

    def _sell(self, code, price, date, reason):
        pos = self.positions.pop(code)
        self.cash += pos['shares'] * price
        profit = (price - pos['cost']) / pos['cost']
        logger.info(f"  📤 {date.strftime('%Y-%m-%d')} 卖出 {code} @ {price:.2f} | {reason} | 盈亏 {profit*100:.1f}%")

def calc_buy_hold(stock_dfs, strategy_start_date):
    """计算等权买入持有的收益 (严格对齐策略起始时间)"""
    initial_investment_per_stock = INIT_CAPITAL / len(stock_dfs)
    final_total_value = 0
    valid_stocks = 0
    start_date_ts = pd.Timestamp(strategy_start_date)
    
    for code, df in stock_dfs.items():
        try:
            # 找到策略开始后的第一个可用价格
            start_price = df.loc[start_date_ts:, 'close'].iloc[0]
        except:
            continue
            
        end_price = df.iloc[-1]['close']
        
        shares = initial_investment_per_stock / start_price
        final_total_value += shares * end_price
        valid_stocks += 1
        
    if valid_stocks == 0: return 0
    
    total_ret = (final_total_value - INIT_CAPITAL) / INIT_CAPITAL
    
    # 计算持有天数
    first_date = stock_dfs[list(stock_dfs.keys())[0]].loc[start_date_ts:, 'close'].index[0]
    last_date = stock_dfs[list(stock_dfs.keys())[0]].index[-1]
    days = (last_date - first_date).days
    
    ann_ret = (1 + total_ret) ** (365/days) - 1 if days > 0 else 0
    return ann_ret

def run_v2():
    logger.info("="*70)
    logger.info("趋势跟踪版 V2 回测 — 宽幅止盈 + 趋势生命线")
    logger.info("="*70)
    
    # 1. 获取数据
    stock_dfs = {}
    for code in STOCK_CODES:
        try:
            df = get_kline_em(code, count=1500)
            if not df.empty: stock_dfs[code] = df
        except: pass

    # 2. 生成信号
    sig_gen = V2SignalGenerator()
    for code in stock_dfs:
        stock_dfs[code] = sig_gen.generate_signals(stock_dfs[code])
    
    # 3. 回测运行
    index_df = get_index_data(days=1500) 
    tester = V2Backtester()
    tester.run(stock_dfs, index_df)
    
    # 4. 结果分析
    res_df = pd.DataFrame(tester.daily_values).set_index('date')
    if res_df.empty: return

    # 计算策略年化
    total_ret = (res_df['value'].iloc[-1] / INIT_CAPITAL) - 1
    days = (res_df.index[-1] - res_df.index[0]).days
    strat_annual = (1 + total_ret) ** (365/days) - 1 if days > 0 else 0
    
    # 计算基准 (严格对齐)
    bh_annual = calc_buy_hold(stock_dfs, tester.actual_start_date)
    
    logger.info(f"\n🚀 策略表现 (V2 趋势跟踪):")
    logger.info(f"  最终资金: ¥{res_df['value'].iloc[-1]:,.0f}")
    logger.info(f"  年化收益: {strat_annual*100:.1f}%")
    
    logger.info(f"\n📊 基准对比 (同一时间段无脑持有):")
    logger.info(f"  基准年化: {bh_annual*100:.1f}%")
    logger.info(f"  策略结果: {'✅ 跑赢' if strat_annual > bh_annual else '❌ 跑输'}")
    logger.info(f"  超额收益: {(strat_annual - bh_annual)*100:.1f}%")

if __name__ == "__main__":
    run_v2()
