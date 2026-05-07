# -*- encoding: utf-8 -*-
"""
简化回测 — 等权Top5组合，每日调仓，验证模型真实预测能力
"""
import logging
import numpy as np
import pandas as pd
import pickle
from pathlib import Path

from train_ranker import (
    FEATURE_COLS, calc_features, get_kline_em, get_money_flow_em,
    get_sample_stocks, get_news_sentiment_for_industry, get_industry_momentum
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SimpleBacktest")

TOP_N = 5
COST = 0.0015  # 单边交易成本


def simple_backtest():
    logger.info("=== 简化回测（等权Top5，每日调仓）===")
    model = load_model()
    stocks = get_sample_stocks(30)
    news_data = get_news_sentiment_for_industry()
    industry_data = get_industry_momentum(stocks)

    # 获取数据
    stock_data = {}
    for code in stocks:
        df = get_kline_em(code, count=200)
        if df.empty or len(df) < 60:
            continue
        mf = get_money_flow_em(code, days=30)
        stock_data[code] = {"kline": df, "mf": mf}

    all_dates = sorted(set().union(*[d["kline"].index for d in stock_data.values()]))
    # 取最后60天
    test_dates = all_dates[-60:]

    daily_returns = []

    for i, date in enumerate(test_dates):
        scores = {}
        for code, data in stock_data.items():
            df_hist = data["kline"][data["kline"].index <= date].tail(100)
            if len(df_hist) < 30:
                continue
            feats = calc_features(df_hist, mf_data=data["mf"], code=code,
                                 news_data=news_data, industry_data=industry_data)
            if feats.empty:
                continue
            X = feats[FEATURE_COLS].values
            score = model.predict(X)[0]
            scores[code] = score

        if len(scores) < TOP_N:
            continue

        # 选Top5
        top5 = sorted(scores.items(), key=lambda x: -x[1])[:TOP_N]
        top_codes = [c for c, _ in top5]

        # 计算明日收益（用T+1日收益近似）
        if i + 1 < len(test_dates):
            next_date = test_dates[i + 1]
            next_rets = []
            for code in top_codes:
                if date in stock_data[code]["kline"].index and next_date in stock_data[code]["kline"].index:
                    buy = stock_data[code]["kline"].loc[date, "close"]
                    sell = stock_data[code]["kline"].loc[next_date, "close"]
                    ret = (sell - buy) / buy - COST * 2  # 买卖双向成本
                    next_rets.append(ret)

            if next_rets:
                avg_ret = sum(next_rets) / len(next_rets)
                daily_returns.append({
                    "date": next_date,
                    "return": avg_ret,
                    "n_stocks": len(next_rets),
                })

    df = pd.DataFrame(daily_returns)
    if df.empty:
        logger.error("无交易数据")
        return

    df["cumulative"] = (1 + df["return"]).cumprod()
    total_ret = (df["cumulative"].iloc[-1] - 1) * 100
    annual_ret = (df["cumulative"].iloc[-1] ** (252 / len(df)) - 1) * 100
    sharpe = df["return"].mean() / (df["return"].std() + 1e-10) * np.sqrt(252)
    win_rate = (df["return"] > 0).sum() / len(df) * 100

    cum_max = df["cumulative"].cummax()
    dd = (df["cumulative"] - cum_max) / cum_max
    max_dd = dd.min() * 100

    logger.info(f"\n{'='*50}")
    logger.info(f"简化回测结果 (Top5等权，每日调仓)")
    logger.info(f"{'='*50}")
    logger.info(f"交易天数: {len(df)}")
    logger.info(f"总收益率: {total_ret:.2f}%")
    logger.info(f"年化收益率: {annual_ret:.2f}%")
    logger.info(f"夏普比率: {sharpe:.2f}")
    logger.info(f"日胜率: {win_rate:.1f}%")
    logger.info(f"最大回撤: {max_dd:.2f}%")
    logger.info(f"平均日收益: {df['return'].mean()*100:.3f}%")
    logger.info(f"{'='*50}")

    # Top因子贡献分析
    logger.info(f"\nTop5股票的行业分布:")
    for _, row in df.head(10).iterrows():
        logger.info(f"  {row['date'].strftime('%Y-%m-%d')}: {row['return']*100:+.2f}%")


def load_model():
    with open(Path(__file__).parent / "model" / "ranker_model.pkl", "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    simple_backtest()
