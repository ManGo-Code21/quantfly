# -*- encoding: utf-8 -*-
"""
新闻信号持久化 — SQLite
===========================
信号存入本地 SQLite，支持：
  - 按时间/产业/突发级别查询
  - 信号冲突检测（新闻看多但成交量不支持）
  - 趋势统计（近N小时每个产业的信号数量变化）
"""
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("SignalStore")

DB_PATH = "/Users/shj/quantfly/.signals.db"


# ============================================================
# 数据结构
# ============================================================
@dataclass
class SignalRecord:
    id: Optional[int] = None
    timestamp: str = ""
    industry: str = ""
    title: str = ""
    content: str = ""
    url: str = ""
    source: str = ""
    news_count: int = 1
    is_breaking: bool = False
    break_reason: str = ""
    sentiment: str = "neutral"
    sentiment_score: float = 0.5
    trade_signal: str = "watch"
    impact_duration: str = "medium-term"
    event_type: str = "其他"
    related_stocks: str = ""       # JSON数组
    reasoning: str = ""
    volume_verified: bool = False
    volume_conflict: bool = False
    llm_raw: str = ""             # 原始LLM输出JSON
    created_at: str = ""


# ============================================================
# 数据库
# ============================================================
class SignalStore:
    """
    SQLite信号库

    CREATE:
      CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        industry TEXT,
        title TEXT,
        content TEXT,
        url TEXT,
        source TEXT,
        news_count INTEGER DEFAULT 1,
        is_breaking INTEGER DEFAULT 0,
        break_reason TEXT,
        sentiment TEXT DEFAULT 'neutral',
        sentiment_score REAL DEFAULT 0.5,
        trade_signal TEXT DEFAULT 'watch',
        impact_duration TEXT DEFAULT 'medium-term',
        event_type TEXT DEFAULT '其他',
        related_stocks TEXT DEFAULT '[]',
        reasoning TEXT,
        volume_verified INTEGER DEFAULT 0,
        volume_conflict INTEGER DEFAULT 0,
        llm_raw TEXT,
        created_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_timestamp ON signals(timestamp DESC);
      CREATE INDEX IF NOT EXISTS idx_industry ON signals(industry);
      CREATE INDEX IF NOT EXISTS idx_breaking ON signals(is_breaking);
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    industry TEXT,
                    title TEXT,
                    content TEXT,
                    url TEXT,
                    source TEXT,
                    news_count INTEGER DEFAULT 1,
                    is_breaking INTEGER DEFAULT 0,
                    break_reason TEXT,
                    sentiment TEXT DEFAULT 'neutral',
                    sentiment_score REAL DEFAULT 0.5,
                    trade_signal TEXT DEFAULT 'watch',
                    impact_duration TEXT DEFAULT 'medium-term',
                    event_type TEXT DEFAULT '其他',
                    related_stocks TEXT DEFAULT '[]',
                    reasoning TEXT,
                    volume_verified INTEGER DEFAULT 0,
                    volume_conflict INTEGER DEFAULT 0,
                    llm_raw TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON signals(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_industry ON signals(industry)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_breaking ON signals(is_breaking)")
            conn.commit()

    # ----------------------------------------------------------
    # 写入
    # ----------------------------------------------------------
    def save(self, signals: list) -> int:
        """批量写入信号，返回新增条数"""
        if not signals:
            return 0

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = 0

        with sqlite3.connect(self.db_path) as conn:
            for sig in signals:
                llm = sig.llm_summary or {}
                stocks = json.dumps(llm.get("relevant_stocks", []))

                # 去重（同一URL不重复写入）
                exists = conn.execute(
                    "SELECT 1 FROM signals WHERE url = ? LIMIT 1",
                    (sig.url,)
                ).fetchone()

                if exists:
                    continue

                conn.execute("""
                    INSERT INTO signals (
                        timestamp, industry, title, content, url, source,
                        news_count, is_breaking, break_reason,
                        sentiment, sentiment_score, trade_signal,
                        impact_duration, event_type, related_stocks, reasoning,
                        volume_verified, volume_conflict, llm_raw, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sig.timestamp, sig.industry, sig.title, sig.content, sig.url,
                    getattr(sig, "source", ""),
                    sig.news_count, int(sig.is_breaking), getattr(sig, "break_reason", ""),
                    llm.get("sentiment", "neutral"),
                    llm.get("sentiment_score", 0.5),
                    llm.get("trade_signal", "watch"),
                    llm.get("impact_duration", "medium-term"),
                    llm.get("event_type", "其他"),
                    stocks,
                    llm.get("reasoning", ""),
                    int(sig.volume_verified), int(sig.volume_conflict),
                    json.dumps(llm, ensure_ascii=False),
                    now,
                ))
                rows += 1

            conn.commit()

        logger.info(f"[SignalStore] 写入 {rows} 条新信号")
        return rows

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------
    def query(
        self,
        hours: int = 24,
        industry: Optional[str] = None,
        breaking_only: bool = False,
        signal: Optional[str] = None,     # "long", "short", "watch"
        conflict_only: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """查询信号"""
        sql = "SELECT * FROM signals WHERE 1=1"
        params: list = []

        if hours > 0:
            cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            sql += " AND timestamp >= ?"
            params.append(cutoff)

        if industry:
            sql += " AND industry = ?"
            params.append(industry)

        if breaking_only:
            sql += " AND is_breaking = 1"

        if signal:
            sql += " AND trade_signal = ?"
            params.append(signal)

        if conflict_only:
            sql += " AND volume_conflict = 1"

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_conflicts(self, hours: int = 24) -> list[dict]:
        """信号冲突：新闻看多但成交量不支持"""
        return self.query(hours=hours, conflict_only=True, limit=20)

    def get_breaking_signals(self, hours: int = 24) -> list[dict]:
        """突发信号"""
        return self.query(hours=hours, breaking_only=True, limit=20)

    def get_long_signals(self, hours: int = 24) -> list[dict]:
        """看多信号"""
        return self.query(hours=hours, signal="long", limit=20)

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------
    def industry_stats(self, hours: int = 24) -> dict:
        """每个产业的信号统计"""
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT industry,
                       COUNT(*) as total,
                       SUM(is_breaking) as breaking,
                       SUM(CASE WHEN trade_signal = 'long' THEN 1 ELSE 0 END) as long_count,
                       SUM(CASE WHEN volume_conflict = 1 THEN 1 ELSE 0 END) as conflicts,
                       AVG(sentiment_score) as avg_sentiment
                FROM signals
                WHERE timestamp >= ?
                GROUP BY industry
                ORDER BY total DESC
            """, (cutoff,)).fetchall()

        return [
            {
                "industry": r[0] or "其他",
                "total": r[1],
                "breaking": r[2] or 0,
                "long_count": r[3] or 0,
                "conflicts": r[4] or 0,
                "avg_sentiment": round(r[5] or 0.5, 3),
            }
            for r in rows
        ]

    def signal_trend(self, industry: str, hours: int = 48) -> list[dict]:
        """指定产业近N小时的信号趋势（每小时一条）"""
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT strftime('%Y-%m-%d %H:00', timestamp) as hour,
                       COUNT(*) as total,
                       SUM(CASE WHEN trade_signal = 'long' THEN 1 ELSE 0 END) as long_signals,
                       AVG(sentiment_score) as sentiment
                FROM signals
                WHERE industry = ? AND timestamp >= ? AND timestamp IS NOT NULL AND timestamp != ''
                GROUP BY hour
                ORDER BY hour ASC
            """, (industry, cutoff)).fetchall()

        return [
            {
                "hour": r[0],
                "total": r[1],
                "long_signals": r[2] or 0,
                "sentiment": round(r[3] or 0.5, 3),
            }
            for r in rows
        ]

    # ----------------------------------------------------------
    # 清理
    # ----------------------------------------------------------
    def cleanup(self, keep_days: int = 7) -> int:
        """删除N天前的旧数据"""
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM signals WHERE timestamp < ?", (cutoff,))
            conn.commit()
        logger.info(f"[SignalStore] 清理 {cur.rowcount} 条旧数据")
        return cur.rowcount
