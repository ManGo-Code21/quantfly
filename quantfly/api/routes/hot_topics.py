# -*- encoding: utf-8 -*-
"""
热点监控API路由
GET /api/hot-topics/report — 生成热点选股报告
GET /api/hot-topics/industries — 获取热点产业列表
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import logging

logger = logging.getLogger("API.HotTopics")

router = APIRouter()


class ReportResponse(BaseModel):
    report: str
    generated_at: str


@router.get("/report", response_model=ReportResponse)
async def get_report():
    """生成热点选股报告"""
    try:
        from quantfly.hot_topics.monitor import HotTopicMonitor
        from quantfly.hot_topics.analyzer import TopicAnalyzer
        from quantfly.hot_topics.screener import TopicDrivenScreener

        monitor = HotTopicMonitor()
        news_items = monitor.fetch_all()

        analyzer = TopicAnalyzer()
        analysis = analyzer.analyze(news_items)

        screener = TopicDrivenScreener()
        top_industries = sorted(
            [(ind, info) for ind, info in analysis.items()
             if ind != '其他' and info.get('count', 0) > 0],
            key=lambda x: x[1].get('opportunity_score', 0),
            reverse=True,
        )[:3]

        all_signals = []
        for industry, _ in top_industries:
            try:
                results = screener.screen(industry, top_n=5)
                for r in results:
                    all_signals.append({**r, 'industry': industry})
            except Exception:
                continue

        all_signals.sort(key=lambda x: x['total_score'], reverse=True)

        lines = []
        lines.append(f"📊 **热点选股报告**")
        lines.append(f"🕐 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append(f"**📰 今日热点** ({len(news_items)}条)")
        lines.append(f"**🏭 产业热度** ({len(top_industries)}个)")
        lines.append(f"**🎯 选股信号** ({len(all_signals)}只)")

        return ReportResponse(
            report="\n".join(lines),
            generated_at=datetime.now().isoformat(),
        )
    except Exception as e:
        logger.error(f"生成报告失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/industries")
async def get_industries():
    """获取热点产业列表"""
    try:
        from quantfly.hot_topics.analyzer import TopicAnalyzer
        from quantfly.hot_topics.monitor import HotTopicMonitor

        monitor = HotTopicMonitor()
        news_items = monitor.fetch_all()
        analyzer = TopicAnalyzer()
        analysis = analyzer.analyze(news_items)

        industries = []
        for industry, info in analysis.items():
            if industry == '其他' or info.get('count', 0) == 0:
                continue
            industries.append({
                "name": industry,
                "news_count": info.get('count', 0),
                "score": info.get('opportunity_score', 0),
                "sentiment": info.get('sentiment', {}).get('direction', '未知'),
            })

        industries.sort(key=lambda x: x['score'], reverse=True)
        return {"industries": industries, "total": len(industries)}
    except Exception as e:
        logger.error(f"获取产业失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
