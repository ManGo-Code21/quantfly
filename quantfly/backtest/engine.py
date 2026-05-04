# -*- encoding: utf-8 -*-
"""
回测引擎 — 选股三原则回测
"""
import pandas as pd
import numpy as np
import logging
from typing import Optional, Callable
from datetime import datetime

from quantfly.screener.principle_filter import analyze_stock
from quantfly.backtest.data_provider import get_kline_em

logger = logging.getLogger("Backtest.Engine")


class BacktestBroker:
    """
    简化回测Broker
    复用QuantDragon的BacktestBroker逻辑，但独立实现
    """

    def __init__(self, config: dict):
        self.initial_cash = config.get("initial_cash", 1_000_000)
        self.commission_rate = config.get("commission_rate", 0.0003)
        self.stamp_tax = config.get("stamp_tax", 0.001)
        self.slippage = config.get("slippage", 0.001)
        self.cash = self.initial_cash
        self.positions = {}  # code -> {volume, avg_price}
        self.trade_history = []
        self.daily_values = []

    def load_data(self, code: str, df: pd.DataFrame):
        self._history_data = getattr(self, "_history_data", {})
        self._history_data[code] = df

    def send_order(self, code: str, direction: str, price: float, volume: int):
        """执行买卖"""
        if direction == "buy":
            cost = price * volume * (1 + self.commission_rate + self.slippage)
            if cost > self.cash:
                return
            self.cash -= cost
            if code in self.positions:
                pos = self.positions[code]
                total_vol = pos["volume"] + volume
                pos["avg_price"] = (pos["avg_price"] * pos["volume"] + price * volume) / total_vol
                pos["volume"] = total_vol
            else:
                self.positions[code] = {"volume": volume, "avg_price": price}
            self.trade_history.append({
                "code": code, "direction": "buy",
                "price": price, "volume": volume,
                "commission": price * volume * self.commission_rate,
            })
        else:  # sell
            if code not in self.positions:
                return
            pos = self.positions[code]
            if pos["volume"] < volume:
                volume = pos["volume"]
            revenue = price * volume * (1 - self.commission_rate - self.stamp_tax - self.slippage)
            self.cash += revenue
            pos["volume"] -= volume
            if pos["volume"] <= 0:
                del self.positions[code]
            self.trade_history.append({
                "code": code, "direction": "sell",
                "price": price, "volume": volume,
                "commission": price * volume * (self.commission_rate + self.stamp_tax),
            })

    def get_trade_history(self):
        return self.trade_history

    def get_metrics(self) -> dict:
        """计算回测绩效指标"""
        if not self.trade_history:
            return {}
        total_return = (self.cash - self.initial_cash) / self.initial_cash * 100
        buys = [t for t in self.trade_history if t["direction"] == "buy"]
        sells = [t for t in self.trade_history if t["direction"] == "sell"]
        wins = sum(1 for t in sells if self._calc_trade_pnl(t) > 0)
        return {
            "total_return": total_return,
            "final_value": self.cash,
            "initial_cash": self.initial_cash,
            "total_trades": len(self.trade_history),
            "win_rate": wins / len(sells) * 100 if sells else 0,
        }

    def _calc_trade_pnl(self, sell_trade: dict) -> float:
        code = sell_trade["code"]
        buy_trades = [t for t in self.trade_history
                      if t["code"] == code and t["direction"] == "buy"]
        if not buy_trades:
            return 0
        avg_buy = sum(t["price"] * t["volume"] for t in buy_trades) / sum(t["volume"] for t in buy_trades)
        return (sell_trade["price"] - avg_buy) * sell_trade["volume"]


class ScreenerBacktestRunner:
    """选股三原则回测运行器"""

    def __init__(self, initial_cash: float = 1_000_000.0):
        self.initial_cash = initial_cash
        self.broker = BacktestBroker({
            "initial_cash": initial_cash,
            "commission_rate": 0.0003,
            "stamp_tax": 0.001,
            "slippage": 0.001,
        })
        self.results = []
        self._on_bar_callback: Optional[Callable] = None

    def add_data(self, code: str, df: pd.DataFrame):
        if df.empty:
            return
        df = df.copy()
        df.name = code
        self.broker.load_data(code, df)

    def set_on_bar(self, callback: Callable):
        self._on_bar_callback = callback

    def run(self, start_date: str, end_date: str):
        """运行回测"""
        broker = self.broker
        codes = list(broker._history_data.keys())

        # 按日期遍历
        all_dates = set()
        for code in codes:
            df = broker._history_data[code]
            all_dates.update(df.index.strftime("%Y-%m-%d"))
        dates = sorted(all_dates)

        in_range = False
        for date in dates:
            if date == start_date:
                in_range = True
            if not in_range:
                continue
            if date > end_date:
                break

            for code in codes:
                df = broker._history_data.get(code)
                if df is None or df.empty:
                    continue
                date_idx = df.index.strftime("%Y-%m-%d")
                if date not in date_idx:
                    continue
                bar = df[date_idx == date].iloc[0].to_dict()
                bar["date"] = date

                if self._on_bar_callback:
                    self._on_bar_callback(code, bar)

        return broker.get_metrics()
