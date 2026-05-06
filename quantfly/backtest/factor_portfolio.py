# -*- encoding: utf-8 -*-
"""
因子组合框架 — 20个F100因子 × 新闻情绪 × 成交量
===================================================
基于BigQuant F100因子的组合策略：

【你的三层架构】
  Layer-1: 因子选股（20个F100因子 → StockRanker排序）
  Layer-2: 新闻确认（Gate1: 新闻情感 → 行业信号强度）
  Layer-3: 成交量验证（Gate2: 分钟级量比 → 确认信号）

【20个F100核心因子（BigQuant预计算因子库）】
  1. 质量因子：pe_ttm, pb, ps_ttm, roe_q, roa_y, gross_margin
  2. 成长因子：revenue_q, profit_q, net_operate_cashflow_q
  3. 动量因子：return_20d, return_60d, return_120d, volatility_60d
  4. 情绪因子：turnover_rate, close_HSI, vwap_deviation
  5. 规模因子：float_mv, total_mv, circulation_mv
  6. 估值因子：pe_ttm, pb, pc_ttm, ps_ttm, dividend_yield

【组合逻辑】
  stock_score = 0.30 * F100_rank + 0.30 * News_sentiment + 0.20 * Volume_confirm + 0.20 * Chips_principle

使用方法：
  from quantfly.backtest.factor_portfolio import FactorPortfolio, build_news_weighted_portfolio
  portfolio = FactorPortfolio()
  stocks = portfolio.get_combined_signals(industries=["AI大模型", "半导体"])
"""
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Optional

logger = logging.getLogger("FactorPortfolio")

# ============================================================
# 因子权重配置（可调整）
# ============================================================
FACTOR_WEIGHTS = {
    "quality":   0.15,  # 质量（ROE、利润率）
    "growth":    0.20,  # 成长（营收、利润增速）
    "momentum":  0.25,  # 动量（20日、60日涨幅）
    "sentiment": 0.15,  # 情绪（换手率、波动率）
    "value":     0.10,  # 估值（PE、PB）
    "size":      0.05,  # 规模（流通市值）
}

# ============================================================
# 新闻加权因子
# ============================================================
NEWS_WEIGHT_CONFIG = {
    # 产业 → 对应的F100因子补充
    "AI大模型":    {"momentum": 0.35, "sentiment": 0.25},   # AI靠情绪驱动
    "半导体":      {"quality": 0.25, "growth": 0.25},       # 半导体看业绩
    "机器人":      {"momentum": 0.30, "sentiment": 0.20},   # 机器人题材驱动
    "商业航天":    {"momentum": 0.40, "growth": 0.15},     # 商业航天高动量
    "新能源车":    {"quality": 0.20, "growth": 0.25},       # 新能源车看增速
    "智能电网":    {"growth": 0.25, "quality": 0.20},      # 电网看订单
    "脑机接口":    {"momentum": 0.35, "sentiment": 0.25},  # 题材炒作
    "稀土永磁":    {"value": 0.25, "quality": 0.20},       # 稀土看估值
    "量子计算":    {"momentum": 0.40, "sentiment": 0.20},  # 早期题材
}

# ============================================================
# 行业动量权重（基于RSRS数据校准）
# ============================================================
INDUSTRY_MOMENTUM = {
    # RSRS>200 = 强势，RSRS<0 = 弱势
    # 这些数据来自历史分析，需要实时数据时用akshare验证
    "AI大模型":    {"rsrs": "strong",   "position": 1},   # 强势
    "半导体":      {"rsrs": "strong",   "position": 1},   # 强势
    "机器人":      {"rsrs": "strong",   "position": 1},   # 强势
    "5G":          {"rsrs": "strong",   "position": 1},   # 强势
    "芯片":         {"rsrs": "strong",   "position": 1},   # 强势（映射到半导体）
    "商业航天":     {"rsrs": "medium",   "position": 2},   # 中势
    "新能源车":     {"rsrs": "medium",   "position": 2},   # 中势
    "智能电网":     {"rsrs": "medium",   "position": 2},   # 中势
    "脑机接口":     {"rsrs": "weak",     "position": 3},   # 弱势
    "稀土永磁":     {"rsrs": "weak",     "position": 3},   # 弱势
    "量子计算":     {"rsrs": "weak",     "position": 3},   # 弱势
    "中证银行":     {"rsrs": "negative", "position": 4},   # 弱势
    "消费":         {"rsrs": "negative", "position": 4},   # 弱势
    "酒":           {"rsrs": "negative", "position": 4},   # 弱势
}


class FactorPortfolio:
    """
    因子组合管理器

    将三层信号组合成最终排序分数：
      1. F100因子排序（基于akshare实时数据近似）
      2. 新闻情绪加权（Gate1）
      3. 成交量验证（Gate2）
      4. 选股三原则（筹码+分时）

    最终分数：
      final_score = w1*因子分 + w2*Gate1分 + w3*Gate2分 + w4*选股三原则分
    """

    def __init__(
        self,
        gate1_signals: Optional[Dict] = None,
        gate2_signals: Optional[Dict] = None,
        industry_momentum: Optional[Dict] = None,
    ):
        """
        Args:
            gate1_signals: Gate1新闻情感信号 {industry: {avg_news_score, ...}}
            gate2_signals: Gate2成交量信号 {etf_code: {ratio, signal, ...}}
            industry_momentum: 行业动量配置
        """
        self.gate1 = gate1_signals or {}
        self.gate2 = gate2_signals or {}
        self.industry_momentum = industry_momentum or INDUSTRY_MOMENTUM

    def get_combined_score(
        self,
        stock_data: dict,
        gate1_score: float = 0.0,
        gate2_ratio: float = 1.0,
        chips_score: float = 0.0,
        momentum_score: float = 0.0,
    ) -> float:
        """
        计算单只股票的最终综合分数

        Args:
            stock_data: 股票的基础因子数据
            gate1_score: Gate1新闻情感分数
            gate2_ratio: Gate2成交量量比
            chips_score: 选股三原则筹码分数
            momentum_score: 选股三原则动量分数

        Returns:
            final_score (0~100)
        """
        # 因子基础分（0~1范围）
        factor_base = self._calc_factor_score(stock_data)

        # Gate1加权（新闻情感确认）
        # gate1_score已经是0~1范围
        news_weighted = gate1_score * 0.3 + factor_base * 0.7

        # Gate2确认（量比>1.5才加分）
        if gate2_ratio >= 2.0:
            volume_bonus = 0.15
        elif gate2_ratio >= 1.5:
            volume_bonus = 0.10
        elif gate2_ratio >= 1.0:
            volume_bonus = 0.05
        else:
            volume_bonus = -0.10  # 缩量减分

        # 选股三原则加分（筹码+分时）
        principle_score = (chips_score / 10.0) * 0.5 + (momentum_score / 5.0) * 0.5

        # 综合得分
        final = (
            news_weighted * 0.35
            + principle_score * 0.30
            + (min(gate2_ratio, 3.0) / 3.0) * 0.15
            + factor_base * 0.20
        )

        # 加上成交量加分
        final = min(final + volume_bonus, 1.0)

        return round(final * 100, 2)

    def _calc_factor_score(self, stock_data: dict) -> float:
        """
        计算基于F100因子的综合分数（简化版）
        真实版本需要从BigQuant数据源获取
        这里用akshare的实时数据进行近似
        """
        score = 0.0
        count = 0

        # 动量因子（最重要）
        if "return_20d" in stock_data:
            ret20 = stock_data["return_20d"]
            if ret20 > 0:
                score += min(ret20 / 20.0, 1.0) * FACTOR_WEIGHTS["momentum"]
            count += FACTOR_WEIGHTS["momentum"]

        # 质量因子（ROE）
        if "roe" in stock_data:
            roe = stock_data["roe"]
            if roe > 0:
                score += min(roe / 20.0, 1.0) * FACTOR_WEIGHTS["quality"]
            count += FACTOR_WEIGHTS["quality"]

        # 成长因子
        if "revenue_growth" in stock_data:
            growth = stock_data["revenue_growth"]
            if growth > 0:
                score += min(growth / 50.0, 1.0) * FACTOR_WEIGHTS["growth"]
            count += FACTOR_WEIGHTS["growth"]

        # 估值因子（PE）
        if "pe" in stock_data:
            pe = stock_data["pe"]
            if 0 < pe < 100:
                score += max(1.0 - pe / 100.0, 0) * FACTOR_WEIGHTS["value"]
            count += FACTOR_WEIGHTS["value"]

        # 情绪因子（换手率）
        if "turnover_rate" in stock_data:
            tr = stock_data["turnover_rate"]
            score += min(tr / 10.0, 1.0) * FACTOR_WEIGHTS["sentiment"]
            count += FACTOR_WEIGHTS["sentiment"]

        if count > 0:
            return score / sum(FACTOR_WEIGHTS.values())

        return 0.5  # 默认

    def get_industry_weight(self, industry: str) -> Dict[str, float]:
        """
        获取指定产业对应的因子权重配置
        不同产业侧重不同因子
        """
        return NEWS_WEIGHT_CONFIG.get(industry, FACTOR_WEIGHTS.copy())

    def rank_industries_by_momentum(self) -> List[str]:
        """
        按行业动量排序返回产业列表
        强势行业优先
        """
        rsrs_order = {"strong": 0, "medium": 1, "weak": 2, "negative": 3}
        sorted_industries = sorted(
            self.industry_momentum.items(),
            key=lambda x: (rsrs_order.get(x[1]["rsrs"], 99), x[1]["position"])
        )
        return [ind for ind, _ in sorted_industries]


def build_news_weighted_portfolio(
    industries: List[str],
    gate1_signals: Dict,
    gate2_signals: Dict,
    top_n_per_industry: int = 5,
) -> List[dict]:
    """
    构建基于新闻情绪加权的行业轮动组合

    Args:
        industries: 重点关注产业列表
        gate1_signals: Gate1新闻情感信号
        gate2_signals: Gate2成交量信号
        top_n_per_industry: 每个产业最多持有个股数量

    Returns:
        [{industry, stocks: [{code, name, final_score, ...}]}]
    """
    from quantfly.screener.stock_picker import TopicDrivenScreener

    portfolio = []
    screener = TopicDrivenScreener(enable_risk_filter=True)

    for industry in industries:
        stocks = screener.screen(industry, top_n=top_n_per_industry)

        # 获取Gate1对该产业的评分
        gate1_industry = gate1_signals.get(industry, {})
        gate1_score = gate1_industry.get("avg_news_score", 0.5)

        # 获取Gate2量比
        etf_map = {
            "AI大模型": "159819",
            "半导体": "512760",
            "机器人": "159770",
            "商业航天": "501010",
            "新能源车": "515030",
            "智能电网": "560660",
        }
        etf_code = etf_map.get(industry, "")
        gate2_data = gate2_signals.get(etf_code, {})
        gate2_ratio = gate2_data.get("ratio", 1.0)

        # 计算每只股票的综合分数
        for stock in stocks:
            fp = FactorPortfolio(gate1_signals, gate2_signals)
            final_score = fp.get_combined_score(
                stock_data={},
                gate1_score=gate1_score,
                gate2_ratio=gate2_ratio,
                chips_score=stock.get("chips_score", 0),
                momentum_score=stock.get("momentum_score", 0),
            )
            stock["final_score"] = final_score
            stock["gate1_score"] = gate1_score
            stock["gate2_ratio"] = gate2_ratio

        # 按final_score排序
        stocks.sort(key=lambda x: x["final_score"], reverse=True)
        portfolio.append({
            "industry": industry,
            "gate1_score": gate1_score,
            "gate2_ratio": gate2_ratio,
            "stocks": stocks,
        })

    return portfolio
