# -*- encoding: utf-8 -*-
"""
实时因子排名 API（QMT数据源）

GET  /live/health               → 健康检查（含QMT连接状态）
GET  /live/rank?days=300        → 提交IC排名任务，返回task_id
GET  /live/status/{task_id}     → 查询任务进度
GET  /live/result/{task_id}     → 获取任务结果
POST /live/factors              → 获取指定股票的因子值（同步，<30只快速返回）
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import threading
import time
import uuid

logger = logging.getLogger("API.Live")

router = APIRouter()

# ── 任务存储 ──────────────────────────────────────────────
_tasks: dict = {}
_lock = threading.Lock()


def _run_rank_task(task_id: str, n_stocks: int, days: int):
    """后台执行 IC 排名计算"""
    try:
        _update_task(task_id, "fetching_stocks", 0)
        from analyze_factor_ic import (
            get_xtquant_daily, get_sample_stocks, calc_features,
            calc_cross_section_ic, rank_factors, FEATURE_COLS,
            fetch_money_flow_batch, merge_money_flow_factors,
        )
        import numpy as np
        import pandas as pd

        # 1. 股票列表
        codes = get_sample_stocks(n=n_stocks)
        if not codes:
            _update_task(task_id, "error", 0, error="获取股票列表失败")
            return
        _update_task(task_id, "downloading", 10)

        # 2. QMT数据
        data = get_xtquant_daily(codes, count=days + 30)
        if len(data) < 10:
            _update_task(task_id, "error", 0, error=f"有效数据不足: {len(data)}只")
            return
        _update_task(task_id, "calculating", 40, progress_detail=f"{len(data)} stocks")

        # 3. 因子计算
        all_features = []
        for code, df in data.items():
            feats = calc_features(df)
            if not feats.empty:
                feats["code"] = code
                all_features.append(feats)

        if not all_features:
            _update_task(task_id, "error", 0, error="无有效因子数据")
            return
        _update_task(task_id, "analyzing", 80)

        df_all = pd.concat(all_features, ignore_index=True)
        for col in FEATURE_COLS + ["future_ret"]:
            if col in df_all.columns:
                df_all[col] = df_all[col].replace([np.inf, -np.inf], np.nan).fillna(0)

        # 3b. 资金流向因子（可选）
        try:
            mf_data = fetch_money_flow_batch(codes[:n_stocks], days=days + 30)
            if mf_data:
                df_all = merge_money_flow_factors(df_all, mf_data)
        except Exception:
            pass
        for col in FEATURE_COLS:
            if col not in df_all.columns:
                df_all[col] = 0

        # 4. IC分析
        ic_results = calc_cross_section_ic(df_all)
        ranked = rank_factors(ic_results)

        _update_task(task_id, "done", 100, result={
            "meta": {
                "source": "qmt",
                "n_stocks": len(data),
                "n_records": int(len(df_all)),
            },
            "ranked": [
                {"factor": name, **{k: round(v, 6) if isinstance(v, float) else v
                                    for k, v in d.items()}}
                for name, d in ranked
            ],
        })

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        _update_task(task_id, "error", 0, error=str(e))


def _update_task(task_id: str, status: str, progress: int,
                 result=None, error=None, progress_detail=None):
    with _lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "result": result,
            "error": error,
            "progress_detail": progress_detail,
            "updated_at": time.time(),
        }


# ── 路由 ──────────────────────────────────────────────────

@router.get("/live/health")
async def live_health():
    """健康检查：测QMT连接和数据可用性"""
    try:
        from xtquant import xtdata
        xtdata.connect()
        test = xtdata.get_market_data(
            stock_list=["000001.SZ"],
            period="1d", count=5,
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
    stocks: int = Query(default=500, ge=50, le=500),
):
    """
    提交因子IC排名任务（异步）

    返回 task_id，用 /live/status/{task_id} 查询进度，
    /live/result/{task_id} 获取结果。
    500只×300天约需2-3分钟。
    """
    task_id = uuid.uuid4().hex[:12]
    _update_task(task_id, "queued", 0)
    t = threading.Thread(target=_run_rank_task, args=(task_id, stocks, days), daemon=True)
    t.start()
    logger.info(f"Task {task_id}: {stocks} stocks, {days} days")
    return {
        "task_id": task_id,
        "status": "queued",
        "check_url": f"/live/status/{task_id}",
        "result_url": f"/live/result/{task_id}",
    }


@router.get("/live/status/{task_id}")
async def live_status(task_id: str):
    """查询任务进度"""
    with _lock:
        task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task["progress"],
        "progress_detail": task.get("progress_detail"),
        "error": task.get("error"),
    }


@router.get("/live/result/{task_id}")
async def live_result(task_id: str):
    """获取任务结果"""
    with _lock:
        task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] == "error":
        raise HTTPException(status_code=500, detail=task.get("error", "Unknown error"))
    if task["status"] != "done":
        return {
            "task_id": task_id,
            "status": task["status"],
            "progress": task["progress"],
            "message": "Task not complete yet",
        }
    return task["result"]


@router.post("/live/factors")
async def live_factors(payload: dict):
    """
    获取指定股票的因子值（同步，小批量快速返回）

    Body: {"codes": ["000001", "000002"], "days": 100}
    """
    try:
        from analyze_factor_ic import get_xtquant_daily, calc_features

        codes = payload.get("codes", [])
        days = payload.get("days", 100)

        if not codes or len(codes) > 30:
            raise HTTPException(status_code=400, detail="codes: 1~30 required")

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
