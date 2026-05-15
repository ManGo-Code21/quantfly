# -*- encoding: utf-8 -*-
"""
V13 MiniQMT 实盘适配器
=====================
用法（在 MiniQMT 里导入）:

    from v13_live import V13Strategy
    v13 = V13Strategy()
    signals = v13.run()  # 返回当天的买卖信号

信号格式:
{
    'date': '2026-05-15',
    'is_bearish': False,          # 🐻 熊市?
    'temperature': 78.5,          # 市场温度 0-100
    'target_position': 0.80,      # 目标仓位 0.0~1.0
    'top_sectors': [...],         # 最强5个板块
    'picks': [                    # 选出的4只股票
        {'code': '300308', 'score': 0.406, 'tier': 'CLIMAX', 'sector': '通信'},
        ...
    ],
    'filtered': [...],            # 被质量过滤排除的
}
"""

import os
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("V13_Live")

# ============================================================
# 配置
# ============================================================
QMT_HOST = "http://127.0.0.1:8765"   # MiniQMT 本地地址
TOP_N = 4
KF_COUNT = 300  # K线回溯天数
TREND_FILTER_MA = 60
BEAR_MAX_POS = 0.30
BULL_MAX_POS = 1.0

# ============================================================
# ETF → 行业股票映射
# ============================================================
ETF_SECTOR_MAP = {
    'sh512760': {'name': '芯片ETF', 'sector': '半导体', 'stocks': ['688041','002415','688111','688256','688008','300474','603501','688981']},
    'sz159995': {'name': '芯片ETF深', 'sector': '半导体', 'stocks': ['688041','002415','688111','688256','688008','300474','603501','688981']},
    'sh515050': {'name': '通信ETF', 'sector': '通信', 'stocks': ['000063','600050','300308','002281','300502']},
    'sh515980': {'name': '人工智能ETF', 'sector': 'AI', 'stocks': ['688111','002230','603019','300496','688256','000977','688041']},
    'sh516510': {'name': '云计算ETF', 'sector': '算力', 'stocks': ['000977','603019','300383','002230','002415','603881','002587']},
    'sz159869': {'name': '游戏ETF', 'sector': '传媒', 'stocks': ['300418','002602','002517','002558']},
    'sh515790': {'name': '光伏ETF', 'sector': '光伏', 'stocks': ['601012','002129','688223','300274','688390']},
    'sh516160': {'name': '新能源ETF', 'sector': '新能源', 'stocks': ['300750','601012','002594','300274','688005']},
    'sh516390': {'name': '新能源车ETF', 'sector': '新能源车', 'stocks': ['300750','002594','601799','002460']},
    'sh516880': {'name': '光伏ETF银华', 'sector': '光伏', 'stocks': ['601012','002129','688223','300274']},
    'sh561560': {'name': '电力ETF', 'sector': '电力', 'stocks': ['600900','601985','600886','600089','002350','601179','600550']},
    'sh516970': {'name': '基建ETF', 'sector': '基建', 'stocks': ['601800','601390','601668','600585']},
    'sh515220': {'name': '煤炭ETF', 'sector': '煤炭', 'stocks': ['601088','600188','601225','600985']},
    'sh512660': {'name': '军工ETF', 'sector': '军工', 'stocks': ['600879','600150','688066','600760']},
    'sh512670': {'name': '国防ETF', 'sector': '军工', 'stocks': ['600879','600150','688066','600760']},
    'sh512710': {'name': '军工龙头', 'sector': '军工', 'stocks': ['600879','600150','688066']},
    'sh512010': {'name': '医药ETF', 'sector': '医药', 'stocks': ['600276','300760','688180','688185','603259']},
    'sz159883': {'name': '医疗器械ETF', 'sector': '医药', 'stocks': ['300760','688180','603259']},
    'sz159996': {'name': '家电ETF', 'sector': '消费', 'stocks': ['000333','000651','002508']},
    'sz159928': {'name': '消费ETF', 'sector': '消费', 'stocks': ['600519','000858','002304','000568']},
    'sh512690': {'name': '酒ETF', 'sector': '消费', 'stocks': ['600519','000858','002304','000568']},
    'sz159865': {'name': '养殖ETF', 'sector': '消费', 'stocks': ['002714','300498','002157']},
    'sz159766': {'name': '旅游ETF', 'sector': '消费', 'stocks': ['601888','600754']},
    'sh512800': {'name': '银行ETF', 'sector': '金融', 'stocks': ['601398','601939','600036','000001']},
    'sh512880': {'name': '证券ETF', 'sector': '金融', 'stocks': ['600030','601066','601881','300059']},
    'sh510230': {'name': '金融ETF', 'sector': '金融', 'stocks': ['601318','600036','000001']},
    'sh512200': {'name': '房地产ETF', 'sector': '金融', 'stocks': ['000002','600048','001979']},
}

ALL_STOCKS = list(set(code for info in ETF_SECTOR_MAP.values() for code in info['stocks']))


class V13Strategy:
    """V13 牛熊切换策略 MiniQMT 适配器"""

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or Path(__file__).parent / "data")
        self._load_fundamentals()

    # ── 基本面加载 ──
    def _load_fundamentals(self):
        """加载所有可用基本面数据"""
        self.fundamentals = {}

        # 优先级：最新Q1 > 2025年报 > 2024年报
        files = [
            ("fundamentals_latest.json", "Q1-2026"),
            ("fundamentals_2025.json", "2025年报"),
            ("fundamentals_2024.json", "2024年报"),
        ]
        for fname, label in files:
            fpath = self.data_dir / fname
            if fpath.exists():
                with open(fpath) as f:
                    data = json.load(f)
                for code, vals in data.items():
                    if code not in self.fundamentals:
                        self.fundamentals[code] = vals
                logger.info(f"  {label}: {len(data)}只")
            else:
                logger.info(f"  {label}: 未找到")

    # ── 数据获取 ──
    def _fetch_kline(self, code, count=KF_COUNT):
        """从 QMT 拉取单只股票日K线"""
        import requests as req
        clean = code.split(".")[0]
        try:
            r = req.get(f"{QMT_HOST}/data/kline",
                       params={"code": clean, "period": "1d", "count": count}, timeout=10)
            candles = r.json().get("candles", [])
            records = []
            for c in candles:
                records.append({
                    "date": pd.to_datetime(str(c["date"])[:10], format="%Y%m%d"),
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": int(c["volume"]),
                })
            return pd.DataFrame(records).set_index("date").sort_index()
        except:
            return pd.DataFrame()

    def _fetch_index(self, count=KF_COUNT):
        """获取上证指数日K线"""
        return self._fetch_kline("000001", count)

    def fetch_all(self):
        """拉取全部数据：指数 + 82只个股"""
        logger.info(f"拉取上证指数 + {len(ALL_STOCKS)}只个股...")

        self.index_df = self._fetch_index(KF_COUNT)
        logger.info(f"  上证: {len(self.index_df)} 条")

        self.stock_dfs = {}
        for code in ALL_STOCKS:
            df = self._fetch_kline(code)
            if not df.empty and len(df) >= 40:
                self.stock_dfs[code] = df
        logger.info(f"  个股: {len(self.stock_dfs)} 只有效")

        # 构建板块→成分股映射
        self.sector_stocks = {}
        for etf_code, info in ETF_SECTOR_MAP.items():
            s = info['sector']
            if s not in self.sector_stocks:
                self.sector_stocks[s] = set()
            for stock in info['stocks']:
                if stock in self.stock_dfs:
                    self.sector_stocks[s].add(stock)

        return len(self.stock_dfs) >= 20

    # ── 板块动量 ──
    def _sector_momentum(self, date, window=20):
        momentum = {}
        for sector, codes in self.sector_stocks.items():
            returns = []
            for code in codes:
                df = self.stock_dfs[code]
                if date not in df.index:
                    continue
                idx = df.index.get_loc(date)
                if idx < window:
                    continue
                close_now = df['close'].iloc[idx]
                close_prev = df['close'].iloc[idx - window]
                if close_prev > 0:
                    returns.append(close_now / close_prev - 1)
            if returns:
                momentum[sector] = np.mean(returns) * 100
        return momentum

    def _get_top_sectors(self, date, top_n=5):
        windows = [5, 10, 20]
        scores = {}
        for w in windows:
            mom = self._sector_momentum(date, window=w)
            for sector, val in mom.items():
                if sector not in scores:
                    scores[sector] = {}
                scores[sector][f'mom_{w}d'] = val

        composite = {}
        for sector, sc in scores.items():
            if all(f'mom_{w}d' in sc for w in windows):
                comp = sc['mom_5d'] * 0.5 + sc['mom_10d'] * 0.3 + sc['mom_20d'] * 0.2
                composite[sector] = {'score': comp, 'mom_5d': sc['mom_5d'],
                                    'mom_10d': sc['mom_10d'], 'mom_20d': sc['mom_20d']}

        ranked = sorted(composite.items(), key=lambda x: x[1]['score'], reverse=True)
        result = []
        for sector, data in ranked:
            mom5, mom10, mom20 = data['mom_5d'], data['mom_10d'], data['mom_20d']
            if mom5 > 3 and mom20 > 5:
                tier = 'CLIMAX'
            elif mom5 > 2 and mom10 > 2:
                tier = 'ACCEL'
            elif mom5 > 0:
                tier = 'START'
            else:
                tier = 'WEAK'
            result.append({'sector': sector, **data, 'tier': tier})
        return result[:top_n]

    # ── 基本面评分 ──
    def _quality_score(self, code):
        f = self.fundamentals.get(code, {})
        if not f:
            return 0.0

        roe = f.get('roe', 0)
        pg = f.get('profit_g', 0)
        rg = f.get('revenue_g', 0)

        rs = 0.10 if roe > 15 else (0.05 if roe > 10 else (0.0 if roe > 5 else -0.15))
        ps = 0.08 if pg > 30 else (0.03 if pg > 0 else -0.15)
        gs = 0.05 if rg > 20 else (0.02 if rg > 0 else -0.05)
        return rs + ps + gs

    def _quality_filter(self, code):
        f = self.fundamentals.get(code, {})
        if not f:
            return True
        return not (f.get('roe', 10) < 5 or f.get('profit_g', 10) < -20)

    # ── 选股 ──
    def _select_stocks(self, top_sectors, date):
        stock_scores = []
        seen = set()
        filtered = []

        for sec_info in top_sectors:
            sector = sec_info['sector']
            for code in self.sector_stocks.get(sector, set()):
                if code in seen:
                    continue
                seen.add(code)

                if not self._quality_filter(code):
                    filtered.append(code)
                    continue

                df = self.stock_dfs[code]
                if date not in df.index:
                    continue
                idx = df.index.get_loc(date)
                if idx < 30:
                    continue

                close = df['close'].iloc[idx-25:idx+1].values
                volume = df['volume'].iloc[idx-25:idx+1].values

                ret_20 = close[-1]/close[-21]-1 if len(close)>=21 else 0
                ret_5 = close[-1]/close[-6]-1 if len(close)>=6 else 0
                momentum = ret_20*0.7 + ret_5*0.3

                ma5 = np.mean(close[-5:])
                ma10 = np.mean(close[-10:])
                ma20 = np.mean(close)
                if ma5 > ma10 > ma20 and close[-1] > ma5:
                    trend = 0.20
                elif ma5 > ma10 and close[-1] > ma10:
                    trend = 0.12
                elif ma5 < ma10 < ma20:
                    trend = -0.15
                else:
                    trend = 0.02

                vol_ratio = np.mean(volume[-5:])/np.mean(volume[-20:]) if np.mean(volume[-20:])>0 else 1
                if ret_20 > 0 and vol_ratio > 1.2:
                    vol_score = 0.15
                elif ret_20 > 0 and vol_ratio > 1.0:
                    vol_score = 0.10
                elif ret_20 < 0 and vol_ratio > 1.5:
                    vol_score = -0.10
                else:
                    vol_score = 0.05

                quality = self._quality_score(code)
                turn_ratio = np.mean(volume[-5:])/np.mean(volume[-20:]) if np.mean(volume[-20:])>0 else 1
                sentiment = 0.05 if turn_ratio > 2.0 else (0.02 if turn_ratio > 1.5 else 0)

                stock_vol = np.std(np.diff(np.log(close[-21:]))) * np.sqrt(252) * 100 if len(close)>=21 else 0
                vol_penalty = max(0, (stock_vol - 50) * 0.005)

                sector_bonus = sec_info['score'] / 100

                final = (momentum * 0.35 + trend * 0.25 + vol_score * 0.10 +
                         quality * 0.15 + sentiment * 0.05 - vol_penalty + sector_bonus)

                stock_scores.append({
                    'code': code, 'score': final,
                    'tier': sec_info['tier'], 'sector': sector,
                })

        stock_scores.sort(key=lambda x: x['score'], reverse=True)
        return stock_scores[:TOP_N], filtered

    # ── 仓位管理 ──
    def _market_signals(self):
        """计算综合市场信号"""
        close = self.index_df['close'].values
        volume = self.index_df['volume'].values

        if len(close) < 60:
            return {'temperature': 50, 'is_bearish': False}

        ma20 = np.mean(close[-20:])
        ma60 = np.mean(close[-60:])

        if close[-1] > ma20 > ma60:
            trend_score = 90
        elif close[-1] > ma20:
            trend_score = 70
        elif close[-1] > ma60:
            trend_score = 45
        else:
            trend_score = 20

        ret_20 = (close[-1]/close[-21]-1)*100 if len(close)>=21 else 0
        mom_score = min(100, max(0, 50 + ret_20*200))
        vol_ratio = np.mean(volume[-5:])/np.mean(volume[-20:]) if np.mean(volume[-20:])>0 else 1
        vol_score = min(100, vol_ratio*50)

        temperature = trend_score*0.5 + mom_score*0.3 + vol_score*0.2
        is_bearish = close[-1] < ma60

        vol_signal = (np.mean(volume[-5:])/np.mean(volume[-20:]) - 1) * 2 if np.mean(volume[-20:])>0 else 0
        returns_20 = np.diff(np.log(close[-21:]))
        volatility = min(100, np.std(returns_20) * np.sqrt(252) * 100 * 2) if len(returns_20) > 0 else 0

        return {
            'temperature': temperature,
            'volume_signal': vol_signal,
            'volatility': volatility,
            'is_bearish': is_bearish,
        }

    def _calc_position(self, current_pos, signals):
        t = signals['temperature']
        vs = signals['volume_signal']
        vol = signals['volatility']

        if t < 20 and vs > 0.5:
            return 0.0
        if vol > 70:
            return max(0.05, current_pos * 0.5)
        if t < 25:
            return 0.0
        if t > 60 and vs > 0 and vol < 50:
            base = min(1.0, current_pos + (0.30 if current_pos < 0.5 else 0.20))
        elif t < 40:
            base = max(0.05, current_pos - 0.25)
        else:
            base = current_pos

        cap = BEAR_MAX_POS if signals['is_bearish'] else BULL_MAX_POS
        return min(base, cap)

    # ── 主入口 ──
    def run(self, current_position=0.5):
        """
        执行一次完整的 V13 策略扫描

        Args:
            current_position: 当前仓位比例 (0.0~1.0)

        Returns:
            dict: 完整信号（供 MiniQMT 下单用）
        """
        import datetime

        # 1. 拉数据
        if not hasattr(self, 'index_df') or self.index_df.empty:
            if not self.fetch_all():
                return {'error': '数据拉取失败'}

        date = self.index_df.index[-1]

        # 2. 市场信号
        signals = self._market_signals()

        # 3. 仓位
        target_pos = self._calc_position(current_position, signals)

        # 4. 板块动量
        top_sectors = self._get_top_sectors(date)

        # 5. 选股
        picks, filtered = self._select_stocks(top_sectors, date)

        result = {
            'date': date.strftime('%Y-%m-%d'),
            'is_bearish': bool(signals['is_bearish']),
            'temperature': float(round(signals['temperature'], 1)),
            'volatility': float(round(signals['volatility'], 1)),
            'target_position': float(round(target_pos, 2)),
            'top_sectors': [f"{s['sector']}({s['tier']}:5d={s['mom_5d']:.1f}%)" for s in top_sectors],
            'picks': picks,
            'filtered': filtered,
            'mode': '🐻熊市防御' if signals['is_bearish'] else '🐂牛市进攻',
        }

        logger.info(f"V13 信号: {result['mode']} | 仓位{target_pos*100:.0f}% | "
                    f"温度{signals['temperature']:.0f} | "
                    f"选股:{[p['code'] for p in picks]}")

        return result


# ============================================================
# 便捷函数：直接集成到 MiniQMT
# ============================================================
def get_v13_signals(current_position=0.5):
    """一键获取 V13 买卖信号"""
    v13 = V13Strategy()
    return v13.run(current_position)


if __name__ == "__main__":
    # 本地测试
    v13 = V13Strategy()
    result = v13.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
