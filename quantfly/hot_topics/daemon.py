# -*- encoding: utf-8 -*-
"""
新闻监控Daemon
===============
常驻进程，持续监控新闻，自动推送飞书

使用方式：
    python -m quantfly.hot_topics.daemon run

原理：
    - 每3分钟轮询一次RSS（不重复加载模型）
    - 首次全量LLM分析，后续只分析新增突发
    - 突发信号即时飞书推送（防刷屏：同类突发5分钟去重）
    - 整点汇总推送（非突发的普通信号）
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Optional

import requests

# ============================================================
# 配置
# ============================================================
FEISHU_WEBHOOK = os.getenv(
    "FEISHU_WEBHOOK",
    ""   # 替换为实际webhook地址
)
POLL_INTERVAL = 180        # 轮询间隔（秒）
BATCH_HOUR_MINUTES = 0    # 整点汇总推送（分），0=不汇总
CRASH_LOG = "/Users/shj/quantfly/.daemon_crash.log"
STATE_FILE = "/Users/shj/quantfly/.daemon_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("NewsDaemon")


# ============================================================
# 飞书推送
# ============================================================
def send_feishu(message: str, mention_str: str = "") -> bool:
    """推送文本消息到飞书群"""
    if not FEISHU_WEBHOOK:
        logger.warning("FEISHU_WEBHOOK 未设置，跳过推送")
        return False

    payload = {
        "msg_type": "text",
        "content": {"text": f"{mention_str}{message}"},
    }
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            return True
        logger.warning(f"飞书推送失败: {result}")
        return False
    except Exception as e:
        logger.error(f"飞书推送异常: {e}")
        return False


def format_signal_text(signals: list[dict]) -> str:
    """格式化信号为飞书推送文本"""
    if not signals:
        return ""

    lines = [f"📊 新闻信号 #{datetime.now().strftime('%H:%M')}\n"]

    # 按产业分组
    by_industry = {}
    for s in signals:
        ind = s.get("industry", "其他")
        if ind not in by_industry:
            by_industry[ind] = []
        by_industry[ind].append(s)

    for industry, sigs in by_industry.items():
        icon = {
            "AI大模型": "🤖", "半导体": "💾", "机器人": "🦾",
            "商业航天": "🚀", "新能源车": "🚗", "智能电网": "⚡",
            "稀土永磁": "🧲", "量子计算": "⚛️",
        }.get(industry, "📡")

        lines.append(f"{icon} **{industry}** ({len(sigs)}条)")

        for s in sigs:
            flag = "🔴" if s.get("is_breaking") else "  "
            sig_icon = {"long": "🟢", "short": "🔴", "watch": "🟡", "ignore": "⚪"}
            icon = sig_icon.get(s.get("trade_signal", ""), "🟡")

            title = s.get("title", "")[:40]
            sentiment = s.get("sentiment", "?")
            reasoning = s.get("reasoning", "")[:40]

            lines.append(
                f"  {flag}{icon} {title}\n"
                f"       情绪:{sentiment} | {reasoning}"
            )
        lines.append("")

    return "\n".join(lines).strip()


def format_breaking_alert(signal: dict) -> str:
    """格式化突发信号告警（即时推送）"""
    industry = signal.get("industry", "其他")
    title = signal.get("title", "")[:50]
    sentiment = signal.get("sentiment", "?")
    reasoning = signal.get("reasoning", "")[:60]
    stocks = signal.get("related_stocks", "[]")
    try:
        stocks_list = json.loads(stocks) if isinstance(stocks, str) else stocks
        stocks_str = ", ".join(stocks_list[:3]) if stocks_list else ""
    except Exception:
        stocks_str = ""

    icon = {"AI大模型": "🤖", "半导体": "💾", "机器人": "🦾",
            "商业航天": "🚀", "新能源车": "🚗", "智能电网": "⚡"}.get(industry, "📡")

    text = (
        f"🔴 **突发信号** {icon} {industry}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"**{title}**\n"
        f"情绪: {sentiment}\n"
        f"推理: {reasoning}\n"
    )
    if stocks_str:
        text += f"关联A股: {stocks_str}\n"
    return text


# ============================================================
# 状态管理（跨轮询去重）
# ============================================================
class DeduplicationCache:
    """同类突发信号5分钟内不重复推送"""

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self._cache: list[tuple[str, float]] = []  # (title_prefix_md5, timestamp)
        self._lock = Lock()
        self._load()

    def _load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                self._cache = [
                    (k, float(v))
                    for k, v in data.get("cache", {}).items()
                    if time.time() - float(v) < 300  # 5分钟TTL
                ]
            except Exception:
                self._cache = []

    def _save(self):
        try:
            data = {"cache": {k: str(v) for k, v in self._cache}}
            Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def is_seen(self, title: str) -> bool:
        """标题是否在5分钟内见过"""
        key = str(hash(title[:60]))
        with self._lock:
            for k, t in self._cache:
                if k == key and time.time() - t < 300:
                    return True
            return False

    def mark_seen(self, title: str):
        """标记标题已推送"""
        key = str(hash(title[:60]))
        with self._lock:
            self._cache = [(k, v) for k, v in self._cache if time.time() - v < 300]
            self._cache.append((key, time.time()))
        self._save()


# ============================================================
# Daemon核心
# ============================================================
class NewsDaemon:
    """
    常驻新闻监控进程

    运行流程：
      ① 每 POLL_INTERVAL 秒轮询 RSS
      ② 全量聚类去重（sentence-transformers 模型只加载一次）
      ③ 新信号触发LLM分析
      ④ 突发信号即时飞书推送（5分钟去重）
      ⑤ 整点汇总推送普通信号
    """

    def __init__(
        self,
        llm_api_key: str,
        llm_model: str = "deepseek-v4-flash",
        provider: str = "deepseek",
        poll_interval: int = POLL_INTERVAL,
        feishu_webhook: str = "",
        dry_run: bool = False,
    ):
        self.poll_interval = poll_interval
        self.feishu_webhook = feishu_webhook or FEISHU_WEBHOOK
        self.dry_run = dry_run

        # 延迟导入避免启动慢
        from quantfly.hot_topics.news_processor import NewsPipeline
        self._pipeline = NewsPipeline(
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            provider=provider,
            max_llm_news=8,
        )
        self._dedup = DeduplicationCache()
        self._pending_signals: list[dict] = []  # 等待整点汇总的信号
        self._pending_lock = Lock()
        self._last_hour = -1
        self._running = Event()
        self._stopping = False

    # ----------------------------------------------------------
    # 轮询逻辑
    # ----------------------------------------------------------
    async def _poll(self) -> list[dict]:
        """单次轮询：采集+分析+返回新信号"""
        from quantfly.hot_topics.news_processor import NewsItem

        try:
            # 全量采集（会复用模型）
            news_raw = await self._pipeline.crawler.fetch_all(max_age_minutes=15)
            if not news_raw:
                return []

            news_items = [
                NewsItem(**{k: v for k, v in n.items() if k in NewsItem.__dataclass_fields__})
                for n in news_raw
            ]

            # 聚类
            news_clustered = self._pipeline.cluster.process(news_items)

            # 突发检测
            news_breaking = self._pipeline.breaking.detect(news_clustered)

            # 只对新的突发新闻触发LLM
            new_signals = []
            for item in news_breaking:
                if not item.is_breaking:
                    continue
                if self._dedup.is_seen(item.title):
                    continue

                # 触发LLM分析
                if self._pipeline.llm:
                    analyzed = await self._pipeline.llm.analyze([item])
                    if analyzed:
                        item.llm_summary = analyzed[0].llm_summary

                # 构造成Signal格式
                llm = item.llm_summary or {}
                stocks = llm.get("relevant_stocks", [])
                signal_dict = {
                    "industry": item.industries[0] if item.industries else "其他",
                    "title": item.title,
                    "content": item.content,
                    "url": item.url,
                    "timestamp": item.timestamp,
                    "news_count": getattr(item, "news_count", 1),
                    "is_breaking": True,
                    "break_reason": getattr(item, "break_reason", ""),
                    "sentiment": llm.get("sentiment", "neutral"),
                    "sentiment_score": llm.get("sentiment_score", 0.5),
                    "trade_signal": llm.get("trade_signal", "watch"),
                    "impact_duration": llm.get("impact_duration", "medium-term"),
                    "event_type": llm.get("event_type", "其他"),
                    "related_stocks": json.dumps(stocks) if stocks else "[]",
                    "reasoning": llm.get("reasoning", ""),
                    "volume_verified": False,
                    "volume_conflict": False,
                }
                new_signals.append(signal_dict)
                self._dedup.mark_seen(item.title)

            return new_signals

        except Exception as e:
            logger.error(f"轮询异常: {e}")
            return []

    async def _handle_signals(self, signals: list[dict]):
        """处理新信号：即时推送突发 + 整点汇总"""
        if not signals:
            return

        # ---- 持久化 ----
        try:
            self._pipeline.store.save([
                type("Signal", (), {**s, "llm_summary": json.loads(s.get("related_stocks", "[]"))})()
                for s in signals
            ])
        except Exception as e:
            logger.warning(f"持久化失败: {e}")

        # ---- 即时推送突发 ----
        breaking = [s for s in signals if s.get("is_breaking")]
        if breaking:
            text = format_signal_text(breaking)
            if text:
                if self.dry_run:
                    logger.info(f"[DryRun] 推送:\n{text}")
                elif self.feishu_webhook:
                    send_feishu(text)

        # ---- 整点汇总 ----
        with self._pending_lock:
            self._pending_signals.extend(
                s for s in signals if not s.get("is_breaking")
            )
            current_hour = datetime.now().hour

            if current_hour != self._last_hour and self._last_hour != -1:
                # 整点了，汇总推送
                pending = self._pending_signals
                self._pending_signals = []
                if pending:
                    text = format_signal_text(pending)
                    if text:
                        if self.dry_run:
                            logger.info(f"[整点汇总]\n{text}")
                        elif self.feishu_webhook:
                            send_feishu(text)

            self._last_hour = current_hour

    # ----------------------------------------------------------
    # 主循环
    # ----------------------------------------------------------
    async def run(self):
        """主循环"""
        logger.info(
            f"[Daemon] 启动 | 轮询间隔:{self.poll_interval}s "
            f"| Webhook:{'已配置' if self.feishu_webhook else '未配置'} "
            f"| DryRun:{self.dry_run}"
        )

        # 注册信号处理
        def on_signal(signum, frame):
            logger.info(f"收到信号 {signum}，准备停止...")
            self._stopping = True
            self._running.set()

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)

        self._running.set()

        while not self._stopping:
            self._running.clear()

            try:
                t0 = time.time()
                signals = await self._poll()
                elapsed = time.time() - t0

                if signals:
                    logger.info(f"轮询完成: {len(signals)}个新信号 ({elapsed:.1f}s)")
                    await self._handle_signals(signals)
                else:
                    logger.info(f"轮询完成: 无新信号 ({elapsed:.1f}s)")

            except Exception as e:
                logger.error(f"轮询出错: {e}")

            # 等待下次轮询
            self._running.wait(timeout=self.poll_interval)

        logger.info("[Daemon] 已停止")


# ============================================================
# CLI入口
# ============================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="新闻监控Daemon")
    parser.add_argument("action", choices=["run", "test"], help="run=启动监控, test=测试一轮")
    parser.add_argument("--poll", type=int, default=180, help="轮询间隔(秒)")
    parser.add_argument("--dry-run", action="store_true", help="不推送，只打印")
    parser.add_argument("--api-key", default="", help="DeepSeek API Key（也可通过环境变量）")
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        # 尝试从 quantfly 配置读取
        try:
            from quantfly.hot_topics.news_processor import NewsPipeline
            api_key = "sk-da51b427a0584732a4dda0bc18d19b3e"  # 已有key
        except Exception:
            pass

    if args.action == "test":
        # 测试一轮
        import asyncio, time
        print("初始化中（首次加载模型约30秒）...")
        t0 = time.time()
        daemon = NewsDaemon(
            llm_api_key=api_key,
            poll_interval=args.poll,
            dry_run=args.dry_run,
        )
        print(f"模型加载完成: {time.time()-t0:.1f}秒")

        print("轮询中...")
        t1 = time.time()
        signals = asyncio.run(daemon._poll())
        print(f"轮询完成: {time.time()-t1:.1f}秒")
        print(f"\n新信号: {len(signals)}条")
        for s in signals:
            flag = "🔴突发" if s.get("is_breaking") else ""
            print(f"  [{s['industry']}] {flag} {s['title'][:50]}")
            if s.get("trade_signal"):
                print(f"    信号:{s['trade_signal']} 情绪:{s.get('sentiment','?')} {s.get('reasoning','')[:50]}")
        return

    # 启动Daemon
    daemon = NewsDaemon(
        llm_api_key=api_key,
        poll_interval=args.poll,
        dry_run=args.dry_run,
    )
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
