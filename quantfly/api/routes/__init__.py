"""
API路由模块
"""
from quantfly.api.routes import hot_topics, screener, backtest, trading, kline

kline_router = kline.router
