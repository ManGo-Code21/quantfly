# -*- encoding: utf-8 -*-
"""
双过滤信号系统 — Gate1 × Gate2
===================================
新闻情感(Gate1) × 成交量验证(Gate2) → 最终交易信号

架构：
  Gate1: NewsSentiment — 新闻信号强度
  Gate2: VolumeMonitor — 成交量异常确认

使用方式：
  signal = DualGateSignal()
  signal.set_news_signals(gate1_results)      # 设置Gate1结果
  signal.set_volume_signals(gate2_results)    # 设置Gate2结果
  result = signal.get_signals()                # 获取最终信号
"""
import logging
from typing import Optional

logger = logging.getLogger("HotTopics.DualGate")

# 行业ETF映射（用于成交量验证）
INDUSTRY_TO_ETF = {
    "AI大模型": "159995",    # 人工智能
    "芯片半导体": "512760",
    "机器人": "159770",      # 机器人
    "稀土永磁": "516680",   # 稀土
    "商业航天": "502003",   # 航天
    "低空经济": "835185",   # 无人机
    "新能源车": "515790",   # 光伏（近似）
    "光伏": "515790",
    "生物医药": "512180",
    "5G通信": "515050",
    "云计算": "515980",
    "军工": "512660",
    "消费": "159928",
    "银行": "512800",
    "房地产": "516110",
}


class DualGateSignal:
    """
    双过滤信号系统

    组合 Gate1(新闻) 和 Gate2(成交量) 的信号
    """

    def __init__(
        self,
        gate1_threshold: float = 0.5,
        gate2_threshold: float = 1.3,
        gate2_strong: float = 1.8,
    ):
        """
        Args:
            gate1_threshold: Gate1新闻分数阈值（>阈值才通过）
            gate2_threshold: Gate2成交量比阈值（>阈值才确认）
            gate2_strong: Gate2强确认阈值
        """
        self._gate1: dict = {}   # {industry: gate1_data}
        self._gate2: dict = {}   # {etf_code: gate2_data}
        self._threshold_gate1 = gate1_threshold
        self._threshold_gate2 = gate2_threshold
        self._strong_gate2 = gate2_strong

    def set_gate1(self, news_signals: dict):
        """
        设置Gate1结果

        Args:
            news_signals: {industry: {avg_news_score, avg_sentiment, news_count, gate1_pass}}
        """
        self._gate1 = news_signals

    def set_gate2(self, volume_signals: dict):
        """
        设置Gate2结果

        Args:
            volume_signals: {etf_code: {name, ratio, signal, current_vol, avg_vol}}
        """
        self._gate2 = volume_signals

    def get_signals(self) -> list[dict]:
        """
        获取最终信号

        Returns:
            list of {industry, gate1_score, gate2_ratio, combined, strength, action, reason}
            strength: "strong" / "medium" / "weak" / "normal"
            action: "buy" / "watch" / "skip"
        """
        results = []

        for industry, g1 in self._gate1.items():
            # 找对应的ETF代码
            etf = INDUSTRY_TO_ETF.get(industry, "")
            g2 = self._gate2.get(etf, {})

            gate1_score = g1.get("avg_news_score", 0)
            gate2_ratio = g2.get("ratio", 1.0)
            gate1_pass = g1.get("gate1_pass", False)

            # Gate1通过但Gate2未确认 → 弱信号
            # Gate1通过且Gate2强确认 → 强信号
            # Gate1未通过 → 跳过或观察

            if not gate1_pass:
                continue  # Gate1未通过，直接跳过

            # 综合分数计算
            # combined = gate1_score × sqrt(gate2_ratio)
            # 用sqrt是因为量比可能很大(3x, 5x)，不要过度放大
            import math
            combined = gate1_score * math.sqrt(gate2_ratio)

            # 信号强度
            if combined >= 1.2 and gate2_ratio >= self._strong_gate2:
                strength = "strong"
                action = "buy"
                reason = f"新闻确认({gate1_score:.2f}) + 量能放大({gate2_ratio:.1f}x)"
            elif combined >= 0.8 and gate2_ratio >= self._threshold_gate2:
                strength = "medium"
                action = "buy"
                reason = f"新闻确认({gate1_score:.2f}) + 量能支撑({gate2_ratio:.1f}x)"
            elif gate2_ratio >= self._threshold_gate2:
                strength = "weak"
                action = "watch"
                reason = f"量能放大({gate2_ratio:.1f}x)但新闻信号弱({gate1_score:.2f})"
            else:
                strength = "normal"
                action = "watch"
                reason = f"新闻确认({gate1_score:.2f})，等待量能确认"

            results.append({
                "industry": industry,
                "etf": etf,
                "gate1_score": round(gate1_score, 3),
                "gate2_ratio": round(gate2_ratio, 2),
                "combined": round(combined, 3),
                "strength": strength,
                "action": action,
                "reason": reason,
                "news_count": g1.get("news_count", 0),
                "sentiment": g1.get("avg_sentiment", 0),
            })

        # 按combined分数排序
        results.sort(key=lambda x: x["combined"], reverse=True)
        return results

    def get_summary(self) -> dict:
        """获取信号摘要"""
        signals = self.get_signals()
        strong = [s for s in signals if s["strength"] == "strong"]
        medium = [s for s in signals if s["strength"] == "medium"]
        weak = [s for s in signals if s["strength"] == "weak"]

        return {
            "total_signals": len(signals),
            "strong": len(strong),
            "medium": len(medium),
            "weak": len(weak),
            "top_signals": signals[:5],
            "recommendations": [s for s in signals if s["action"] == "buy"][:3],
        }


# ============================================================
# 简化版：仅用Gate1（用于没有成交量的场景）
# ============================================================

class NewsSignalOnly:
    """
    仅用Gate1新闻信号（不需要成交量数据时使用）
    """

    def __init__(self, threshold: float = 0.6):
        self._threshold = threshold

    def evaluate(self, news_signals: dict) -> list[dict]:
        """
        评估新闻信号

        Args:
            news_signals: {industry: {avg_news_score, avg_sentiment, news_count, gate1_pass}}

        Returns:
            list of {industry, score, sentiment, action}
        """
        results = []
        for industry, data in news_signals.items():
            score = data.get("avg_news_score", 0)
            sentiment = data.get("avg_sentiment", 0)

            if score >= self._threshold:
                action = "buy"
            elif score >= 0.4:
                action = "watch"
            else:
                action = "skip"

            results.append({
                "industry": industry,
                "score": round(score, 3),
                "sentiment": round(sentiment, 3),
                "news_count": data.get("news_count", 0),
                "action": action,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
