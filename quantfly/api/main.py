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
from quantfly.api.routes import hot_topics, screener, backtest, kline, trading, live
app.include_router(hot_topics.router)
app.include_router(screener.router)
app.include_router(backtest.router)
app.include_router(kline.router)
app.include_router(trading.router)
app.include_router(live.router)

# Static files (frontend)
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


@app.get("/")
async def root():
    return {
        "name": "QuantFly",
        "version": "0.2.0",
        "endpoints": [
            "/live/health",
            "/live/rank?days=300",
            "/live/factors",
            "/hot/topics",
            "/hot/industries",
            "/screener/screen/{industry}",
            "/backtest/run/{industry}",
            "/kline/{code}",
        ],
        "docs": "/docs",
        "dashboard": "/static/index.html",
    }
