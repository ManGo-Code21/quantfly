# -*- encoding: utf-8 -*-
"""
新闻情感分析 — Gate1 模块
============================
两层过滤架构：
  Gate1: 新闻情感 + 行业识别 → 新闻信号强度
  Gate2: 成交量验证 → 确认信号

核心逻辑：
1. 规则快速过滤（高精度，低成本）
2. 规则判断不了的 → MiniMax LLM（少量调用）
3. 最终输出 news_score ∈ [0, 1]
"""
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("HotTopics.NewsSentiment")

# ============================================================
# 行业关键词映射
# ============================================================

INDUSTRY_KEYWORDS = {
    "AI大模型": ["ai", "人工智能", "大模型", "llm", "gpt", "chatgpt", "openai", "deepseek", "通义", "文心", "kimi", "月之暗面"],
    "芯片半导体": ["芯片", "半导体", "集成电路", "晶圆", "光刻", "封装", "代工", "fab", " wafer", "asml", "台积电", "英伟达", "华为芯片"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "机器臂", "自动化", "减速器", "伺服电机"],
    "稀土永磁": ["稀土", "永磁", "钕铁硼", "氧化镨", "氧化钕", "氟化锂", "锂矿"],
    "商业航天": ["商业航天", "卫星", "火箭", "航天", "北斗", "星链", "spacex"],
    "低空经济": ["低空经济", "无人机", "evtol", "飞行汽车", "eVTOL"],
    "新能源车": ["新能源车", "电动车", "锂电", "电池", "宁德时代", "比亚迪", "特斯拉"],
    "光伏": ["光伏", "太阳能", "硅料", "组件", "逆变器", "隆基绿能", "通威股份"],
    "生物医药": ["生物医药", "创新药", "医疗器械", "疫苗", "CXO", "药明康德", "恒瑞医药"],
    "5G通信": ["5g", "通信", "基站", "光模块", "光纤", "华为中兴"],
    "云计算": ["云计算", "数据中心", "服务器", "IDC", "算力"],
    "军工": ["军工", "国防", "导弹", "军机", "舰船", "航发"],
    "消费": ["消费", "食品", "白酒", "家电", "纺织", "零售"],
    "银行": ["银行", "金融", "保险", "券商"],
    "房地产": ["房地产", "地产", "建筑", "建材", "家居"],
}


# ============================================================
# 影响力 + 情感规则引擎
# ============================================================

# 政策关键词（高影响力）
POLICY_PATTERNS = [
    re.compile(r"国务院|中共中央|全国人大|央行|发改委|工信部|财政部|商务部|证监会|银保监会|科技部"),
    re.compile(r"国务院常务会议|中央政治局|中央经济工作会议"),
    re.compile(r"政策支持|政策利好|政策出台|顶层设计|产业政策"),
]

# 财报/业绩关键词
EARNINGS_PATTERNS = [
    re.compile(r"\d{4}年[一二三四]季度?(?:营收|收入|净利润|业绩|归母)"),
    re.compile(r"(业绩增长|业绩下滑|超预期|不及预期|扭亏|首亏)"),
    re.compile(r"(?:同比增长|环比增长|同比下降|环比下降)\s*[\d\.]+%?"),
]

# 公告关键词
ANNOUNCEMENT_PATTERNS = [
    re.compile(r"重大合同|战略合作|并购|重组|分拆|IPO"),
    re.compile(r"(收到|发布|签署)\s*[\《\"'](?:合同|协议|合作|订单)[\"\'》]"),
]

# 制裁/限制（负面，排除类）
NEGATIVE_PATTERNS = [
    re.compile(r"(?:美国|欧盟|英国|日本)[^\\n]{0,30}(?:制裁|限制|禁运|封锁|实体清单|列入黑名单)"),
    re.compile(r"(?:调查|处罚|立案|违规|警示函|监管措施)"),
    re.compile(r"(?:业绩造假|财务造假|欺诈|内幕交易)"),
]

# 行业中性词
NEUTRAL_PATTERNS = [
    re.compile(r"(买入|增持|中性|减持|卖出)评级"),
    re.compile(r"(券商|研报|机构|基金)[\[\]（）\(\)a-zA-Z]*(看|观点|认为|表示|预计)"),
]


def detect_industry(text: str) -> list[str]:
    """
    检测新闻涉及的行业

    Returns:
        list of matched industry names
    """
    text_lower = text.lower()
    matched = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.append(industry)
                break
    return matched


def get_news_impact(text: str) -> float:
    """
    评估新闻影响力（规则快速判断）

    Returns:
        impact ∈ [0.5, 1.5]
    """
    text_lower = text.lower()

    # 高影响力
    for p in POLICY_PATTERNS:
        if p.search(text):
            return 1.5

    # 中影响力
    for p in EARNINGS_PATTERNS:
        if p.search(text):
            return 1.2

    for p in ANNOUNCEMENT_PATTERNS:
        if p.search(text):
            return 1.2

    # 低影响力
    for p in NEUTRAL_PATTERNS:
        if p.search(text):
            return 0.5

    # 常规资讯
    return 0.8


def get_sentiment_rule(text: str) -> Optional[float]:
    """
    规则判断情感，仅处理明显案例

    Returns:
        sentiment ∈ [-1, 1] 或 None（规则无法判断）
    """
    text_lower = text.lower()

    # 负面：规则判断
    for p in NEGATIVE_PATTERNS:
        if p.search(text):
            return -1.0

    # 正面：明确利好词
    positive_words = ["暴涨", "涨停", "大涨", "突破", "创新高", "大超预期", "强劲增长", "重磅", "史上最大", "紧急", "突发利好", "利好", "政策支持", "受益", "超预期", "史上最高", "订单爆发"]
    for w in positive_words:
        if w in text:
            return 1.0

    # 负面：明确利差词
    negative_words = ["暴跌", "跌停", "破位", "创新低", "大幅下滑", "亏损", "造假", "处罚", "制裁"]
    for w in negative_words:
        if w in text:
            return -1.0

    # 无法规则判断
    return None


# ============================================================
# MiniMax LLM 情感判断（用于规则无法判断的情况）
# ============================================================

_minimax_client = None


def _get_minimax_client():
    """获取MiniMax客户端（延迟初始化）"""
    global _minimax_client
    if _minimax_client is None:
        try:
            from openai import OpenAI
            _minimax_client = OpenAI(
                api_key=os.environ.get("MINIMAX_API_KEY", ""),
                base_url="https://api.minimax.chat/v1",
            )
        except Exception as e:
            logger.warning(f"MiniMax客户端初始化失败: {e}")
            return None
    return _minimax_client


def get_sentiment_llm(text: str, industry: str) -> float:
    """
    用MiniMax LLM判断情感（兜底方案）

    Args:
        text: 新闻文本（截取前500字）
        industry: 目标行业

    Returns:
        sentiment ∈ [-1, 1]
    """
    client = _get_minimax_client()
    if client is None:
        return 0.0  # 无法判断时返回中性

    prompt = f"""你是一个A股行业新闻情感分析专家。

新闻文本：{text[:500]}

目标行业：{industry}

请判断这条新闻对目标行业的短期影响（1-3天内）：
- 如果是明确利好（如政策支持、业绩大涨、重大合作）：回答 +1
- 如果是明确利空（如政策打压、业绩大跌、风险事件）：回答 -1
- 如果是中性或无法判断：回答 0

只输出一个数字（-1, 0, 或 +1），不要解释。"""

    try:
        response = client.chat.completions.create(
            model="MiniMax-M2.7",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0,
        )
        result = response.choices[0].message.content.strip()
        # 解析数字
        if result in ["+1", "1", "＋1", "正1"]:
            return 1.0
        elif result in ["-1", "－1", "负1"]:
            return -1.0
        else:
            return 0.0
    except Exception as e:
        logger.warning(f"MiniMax情感判断失败: {e}")
        return 0.0


# ============================================================
# 主分析函数
# ============================================================

def analyze_news(text: str, use_llm_fallback: bool = True) -> dict:
    """
    分析单条新闻的情感和行业

    Returns:
        dict with keys:
            - industries: list of detected industries
            - impact: impact score ∈ [0.5, 1.5]
            - sentiment: sentiment ∈ [-1, 1]
            - sentiment_source: "rule" or "llm"
            - news_score: combined score ∈ [0, 1]
            - gate1_pass: bool (是否通过Gate1)
    """
    if not text or len(text) < 10:
        return {
            "industries": [],
            "impact": 0.0,
            "sentiment": 0.0,
            "sentiment_source": "none",
            "news_score": 0.0,
            "gate1_pass": False,
        }

    # 行业检测
    industries = detect_industry(text)
    if not industries:
        industries = ["其他"]

    # 影响力
    impact = get_news_impact(text)

    # 情感判断
    sentiment = get_sentiment_rule(text)
    sentiment_source = "rule"

    if sentiment is None and use_llm_fallback:
        # 规则无法判断，用LLM
        sentiment = get_sentiment_llm(text, industries[0])
        sentiment_source = "llm"

    if sentiment is None:
        sentiment = 0.0
        sentiment_source = "neutral"

    # 综合分数 = 影响力 × (1 + 情感) / 2
    # impact ∈ [0.5, 1.5], sentiment ∈ [-1, 1]
    # news_score ∈ [0, 1]
    news_score = impact * (1 + sentiment) / 2

    # Gate1通过条件：news_score > 0.5
    gate1_pass = news_score > 0.5

    return {
        "industries": industries,
        "impact": impact,
        "sentiment": sentiment,
        "sentiment_source": sentiment_source,
        "news_score": round(news_score, 3),
        "gate1_pass": gate1_pass,
    }


def analyze_news_batch(items: list[dict], use_llm_fallback: bool = True) -> list[dict]:
    """
    批量分析新闻

    Args:
        items: list of {title, content, source, time}

    Returns:
        list of analysis results (添加了 analysis 字段)
    """
    results = []
    llm_count = 0

    for item in items:
        text = f"{item.get('title', '')} {item.get('content', '')}"
        analysis = analyze_news(text, use_llm_fallback=use_llm_fallback)

        if analysis["sentiment_source"] == "llm":
            llm_count += 1

        results.append({
            **item,
            "analysis": analysis,
        })

    logger.info(f"批量分析 {len(items)} 条新闻，LLM调用 {llm_count} 次")
    return results


# ============================================================
# Gate1 信号聚合（时间窗口内）
# ============================================================

from collections import defaultdict


def aggregate_industry_signals(news_items: list[dict], time_window_hours: int = 4) -> dict:
    """
    按行业聚合新闻信号

    Args:
        news_items: 批量分析结果
        time_window_hours: 时间窗口（小时）

    Returns:
        dict: {industry: {news_score, count, sentiment_sum, signals}}
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    window_start = now - timedelta(hours=time_window_hours)

    industry_signals = defaultdict(lambda: {
        "scores": [],
        "sentiments": [],
        "signals": [],
    })

    for item in news_items:
        analysis = item.get("analysis", {})
        if not analysis.get("gate1_pass"):
            continue

        item_time_str = item.get("time", "")
        # 简单时间过滤（如果有时间字段的话）
        # 这里简化处理，默认都在窗口内

        for industry in analysis.get("industries", []):
            industry_signals[industry]["scores"].append(analysis["news_score"])
            industry_signals[industry]["sentiments"].append(analysis["sentiment"])
            industry_signals[industry]["signals"].append(item)

    # 汇总
    result = {}
    for industry, data in industry_signals.items():
        avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        avg_sentiment = sum(data["sentiments"]) / len(data["sentiments"]) if data["sentiments"] else 0
        result[industry] = {
            "avg_news_score": round(avg_score, 3),
            "avg_sentiment": round(avg_sentiment, 3),
            "news_count": len(data["scores"]),
            "signals": data["signals"],
            "gate1_pass": avg_score > 0.5,
        }

    return result


import os
