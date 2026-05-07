# -*- encoding: utf-8 -*-
"""
回测脚本 — 评估33因子组合的历史收益和因子有效性
================================================
"""
import json
import logging
import numpy as np
import pandas as pd
import requests
import pickle
from pathlib import Path
from datetime import datetime

from train_ranker import (
    FEATURE_COLS, calc_features, get_kline_em, get_money_flow_em,
    get_sample_stocks, get_news_sentiment_for_industry, get_industry_momentum,
    STOCK_INDUSTRY
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Backtest")

TOP_N = 10           # 每天选Top10
INIT_CAPITAL = 1_000_000  # 初始资金100万
REBALANCE_DAYS = 1    # 每日调仓


def load_model():
    model_path = Path(__file__).parent / "model" / "ranker_model.pkl"
    with open(model_path, "rb") as f:
        return pickle.load(f)


def backtest():
    logger.info("=== 回测开始 ===")
    model = load_model()
    stocks = get_sample_stocks(30)
    
    # 获取全局数据
    news_data = get_news_sentiment_for_industry()
    industry_data = get_industry_momentum(stocks)
    
    # 获取每只股票的K线和特征
    all_scores = []
    for code in stocks:
        df = get_kline_em(code, count=120)  # 回测需要60天窗口
        if df.empty or len(df) < 30:
            continue
        mf_data = get_money_flow_em(code, days=30)
        feats = calc_features(df, mf_data=mf_data, code=code,
                             news_data=news_data, industry_data=industry_data)
        if feats.empty:
            continue
        feats["code"] = code
        all_scores.append(feats)
    
    if not all_scores:
        logger.error("无有效数据")
        return
    
    df_all = pd.concat(all_scores, ignore_index=True)
    
    # 用模型打分
    X = df_all[FEATURE_COLS].values
    df_all["score"] = model.predict(X)
    
    # 按日期分组选TopN
    dates = sorted(df_all["date"].unique())
    portfolio_returns = []
    
    for d in dates:
        day_data = df_all[df_all["date"] == d].sort_values("score", ascending=False)
        top_stocks = day_data.head(TOP_N)
        
        # 计算等权组合当日收益（假设T+1执行，用次日开盘价）
        avg_score = top_stocks["score"].mean()
        avg_future_ret = top_stocks["label"].mean() if "label" in top_stocks.columns else 0
        
        portfolio_returns.append({
            "date": d,
            "top_n_return": avg_future_ret,
            "avg_score": avg_score,
            "n_stocks": len(top_stocks),
        })
    
    df_ret = pd.DataFrame(portfolio_returns)
    if df_ret.empty:
        logger.error("回测无有效日期")
        return
    
    # 收益统计
    df_ret["cumulative"] = (1 + df_ret["top_n_return"]).cumprod()
    df_ret["daily_pct"] = df_ret["top_n_return"] * 100
    
    total_return = (df_ret["cumulative"].iloc[-1] - 1) * 100
    annual_return = ((df_ret["cumulative"].iloc[-1]) ** (252 / len(df_ret)) - 1) * 100
    
    # 夏普比率
    daily_returns = df_ret["top_n_return"].dropna()
    sharpe = daily_returns.mean() / (daily_returns.std() + 1e-10) * np.sqrt(252)
    
    # 最大回撤
    cum_max = df_ret["cumulative"].cummax()
    drawdown = (df_ret["cumulative"] - cum_max) / cum_max
    max_drawdown = drawdown.min() * 100
    
    # 胜率
    win_rate = (daily_returns > 0).sum() / len(daily_returns) * 100
    
    logger.info(f"\n{'='*50}")
    logger.info(f"回测结果 (Top{TOP_N} 等权组合)")
    logger.info(f"{'='*50}")
    logger.info(f"交易天数: {len(df_ret)}")
    logger.info(f"总收益率: {total_return:.2f}%")
    logger.info(f"年化收益率: {annual_return:.2f}%")
    logger.info(f"夏普比率: {sharpe:.2f}")
    logger.info(f"最大回撤: {max_drawdown:.2f}%")
    logger.info(f"日胜率: {win_rate:.1f}%")
    logger.info(f"{'='*50}")
    
    return df_ret


def factor_analysis():
    """因子有效性分析"""
    logger.info("\n=== 因子有效性分析 ===")
    
    # 加载模型
    model = load_model()
    stocks = get_sample_stocks(30)
    news_data = get_news_sentiment_for_industry()
    industry_data = get_industry_momentum(stocks)
    
    # 收集所有特征
    all_feats = []
    for code in stocks:
        df = get_kline_em(code, count=120)
        if df.empty or len(df) < 30:
            continue
        mf_data = get_money_flow_em(code, days=30)
        feats = calc_features(df, mf_data=mf_data, code=code,
                             news_data=news_data, industry_data=industry_data)
        if feats.empty:
            continue
        feats["code"] = code
        all_feats.append(feats)
    
    if not all_feats:
        return
    
    df_all = pd.concat(all_feats, ignore_index=True)
    
    # 计算每个因子与标签的相关性
    correlations = {}
    for col in FEATURE_COLS:
        if col in df_all.columns and df_all[col].std() > 0:
            corr = df_all[col].corr(df_all["label"])
            correlations[col] = corr
    
    # 排序
    sorted_factors = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)
    
    # 因子分类
    factor_groups = {
        "技术面 (20个)": [c for c in FEATURE_COLS if not c.startswith(("mf_", "news_", "industry_")) and c != "stock_vs_industry"][:20],
        "资金流向 (6个)": [c for c in FEATURE_COLS if c.startswith("mf_")],
        "新闻情绪 (3个)": [c for c in FEATURE_COLS if c.startswith("news_")],
        "产业动量 (4个)": [c for c in FEATURE_COLS if c.startswith("industry_") or c == "stock_vs_industry"],
    }
    
    logger.info(f"\n{'因子':<25} {'IC':>8} {'分类':<12} {'有效性':<8}")
    logger.info("-" * 60)
    
    for factor, ic in sorted_factors:
        # 确定分类
        if factor.startswith("mf_"):
            cat = "资金流向"
        elif factor.startswith("news_"):
            cat = "新闻情绪"
        elif factor.startswith("industry_") or factor == "stock_vs_industry":
            cat = "产业动量"
        else:
            cat = "技术面"
        
        # 有效性评级
        abs_ic = abs(ic)
        if abs_ic > 0.05:
            rating = "★★★"
        elif abs_ic > 0.02:
            rating = "★★☆"
        else:
            rating = "★☆☆"
        
        logger.info(f"{factor:<25} {ic:>8.4f} {cat:<12} {rating:<8}")
    
    # 分组统计
    logger.info(f"\n{'='*50}")
    logger.info("因子分组平均|IC|")
    logger.info(f"{'='*50}")
    
    for group_name, factors in factor_groups.items():
        valid_ics = [abs(correlations[f]) for f in factors if f in correlations]
        if valid_ics:
            avg_ic = sum(valid_ics) / len(valid_ics)
            logger.info(f"{group_name}: 平均|IC|={avg_ic:.4f} ({len(valid_ics)}个因子)")


if __name__ == "__main__":
    backtest()
    factor_analysis()
