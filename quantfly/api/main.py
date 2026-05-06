"""QuantFly FastAPI Application"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(
    title="QuantFly",
    description="A股量化交易框架",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
from quantfly.api.routes import hot_topics, screener, backtest, kline, trading, live, data
app.include_router(hot_topics.router)
app.include_router(screener.router)
app.include_router(backtest.router)
app.include_router(kline.router)
app.include_router(trading.router)
app.include_router(live.router)
app.include_router(data.router)

# Static files (frontend)
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


@app.get("/")
async def root():
    return {
        "name": "QuantFly",
        "version": "0.2.0",
        "endpoints": {
            "live": [
                "GET  /live/health",
                "GET  /live/rank?days=300&stocks=500",
                "POST /live/factors  {\"codes\":[\"000001\"],\"days\":100}",
            ],
            "data": [
                "GET  /data/quote?codes=000001,600000",
                "GET  /data/kline?code=000001&period=1d&count=100",
                "GET  /data/minute?code=000001&period=5m&count=240",
                "GET  /data/tick?code=000001&count=100",
                "GET  /data/sectors",
                "GET  /data/sector/stocks?sector=沪深300",
                "GET  /data/index_weight?index=000300",
                "GET  /data/financial?codes=000001",
                "GET  /data/calendar",
            ],
        },
        "docs": "/docs",
    }
