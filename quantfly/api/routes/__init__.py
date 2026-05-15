"""
API路由模块
"""
from quantfly.api.routes import hot_topics, screener, backtest, trading, kline, strategy, sentiment, monitor

kline_router = kline.router
sentiment_router = sentiment.router
monitor_router = monitor.router
