# -*- encoding: utf-8 -*-
"""
热点新闻监控 — 从多个数据源采集热点
=====================================
集成双过滤信号系统：
  Gate1: 新闻情感分析（规则 + MiniMax LLM）
  Gate2: 成交量验证（QMT / akshare）
"""
import requests
import logging
from datetime import datetime
from typing import Optional, Literal

logger = logging.getLogger("HotTopics.Monitor")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.eastmoney.com/",
}
_em_session = requests.Session()
_em_session.trust_env = False


class HotTopicMonitor:
    """热点新闻采集器"""

    def __init__(self, use_llm: bool = True):
        """
        Args:
            use_llm: 是否使用MiniMax LLM情感判断
        """
        self._use_llm = use_llm
        self._news_sentiment = None
        self._dual_gate = None

    def _init_modules(self):
        """延迟初始化（避免循环导入）"""
        if self._news_sentiment is None:
            from quantfly.hot_topics.news_sentiment import analyze_news_batch, aggregate_industry_signals
            self._analyze_batch = analyze_news_batch
            self._aggregate = aggregate_industry_signals

    def fetch_all(self) -> list[dict]:
        """从所有数据源采集热点新闻"""
        items = []
        items.extend(self._fetch_eastmoney_boards())
        items.extend(self._fetch_eastmoney_news())  # 新增：真实新闻文本
        return items

    def _fetch_eastmoney_boards(self) -> list[dict]:
        """东方财富概念板块涨幅榜"""
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": 1, "pz": 20,
                "po": 1, "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2,
                "fid": "f3",
                "fs": "m:90+t:3",
                "fields": "f12,f14,f3,f6,f8",
            }
            r = _em_session.get(url, params=params, headers=EM_HEADERS, timeout=10)
            data = r.json().get("data", {}).get("diff", [])
            result = []
            for item in data[:15]:
                name = item.get("f14", "")
                result.append({
                    "source": "eastmoney_board",
                    "title": name,
                    "content": f"概念板块：{name}，涨幅{item.get('f3', 0)}%",
                    "code": str(item.get("f12", "")),
                    "change_pct": item.get("f3", 0),
                    "amount": item.get("f6", 0),
                    "topic": self._map_board_to_industry(name),
                    "timestamp": datetime.now().isoformat(),
                })
            logger.info(f"东方财富板块采集 {len(result)} 条")
            return result
        except Exception as e:
            logger.warning(f"东方财富板块采集失败: {e}")
            return []

    def _fetch_eastmoney_news(self) -> list[dict]:
        """
        东方财富新闻 — 使用akshare news_cctv
        注意：东方财富快讯API需要认证，返回null，改用CCTV新闻作为替代
        """
        try:
            import akshare as ak

            items = []

            # CCTV新闻（最快）
            try:
                df = ak.news_cctv()
                if df is not None and not df.empty:
                    for _, row in df.head(20).iterrows():
                        title = str(row.get("title", ""))
                        content = str(row.get("content", ""))[:300]
                        items.append({
                            "source": "cctv_news",
                            "title": title,
                            "content": f"{title} {content}",
                            "code": "",
                            "topic": self._map_board_to_industry(title + content),
                            "timestamp": str(row.get("date", "")),
                            "url": "",
                        })
            except Exception as e:
                logger.warning(f"CCTV新闻获取失败: {e}")

            logger.info(f"新闻采集 {len(items)} 条")
            return items

        except Exception as e:
            logger.warning(f"东方财富新闻采集失败: {e}")
            return []

    def analyze_with_sentiment(self, items: list[dict]) -> dict:
        """
        对新闻进行情感分析（Gate1）

        Args:
            items: fetch_all() 返回的新闻列表

        Returns:
            {industry: {avg_news_score, avg_sentiment, news_count, signals}}
        """
        self._init_modules()

        # 批量情感分析
        analyzed = self._analyze_batch(items, use_llm_fallback=self._use_llm)

        # 按行业聚合
        industry_signals = self._aggregate(analyzed, time_window_hours=4)

        return industry_signals

    def evaluate_with_volume(self, gate1_signals: dict, data_source: Literal["qmt", "akshare"] = "akshare") -> list[dict]:
        """
        结合成交量验证（Gate2）

        Args:
            gate1_signals: Gate1结果
            data_source: "qmt" 或 "akshare"

        Returns:
            最终信号列表
        """
        from quantfly.hot_topics.dual_gate_signal import DualGateSignal

        if data_source == "qmt":
            from quantfly.hot_topics.qmt_minute_data import get_qmt_minute_data
            qmt = get_qmt_minute_data()
            volume = qmt.monitor_all_etfs()
        else:
            from quantfly.hot_topics.akshare_minute_data import get_akshare_minute_data
            akshare = get_akshare_minute_data()
            volume = akshare.monitor_all_etfs()

        # 双过滤
        dual = DualGateSignal()
        dual.set_gate1(gate1_signals)
        dual.set_gate2(volume)
        return dual.get_signals()

    def _map_board_to_industry(self, board_name: str) -> str:
        """板块名 → 产业名映射"""
        mapping = {
            "AI": "AI大模型", "人形机器人": "机器人", "机器人": "机器人",
            "半导体": "半导体", "芯片": "芯片半导体",
            "稀土": "稀土永磁", "永磁": "稀土永磁",
            "商业航天": "商业航天", "低空经济": "商业航天",
            "量子": "量子计算",
            "固态电池": "新能源车", "新能源车": "新能源车",
            "脑机": "脑机接口",
        }
        for kw, industry in mapping.items():
            if kw in board_name:
                return industry
        return "其他"
