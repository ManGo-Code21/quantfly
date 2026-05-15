# -*- encoding: utf-8 -*-
"""
V13 独立下单器 — 本地运行 V13 策略 + MiniQMT 执行下单
=====================================================
用法:
    python v13_trader.py              # 单次执行
    python v13_trader.py --daemon     # 守护模式
    python v13_trader.py --dry-run    # 演习模式（只打印不下单）
    python v13_trader.py --daemon --dry-run  # 守护+演习

依赖: xtquant（QMT客户端自带）+ v13_live.py（同目录）
"""
import sys
import os
import time
import datetime
import sqlite3
import argparse

# ── QMT xtquant 路径 ──
QMT_BIN = r"D:\国金证券QMT交易端\bin.x64"
sys.path.insert(0, QMT_BIN)

# ── v13_live 路径 ──
V13_DIR = r"D:\国金证券QMT交易端\mpython"
sys.path.insert(0, V13_DIR)

# ── 配置 ──
QMT_PATH = r"D:\国金证券QMT交易端\userdata_mini"
ACCOUNT_ID = "8886001679"
CHECK_INTERVAL = 600  # 守护模式检查间隔（秒）
MAX_DAILY_LOSS = 0.05  # 单日亏损上限 5%
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trades.db')


class RiskController:
    """单日风控：累计亏损超过 MAX_DAILY_LOSS 则暂停当日交易"""

    def __init__(self):
        self.day_initial_value = None
        self.current_date = None
        self.blocked = False

    def update(self, total_value: float) -> tuple[float, bool]:
        """
        更新风控状态，返回 (当日盈亏率, 是否暂停交易)
        """
        today = datetime.date.today()
        if self.current_date != today:
            # 新交易日，重置
            self.current_date = today
            self.day_initial_value = total_value
            self.blocked = False

        if self.day_initial_value is None or self.day_initial_value <= 0:
            return 0.0, False

        pnl_pct = (total_value - self.day_initial_value) / self.day_initial_value
        if pnl_pct < -MAX_DAILY_LOSS:
            self.blocked = True

        return pnl_pct, self.blocked

    @property
    def is_blocked(self) -> bool:
        return self.blocked


def init_db():
    """初始化 SQLite 交易日志表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL,
            volume INTEGER,
            pnl REAL,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def log_trade(code: str, action: str, price: float, volume: int,
              pnl: float = 0.0, reason: str = ""):
    """写入一条交易日志到 SQLite"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO trades (date, code, action, price, volume, pnl, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.date.today().isoformat(), code, action, price, volume, pnl, reason)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠️ 日志写入失败: {e}")


def connect_trader():
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAccount

    session_id = int(time.time()) % 10000 + 1000
    xt_trader = XtQuantTrader(QMT_PATH, session_id)
    xt_trader.start()
    if xt_trader.connect() != 0:
        print("[ERROR] 交易连接失败")
        return None, None

    acc = StockAccount(ACCOUNT_ID, 'STOCK')
    if xt_trader.subscribe(acc) != 0:
        print("[ERROR] 账号订阅失败")
        return None, None

    print(f"[OK] 交易连接成功, 账号 {ACCOUNT_ID}")
    return xt_trader, acc


def get_positions(xt_trader, acc):
    positions = xt_trader.query_stock_positions(acc)
    return {p.stock_code: p.m_nCanUseVolume for p in positions if p.m_nCanUseVolume > 0}


def get_available_cash(xt_trader, acc):
    asset = xt_trader.query_stock_asset(acc)
    return asset.m_dCash if asset else 0


def get_current_price(code):
    from xtquant import xtdata
    tick = xtdata.get_full_tick([code])
    if code in tick:
        return tick[code]['lastPrice']
    return None


def execute_trades(xt_trader, acc, signals, dry_run=False, risk=None):
    from xtquant import xtconstant, xtdata

    picks = signals.get('picks', [])
    target_codes = [p['code'] for p in picks]
    target_position = signals.get('target_position', 0.5)

    if not target_codes:
        print("[INFO] 无选股信号，清仓")
        for code in list(get_positions(xt_trader, acc).keys()):
            sell(xt_trader, acc, code, get_positions(xt_trader, acc)[code],
                 dry_run=dry_run)
        return

    current_positions = get_positions(xt_trader, acc)
    available_cash = get_available_cash(xt_trader, acc)

    # 估算总资产
    total_value = available_cash
    for code, vol in current_positions.items():
        px = get_current_price(code)
        if px:
            total_value += vol * px

    # ── 风控检查 ──
    daily_pnl = 0.0
    if risk is not None:
        daily_pnl, blocked = risk.update(total_value)
        if blocked:
            print(f"\n{'='*50}")
            print(f"[RISK] ⛔ 单日亏损已达 {daily_pnl*100:.2f}%，超过上限 {MAX_DAILY_LOSS*100:.0f}%，暂停当日交易")
            print(f"总资产: ¥{total_value:,.0f} | 初始: ¥{risk.day_initial_value:,.0f}")
            log_trade("PORTFOLIO", "RISK_BLOCK", 0, 0, daily_pnl,
                      f"单日亏损 {daily_pnl*100:.2f}% > {MAX_DAILY_LOSS*100:.0f}%")
            return

    target_val = total_value * target_position
    per_stock = target_val / len(target_codes)

    mode_tag = "[DRY-RUN] " if dry_run else ""
    print(f"\n{'='*50}")
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] V13 调仓 {mode_tag}")
    print(f"模式: {signals.get('mode')} | 仓位: {target_position*100:.0f}%")
    print(f"总资产: ¥{total_value:,.0f} | 可用: ¥{available_cash:,.0f}")
    if risk is not None:
        print(f"当日盈亏: {daily_pnl*100:+.2f}%")
    print(f"选股: {', '.join(target_codes)}")

    # 卖旧
    for code in list(current_positions.keys()):
        if code not in target_codes:
            sell(xt_trader, acc, code, current_positions[code], dry_run=dry_run)

    # 买新
    for code in target_codes:
        px = get_current_price(code)
        if not px:
            print(f"  ⚠️ {code} 无行情")
            continue

        current_vol = current_positions.get(code, 0)
        current_val = current_vol * px

        if abs(current_val - per_stock) / max(per_stock, 1) < 0.15:
            continue

        if current_val < per_stock:
            buy_vol = int((per_stock - current_val) / px / 100) * 100
            if buy_vol >= 100:
                buy(xt_trader, acc, code, buy_vol, px, dry_run=dry_run)
        else:
            sell_vol = int((current_val - per_stock) / px / 100) * 100
            if sell_vol >= 100:
                sell(xt_trader, acc, code, min(sell_vol, current_vol), dry_run=dry_run)

    print("调仓完成\n")


def buy(xt_trader, acc, code, volume, price, dry_run=False):
    from xtquant import xtconstant
    if dry_run:
        print(f"  📈 [DRY-RUN] 买入 {code} {volume}股 @¥{price:.2f} (未实际下单)")
        log_trade(code, "BUY", price, volume, reason="DRY-RUN: V13 rebalance")
        return
    xt_trader.order_stock_async(acc, code, xtconstant.STOCK_BUY, volume, xtconstant.FIX_PRICE, price, 'V13', code)
    print(f"  📈 买入 {code} {volume}股 @¥{price:.2f}")
    log_trade(code, "BUY", price, volume, reason="V13 rebalance")


def sell(xt_trader, acc, code, volume, dry_run=False):
    from xtquant import xtconstant
    if dry_run:
        print(f"  📉 [DRY-RUN] 卖出 {code} {volume}股 (未实际下单)")
        log_trade(code, "SELL", 0, volume, reason="DRY-RUN: V13 rebalance")
        return
    xt_trader.order_stock_async(acc, code, xtconstant.STOCK_SELL, volume, xtconstant.LATEST_PRICE, -1, 'V13', code)
    print(f"  📉 卖出 {code} {volume}股")
    log_trade(code, "SELL", 0, volume, reason="V13 rebalance")


def run_once(dry_run=False):
    init_db()

    print("[INFO] 加载 V13 策略...")
    from v13_live import V13Strategy
    v13 = V13Strategy()

    print("[INFO] 连接交易接口...")
    xt_trader, acc = connect_trader()
    if not xt_trader:
        return

    print("[INFO] 获取持仓...")
    positions = get_positions(xt_trader, acc)
    cash = get_available_cash(xt_trader, acc)
    print(f"  持仓: {len(positions)}只 | 可用资金: ¥{cash:,.0f}")

    # ── 初始化风控 ──
    total_value = cash + sum(
        get_current_price(c) * v for c, v in positions.items() if get_current_price(c)
    )
    risk = RiskController()
    risk.update(total_value)  # 记录初始资产
    print(f"  初始总资产: ¥{total_value:,.0f}")

    print("[INFO] 运行 V13 策略（拉数据约30秒）...")
    current_pos = 0.5
    result = v13.run(current_position=current_pos)

    if 'error' in result:
        print(f"[ERROR] {result['error']}")
        return

    execute_trades(xt_trader, acc, result, dry_run=dry_run, risk=risk)


def run_daemon(dry_run=False):
    init_db()

    from v13_live import V13Strategy
    v13 = V13Strategy()

    xt_trader, acc = connect_trader()
    if not xt_trader:
        return

    last_trade_date = None
    risk = RiskController()
    print("[INFO] 守护模式启动" + (" [DRY-RUN]" if dry_run else ""))

    while True:
        now = datetime.datetime.now()
        today = now.date()

        if now.weekday() >= 5 or not (datetime.time(9, 30) <= now.time() <= datetime.time(15, 0)):
            time.sleep(60)
            continue

        if last_trade_date == today:
            time.sleep(CHECK_INTERVAL)
            continue

        positions = get_positions(xt_trader, acc)
        cash = get_available_cash(xt_trader, acc)
        total = cash + sum(
            get_current_price(c) * v for c, v in positions.items() if get_current_price(c)
        )

        # ── 风控检查（新交易日自动重置）──
        daily_pnl, blocked = risk.update(total)
        if blocked:
            print(f"[RISK] ⛔ 单日亏损 {daily_pnl*100:.2f}%，暂停今日交易")
            log_trade("PORTFOLIO", "RISK_BLOCK", 0, 0, daily_pnl,
                      f"单日亏损 {daily_pnl*100:.2f}% > {MAX_DAILY_LOSS*100:.0f}%")
            time.sleep(CHECK_INTERVAL)
            continue

        current_pos = sum(
            get_current_price(c) * v for c, v in positions.items() if get_current_price(c)
        ) / max(total, 1)

        result = v13.run(current_position=current_pos)
        if 'error' not in result:
            execute_trades(xt_trader, acc, result, dry_run=dry_run, risk=risk)
            last_trade_date = today

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help='守护模式')
    parser.add_argument('--dry-run', action='store_true', help='演习模式：只打印不下单')
    args = parser.parse_args()

    if args.daemon:
        run_daemon(dry_run=args.dry_run)
    else:
        run_once(dry_run=args.dry_run)
