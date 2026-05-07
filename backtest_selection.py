# -*- encoding: utf-8 -*-
"""
截面选股回测 — 验证Ranking模型选股能力
========================================
逻辑:
  1. 用历史数据训练模型（已完成）
  2. 每天计算特征，模型打分，选Top5
  3. 计算Top5次日收益 vs 全市场次日平均收益
  4. 统计超额收益（Alpha）
"""
import logging
import numpy as np
import pandas as pd
import pickle
from pathlib import Path

from train_ranker import (
    FEATURE_COLS, calc_features, get_kline_em, get_money_flow_em,
    get_sample_stocks, get_news_sentiment_for_industry, STOCK_INDUSTRY,
    build_industry_map, calc_industry_momentum_from_board
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SelectionBacktest")

TOP_N = 5


def selection_backtest():
    """截面选股回测"""
    logger.info("=== 截面选股回测 ===")
    
    # 加载模型
    model_path = Path(__file__).parent / "model" / "ranker_model.pkl"
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    logger.info("模型已加载")

    # 获取股票列表（30只做回测）
    stocks = get_sample_stocks(30)
    logger.info(f"回测股票池: {len(stocks)} 只")

    # 构建行业映射
    industry_map = build_industry_map()
    industry_data = calc_industry_momentum_from_board()
    news_data = get_news_sentiment_for_industry()

    # 获取每只股票的K线数据（需要足够历史来计算特征）
    stock_data = {}
    for code in stocks:
        df = get_kline_em(code, count=200)  # 约200天历史
        if df.empty or len(df) < 60:
            continue
        mf = get_money_flow_em(code, days=30)
        stock_data[code] = {"kline": df, "mf": mf}
    
    logger.info(f"有效股票: {len(stock_data)} 只")

    # 确定回测日期（取最近60个交易日）
    all_dates = sorted(set().union(*[d["kline"].index for d in stock_data.values()]))
    test_dates = all_dates[-60:]  # 最近60天
    logger.info(f"回测区间: {test_dates[0].strftime('%Y-%m-%d')} ~ {test_dates[-1].strftime('%Y-%m-%d')}")

    results = []

    for date in test_dates:
        scores = {}
        daily_rets = {}
        
        for code, data in stock_data.items():
            # 获取截至date的历史数据
            df_hist = data["kline"][data["kline"].index <= date].tail(100)
            if len(df_hist) < 30:
                continue
            
            # 计算特征
            feats = calc_features(df_hist, mf_data=data["mf"], code=code,
                                 news_data=news_data, industry_data=industry_data,
                                 industry_map=industry_map)
            if feats.empty:
                continue
            
            # 模型打分
            X = feats[FEATURE_COLS].values
            score = model.predict(X)[0]
            scores[code] = score
            
            # 计算当日收益
            dates_avail = df_hist.index
            if date in dates_avail:
                idx = dates_avail.get_loc(date)
                if idx > 0:
                    prev_close = df_hist.iloc[idx - 1]["close"]
                    curr_close = df_hist.iloc[idx]["close"]
                    daily_rets[code] = (curr_close - prev_close) / prev_close

        if len(scores) < TOP_N + 5:
            continue

        # Top5平均收益
        top5 = sorted(scores.items(), key=lambda x: -x[1])[:TOP_N]
        top5_codes = [c for c, _ in top5]
        top5_rets = [daily_rets.get(c, 0) for c in top5_codes]
        top5_ret = np.mean(top5_rets)
        
        # 全市场平均收益（基准）
        all_rets = list(daily_rets.values())
        benchmark_ret = np.mean(all_rets) if all_rets else 0
        
        # 超额收益（Alpha）
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
    
    # 年化Alpha
    annual_alpha = avg_daily_alpha * 252
    
    # 信息比率（IR）
    ir = df["alpha"].mean() / (df["alpha"].std() + 1e-10) * np.sqrt(252)

    logger.info(f"\n{'='*50}")
    logger.info(f"截面选股回测结果 (Top{TOP_N} vs 全市场平均)")
    logger.info(f"{'='*50}")
    logger.info(f"交易天数: {len(df)}")
    logger.info(f"累计超额收益: {total_alpha:.2f}%")
    logger.info(f"日均超额: {avg_daily_alpha:.3f}%")
    logger.info(f"年化超额: {annual_alpha:.2f}%")
    logger.info(f"超额胜率: {win_rate:.1f}%")
    logger.info(f"信息比率(IR): {ir:.2f}")
    logger.info(f"{'='*50}")
    
    # 每日明细（前10天）
    logger.info(f"\n每日明细(前10天):")
    for _, row in df.head(10).iterrows():
        logger.info(f"  {row['date'].strftime('%Y-%m-%d')}: Top5={row['top5_ret']*100:+.2f}%, "
                   f"基准={row['benchmark_ret']*100:+.2f}%, Alpha={row['alpha']*100:+.2f}%")

    return df


if __name__ == "__main__":
    selection_backtest()
