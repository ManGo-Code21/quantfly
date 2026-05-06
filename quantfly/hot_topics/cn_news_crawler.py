# -*- encoding: utf-8 -*-
"""
中文财经新闻采集器 — CNNewsCrawler
===================================
覆盖A股相关中文媒体，比英文RSS更快、更精准。

数据源（优先级从高到低）：
  1. akshare.news_cctv()          — 央视新闻，政策信号强
  2. akshare.stock_news_main_cx() — 财新快讯，实时财经
  3. akshare.stock_news_em()      — 同花顺，按行业关键词

行业关键词映射（用于过滤非A股相关内容）：
  - AI大模型: AI、人工智能、大模型、ChatGPT、LLM、DeepSeek
  - 半导体: 半导体、芯片、光刻、晶圆、GPU、CPU
  - 新能源车: 电动车、锂电池、固态电池、比亚迪、特斯拉
  - 机器人: 机器人、人形机器人、工业自动化
  - 商业航天: 卫星、火箭、SpaceX、星舰
  - 智能电网: 储能、特高压、虚拟电厂

使用方式：
  from quantfly.hot_topics.cn_news_crawler import CNNewsCrawler
  crawler = CNNewsCrawler()
  news = crawler.fetch_all(max_age_minutes=120)

  # 集成到现有pipeline：
  from quantfly.hot_topics.news_processor import NewsPipeline
  pipeline = NewsPipeline(...)
  pipeline.cn_crawler = CNNewsCrawler()   # 自动包含中文
"""
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
import requests

logger = logging.getLogger("CNNewsCrawler")

# ============================================================
# 行业关键词（中文，用于过滤和分类）
# ============================================================
INDUSTRY_KEYWORDS = {
    "AI大模型": ["AI", "人工智能", "大模型", "ChatGPT", "LLM", "DeepSeek", "通义千问", "文心一言", "Kimi", "智谱", "字节AI", "OpenAI"],
    "半导体": ["半导体", "芯片", "光刻", "晶圆", "GPU", "CPU", "先进封装", "HBM", "AI芯片", "英伟达", "AMD", "英特尔", "台积电", "中芯国际", "华为芯片"],
    "新能源车": ["电动车", "锂电池", "固态电池", "比亚迪", "特斯拉", "理想汽车", "蔚来汽车", "小鹏汽车", "新能源乘用车", "动力电池"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "协作机器人", "自动化", "具身智能"],
    "商业航天": ["卫星", "火箭", "SpaceX", "星舰", "商业航天", "遥感卫星", "北斗"],
    "智能电网": ["储能", "特高压", "虚拟电厂", "智能电网", "新能源发电", "光伏", "风电", "海上风电"],
    "稀土永磁": ["稀土", "永磁", "钕铁硼", "镨钕", "有色金属"],
    "量子计算": ["量子计算", "量子通信", "量子芯片"],
}

# 同花顺行业板块关键词
THS_KEYWORDS = [
    "AI", "半导体", "机器人", "新能源汽车", "锂电池",
    "商业航天", "智能电网", "稀土", "量子科技"
]

# ============================================================
# 工具函数
# ============================================================

def _strip_html(text: str) -> str:
    """去除HTML标签"""
    text = re.sub(r'<[^>]+>', '', str(text))
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    return text.strip()


def _match_industries(text: str) -> list[str]:
    """根据文本内容匹配行业"""
    text_lower = text.lower()
    matched = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower or kw in text:
                matched.append(industry)
                break
    return matched if matched else ["其他"]


def _normalize_cctv(raw: pd.DataFrame) -> list[dict]:
    """规范化央视新闻"""
    results = []
    for _, row in raw.iterrows():
        title = str(row.get("title", ""))
        content = _strip_html(str(row.get("content", "")))[:300]
        text = title + " " + content
        industries = _match_industries(text)
        results.append({
            "source": "CCTV",
            "title": title,
            "content": content,
            "url": "",
            "timestamp": str(row.get("date", "")),
            "industries": industries,
            "lang": "zh",
            "is_cn": True,
        })
    return results


def _normalize_cx(raw: pd.DataFrame) -> list[dict]:
    """规范化财新快讯（财新无关键词，用内容自动匹配行业）"""
    results = []
    for _, row in raw.iterrows():
        summary = _strip_html(str(row.get("summary", "")))[:300]
        url = str(row.get("url", ""))
        tag = str(row.get("tag", ""))
        # 财新用tag + 内容双重匹配行业
        text = summary + " " + tag
        industries = _match_industries(text)
        # 财新tag本身也有行业信号
        tag_industry_map = {
            "市场动态": "其他",
            "行业速递": None,  # 内容决定
            "公司": None,
            "宏观": "其他",
            "数据": "其他",
            "政策": "其他",
        }
        if not industries or industries == ["其他"]:
            # 兜底：用内容再匹配一次（宽松模式）
            industries = _match_industries(summary)
        results.append({
            "source": "Caixin",
            "title": tag,
            "content": summary,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "industries": industries if industries else ["其他"],
            "lang": "zh",
            "is_cn": True,
        })
    return results


def _normalize_ths(raw: pd.DataFrame, industry: str) -> list[dict]:
    """规范化同花顺行业新闻"""
    results = []
    for _, row in raw.iterrows():
        title = str(row.get("新闻标题", ""))
        content = _strip_html(str(row.get("新闻内容", "")))[:300]
        source = str(row.get("文章来源", ""))
        pub_time = str(row.get("发布时间", ""))
        link = str(row.get("新闻链接", ""))
        text = title + " " + content
        industries = _match_industries(text)
        if not industries or industries == ["其他"]:
            industries = [industry]  # 用查询关键词兜底
        results.append({
            "source": f"THS-{source}",
            "title": title,
            "content": content,
            "url": link,
            "timestamp": pub_time,
            "industries": industries,
            "lang": "zh",
            "is_cn": True,
        })
    return results


# ============================================================
# 核心爬虫
# ============================================================
class CNNewsCrawler:
    """
    中文财经新闻采集器

    并发请求所有中文数据源，按行业关键词过滤A股相关内容。
    速度：~5秒（并发请求）
    """

    def __init__(self, timeout: int = 10, max_concurrent: int = 8):
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self._session = None

    def fetch_all(self, max_age_minutes: int = 120) -> list[dict]:
        """
        采集所有中文财经新闻

        Args:
            max_age_minutes: 只返回最近N分钟内的新闻（对CCTV无效，因其按天）

        Returns:
            [{source, title, content, url, timestamp, industries, lang, is_cn}, ...]
        """
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        all_news = []
        seen_urls = set()

        # 1. 央视新闻（最权威，但日期粒度粗）
        try:
            df_cctv = ak.news_cctv()
            for item in _normalize_cctv(df_cctv):
                url = item.get("url", "") or f"cctv_{item['timestamp']}"
                url_md5 = hashlib.md5(url.encode()).hexdigest()
                if url_md5 in seen_urls:
                    continue
                seen_urls.add(url_md5)
                all_news.append(item)
            logger.info(f"[CNNews] CCTV: {len(all_news)} 条")
        except Exception as e:
            logger.warning(f"[CNNews] CCTV采集失败: {e}")

        # 2. 财新快讯（最快）
        try:
            df_cx = ak.stock_news_main_cx()
            for item in _normalize_cx(df_cx):
                url = item.get("url", "")
                if not url or url == "nan":
                    continue
                url_md5 = hashlib.md5(url.encode()).hexdigest()
                if url_md5 in seen_urls:
                    continue
                seen_urls.add(url_md5)
                # 财新是实时快讯，不过滤时间
                all_news.append(item)
            logger.info(f"[CNNews] 财新: {len(all_news)} 条")
        except Exception as e:
            logger.warning(f"[CNNews] 财新采集失败: {e}")

        # 3. 同花顺行业新闻（并发，按行业关键词）
        ths_industries = THS_KEYWORDS
        with ThreadPoolExecutor(max_workers=min(self.max_concurrent, len(ths_industries))) as executor:
            futures = {
                executor.submit(self._fetch_ths_industry, ind): ind
                for ind in ths_industries
            }
            for future in futures:
                industry = futures[future]
                try:
                    items = future.result(timeout=self.timeout)
                    for item in items:
                        url = item.get("url", "")
                        if not url or url == "nan":
                            continue
                        url_md5 = hashlib.md5(url.encode()).hexdigest()
                        if url_md5 in seen_urls:
                            continue
                        seen_urls.add(url_md5)
                        # 过滤时间
                        try:
                            ts_str = item.get("timestamp", "")
                            if ts_str:
                                news_time = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                                if news_time < cutoff:
                                    continue
                        except (ValueError, TypeError):
                            pass
                        all_news.append(item)
                except Exception as e:
                    logger.warning(f"[CNNews] 同花顺-{industry}采集失败: {e}")

        # 按时间排序（新的在前）
        all_news.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # 去重摘要
        cn_count = sum(1 for n in all_news if n.get("is_cn"))
        logger.info(f"[CNNews] 共 {len(all_news)} 条（中文 {cn_count}） / {len(seen_urls)} 去重")
        return all_news

    def _fetch_ths_industry(self, industry: str) -> list[dict]:
        """采集单个行业的同花顺新闻"""
        try:
            df = ak.stock_news_em(symbol=industry)
            if df is None or len(df) == 0:
                return []
            return _normalize_ths(df, industry)
        except Exception as e:
            logger.warning(f"[CNNews] THS-{industry}失败: {e}")
            return []

    def fetch_by_industry(self, industry: str, max_age_minutes: int = 120) -> list[dict]:
        """只采集指定行业的新闻"""
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        all_news = self.fetch_all(max_age_minutes)
        return [
            n for n in all_news
            if industry in n.get("industries", [])
        ]

    def get_industry_summary(self, max_age_minutes: int = 120) -> dict[str, list[dict]]:
        """
        按行业分组返回新闻（用于Gate1行业情绪分析）

        Returns:
            {
                "AI大模型": [news_item, ...],
                "半导体": [...],
                ...
            }
        """
        all_news = self.fetch_all(max_age_minutes)
        summary = {}
        for news in all_news:
            for industry in news.get("industries", []):
                if industry not in summary:
                    summary[industry] = []
                summary[industry].append(news)
        return summary
