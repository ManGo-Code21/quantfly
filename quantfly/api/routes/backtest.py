# -*- encoding: utf-8 -*-
"""
回测API路由
POST /api/backtest/run — 运行回测
GET  /api/backtest/results — 获取历史回测结果
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger("API.Backtest")

router = APIRouter()


class BacktestRequest(BaseModel):
    start_date: str = "20240101"
    end_date: str = "20250603"
    initial_cash: float = 1_000_000.0
    industry: Optional[str] = None  # None表示全市场


@router.post("/run")
async def run_backtest(req: BacktestRequest):
    """运行选股三原则回测"""
    try:
        from quantfly.backtest.engine import ScreenerBacktestRunner
        from quantfly.backtest.data_provider import get_kline_em, get_realtime_quotes, get_all_limit_up_codes
        import time

        START = req.start_date.replace("-", "")
        END = req.end_date.replace("-", "")

        runner = ScreenerBacktestRunner(initial_cash=req.initial_cash)

        # 加载数据
        stocks = {
            '000001': '平安银行',  '000002': '万科A',
            '600519': '贵州茅台',  '600036': '招商银行',
            '601318': '中国平安', '000858': '五粮液',
            '600276': '恒瑞医药', '002475': '立讯精密',
            '300750': '宁德时代', '601888': '中国中免',
            '300015': '爱尔眼科', '002594': '比亚迪',
            '601012': '隆基绿能', '600900': '长江电力',
            '601398': '工商银行', '600028': '中国石化',
            '601899': '紫金矿业', '002415': '海康威视',
            '300059': '东方财富', '601166': '兴业银行',
        }

        for code, name in stocks.items():
            df = get_kline_em(code, count=500)
            if not df.empty:
                runner.add_data(code, df)
            time.sleep(0.15)

        sd = f"{START[:4]}-{START[4:6]}-{START[6:]}"
        ed = f"{END[:4]}-{END[4:6]}-{END[6:]}"

        metrics = runner.run(sd, ed)
        trades = runner.broker.get_trade_history()

        return {
            "status": "success",
            "metrics": metrics,
            "total_trades": len(trades),
            "trades": trades[-20:],  # 最近20笔
        }
    except Exception as e:
        logger.error(f"回测失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/results")
async def get_backtest_results():
    """获取历史回测结果"""
    import json
    from pathlib import Path

    result_file = Path(__file__).parent.parent.parent.parent / "data" / "screener_backtest_result.json"
    if result_file.exists():
        with open(result_file, encoding="utf-8") as f:
            data = json.load(f)
        return data
    return {"metrics": {}, "trades": []}
