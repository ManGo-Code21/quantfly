# -*- encoding: utf-8 -*-
"""
新闻处理器 — NewsPipeline
===========================
三级漏斗架构：
  ① ClusterDedup  — 并发采集后，聚类去重（TF-IDF余弦相似度）
  ② BreakingNews  — 突发识别：关键词 + 首报检测
  ③ LLMAnalyzer   — 只对优先新闻做LLM分析（节省token）
  ④ VolumeVerifier— 分钟级成交量验证

使用方式：
  pipeline = NewsPipeline()
  signals = await pipeline.run()
"""
import asyncio
import hashlib
import logging
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from quantfly.hot_topics.news_crawler import AsyncNewsCrawler
from quantfly.hot_topics.signal_store import SignalStore

logger = logging.getLogger("NewsPipeline")


# ============================================================
# 数据结构
# ============================================================
@dataclass
class NewsItem:
    source: str
    title: str
    content: str
    url: str
    timestamp: str
    industries: list[str]
    lang: str = "en"
    # 后续填充
    cluster_id: Optional[int] = None
    is_breaking: bool = False
    break_reason: str = ""
    llm_summary: Optional[dict] = None
    volume_verified: bool = False


@dataclass
class Signal:
    industry: str
    title: str
    content: str
    url: str
    timestamp: str
    news_count: int          # 该信号关联的新闻数
    is_breaking: bool
    volume_verified: bool
    volume_conflict: bool    # True=新闻看多但成交量不支持
    llm_summary: dict
    related_stocks: list[str]


# ============================================================
# 第一级：聚类去重
# ============================================================
class ClusterDedup:
    """
    语义聚类去重 — sentence-transformers
    - 使用 all-MiniLM-L6-v2 向量模型（轻量，Mac CPU 可跑）
    - 余弦相似度 > SIM_THRESHOLD 的新闻归为同一簇
    - 每簇保留最具时效性的一条
    """

    SIM_THRESHOLD = 0.62   # 语义相似度阈值
    MAX_HISTORY = 500      # 跨批次历史保留条数
    _model = None          # 全局复用模型
    _vectorizer = None

    def __init__(self):
        self._seen_hashes: dict[str, datetime] = {}
        self._history: list[NewsItem] = []

    @classmethod
    def _get_model(cls):
        """延迟加载模型，全局复用"""
        if cls._model is None:
            from sentence_transformers import SentenceTransformer
            cls._model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("[Cluster] sentence-transformers 模型加载完成")
        return cls._model

    def process(self, news: list[NewsItem], max_history: int = 500) -> list[NewsItem]:
        if not news:
            return []

        # ---- A. URL去重 ----
        deduped = []
        for item in news:
            url_md5 = hashlib.md5(item.url.encode()).hexdigest()
            if url_md5 in self._seen_hashes:
                continue
            self._seen_hashes[url_md5] = datetime.now()
            deduped.append(item)

        if not deduped:
            return []

        # ---- B. 合并历史进行语义聚类 ----
        all_items = self._history[-max_history:] + deduped
        texts = [self._make_text(n) for n in all_items]

        try:
            model = self._get_model()
            embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        except Exception as e:
            logger.warning(f"[Cluster] 向量化失败: {e}，降级为URL去重")
            self._history = all_items[-max_history:]
            return deduped

        n = len(all_items)
        sim_matrix = cosine_similarity(embeddings)

        # ---- C. 贪心聚类 ----
        clusters: list[list[int]] = []
        assigned: set[int] = set()

        for i in range(n):
            if i in assigned:
                continue
            cluster = [i]
            for j in range(i + 1, n):
                if j in assigned:
                    continue
                if sim_matrix[i, j] >= self.SIM_THRESHOLD:
                    cluster.append(j)
                    assigned.add(j)
            clusters.append(cluster)
            assigned.add(i)

        # ---- D. 每簇选代表 ----
        results: list[NewsItem] = []
        for cluster in clusters:
            items_in = [all_items[idx] for idx in cluster]
            items_in.sort(key=lambda x: x.timestamp or "", reverse=True)
            rep = items_in[0]
            rep.news_count = len(items_in)  # type: ignore
            results.append(rep)

        self._history = all_items[-max_history:]

        logger.info(
            f"[Cluster] {len(news)}条 → URL去重{len(deduped)} "
            f"→ 语义聚类{len(deduped)} → {len(results)}个事件簇 "
            f"(历史{len(self._history)}条)"
        )
        return results

    def _make_text(self, item: NewsItem) -> str:
        industries = " ".join(item.industries)
        return f"{item.title} {industries} {item.content}"[:400]


# ============================================================
# 第二级：突发识别
# ============================================================
BREAKING_KEYWORDS = {
    "en": [
        "breaking", "urgent", "developing", "just in",
        "alert", "emergency",
        "ban", "sanction", "tariff",
        "halts", "suspends", "pauses",
        "resign", "fired", "ceo out",
        "accident", "crash", "explosion",
        "collapse", "outage",
        "lawsuit", "probe", "investigation",
        "data breach", "hack", "cyberattack",
    ],
    "zh": [
        "突发", "紧急", "刚刚", "快讯",
        "禁", "制裁", "关税",
        "暂停", "中止", "停止",
        "辞职", "被免", "CEO离职",
        "事故", "爆炸", "坍塌",
        "故障", "中断", "黑客",
        "调查", "起诉",
    ],
}


class BreakingNewsDetector:
    """
    突发新闻识别
    - 关键词匹配（英文+中文）
    - 首报检测（跨批次新出现，持久化到文件）
    - 高影响关键词匹配
    """

    STATE_FILE = "/Users/shj/quantfly/.news_breaking_state.json"

    HIGH_IMPACT_KEYWORDS = {
        "AI大模型": [
            "openai", "anthropic", "google deepmind",
            "model shutdown", "AI ban", "AI regulation",
            "AGI", "frontier model",
        ],
        "半导体": [
            "ASML", "chip ban", "export control",
            "TSMC", "semiconductor embargo",
            "Nvidia", "H100", "B200",
        ],
        "机器人": [
            "humanoid robot", "Figure", "Tesla Optimus",
            "robotaxi",
        ],
        "商业航天": [
            "SpaceX", "rocket launch", "satellite launch",
            "Starlink", "Starship",
        ],
        "新能源车": [
            "Tesla recall", "EV fire", "battery explosion",
            "BYD", "CATL",
        ],
        "智能电网": [
            "power outage", "grid failure", "blackout",
            "electricity emergency",
        ],
        "稀土永磁": [
            "China rare earth", "export ban", "dysprosium",
            "neodymium",
        ],
        "量子计算": [
            "quantum computer", "quantum supremacy",
            "IBM", "Google quantum",
        ],
    }

    def __init__(self, seen_titles: Optional[set] = None):
        self._seen_titles_window: list[tuple[str, datetime]] = []
        self._load_state()

    def _load_state(self):
        """从文件加载历史标题前缀（跨进程持久化）"""
        import json, os
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                now = datetime.now()
                cutoff = now - timedelta(hours=48)
                self._seen_titles_window = [
                    (t, datetime.fromisoformat(dt))
                    for t, dt in data.get("titles", [])
                    if datetime.fromisoformat(dt) > cutoff
                ]
                logger.info(
                    f"[Breaking] 加载{len(self._seen_titles_window)}个历史标题 "
                    f"(过滤48h外后)"
                )
            except Exception as e:
                logger.warning(f"[Breaking] 状态加载失败: {e}")
                self._seen_titles_window = []

    def _save_state(self):
        """保存标题前缀到文件"""
        import json, os
        try:
            data = {
                "titles": [
                    (t, dt.isoformat())
                    for t, dt in self._seen_titles_window[-2000:]
                ]
            }
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            with open(self.STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"[Breaking] 状态保存失败: {e}")

    def detect(self, news: list[NewsItem]) -> list[NewsItem]:
        # 当前批次内已见过的标题前缀
        batch_seen: set[str] = set()

        for item in news:
            reasons = []
            is_breaking = False
            text = f"{item.title} {item.content}".lower()

            # ---- 1. 突发关键词 ----
            kws = BREAKING_KEYWORDS.get(item.lang, BREAKING_KEYWORDS["en"])
            if any(kw in text for kw in kws):
                reasons.append("突发关键词")
                is_breaking = True

            # ---- 2. 首报检测（跨批次持久化）----
            title_prefix = item.title[:80].lower()
            window_cutoff = datetime.now() - timedelta(hours=24)

            # 检查历史 + 当前批次
            already_seen = any(
                title_prefix == t and dt > window_cutoff
                for t, dt in self._seen_titles_window
            ) or (title_prefix in batch_seen)

            if not already_seen:
                reasons.append("首报")
                is_breaking = True
                batch_seen.add(title_prefix)
                self._seen_titles_window.append((title_prefix, datetime.now()))

            # ---- 3. 高影响关键词匹配 ----
            for industry, hi_kws in self.HIGH_IMPACT_KEYWORDS.items():
                if any(kw in text for kw in hi_kws):
                    reasons.append(f"高影响({industry})")
                    is_breaking = True
                    break

            item.is_breaking = is_breaking
            item.break_reason = " | ".join(reasons) if reasons else ""

        # 持久化
        self._save_state()

        breaking_count = sum(1 for n in news if n.is_breaking)
        first_reports = sum(1 for n in news if "首报" in n.break_reason)
        logger.info(
            f"[Breaking] {len(news)}条 → {breaking_count}条突发 "
            f"(含{first_reports}首报)"
        )
        return news

    def get_prioritized(self, news: list[NewsItem], max_llm: int = 8) -> list[NewsItem]:
        """返回最多max_llm条需要LLM分析的新闻"""
        breaking = [n for n in news if n.is_breaking]
        normal = [n for n in news if not n.is_breaking]

        # 突发优先；其次按产业关键词覆盖度
        normal.sort(key=lambda x: len(x.industries), reverse=True)

        selected = breaking[:max_llm]
        remaining_slots = max_llm - len(selected)
        if remaining_slots > 0 and normal:
            selected += normal[:remaining_slots]

        return selected


# ============================================================
# 第三级：LLM分析（只分析优先新闻）
# ============================================================
class LLMAnalyzer:
    """
    MiniMax API 批量分析
    - 只分析 BreakingNewsDetector 筛选出的优先新闻
    - 批量一次发完（节省API调用）
    """

    SYSTEM_PROMPT = """你是一个A股量化交易新闻分析助手。
输出JSON（无markdown，无解释）：
{
  "sentiment": "bullish|bearish|neutral",
  "sentiment_score": 0.0到1.0,
  "relevant_stocks": ["股票代码1", "股票代码2"],
  "related_industries": ["产业1", "产业2"],
  "event_type": "政策|业绩|技术突破|供应链|监管|市场需求|其他",
  "impact_duration": "short-term（<1周）|medium-term（1周-3月）|long-term（>3月）",
  "trade_signal": "long|short|watch|ignore",
  "reasoning": "50字以内的分析逻辑"
}
"""

    def __init__(self, api_key: str = "", model: str = "deepseek-v4-flash", provider: str = "deepseek"):
        self.api_key = api_key
        self.model = model
        self.provider = provider  # "deepseek" | "minimax"

    async def analyze(self, news: list[NewsItem]) -> list[NewsItem]:
        if not news:
            return news

        # 构造批量prompt（每个新闻独立一段）
        articles_text = ""
        for i, item in enumerate(news):
            articles_text += f"\n--- 新闻{i+1} ---\n"
            articles_text += f"来源: {item.source}\n"
            articles_text += f"时间: {item.timestamp}\n"
            articles_text += f"产业: {', '.join(item.industries)}\n"
            articles_text += f"标题: {item.title}\n"
            articles_text += f"内容: {item.content[:400]}\n"

        user_prompt = (
            f"分析以下{len(news)}条新闻，返回JSON数组：\n"
            f"[{articles_text}]\n\n"
            f"按顺序为每条新闻输出一个JSON对象，所有对象放在一个JSON数组中。"
        )

        import json as _json
        try:
            import aiohttp
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp", "-q"])
            import aiohttp

        if self.provider == "deepseek":
            endpoint = "https://api.deepseek.com/chat/completions"
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            }
        else:
            # MiniMax
            endpoint = "https://api.minimax.chat/v1/text/chatcompletion_v2"
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"LLM API失败: {resp.status} {body[:200]}")
                        return news
                    data = await resp.json()
                    if self.provider == "deepseek":
                        content = data["choices"][0]["message"]["content"]
                    else:
                        # MiniMax wraps in reasoning_content
                        content = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
                        if not content:
                            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

                    # 解析JSON数组
                    content = content.strip()
                    if content.startswith("```"):
                        content = re.sub(r"```(?:json)?", "", content, flags=re.IGNORECASE).strip()
                    results = _json.loads(content)

                    if not isinstance(results, list):
                        results = [results]

                    for i, item in enumerate(news):
                        if i < len(results):
                            item.llm_summary = results[i]
                        else:
                            item.llm_summary = {
                                "sentiment": "neutral",
                                "sentiment_score": 0.5,
                                "relevant_stocks": [],
                                "related_industries": item.industries,
                                "event_type": "其他",
                                "impact_duration": "medium-term",
                                "trade_signal": "watch",
                                "reasoning": "",
                            }

        except Exception as e:
            logger.warning(f"LLM分析异常: {e}")
            for item in news:
                item.llm_summary = {
                    "sentiment": "neutral", "sentiment_score": 0.5,
                    "relevant_stocks": [], "related_industries": item.industries,
                    "event_type": "其他", "impact_duration": "medium-term",
                    "trade_signal": "watch", "reasoning": "",
                }

        return news


# ============================================================
# 第四级：分钟级成交量验证
# ============================================================
class VolumeVerifier:
    """
    分钟级成交量验证
    - 逻辑：新闻发布后 N 分钟内，相关股票成交量是否异常放大
    - 异常定义：成交量 > 过去N天同时段均量的2倍

    支持数据源：
    - tushare（需要token）
    - akshare（免费，但数据延迟）
    """

    LOOKBACK_DAYS = 5
    VOL_MULTIPLIER = 2.0   # 超过均量的2倍视为异常

    def __init__(self, data_source: str = "akshare", tushare_token: str = ""):
        self.data_source = data_source
        self.tushare_token = tushare_token

    async def verify(
        self,
        signals: list[Signal],
        news_timestamp: str,
    ) -> list[Signal]:
        """
        验证信号对应的股票分钟成交量
        返回更新后的signals列表（volume_verified字段）
        """
        if not signals:
            return signals

        stocks = []
        for sig in signals:
            stocks.extend(sig.related_stocks)
        stocks = list(set(stocks))[:10]  # 最多验证10只

        # 解析新闻时间
        try:
            news_dt = datetime.strptime(news_timestamp[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            news_dt = datetime.now()

        # 获取分钟数据
        minute_data = await self._fetch_minute_data(stocks, news_dt)

        for sig in signals:
            if not sig.related_stocks:
                continue

            volume_conflict = False
            verified = False

            for stock in sig.related_stocks:
                if stock in minute_data:
                    stock_data = minute_data[stock]
                    # 检查新闻发布后30分钟内是否放量
                    is_surge = self._check_volume_surge(
                        stock_data, news_dt, window_minutes=30
                    )
                    if is_surge:
                        verified = True
                        break

            # 新闻看多但成交量不支持 → conflict
            if sig.llm_summary.get("trade_signal") in ("long", "bullish") and not verified:
                volume_conflict = True

            sig.volume_verified = verified
            sig.volume_conflict = volume_conflict

        return signals

    def _check_volume_surge(
        self,
        stock_data: dict,
        news_dt: datetime,
        window_minutes: int = 30,
    ) -> bool:
        """
        检查新闻发布后window_minutes内成交量是否异常放大
        stock_data: {minute_str: volume}
        """
        if not stock_data:
            return False

        # 过滤新闻后的窗口
        window_start = news_dt
        window_end = news_dt + timedelta(minutes=window_minutes)

        window_volumes = []
        for minute_str, vol in stock_data.items():
            try:
                m_dt = datetime.strptime(minute_str, "%Y-%m-%d %H:%M")
                if window_start <= m_dt <= window_end:
                    window_volumes.append(vol)
            except Exception:
                continue

        if not window_volumes:
            return False

        # 均量对比
        avg_vol = np.mean(list(stock_data.values()))
        window_avg = np.mean(window_volumes)
        return window_avg > avg_vol * self.VOL_MULTIPLIER

    async def _fetch_minute_data(
        self,
        stocks: list[str],
        news_dt: datetime,
    ) -> dict:
        """获取股票分钟数据"""
        result = {}
        date_str = news_dt.strftime("%Y%m%d")

        if self.data_source == "akshare":
            try:
                import akshare as ak

                for stock in stocks[:5]:  # 限制每批数量
                    try:
                        # 判断市场
                        if stock.startswith("6"):
                            mkt = "sh"
                        else:
                            mkt = "sz"

                        df = ak.stock_zh_a_minute(
                            symbol=f"{mkt}{stock}",
                            period="1",
                            adjust="qfq",
                        )
                        if df is not None and not df.empty:
                            result[stock] = {
                                str(row[0]): float(row[1])
                                for _, row in df.iterrows()
                            }
                    except Exception:
                        continue

            except ImportError:
                logger.warning("akshare未安装，成交量验证跳过")

        elif self.data_source == "tushare" and self.tushare_token:
            try:
                import tushare as ts
                ts.set_token(self.tushare_token)
                pro = ts.pro_api()

                for stock in stocks[:5]:
                    try:
                        df = pro.major_hq(
                            ts_code=f"{stock}.SZ"
                                if not stock.startswith("6") else f"{stock}.SH",
                            start_date=date_str,
                            end_date=date_str,
                        )
                        if df is not None and not df.empty:
                            result[stock] = {
                                str(row["trade_time"]): float(row["vol"])
                                for _, row in df.iterrows()
                            }
                    except Exception:
                        continue

            except ImportErrorError:
                logger.warning("tushare未安装，成交量验证跳过")

        return result


# ============================================================
# 主Pipeline
# ============================================================
class NewsPipeline:
    """
    新闻处理完整流水线
    ① 聚类去重 → ② 突发识别 → ③ LLM分析 → ④ 成交量验证
    """

    def __init__(
        self,
        llm_api_key: str = "",
        llm_model: str = "deepseek-v4-flash",
        provider: str = "deepseek",
        max_llm_news: int = 8,
        data_source: str = "akshare",
        tushare_token: str = "",
        db_path: str = "/Users/shj/quantfly/.signals.db",
    ):
        self.crawler = AsyncNewsCrawler(timeout=8, max_concurrent=15)
        self.cluster = ClusterDedup()
        self.breaking = BreakingNewsDetector()
        self.llm = LLMAnalyzer(llm_api_key, llm_model, provider) if llm_api_key else None
        self.volume = VolumeVerifier(data_source, tushare_token)
        self.max_llm_news = max_llm_news
        self.store = SignalStore(db_path)

    async def run(
        self,
        max_age_minutes: int = 120,
        target_industries: Optional[list] = None,
    ) -> list[Signal]:
        t0 = time.time()

        # ---- ① 并发采集 ----
        news_raw = await self.crawler.fetch_all(max_age_minutes, target_industries)
        news_items = [NewsItem(**{k: v for k, v in n.items() if k in NewsItem.__dataclass_fields__}) for n in news_raw]

        if not news_items:
            return []

        # ---- ② 聚类去重 ----
        news_clustered = self.cluster.process(news_items)

        # ---- ③ 突发识别 ----
        news_breaking = self.breaking.detect(news_clustered)
        news_priority = self.breaking.get_prioritized(
            news_breaking, max_llm=self.max_llm_news
        )

        # ---- ④ LLM分析（只分析优先新闻）----
        if self.llm and news_priority:
            news_analyzed = await self.llm.analyze(news_priority)
        else:
            news_analyzed = news_priority

        # 合并分析结果
        analyzed_map = {id(n): n for n in news_analyzed}
        for n in news_clustered:
            if id(n) in analyzed_map:
                n.llm_summary = analyzed_map[id(n)].llm_summary

        # ---- ⑤ 构建Signal列表 ----
        signals = []
        for item in news_clustered:
            summary = item.llm_summary or {}
            stocks = summary.get("relevant_stocks", [])
            signal = Signal(
                industry=item.industries[0] if item.industries else "其他",
                title=item.title,
                content=item.content,
                url=item.url,
                timestamp=item.timestamp,
                news_count=getattr(item, "news_count", 1),
                is_breaking=item.is_breaking,
                volume_verified=False,
                volume_conflict=False,
                llm_summary=summary,
                related_stocks=stocks,
            )
            signals.append(signal)

        # ---- ⑥ 成交量验证（异步）----
        if news_raw:
            first_timestamp = news_raw[0].get("timestamp", "")
            signals = await self.volume.verify(signals, first_timestamp)

        # ---- ⑦ 持久化到SQLite ----
        saved = self.store.save(signals)

        elapsed = time.time() - t0
        logger.info(
            f"[Pipeline] {len(news_raw)}条 → {len(news_clustered)}簇 "
            f"→ {len(news_priority)}优先 → {len(signals)}信号 "
            f"→ 写入{saved}条 "
            f"(耗时{elapsed:.1f}秒)"
        )

        return signals

    def run_sync(
        self,
        max_age_minutes: int = 120,
        target_industries: Optional[list] = None,
    ) -> list[Signal]:
        """同步入口"""
        return asyncio.run(self.run(max_age_minutes, target_industries))
