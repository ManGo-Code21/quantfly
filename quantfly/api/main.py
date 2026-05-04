# -*- encoding: utf-8 -*-
"""
QuantFly API Server — FastAPI后端
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os
import logging

from quantfly.api.routes import hot_topics, screener, backtest, trading, kline

logger = logging.getLogger("QuantFlyAPI")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantFly API 启动")
    yield
    logger.info("QuantFly API 关闭")


app = FastAPI(
    title="QuantFly",
    description="A股量化交易框架 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
static_dir = os.path.join(os.path.dirname(__file__), "..", "ui", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "QuantFly API", "docs": "/docs"}


app.include_router(hot_topics.router, prefix="/api/hot-topics")
app.include_router(screener.router, prefix="/api/screener")
app.include_router(backtest.router, prefix="/api/backtest")
app.include_router(kline.router, prefix="/api/backtest")
app.include_router(trading.router, prefix="/api/trading")
