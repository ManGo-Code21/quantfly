# -*- encoding: utf-8 -*-
"""
MiniQMT 增强策略模板 — V13 牛熊切换
====================================
放到 MiniQMT 的 Python 策略编辑器中运行。

核心逻辑:
  1. 每10个交易日调仓一次
  2. 调用 V13 获取信号（板块动量 + 选股 + 仓位）
  3. 🐂牛市全仓 → 🐻熊市30%

买入金额: 账户总资产 × 目标仓位 ÷ 4只 = 每只等权
"""

import time
import datetime
import pandas as pd
import numpy as np

# ── V13 策略模块 ──
# 把 v13_live.py 放在 MiniQMT 的 Python 目录下
from v13_live import V13Strategy

# ── MiniQMT 全局变量 ──
v13 = V13Strategy()
REBALANCE_DAYS = 10          # 调仓周期
TOP_N = 4                    # 持有股票数
last_rebalance_date = None   # 上次调仓日期
current_target_codes = []    # 当前持有的目标股
current_position = 0.5       # 当前仓位（初始50%）


def init(context):
    """策略初始化"""
    log.info("=" * 50)
    log.info("V13 牛熊切换策略 启动")
    log.info("🐂 沪深300 > MA60 → 全仓进攻")
    log.info("🐻 沪深300 < MA60 → 30%防御")
    log.info("=" * 50)

    # 拉取初始数据
    # ⚠️ MiniQMT init() 有超时限制，数据由 handle_data() 中的 v13.run() 自动拉取
    # 如需调试可手动调用: v13.fetch_all()


def handle_data(context, data):
    """每个交易日执行"""
    global last_rebalance_date, current_target_codes, current_position

    today = context.current_dt.date()

    # ── 不是调仓日 → 跳过 ──
    if not should_rebalance(today):
        return

    last_rebalance_date = today
    log.info(f"\n{'='*40}")
    log.info(f"📅 {today} 调仓日")

    # ── 1. 刷新数据 ──
    v13.fetch_all()

    # ── 2. 获取 V13 信号 ──
    result = v13.run(current_position=current_position)

    if 'error' in result:
        log.error(f"V13 信号异常: {result['error']}")
        return

    # ── 3. 打印诊断 ──
    log.info(f"模式: {result['mode']}")
    log.info(f"市场温度: {result['temperature']:.1f}")
    log.info(f"波动率: {result['volatility']:.1f}")
    log.info(f"目标仓位: {result['target_position']*100:.0f}%")
    log.info(f"最强板块: {', '.join(result['top_sectors'][:3])}")

    picks = result['picks']
    if not picks:
        log.warning("无选股信号 → 空仓")
        sell_all(context)
        current_target_codes = []
        current_position = result['target_position']
        return

    target_codes = [p['code'] for p in picks]
    log.info(f"选股: {', '.join(target_codes)}")
    for p in picks:
        log.info(f"  {p['code']} {p['sector']} {p['tier']} score={p['score']:.3f}")

    if result.get('filtered'):
        log.info(f"质量过滤: {result['filtered']}")

    # ── 4. 计算买入金额 ──
    total_asset = context.portfolio.total_value
    target_value = total_asset * result['target_position']
    per_stock_value = target_value / len(target_codes)

    log.info(f"总资产: ¥{total_asset:,.0f}")
    log.info(f"投入: ¥{target_value:,.0f} ({result['target_position']*100:.0f}%)")
    log.info(f"每只: ¥{per_stock_value:,.0f}")

    # ── 5. 调仓 ──
    # 卖出不在目标列表的持仓
    sell_old_positions(context, target_codes)

    # 买入目标股票
    for code in target_codes:
        buy_stock(context, code, per_stock_value)

    # 更新状态
    current_target_codes = target_codes
    current_position = result['target_position']

    log.info(f"调仓完成 ✓")


# ============================================================
# 辅助函数
# ============================================================
def should_rebalance(today):
    """判断今天是否需要调仓"""
    global last_rebalance_date

    if last_rebalance_date is None:
        return True

    # 从上次调仓日到今天之间的交易日数
    days_since = len(pd.bdate_range(last_rebalance_date, today)) - 1
    return days_since >= REBALANCE_DAYS


def sell_all(context):
    """清空所有持仓"""
    for code in list(context.portfolio.positions.keys()):
        order_target_value(code, 0)
        log.info(f"  卖出 {code}")


def sell_old_positions(context, target_codes):
    """卖出不在目标列表的持仓"""
    for code in list(context.portfolio.positions.keys()):
        if code not in target_codes:
            order_target_value(code, 0)
            log.info(f"  卖出 {code}")


def buy_stock(context, code, target_value):
    """买入单只股票到目标市值"""
    # 检查是否可交易
    current_data = get_current_data()
    if code not in current_data:
        log.warning(f"  ⚠️ {code} 无行情数据，跳过")
        return

    stock = current_data[code]
    if stock is None or stock.paused or stock.is_st:
        log.warning(f"  ⚠️ {code} 停牌/ST，跳过")
        return

    # MiniQMT: order_target_value(code, 目标金额)
    order_target_value(code, target_value)
    log.info(f"  买入 {code} → ¥{target_value:,.0f}")


def log_performance(context):
    """定期打印绩效"""
    p = context.portfolio
    returns = (p.total_value / p.starting_cash - 1) * 100
    log.info(f"📊 绩效: ¥{p.total_value:,.0f} ({returns:+.1f}%) | "
             f"仓位: {p.positions_value/p.total_value*100:.0f}%")
