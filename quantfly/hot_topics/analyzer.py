# -*- encoding: utf-8 -*-
"""
产业热度分析器 — 根据热点新闻计算产业机会评分
"""
import logging
from typing import Optional

logger = logging.getLogger("HotTopics.Analyzer")

# 核心产业关键词映射
INDUSTRY_KEYWORDS = {
    "AI大模型": ["大模型", "LLM", "ChatGPT", "GPT", "AI", "人工智能"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "光刻"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "具身智能"],
    "脑机接口": ["脑机", "Neuralink", "意念控制"],
    "商业航天": ["航天", "火箭", "卫星", "SpaceX", "商业火箭"],
    "新能源车": ["新能源车", "电动车", "锂电", "电池"],
    "稀土永磁": ["稀土", "永磁", "钕铁硼"],
}


class TopicAnalyzer:
    """产业热度分析"""

    def analyze(self, news_items: list[dict]) -> dict[str, dict]:
        """
        分析新闻，输出各产业热度

        Returns:
            {产业名: {count, top_title, sentiment, opportunity_score, leaders}}
        """
        industry_counts = {ind: 0 for ind in INDUSTRY_KEYWORDS}
        industry_top_news = {ind: "" for ind in INDUSTRY_KEYWORDS}

        for item in news_items:
            title = item.get("title", "")
            topic = item.get("topic", "")
            text = title + " " + topic

            for industry, keywords in INDUSTRY_KEYWORDS.items():
                for kw in keywords:
                    if kw in text:
                        industry_counts[industry] += 1
                        if not industry_top_news[industry]:
                            industry_top_news[industry] = title[:50]
                        break

        # 计算机会评分
        result = {}
        for industry, count in industry_counts.items():
            if count == 0:
                continue
            # 简单评分：新闻数量 * 5 + 基础分
            score = min(count * 5 + 60, 100)
            result[industry] = {
                "count": count,
                "top_title": industry_top_news[industry],
                "sentiment": {"direction": "正面"},
                "opportunity_score": score,
                "leaders": [],
            }

        # 填充"其他"
        if not result:
            result["其他"] = {
                "count": 0,
                "top_title": "",
                "sentiment": {"direction": "未知"},
                "opportunity_score": 0,
                "leaders": [],
            }

        return result
