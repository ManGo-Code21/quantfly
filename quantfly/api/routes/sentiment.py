# -*- encoding: utf-8 -*-
"""
情绪 + 新闻 API
=================
GET /sentiment/index   — 市场情绪指数
GET /sentiment/history — 历史情绪曲线
GET /sentiment/news    — 最新财经新闻（带情感标签）
"""
from fastapi import APIRouter, Query
import logging
import time
import requests

logger = logging.getLogger("API.Sentiment")
router = APIRouter(prefix="/sentiment")

EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}


@router.get("/index")
async def sentiment_index():
    """综合情绪指数"""
    from quantfly.sentiment.market_sentiment import get_market_sentiment
    return get_market_sentiment()


@router.get("/history")
async def sentiment_history(days: int = Query(20, ge=1, le=60)):
    """历史情绪曲线"""
    from quantfly.sentiment.market_sentiment import MarketSentiment
    ms = MarketSentiment()
    return {"history": ms.get_history(days)}


@router.get("/news")
async def sentiment_news(limit: int = Query(20, ge=1, le=50)):
    """最新财经新闻（带情感标签）"""
    news = _fetch_latest_news(limit)
    # 情感标注
    for n in news:
        n["sentiment"] = _classify_sentiment(n.get("title", "") + n.get("summary", ""))
    return {"news": news, "timestamp": time.time()}


NEWS_KEYWORDS = {
    "positive": ["利好", "涨停", "大涨", "突破", "增持", "回购", "业绩预增", "超预期", "签约", "中标", "获批"],
    "negative": ["利空", "跌停", "大跌", "减持", "处罚", "亏损", "退市", "违约", "暴雷", "被查", "诉讼", "下调"],
    "neutral": ["公告", "披露", "发布", "召开", "审议", "通过"],
}


def _classify_sentiment(text: str) -> str:
    """基于关键词的简单情感标注"""
    pos = sum(text.count(k) for k in NEWS_KEYWORDS["positive"])
    neg = sum(text.count(k) for k in NEWS_KEYWORDS["negative"])
    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


def _fetch_latest_news(limit: int = 20) -> list:
    """获取最新财经新闻"""
    try:
        # 东方财富快讯
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        r = requests.get(url, params={
            "sr": -1, "page_size": limit, "page_index": 1,
            "ann_type": "A", "client_source": "web",
        }, headers=EM_HEADERS, timeout=10)
        data = r.json()
        items = data.get("data", {}).get("list", [])
        return [
            {
                "title": it.get("title", ""),
                "summary": it.get("summary", ""),
                "time": it.get("notice_date", ""),
                "code": it.get("codes", [{}])[0].get("stock_code", "") if it.get("codes") else "",
                "url": f"https://data.eastmoney.com/notices/detail/{it.get('art_code', '')}.html",
            }
            for it in items[:limit]
        ]
    except Exception as e:
        logger.warning(f"新闻获取失败: {e}")
        return _fallback_news(limit)


def _fallback_news(limit: int) -> list:
    """备用新闻源"""
    try:
        import akshare as ak
        df = ak.stock_info_global_cls()
        if df is not None and not df.empty:
            return [
                {"title": str(row.get("title", "")), "summary": "",
                 "time": str(row.get("time", "")), "code": "", "url": ""}
                for _, row in df.head(limit).iterrows()
            ]
    except:
        pass
    return [{"title": "新闻服务暂不可用", "summary": "", "time": "", "code": "", "url": ""}]
