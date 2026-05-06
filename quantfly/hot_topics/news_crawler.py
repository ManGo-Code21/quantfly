# -*- encoding: utf-8 -*-
"""
并发新闻采集器 — AsyncNewsCrawler
===================================
真正的asyncio并发，同时请求所有新闻源：
  1. 国内：akshare (CCTV新闻)
  2. 国际：Reuters, BBC, NPR, TechCrunch, Bloomberg, FT, WSJ等

使用方式：
  from quantfly.hot_topics.news_crawler import AsyncNewsCrawler
  crawler = AsyncNewsCrawler()
  news = await crawler.fetch_all()
  # 或同步调用：
  news = crawler.fetch_all_sync()
"""
import asyncio
import feedparser
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger("NewsCrawler")

# ============================================================
# 新闻源配置（RSS feed列表）
# ============================================================
NEWS_SOURCES = {
    # ---------- 综合/通讯社 ----------
    "Reuters": {
        "feed": "https://feeds.reuters.com/reuters/businessNews",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "OpenAI", "LLM", "machine learning"],
            "半导体": ["semiconductor", "chip", "NVIDIA", "TSMC", "AMD", "Intel"],
            "机器人": ["robot", "automation", "Boston Dynamics", "humanoid"],
            "商业航天": ["space", "rocket", "SpaceX", "satellite", "Starlink"],
            "新能源车": ["electric vehicle", "Tesla", "EV", "battery", "BYD"],
            "智能电网": ["smart grid", "renewable energy", "solar", "wind power"],
        },
    },
    "BBC_News": {
        "feed": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "technology"],
            "半导体": ["technology", "semiconductor", "chip"],
            "机器人": ["robot", "technology"],
            "商业航天": ["space", "technology"],
            "新能源车": ["electric vehicle", "climate", "energy"],
            "智能电网": ["energy", "climate", "renewable"],
        },
    },
    "NPR": {
        "feed": "https://feeds.npr.org/1019/rss.xml",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "technology"],
            "半导体": ["technology", "semiconductor"],
            "新能源车": ["electric vehicle", "energy", "climate"],
            "智能电网": ["energy", "climate", "grid"],
        },
    },
    "AP_News": {
        "feed": "https://rsshub.app/apnews/articles/CDIC0IFIN5",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "tech"],
            "半导体": ["chip", "semiconductor", "technology"],
            "商业航天": ["space", "technology"],
            "新能源车": ["electric vehicle", "Tesla", "automotive"],
        },
    },

    # ---------- 科技媒体 ----------
    "TechCrunch": {
        "feed": "https://techcrunch.com/feed/",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "startup", "funding", "OpenAI", "Anthropic"],
            "机器人": ["robot", "robotics", "startup", "funding"],
            "商业航天": ["space", "startup", "funding"],
            "新能源车": ["electric vehicle", "EV", "startup", "funding", "Tesla"],
        },
    },
    "The_Verge": {
        "feed": "https://www.theverge.com/rss/index.xml",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "OpenAI", "Google", "Microsoft"],
            "机器人": ["robot", "AI", "technology"],
            "半导体": ["chip", "processor", "Apple", "Intel", "AMD"],
            "新能源车": ["electric vehicle", "Tesla", "car"],
        },
    },
    "Wired": {
        "feed": "https://www.wired.com/feed/rss",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence", "machine learning"],
            "半导体": ["semiconductor", "chip", "technology"],
            "机器人": ["robot", "automation", "AI"],
            "智能电网": ["energy", "climate", "grid"],
        },
    },
    "Engadget": {
        "feed": "https://www.engadget.com/rss.xml",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["AI", "artificial intelligence"],
            "半导体": ["chip", "processor", "hardware"],
            "新能源车": ["electric vehicle", "car", "tech"],
        },
    },

    # ---------- 财经/产业 ----------
    "Bloomberg": {
        "feed": "https://feeds.bloomberg.com/markets/news.rss",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["artificial intelligence", "AI", "technology", "markets"],
            "半导体": ["semiconductor", "chip", "technology", "markets"],
            "机器人": ["robot", "automation", "manufacturing"],
            "商业航天": ["space", "satellite", "aerospace"],
            "新能源车": ["electric vehicle", "Tesla", "energy", "markets"],
            "智能电网": ["smart grid", "renewable", "energy transition"],
            "稀土永磁": ["rare earth", "commodity", "mining"],
            "量子计算": ["quantum", "computing", "technology"],
        },
    },
    "WSJ": {
        "feed": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["artificial intelligence", "AI", "technology"],
            "半导体": ["semiconductor", "chip", "technology"],
            "新能源车": ["electric vehicle", "automotive", "Tesla"],
            "智能电网": ["energy", "renewable", "utility"],
        },
    },
    "FT": {
        "feed": "https://www.ft.com/?format=rss",
        "lang": "en",
        "industry_keywords": {
            "AI大模型": ["artificial intelligence", "AI", "China tech"],
            "半导体": ["semiconductor", "chip", "China", "technology"],
            "机器人": ["robot", "automation", "manufacturing"],
            "商业航天": ["space", "satellite", "aerospace"],
            "新能源车": ["electric vehicle", "China", "energy"],
            "智能电网": ["energy", "renewable", "China"],
        },
    },
    "IndustryWeek": {
        "feed": "https://www.industryweek.com/rss/sections/technology/rss.xml",
        "lang": "en",
        "industry_keywords": {
            "机器人": ["robot", "automation", "manufacturing", "AI"],
            "智能电网": ["smart grid", "manufacturing", "industrial"],
            "半导体": ["semiconductor", "manufacturing", "industry"],
        },
    },
}

# ============================================================
# 缓存
# ============================================================
_CONTENT_CACHE = {}   # {url_md5: (timestamp, item)}
_CACHE_TTL = 300     # 5分钟

# ============================================================
# 核心爬虫
# ============================================================
class AsyncNewsCrawler:
    """
    真正的asyncio并发新闻采集器

    同时并发请求所有RSS源，超时隔离，失败不影响整体。
    支持增量更新（相同URL 5分钟内不重复处理）。

    速度：10个源并发，总耗时 = 最慢的1个源（而非累加）
    """

    def __init__(self, timeout: int = 8, max_concurrent: int = 15):
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self._session = None

    # ----------------------------------------------------------
    # 异步主入口
    # ----------------------------------------------------------
    async def fetch_all(
        self,
        max_age_minutes: int = 120,
        target_industries: Optional[list] = None,
    ) -> list[dict]:
        """
        并发抓取所有RSS源

        Args:
            max_age_minutes: 只返回最近N分钟内的新闻
            target_industries: 只抓取特定产业相关（None=全部）

        Returns:
            [{source, title, content, url, timestamp, industries}, ...]
        """
        # 创建信号量控制并发数
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def bounded_fetch(source_name: str, cfg: dict) -> list:
            async with semaphore:
                return await self._fetch_rss(source_name, cfg, max_age_minutes)

        tasks = [
            bounded_fetch(name, cfg)
            for name, cfg in NEWS_SOURCES.items()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_news = []
        seen_urls = set()

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Source failed: {result}")
                continue
            for item in result:
                url = item.get("url", "")
                if not url:
                    continue
                # 去重
                url_md5 = hashlib.md5(url.encode()).hexdigest()
                if url_md5 in seen_urls:
                    continue
                seen_urls.add(url_md5)

                # 产业过滤
                if target_industries:
                    industries = item.get("industries", [])
                    if not any(ind in industries for ind in target_industries):
                        continue

                all_news.append(item)

        # 按时间排序
        all_news.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        logger.info(
            f"[NewsCrawler] 抓取 {len(all_news)} 条新闻 "
            f"({len(seen_urls)} 去重) / {len(NEWS_SOURCES)} 个源并发"
        )
        return all_news

    # ----------------------------------------------------------
    # 单源RSS抓取（异步 + 超时）
    # ----------------------------------------------------------
    async def _fetch_rss(
        self,
        source_name: str,
        cfg: dict,
        max_age_minutes: int,
    ) -> list[dict]:
        """抓取单个RSS源"""
        loop = asyncio.get_event_loop()
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)

        try:
            # 在线程池中执行同步HTTP请求
            items = await loop.run_in_executor(
                None, self._parse_rss, source_name, cfg
            )

            results = []
            for item in items:
                try:
                    ts_str = item.get("timestamp", "")
                    if ts_str:
                        if isinstance(ts_str, str) and len(ts_str) >= 10:
                            news_time = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                            if news_time < cutoff:
                                continue

                    results.append(item)
                except Exception:
                    results.append(item)

            return results

        except Exception as e:
            logger.warning(f"[{source_name}] 抓取失败: {e}")
            return []

    def _parse_rss(self, source_name: str, cfg: dict) -> list[dict]:
        """同步解析RSS（在线程池中运行）"""
        feed_url = cfg["feed"]
        lang = cfg.get("lang", "en")
        industry_kws = cfg.get("industry_keywords", {})

        try:
            resp = requests.get(
                feed_url,
                timeout=self.timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; QuantFly/1.0)",
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                },
            )
            if resp.status_code != 200:
                return []

            feed = feedparser.parse(resp.content)
            if not feed.entries:
                return []

            results = []
            for entry in feed.entries[:20]:  # 每源最多20条
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                if not summary:
                    summary = getattr(entry, "description", "") or ""

                # 清洗HTML
                import re
                content = re.sub(r"<[^>]+>", "", summary)[:300]

                url = getattr(entry, "link", "") or ""
                published = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""

                # 识别相关产业
                text = (title + " " + content).lower()
                matched_industries = []
                for industry, keywords in industry_kws.items():
                    if any(kw.lower() in text for kw in keywords):
                        matched_industries.append(industry)

                # URL缓存去重
                url_md5 = hashlib.md5(url.encode()).hexdigest()
                if url_md5 in _CONTENT_CACHE:
                    cached_time, _ = _CONTENT_CACHE[url_md5]
                    if time.time() - cached_time < _CACHE_TTL:
                        continue  # 5分钟内不重复处理

                _CONTENT_CACHE[url_md5] = (time.time(), entry)

                results.append({
                    "source": source_name,
                    "title": title.strip(),
                    "content": content.strip(),
                    "url": url,
                    "timestamp": published,
                    "industries": matched_industries,
                    "lang": lang,
                })

            return results

        except Exception as e:
            logger.warning(f"[{source_name}] RSS解析失败: {e}")
            return []

    # ----------------------------------------------------------
    # 同步封装（用于非async环境）
    # ----------------------------------------------------------
    def fetch_all_sync(
        self,
        max_age_minutes: int = 120,
        target_industries: Optional[list] = None,
    ) -> list[dict]:
        """同步入口"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有事件循环中，用线程池
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.fetch_all(max_age_minutes, target_industries)
                    )
                    return future.result(timeout=60)
            else:
                return asyncio.run(
                    self.fetch_all(max_age_minutes, target_industries)
                )
        except Exception as e:
            logger.error(f"异步采集失败: {e}")
            return []

    # ----------------------------------------------------------
    # 按产业过滤新闻（用于Gate1）
    # ----------------------------------------------------------
    def get_news_by_industry(
        self,
        industry: str,
        max_age_minutes: int = 120,
    ) -> list[dict]:
        """只抓取指定产业的新闻"""
        return self.fetch_all_sync(max_age_minutes, target_industries=[industry])


# ============================================================
# 简单同步包装（替换monitor.py中的东方财富新闻）
# ============================================================
class SimpleNewsSource:
    """同步简单入口，兼容现有monitor.py"""

    def __init__(self):
        self.crawler = AsyncNewsCrawler(timeout=8, max_concurrent=15)

    def fetch(self) -> list[dict]:
        """
        返回格式兼容monitor.py:
        [{source, title, content, code, topic, timestamp, url}, ...]
        """
        news_list = self.crawler.fetch_all_sync(max_age_minutes=120)
        result = []
        for item in news_list:
            industries = item.get("industries", [])
            result.append({
                "source": item.get("source", "rss"),
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "code": "",
                "topic": industries[0] if industries else "其他",
                "timestamp": item.get("timestamp", ""),
                "url": item.get("url", ""),
            })
        return result
