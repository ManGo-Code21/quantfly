# -*- encoding: utf-8 -*-
"""FastAPI 路由 — 回测"""
from fastapi import APIRouter, Query
from quantfly.backtest.engine import BacktestBroker, ScreenerBacktestRunner
from quantfly.screener.stock_picker import get_kline_em, TopicDrivenScreener

router = APIRouter(prefix="/api/backtest", tags=["回测"])


@router.get("/run/{industry}")
async def run_backtest(
    industry: str,
    hold_days: int = Query(3, ge=1, le=10),
    top_n: int = Query(5, ge=1, le=10),
):
    """运行热点选股回测"""
    import numpy as np
    
    screener = TopicDrivenScreener()
    results = screener.screen(industry, top_n=top_n)
    buyable = [r for r in results if r.get("is_buyable")]

    trades = []
    for r in buyable:
        code = r["code"]
        df = get_kline_em(code, count=200)
        if df.empty or len(df) < hold_days + 1:
            continue
        
        close = df["close"].values
        buy_price = close[-1]
        sell_price = close[-1 - hold_days]
        ret_pct = (sell_price - buy_price) / buy_price * 100
        
        trades.append({
            "code": code,
            "name": r["name"],
            "score": r["total_score"],
            "chg_pct": r.get("chg_pct", 0),
            "buy_price": round(buy_price, 2),
            "sell_price": round(sell_price, 2),
            "return_pct": round(ret_pct, 2),
        })
    
    rets = [t["return_pct"] for t in trades]
    return {
        "industry": industry,
        "hold_days": hold_days,
        "total_trades": len(trades),
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else 0,
        "avg_return": round(float(np.mean(rets)), 2) if rets else 0,
        "trades": trades,
    }
