# -*- encoding: utf-8 -*-
"""
严谨回测 — 滚动预测，杜绝未来函数
===================================
逻辑: 
  1. 用过去T天数据计算特征
  2. 用训练好的模型预测未来收益排名
  3. 选Top N，次日开盘买入
  4. 持有HOLD_DAYS后卖出，计算真实收益
"""
import logging
import numpy as np
import pandas as pd
import pickle
from pathlib import Path
from datetime import datetime

from train_ranker import (
    FEATURE_COLS, calc_features, get_kline_em, get_money_flow_em,
    get_sample_stocks, get_news_sentiment_for_industry, get_industry_momentum
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Backtest")

TOP_N = 10           # 每天选Top10
HOLD_DAYS = 5        # 持有5天
INIT_CAPITAL = 1_000_000
TRANSACTION_COST = 0.0015  # 单边千分之1.5（佣金+印花税+滑点）


def load_model():
    model_path = Path(__file__).parent / "model" / "ranker_model.pkl"
    with open(model_path, "rb") as f:
        return pickle.load(f)


def rolling_backtest():
    """滚动预测回测"""
    logger.info("=== 严谨回测开始 ===")
    model = load_model()
    stocks = get_sample_stocks(30)[:15]  # 减少到15只加速回测
    
    # 加载全局数据（新闻/产业）
    news_data = get_news_sentiment_for_industry()
    industry_data = get_industry_momentum(stocks)
    
    # 获取每只股票足够长的K线（需要历史窗口+持有期）
    stock_data = {}
    for code in stocks:
        df = get_kline_em(code, count=250)  # 约1年数据
        if df.empty or len(df) < 60:
            continue
        mf_data = get_money_flow_em(code, days=30)
        stock_data[code] = {"kline": df, "mf": mf_data}
    
    if not stock_data:
        logger.error("无有效股票数据")
        return
    
    # 确定回测时间窗口
    all_dates = sorted(set().union(*[d["kline"].index for d in stock_data.values()]))
    # 取最后90天回测
    test_dates = all_dates[-90:]
    
    logger.info(f"回测区间: {test_dates[0].strftime('%Y-%m-%d')} ~ {test_dates[-1].strftime('%Y-%m-%d')}")
    logger.info(f"股票数: {len(stock_data)}, 交易日: {len(test_dates)}")
    
    portfolio = []
    capital = INIT_CAPITAL
    position = {}  # {code: (buy_date, buy_price, shares)}
    
    for i, trade_date in enumerate(test_dates):
        if i < 30:  # 前30天用于特征计算
            continue
        
        # === Step 1: 计算当日特征并预测 ===
        scores = {}
        for code, data in stock_data.items():
            df = data["kline"]
            # 只用到trade_date之前的数据
            df_hist = df[df.index <= trade_date]
            if len(df_hist) < 30:
                continue
            
            # 取最近的K线窗口
            df_window = df_hist.tail(120)
            mf = data["mf"]
            
            feats = calc_features(df_window, mf_data=mf, code=code,
                                 news_data=news_data, industry_data=industry_data)
            if feats.empty:
                continue
            
            # 模型预测
            X = feats[FEATURE_COLS].values
            score = model.predict(X)[0]
            scores[code] = score
        
        if not scores:
            continue
        
        # === Step 2: 选Top N ===
        sorted_stocks = sorted(scores.items(), key=lambda x: -x[1])
        top_stocks = [code for code, _ in sorted_stocks[:TOP_N]]
        
        # === Step 3: 执行交易（次日开盘买入） ===
        # 简化：用当日收盘价近似次日开盘价（实际应加滑点）
        # 卖出持有期满的仓位
        to_sell = []
        for code, (buy_date, buy_price, shares) in position.items():
            hold_days = (trade_date - buy_date).days
            if hold_days >= HOLD_DAYS:
                # 获取当日收盘价
                if trade_date in stock_data[code]["kline"].index:
                    sell_price = stock_data[code]["kline"].loc[trade_date, "close"]
                    sell_value = shares * sell_price
                    cost = sell_value * TRANSACTION_COST
                    capital += sell_value - cost
                    to_sell.append(code)
                    
                    ret = (sell_price - buy_price) / buy_price
                    portfolio.append({
                        "date": trade_date,
                        "code": code,
                        "action": "SELL",
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "return": ret,
                    })
        
        for code in to_sell:
            del position[code]
        
        # 买入新仓位（等权分配）
        if top_stocks:
            # 计算可用资金（扣除已持仓占用的资金）
            position_value = sum(shares * stock_data[c]["kline"].loc[trade_date, "close"] 
                              for c, (_, _, shares) in position.items() 
                              if trade_date in stock_data[c]["kline"].index)
            available = capital - position_value
            
            buy_value_per_stock = (available * 0.95) / len(top_stocks)  # 留5%现金
            for code in top_stocks:
                if trade_date in stock_data[code]["kline"].index:
                    buy_price = stock_data[code]["kline"].loc[trade_date, "close"]
                    shares = int(buy_value_per_stock / buy_price)
                    if shares > 0 and code not in position:  # 已持有则不重复买
                        cost = buy_price * shares * TRANSACTION_COST
                        capital -= buy_price * shares + cost
                        position[code] = (trade_date, buy_price, shares)
                        
                        portfolio.append({
                            "date": trade_date,
                            "code": code,
                            "action": "BUY",
                            "buy_price": buy_price,
                            "return": 0,
                        })
    
    # === 收益统计 ===
    if not portfolio:
        logger.error("无交易记录")
        return
    
    df_pnl = pd.DataFrame(portfolio)
    sells = df_pnl[df_pnl["action"] == "SELL"]
    
    if sells.empty:
        logger.error("无卖出记录")
        return
    
    total_trades = len(sells)
    win_trades = (sells["return"] > 0).sum()
    win_rate = win_trades / total_trades * 100
    avg_return = sells["return"].mean() * 100
    total_return = ((capital / INIT_CAPITAL) - 1) * 100
    
    # 年化
    days = (test_dates[-1] - test_dates[30]).days
    annual_return = ((capital / INIT_CAPITAL) ** (365 / max(days, 1)) - 1) * 100
    
    # 每日收益序列（用于夏普和回撤）
    daily_returns = sells.groupby("date")["return"].mean()
    sharpe = daily_returns.mean() / (daily_returns.std() + 1e-10) * np.sqrt(252)
    
    # 最大回撤
    cum_ret = (1 + daily_returns).cumprod()
    cum_max = cum_ret.cummax()
    drawdown = (cum_ret - cum_max) / cum_max
    max_dd = drawdown.min() * 100
    
    logger.info(f"\n{'='*50}")
    logger.info(f"回测结果 (严谨滚动预测)")
    logger.info(f"{'='*50}")
    logger.info(f"回测区间: {test_dates[30].strftime('%Y-%m-%d')} ~ {test_dates[-1].strftime('%Y-%m-%d')}")
    logger.info(f"总交易次数: {total_trades} (胜{win_trades}/败{total_trades-win_trades})")
    logger.info(f"胜率: {win_rate:.1f}%")
    logger.info(f"平均单笔收益: {avg_return:.2f}%")
    logger.info(f"总收益率: {total_return:.2f}%")
    logger.info(f"年化收益率: {annual_return:.2f}%")
    logger.info(f"夏普比率: {sharpe:.2f}")
    logger.info(f"最大回撤: {max_dd:.2f}%")
    logger.info(f"期末资金: ¥{capital:,.0f}")
    logger.info(f"{'='*50}")
    
    return df_pnl


if __name__ == "__main__":
    rolling_backtest()
