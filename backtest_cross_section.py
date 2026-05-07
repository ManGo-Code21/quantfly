# -*- encoding: utf-8 -*-
"""
截面回测 — 验证模型选股能力（Top5超额收益）
================================================
逻辑:
  1. 每天用模型选Top5
  2. 计算Top5当日收益 vs 全市场当日平均收益
  3. 超额收益 > 0 说明选股有效
"""
import logging
import numpy as np
import pandas as pd
import pickle
from pathlib import Path

from train_ranker import (
    FEATURE_COLS, calc_features, get_kline_em, get_money_flow_em,
    get_sample_stocks, get_news_sentiment_for_industry, STOCK_INDUSTRY
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CrossSectionBacktest")

TOP_N = 5
BENCHMARK_N = 20  # 基准取全市场平均


def cross_section_backtest():
    logger.info("=== 截面回测（Top5超额收益）===")
    model_path = Path(__file__).parent / "model" / "ranker_model.pkl"
    with open(model_path, "rb") as f:
        model = pickle.load(f)

    # 取30只股票做截面回测
    stocks = get_sample_stocks(30)
    news_data = get_news_sentiment_for_industry()

    # 获取所有股票K线
    stock_data = {}
    for code in stocks:
        df = get_kline_em(code, count=200)
        if df.empty or len(df) < 60:
            continue
        mf = get_money_flow_em(code, days=30)
        stock_data[code] = {"kline": df, "mf": mf}

    # 确定回测日期范围
    all_dates = sorted(set().union(*[d["kline"].index for d in stock_data.values()]))
    # 取中间段（避开最开头数据不足）
    test_dates = all_dates[40:-10]  # 留前后缓冲
    logger.info(f"回测区间: {test_dates[0].strftime('%Y-%m-%d')} ~ {test_dates[-1].strftime('%Y-%m-%d')}, {len(test_dates)}天")

    results = []

    for date in test_dates:
        scores = {}
        daily_rets = {}

        for code, data in stock_data.items():
            df_hist = data["kline"][data["kline"].index <= date].tail(100)
            if len(df_hist) < 30:
                continue

            # 计算当日特征
            ind = STOCK_INDUSTRY.get(code, "其他")
            industry_data = {ind: {"ret5": 0, "ret10": 0, "rank": 0.5}}
            feats = calc_features(df_hist, mf_data=data["mf"], code=code,
                                 news_data=news_data, industry_data=industry_data)
            if feats.empty:
                continue

            X = feats[FEATURE_COLS].values
            score = model.predict(X)[0]
            scores[code] = score

            # 计算当日收益（如果有前一日数据）
            dates_available = df_hist.index
            if date in dates_available:
                idx = dates_available.get_loc(date)
                if idx > 0:
                    prev_close = df_hist.iloc[idx - 1]["close"]
                    curr_close = df_hist.iloc[idx]["close"]
                    daily_rets[code] = (curr_close - prev_close) / prev_close

        if len(scores) < TOP_N + 5:
            continue

        # Top5收益
        top5 = sorted(scores.items(), key=lambda x: -x[1])[:TOP_N]
        top5_codes = [c for c, _ in top5]
        top5_ret = np.mean([daily_rets.get(c, 0) for c in top5_codes])

        # 全市场平均收益（基准）
        all_rets = list(daily_rets.values())
        benchmark_ret = np.mean(all_rets) if all_rets else 0

        # 超额收益
        alpha = top5_ret - benchmark_ret

        results.append({
            "date": date,
            "top5_ret": top5_ret,
            "benchmark_ret": benchmark_ret,
            "alpha": alpha,
            "n_stocks": len(daily_rets),
        })

    df = pd.DataFrame(results)
    if df.empty:
        logger.error("无有效数据")
        return

    # 统计
    total_alpha = df["alpha"].sum() * 100
    win_days = (df["alpha"] > 0).sum()
    win_rate = win_days / len(df) * 100
    avg_daily_alpha = df["alpha"].mean() * 100
    sharpe = df["alpha"].mean() / (df["alpha"].std() + 1e-10) * np.sqrt(252)

    # 最大回撤
    cum_alpha = (1 + df["alpha"]).cumprod()
    cum_max = cum_alpha.cummax()
    dd = (cum_alpha - cum_max) / cum_max
    max_dd = dd.min() * 100

    logger.info(f"\n{'='*50}")
    logger.info(f"截面回测结果 (Top5 vs 全市场平均)")
    logger.info(f"{'='*50}")
    logger.info(f"交易天数: {len(df)}")
    logger.info(f"累计超额收益: {total_alpha:.2f}%")
    logger.info(f"日均超额: {avg_daily_alpha:.3f}%")
    logger.info(f"超额胜率: {win_rate:.1f}%")
    logger.info(f"超额夏普: {sharpe:.2f}")
    logger.info(f"最大超额回撤: {max_dd:.2f}%")
    logger.info(f"{'='*50}")

    # 月度统计
    df["month"] = df["date"].dt.month
    monthly = df.groupby("month")["alpha"].agg(["mean", "sum", "count"])
    logger.info(f"\n月度超额收益:")
    for m, row in monthly.iterrows():
        logger.info(f"  {m}月: 日均{row['mean']*100:.3f}%, 累计{row['sum']*100:.2f}%, {int(row['count'])}天")

    return df


if __name__ == "__main__":
    cross_section_backtest()
