# -*- encoding: utf-8 -*-
"""
扩展因子 IC 分析 — 资金流向 + 基本面因子
==========================================
调用 Windows QMT API 获取数据，计算扩展因子并做 IC 分析

用法:
  python extended_factor_ic.py --days 300 --output output/extended_ic.json
"""
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("ExtendedIC")

QMT_HOST = "10.6.98.168:8765"

# ============================================================
# 资金流向因子（从 /data/money_flow 获取）
# ============================================================
MONEY_FLOW_FACTORS = [
    "main_net_5d",      # 5日主力净流入均值（归一化）
    "main_net_10d",     # 10日主力净流入均值
    "main_net_ratio",   # 主力净流入 / (|主力| + |小单| + 1)
    "super_large_5d",   # 5日超大单净流入
    "large_5d",         # 5日大单净流入
    "small_5d",         # 5日小单净流入（反向）
    "money_flow_5d",    # 资金流向综合得分
]

# ============================================================
# 基本面因子（从 /data/financial 获取）
# ============================================================
FINANCIAL_FACTORS = [
    "pe",               # 市盈率
    "pb",               # 市净率
    "roe",              # 净资产收益率
    "revenue_growth",   # 营收增速
    "profit_growth",    # 利润增速
]

# ============================================================
# 数据获取
# ============================================================

def get_csi500_stocks() -> list[str]:
    """获取中证500成分股"""
    try:
        import akshare as ak
        df = ak.index_stock_cons(symbol="000905")
        return df["品种代码"].tolist()
    except Exception as e:
        logger.warning(f"中证500获取失败: {e}")
        return []


def fetch_live_factors(codes: list[str], days: int = 30) -> dict:
    """从 QMT API 获取技术因子（含 future_ret）"""
    try:
        r = requests.get(
            f"http://{QMT_HOST}/live/factors",
            json={"codes": codes, "days": days},
            timeout=60
        )
        return r.json()
    except Exception as e:
        logger.warning(f"技术因子获取失败: {e}")
        return {}


def fetch_money_flow(codes: list[str], days: int = 10) -> dict:
    """从 QMT API 获取资金流向"""
    try:
        r = requests.get(
            f"http://{QMT_HOST}/data/money_flow",
            params={"codes": ",".join(codes), "days": days},
            timeout=60
        )
        return r.json().get("money_flow", {})
    except Exception as e:
        logger.warning(f"资金流向获取失败: {e}")
        return {}


def fetch_financial(codes: list[str]) -> dict:
    """从 QMT API 获取基本面数据"""
    try:
        r = requests.get(
            f"http://{QMT_HOST}/data/financial",
            params={
                "codes": ",".join(codes),
                "fields": "pe,pb,roe,revenue_growth,profit_growth"
            },
            timeout=30
        )
        return r.json().get("data", {})
    except Exception as e:
        logger.warning(f"基本面获取失败: {e}")
        return {}


# ============================================================
# 因子计算
# ============================================================

def calc_money_flow_factors(mf_data: dict) -> pd.DataFrame:
    """计算资金流向因子"""
    records = []
    for code, days_data in mf_data.items():
        if not days_data or len(days_data) < 5:
            continue
        df = pd.DataFrame(days_data)
        df = df.sort_values("date")

        main_net = df["main_net"].astype(float)
        super_large = df["super_large"].astype(float)
        large = df["large"].astype(float)
        small = df["small"].astype(float)

        m5 = main_net.rolling(5, min_periods=3).mean()
        m10 = main_net.rolling(10, min_periods=5).mean()
        s5 = super_large.rolling(5, min_periods=3).mean()
        l5 = large.rolling(5, min_periods=3).mean()
        sm5 = small.rolling(5, min_periods=3).mean()

        denom = main_net.abs() + small.abs() + 1
        ratio = main_net / denom

        # 资金流向综合得分：标准化主力净流入
        mf_score = (main_net - main_net.mean()) / (main_net.std() + 1e-10)

        records.append({
            "code": code,
            "main_net_5d": m5.iloc[-1] if pd.notna(m5.iloc[-1]) else 0,
            "main_net_10d": m10.iloc[-1] if pd.notna(m10.iloc[-1]) else 0,
            "main_net_ratio": ratio.iloc[-1] if pd.notna(ratio.iloc[-1]) else 0,
            "super_large_5d": s5.iloc[-1] if pd.notna(s5.iloc[-1]) else 0,
            "large_5d": l5.iloc[-1] if pd.notna(l5.iloc[-1]) else 0,
            "small_5d": sm5.iloc[-1] if pd.notna(sm5.iloc[-1]) else 0,
            "money_flow_5d": mf_score.iloc[-1] if pd.notna(mf_score.iloc[-1]) else 0,
        })
    return pd.DataFrame(records)


# ============================================================
# IC 计算
# ============================================================

def calc_cross_section_ic(df_all: pd.DataFrame, factor_cols: list) -> dict:
    """截面 IC：每天计算因子与未来收益的 Pearson 相关系数"""
    daily_ics = {col: [] for col in factor_cols if col in df_all.columns}

    for date, group in df_all.groupby("date"):
        if len(group) < 10:
            continue
        future = group["future_ret"].values
        for col in daily_ics:
            vals = group[col].values.astype(float)
            mask = np.isfinite(vals) & np.isfinite(future)
            if mask.sum() < 5:
                continue
            corr = np.corrcoef(vals[mask], future[mask])[0, 1]
            if np.isfinite(corr):
                daily_ics[col].append(corr)

    result = {}
    for col, ic_list in daily_ics.items():
        if ic_list:
            ic_arr = np.array(ic_list)
            result[col] = {
                "ic_mean": float(np.mean(ic_arr)),
                "ic_std": float(np.std(ic_arr)),
                "ic_ir": float(np.mean(ic_arr) / (np.std(ic_arr) + 1e-10)),
                "n_days": len(ic_list),
                "positive_rate": float(np.mean(ic_arr > 0)),
            }
    return result


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=300)
    parser.add_argument("--stocks", type=int, default=100)
    parser.add_argument("--output", default="output/extended_ic.json")
    args = parser.parse_args()

    logger.info(f"=== 扩展因子IC分析 | {args.days}天 | {args.stocks}只 ===")

    # 1. 股票列表
    stocks = get_csi500_stocks()[:args.stocks]
    if not stocks:
        logger.error("获取股票列表失败"); return
    logger.info(f"股票池: {len(stocks)} 只")

    # 2. 获取技术因子（含 future_ret）
    logger.info("获取技术因子（含future_ret标签）...")
    factors_data = fetch_live_factors(stocks, days=30)
    n_factors = len(factors_data.get("factors", {}))
    logger.info(f"技术因子获取: {n_factors} 只")

    # 3. 获取资金流向
    logger.info("获取资金流向...")
    mf_all = {}
    batch_size = 50
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i+batch_size]
        mf = fetch_money_flow(batch, days=10)
        mf_all.update(mf)
        time.sleep(0.5)
    logger.info(f"资金流向获取: {len(mf_all)} 只")

    # 4. 获取基本面
    logger.info("获取基本面数据...")
    fin_data = fetch_financial(stocks)
    logger.info(f"基本面获取: {len(fin_data)} 只")

    # 5. 构建立体数据
    tech_records = []
    for code, fd in factors_data.get("factors", {}).items():
        if not fd:
            continue
        tech_records.append({"code": code, "date": fd.get("date", ""), **fd})

    tech_df = pd.DataFrame(tech_records)
    if "date" in tech_df.columns:
        tech_df["date"] = pd.to_datetime(tech_df["date"], errors="coerce")

    # 资金流向因子
    mf_df = calc_money_flow_factors(mf_all)

    # 基本面
    fin_records = [{"code": code, **vals} for code, vals in fin_data.items() if vals]
    fin_df = pd.DataFrame(fin_records)

    # 合并
    if not mf_df.empty:
        combined = tech_df.merge(mf_df, on="code", how="left")
    else:
        combined = tech_df
    if not fin_df.empty:
        combined = combined.merge(fin_df, on="code", how="left")

    # 清理
    for col in list(combined.columns):
        if combined[col].dtype == object and col not in ("code", "date"):
            combined[col] = pd.to_numeric(combined[col], errors="coerce")
    combined = combined.replace([np.inf, -np.inf], np.nan).fillna(0)

    logger.info(f"合并后记录: {len(combined)} 条, 列数: {len(combined.columns)}")

    # 6. IC 分析
    all_factor_cols = MONEY_FLOW_FACTORS + FINANCIAL_FACTORS
    all_factor_cols = [c for c in all_factor_cols if c in combined.columns]
    all_factor_cols.append("future_ret")  # 确保标签列存在

    ic_results = calc_cross_section_ic(combined, all_factor_cols)

    # 7. 打印
    logger.info("\n" + "=" * 65)
    logger.info("📊 扩展因子 IC 排名（资金流向 + 基本面）")
    logger.info("=" * 65)
    ranked = sorted(ic_results.items(), key=lambda x: abs(x[1]["ic_mean"]), reverse=True)
    logger.info(f"{'因子':<22} {'IC均值':>8} {'IR':>7} {'胜率':>6}  评价")
    logger.info("-" * 65)
    for name, d in ranked:
        strength = "★★★强" if abs(d["ic_mean"]) > 0.05 \
                   else "★★中" if abs(d["ic_mean"]) > 0.03 \
                   else "★弱" if abs(d["ic_mean"]) > 0.02 else "—无效"
        flag = "✅" if d["ic_mean"] > 0.03 else "🔻" if d["ic_mean"] < -0.03 else "  "
        logger.info(
            f"{flag}{name:<20} {d['ic_mean']:>+8.4f} {d['ic_ir']:>+7.3f} "
            f"{d['positive_rate']*100:>5.0f}%  {strength}"
        )

    # 8. 保存
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    report = {
        "meta": {
            "n_stocks": len(stocks),
            "n_records": len(combined),
            "generated_at": datetime.now().isoformat(),
        },
        "ranked": [{"factor": n, **d} for n, d in ranked],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"报告: {args.output}")


if __name__ == "__main__":
    main()
