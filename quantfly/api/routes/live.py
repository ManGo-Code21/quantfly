# -*- encoding: utf-8 -*-
"""
实时因子排名 API（QMT数据源）

GET  /api/live/rank?days=300        → 返回全量因子IC排名JSON
GET  /api/live/health               → 健康检查（含QMT连接状态）
POST /api/live/factors              → 获取指定股票的因子值
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import time

logger = logging.getLogger("API.Live")

router = APIRouter()


@router.get("/live/health")
async def live_health():
    """健康检查：测QMT连接和数据可用性"""
    try:
        import xtquant.xtdata as xtdata
        xtdata.connect()
        # 快速测试：拉一只股票的最近5根K线
        test = xtdata.get_market_data(
            stock_list=["000001.SZ"],
            period="1d",
            count=5,
            field_list=["close"],
            dividend_type="front",
        )
        qmt_ok = test is not None and "close" in test and not test["close"].empty
    except Exception as e:
        qmt_ok = False
        logger.warning(f"QMT health check failed: {e}")

    return {
        "status": "ok" if qmt_ok else "degraded",
        "qmt_connected": qmt_ok,
        "timestamp": time.time(),
    }


@router.get("/live/rank")
async def live_rank(
    days: int = Query(default=300, ge=30, le=500),
    stocks: Optional[int] = Query(default=None, ge=50, le=500),
):
    """
    QMT实时因子IC排名

    使用中证500成分股，计算21个因子的截面IC排名。
    返回与 analyze_factor_ic.py 相同格式的JSON。
    """
    try:
        from analyze_factor_ic import (
            get_xtquant_daily,
            get_sample_stocks,
            calc_features,
            calc_cross_section_ic,
            rank_factors,
            FEATURE_COLS,
        )
        import numpy as np
        import pandas as pd

        t0 = time.time()

        # 1. 股票列表
        all_codes = get_sample_stocks(n=stocks or 500)
        if not all_codes:
            raise HTTPException(status_code=500, detail="获取股票列表失败")

        n_stocks = min(stocks or 500, len(all_codes))
        codes = all_codes[:n_stocks]
        logger.info(f"Live rank: {n_stocks} stocks, {days} days")

        # 2. QMT数据
        data = get_xtquant_daily(codes, count=days + 30)
        if len(data) < 10:
            raise HTTPException(status_code=500, detail=f"有效数据不足: {len(data)}只")

        # 3. 因子计算
        all_features = []
        for code, df in data.items():
            feats = calc_features(df)
            if not feats.empty:
                feats["code"] = code
                all_features.append(feats)

        if not all_features:
            raise HTTPException(status_code=500, detail="无有效因子数据")

        df_all = pd.concat(all_features, ignore_index=True)
        for col in FEATURE_COLS + ["future_ret"]:
            if col in df_all.columns:
                df_all[col] = df_all[col].replace([np.inf, -np.inf], np.nan).fillna(0)

        # 4. IC分析
        ic_results = calc_cross_section_ic(df_all)
        ranked = rank_factors(ic_results)

        elapsed = time.time() - t0
        logger.info(f"Live rank done: {len(df_all)} records, {elapsed:.1f}s")

        return {
            "meta": {
                "source": "qmt",
                "n_stocks": len(data),
                "n_records": len(df_all),
                "elapsed_s": round(elapsed, 1),
            },
            "ranked": [
                {"factor": name, **{k: round(v, 6) if isinstance(v, float) else v for k, v in d.items()}}
                for name, d in ranked
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Live rank failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/live/factors")
async def live_factors(payload: dict):
    """
    获取指定股票的因子值（POST）

    Body: {"codes": ["000001", "000002"], "days": 300}
    Returns: {"000001": {"ret5": 0.01, ...}, ...}
    """
    try:
        from analyze_factor_ic import get_xtquant_daily, calc_features

        codes = payload.get("codes", [])
        days = payload.get("days", 100)

        if not codes or len(codes) > 50:
            raise HTTPException(status_code=400, detail="codes: 1~50 required")

        data = get_xtquant_daily(codes, count=days + 30)
        if not data:
            raise HTTPException(status_code=500, detail="QMT数据获取失败")

        result = {}
        for code, df in data.items():
            feats = calc_features(df)
            if not feats.empty:
                last = feats.iloc[-1].to_dict()
                result[code] = {k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in last.items()
                                if k != "code"}

        return {"factors": result, "n_stocks": len(result)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Live factors failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
