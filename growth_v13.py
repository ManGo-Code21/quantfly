# -*- encoding: utf-8 -*-
"""
V13 — V12.1 增强版：牛熊切换 + 趋势过滤器 + 质量/情绪因子
================================================================
定位：牛市全仓进攻 (V12.1)，熊市 30% 防御。

核心逻辑：
  🐂 沪深300 > MA60 → V12.1 原版（全仓，质量+情绪因子）
  🐻 沪深300 < MA60 → 仓位压缩至 30%（继续用同策略选股）
"""
import os
for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    if var in os.environ:
        del os.environ[var]

import numpy as np
import pandas as pd
import json
from pathlib import Path
import logging
from datetime import timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Growth_v13")

START_DATE = "2023-01-01"  # 🆕 扩展至3年跨牛熊
TRAIN_WINDOW = 60
REBALANCE_FREQ = 10
TOP_N = 4
INIT_CAPITAL = 1_000_000
MAX_DRAWDOWN_STOP = -0.15

# ============================================================
# 🆕 趋势过滤器配置
# ============================================================
TREND_FILTER_MA = 60       # 使用 MA60 作为牛熊分界线
TREND_FILTER_MAX_POS = 0.30  # 熊市最大仓位
BULL_MAX_POS = 1.0           # 🆕 牛市最大仓位（全仓）

# ============================================================
# 指数数据获取（push2his → 腾讯 fallback）
# ============================================================
def get_index_data_robust(days=1200):
    """获取上证指数日K线（带腾讯fallback）"""
    import requests as req

    # 方式1：QMT
    try:
        r = req.get("http://10.6.98.168:8765/data/kline",
                    params={"code": "000001", "period": "1d", "count": days}, timeout=10)
        data = r.json()
        candles = data.get("candles", [])
        if candles:
            records = []
            for c in candles:
                records.append({
                    'date': pd.to_datetime(str(c['date'])[:10], format='%Y%m%d'),
                    'open': float(c['open']), 'high': float(c['high']),
                    'low': float(c['low']), 'close': float(c['close']),
                    'volume': int(c['volume']),
                })
            df = pd.DataFrame(records).set_index('date').sort_index()
            df = df[df.index >= START_DATE]
            if not df.empty:
                logger.info(f"上证指数 (QMT): {len(df)} 条")
                return df
    except Exception as e:
        logger.debug(f"QMT指数失败: {e}")

    # 方式2：push2his
    try:
        params = {
            "secid": "1.000001",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": days,
        }
        r = req.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params=params, timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"})
        klines = r.json().get("data", {}).get("klines", [])
        if klines:
            records = []
            for k in klines:
                p = k.split(",")
                records.append({
                    'date': pd.to_datetime(p[0]),
                    'open': float(p[1]), 'high': float(p[2]),
                    'low': float(p[3]), 'close': float(p[4]),
                    'volume': float(p[5]),
                })
            df = pd.DataFrame(records).set_index('date').sort_index()
            df = df[df.index >= START_DATE]
            if not df.empty:
                logger.info(f"上证指数 (push2his): {len(df)} 条")
                return df
    except Exception as e:
        logger.debug(f"push2his指数失败: {e}")

    # 方式3：腾讯 fallback
    try:
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        r = req.get(url, params={'param': f'sh000001,day,,,{days},qfq'},
                    timeout=15, headers={'Referer': 'https://finance.qq.com'})
        data = r.json()
        day = data.get('data', {}).get('sh000001', {})
        klines = day.get('day', []) or day.get('qfqday', [])
        if klines:
            records = []
            for k in klines:
                records.append({
                    'date': pd.to_datetime(k[0]),
                    'open': float(k[1]), 'close': float(k[2]),
                    'high': float(k[3]), 'low': float(k[4]),
                    'volume': float(k[5]) if len(k) > 5 else 0,
                })
            df = pd.DataFrame(records).set_index('date').sort_index()
            df = df[df.index >= START_DATE]
            if not df.empty:
                logger.info(f"上证指数 (腾讯): {len(df)} 条")
                return df
    except Exception as e:
        logger.warning(f"腾讯fallback失败: {e}")

    return pd.DataFrame()


# ============================================================
# 基本面数据加载（时间感知，避免未来函数）
# ============================================================
FUNDAMENTALS_2024 = {}
FUNDAMENTALS_2025 = {}

fund_2024_path = Path(__file__).parent / "data" / "fundamentals_2024.json"
fund_2025_path = Path(__file__).parent / "data" / "fundamentals_2025.json"

if fund_2024_path.exists():
    with open(fund_2024_path) as f:
        FUNDAMENTALS_2024 = json.load(f)
    logger.info(f"加载2024年报: {len(FUNDAMENTALS_2024)}只")

if fund_2025_path.exists():
    with open(fund_2025_path) as f:
        FUNDAMENTALS_2025 = json.load(f)
    logger.info(f"加载2025年报: {len(FUNDAMENTALS_2025)}只")

FUND_SWITCH_DATE = pd.Timestamp("2026-04-01")

def get_fundamentals(code, date):
    """时间感知：2026-04前用2024年报，之后用2025年报"""
    if date >= FUND_SWITCH_DATE:
        return FUNDAMENTALS_2025.get(code, {})
    return FUNDAMENTALS_2024.get(code, {})


# ============================================================
# ETF → 行业股票映射（继承 V12.1）
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

# 去重
SECTOR_TO_ETF = {}
for code, info in ETF_SECTOR_MAP.items():
    s = info['sector']
    if s not in SECTOR_TO_ETF:
        SECTOR_TO_ETF[s] = []
    SECTOR_TO_ETF[s].append(code)

ALL_STOCKS = list(set(
    code for info in ETF_SECTOR_MAP.values() for code in info['stocks']
))


# ============================================================
# 数据获取
# ============================================================
def fetch_stock_data():
    """获取所有股票K线 — 直接用 QMT（Mac 上 push2his 不可靠）"""
    import requests as req
    logger.info(f"获取 {len(ALL_STOCKS)} 只股票K线 (QMT)...")
    stock_dfs = {}
    for code in ALL_STOCKS:
        try:
            clean = code.split(".")[0]
            r = req.get(f"http://10.6.98.168:8765/data/kline",
                        params={"code": clean, "period": "1d", "count": 800},
                        timeout=15)
            data = r.json()
            candles = data.get("candles", [])
            if candles:
                records = []
                for c in candles:
                    records.append({
                        "date": pd.to_datetime(str(c["date"])[:10], format="%Y%m%d"),
                        "open": float(c["open"]), "high": float(c["high"]),
                        "low": float(c["low"]), "close": float(c["close"]),
                        "volume": int(c["volume"]),
                    })
                df = pd.DataFrame(records).set_index("date").sort_index()
                df = df[df.index >= START_DATE]
                if not df.empty and len(df) >= 40:
                    stock_dfs[code] = df
        except:
            pass
    logger.info(f"  有效股票: {len(stock_dfs)} 只")
    return stock_dfs


def build_sector_momentum_from_stocks(stock_dfs):
    """用各板块成分股等权平均计算板块动量"""
    sector_stocks = {}
    for etf_code, info in ETF_SECTOR_MAP.items():
        s = info['sector']
        if s not in sector_stocks:
            sector_stocks[s] = set()
        for stock in info['stocks']:
            if stock in stock_dfs:
                sector_stocks[s].add(stock)

    logger.info(f"板块动量覆盖: {len(sector_stocks)} 个行业")
    for s, stocks in sorted(sector_stocks.items()):
        logger.debug(f"  {s}: {len(stocks)} 只")
    return sector_stocks


# ============================================================
# 板块动量信号（继承 V12.1）
# ============================================================
def calc_sector_momentum(sector_stocks, stock_dfs, date, window=20):
    """计算所有板块的指定窗口动量"""
    momentum = {}
    for sector, codes in sector_stocks.items():
        returns = []
        for code in codes:
            if code not in stock_dfs or date not in stock_dfs[code].index:
                continue
            idx = stock_dfs[code].index.get_loc(date)
            if idx < window:
                continue
            close_now = stock_dfs[code]['close'].iloc[idx]
            close_prev = stock_dfs[code]['close'].iloc[idx - window]
            if close_prev > 0:
                returns.append(close_now / close_prev - 1)
        if returns:
            momentum[sector] = np.mean(returns) * 100
    return momentum


def get_top_sectors(sector_stocks, stock_dfs, date, top_n=5, windows=[5, 10, 20]):
    """三层信号体系：START / ACCEL / CLIMAX"""
    scores = {}
    for w in windows:
        mom = calc_sector_momentum(sector_stocks, stock_dfs, date, window=w)
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
    for rank, (sector, data) in enumerate(ranked):
        mom5, mom10, mom20 = data['mom_5d'], data['mom_10d'], data['mom_20d']
        if mom5 > 3 and mom20 > 5:
            tier = 'CLIMAX'
        elif mom5 > 2 and mom10 > 2:
            tier = 'ACCEL'
        elif mom5 > 0 and (mom20 < 3 or mom10 < 2):
            tier = 'START'
        else:
            tier = 'WEAK'
        result.append((sector, data, tier))

    return result[:top_n]


# ============================================================
# V12.1 质量 + 情绪因子（继承）
# ============================================================
def quality_score(code, date):
    """基本面质量评分"""
    f = get_fundamentals(code, date)
    if not f:
        return 0.0
    roe = f.get('roe', 0)
    pg = f.get('profit_g', 0)
    rg = f.get('revenue_g', 0)

    if roe > 15: roe_s = 0.10
    elif roe > 10: roe_s = 0.05
    elif roe > 5: roe_s = 0.0
    else: roe_s = -0.15

    if pg > 30: pg_s = 0.08
    elif pg > 0: pg_s = 0.03
    else: pg_s = -0.15

    if rg > 20: rg_s = 0.05
    elif rg > 0: rg_s = 0.02
    else: rg_s = -0.05

    return roe_s + pg_s + rg_s


def quality_filter(code, date):
    """质量预过滤：ROE<5% 或 净利增速<-20% 排除"""
    f = get_fundamentals(code, date)
    if not f:
        return True
    roe = f.get('roe', 10)
    pg = f.get('profit_g', 10)
    return not (roe < 5 or pg < -20)


def sentiment_score(df, idx):
    """情绪代理：换手率飙升"""
    if idx < 20:
        return 0.0
    volume = df['volume'].iloc[idx-20:idx+1].values
    turn_ratio = np.mean(volume[-5:]) / np.mean(volume[-20:]) if np.mean(volume[-20:]) > 0 else 1
    if turn_ratio > 2.0:
        return 0.05
    elif turn_ratio > 1.5:
        return 0.02
    return 0.0


# ============================================================
# 动态选股（V12.1 权重）
# ============================================================
def select_stocks_from_sectors(top_sectors, sector_stocks, stock_dfs, date, top_n=TOP_N):
    """在最强板块中选个股（质量+情绪因子增强）"""
    stock_scores = []
    seen_codes = set()
    filtered_count = 0

    for sector, data, tier in top_sectors:
        candidates = list(sector_stocks.get(sector, set()))
        for code in candidates:
            if code in seen_codes:
                continue
            seen_codes.add(code)

            if not quality_filter(code, date):
                filtered_count += 1
                continue

            if code not in stock_dfs or date not in stock_dfs[code].index:
                continue

            df = stock_dfs[code]
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

            quality = quality_score(code, date)
            sentiment = sentiment_score(df, idx)

            stock_vol = np.std(np.diff(np.log(close[-21:]))) * np.sqrt(252) * 100 if len(close)>=21 else 0
            vol_penalty = max(0, (stock_vol - 50) * 0.005)

            sector_bonus = data['score'] / 100

            final = (momentum * 0.35 + trend * 0.25 + vol_score * 0.10 +
                     quality * 0.15 + sentiment * 0.05 -
                     vol_penalty + sector_bonus)

            stock_scores.append((code, final, tier, sector))

    if filtered_count > 0:
        logger.debug(f"  质量过滤排除: {filtered_count}只")

    stock_scores.sort(key=lambda x: x[1], reverse=True)
    return stock_scores[:top_n]


# ============================================================
# 🆕 趋势过滤器
# ============================================================
class TrendFilter:
    """MA60趋势过滤器：判断市场牛熊状态"""

    def __init__(self, index_df):
        self.index_df = index_df

    def is_bearish(self, date, lookback=60):
        """指数是否在 MA60 下方 → 熊市"""
        if date not in self.index_df.index:
            return False
        idx = self.index_df.index.get_loc(date)
        if idx < lookback:
            return False
        close = self.index_df['close'].iloc[idx]
        ma60 = np.mean(self.index_df['close'].iloc[idx-lookback+1:idx+1])
        return close < ma60

    def get_trend_score(self, date):
        """返回趋势强度 (0~100)，用于仓位缩放"""
        if date not in self.index_df.index:
            return 50
        idx = self.index_df.index.get_loc(date)
        if idx < 60:
            return 50
        close = self.index_df['close'].iloc[idx]
        ma20 = np.mean(self.index_df['close'].iloc[idx-19:idx+1])
        ma60 = np.mean(self.index_df['close'].iloc[idx-59:idx+1])

        if close > ma20 > ma60:
            return 100  # 多头排列
        elif close > ma20:
            return 80   # 短期看多
        elif close > ma60:
            return 50   # 中长期有支撑
        else:
            # MA60下方 → 根据乖离率给分
            deviation = (close - ma60) / ma60 * 100
            return max(10, 40 + deviation * 5)


# ============================================================
# 仓位管理（继承 V12 + 趋势过滤器）
# ============================================================
def calc_market_signals(index_df, current_idx):
    """综合市场信号"""
    if current_idx < 60:
        return {'temperature': 50, 'volume_signal': 0, 'volatility': 0, 'trend': 0}

    close = index_df['close'].iloc[:current_idx+1].values
    volume = index_df['volume'].iloc[:current_idx+1].values

    ma20, ma60 = np.mean(close[-20:]), np.mean(close[-60:])
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
    vol_signal = (np.mean(volume[-5:])/np.mean(volume[-20:]) - 1) * 2 if np.mean(volume[-20:])>0 else 0
    returns_20 = np.diff(np.log(close[-21:]))
    volatility = min(100, np.std(returns_20) * np.sqrt(252) * 100 * 2)
    trend_strength = (close[-1] - ma20) / ma20 * 100

    return {
        'temperature': temperature, 'volume_signal': vol_signal,
        'volatility': volatility, 'trend': trend_strength,
        'ma20': ma20, 'ma60': ma60, 'close': close[-1],
        'is_bearish': close[-1] < ma60,
    }


def calc_position_level(current_position, signals):
    """
    🆕 牛熊切换仓位策略
    
    牛市 (指数 > MA60): V12.1 原版逻辑 — 全仓进攻
    熊市 (指数 < MA60): 仓位上限 30% — 防御
    """
    temperature = signals['temperature']
    volume_signal = signals['volume_signal']
    volatility = signals['volatility']
    is_bearish = signals.get('is_bearish', False)

    # 极端情况 → 空仓（牛熊通用安全规则）
    if temperature < 20 and volume_signal > 0.5:
        return 0.00
    if volatility > 70:
        return max(0.05, current_position * 0.5)
    if temperature < 25:
        return 0.00

    # 计算基础仓位
    if temperature > 60 and volume_signal > 0 and volatility < 50:
        if current_position < 0.5:
            base = min(1.0, current_position + 0.30)
        elif current_position < 1.0:
            base = min(1.0, current_position + 0.20)
        else:
            base = current_position
    elif temperature < 40:
        base = max(0.05, current_position - 0.25)
    else:
        base = current_position

    # 🆕 牛熊切换仓位上限
    if is_bearish:
        position = min(base, TREND_FILTER_MAX_POS)   # 熊市：30%上限
    else:
        position = min(base, BULL_MAX_POS)            # 牛市：100%上限

    return position


# ============================================================
# 主回测
# ============================================================
def growth_backtest_v13():
    logger.info("=" * 70)
    logger.info("V13 — 牛熊切换：🐂全仓进攻 / 🐻30%防御")
    logger.info(f"趋势过滤: MA{TREND_FILTER_MA}, 熊市最大仓位 {TREND_FILTER_MAX_POS*100:.0f}%")
    logger.info("=" * 70)

    # 获取数据
    index_df = get_index_data_robust(days=400)
    if not index_df.empty:
        index_df = index_df[index_df.index >= START_DATE]
    if index_df.empty:
        logger.error("指数为空"); return

    stock_dfs = fetch_stock_data()
    if len(stock_dfs) < 20:
        logger.error("股票数据不足"); return

    sector_stocks = build_sector_momentum_from_stocks(stock_dfs)

    # Walk-Forward
    dates = index_df.index
    cash = INIT_CAPITAL
    positions = {}
    cost_basis = {}

    daily_values = []
    sector_log = []

    peak_value = INIT_CAPITAL
    is_stopped_out = False
    cooldown_until = None
    current_market_position = 0.5

    # 统计：熊市天数
    bearish_days = 0

    for day_idx, date in enumerate(dates):
        if day_idx < TRAIN_WINDOW:
            continue

        # 每日市值
        portfolio_value = cash
        for code, shares in positions.items():
            if code in stock_dfs and date in stock_dfs[code].index:
                portfolio_value += shares * stock_dfs[code].loc[date, 'close']

        if portfolio_value > peak_value:
            peak_value = portfolio_value
        current_drawdown = (portfolio_value - peak_value) / peak_value

        # 市场信号
        signals = calc_market_signals(index_df, day_idx)
        if signals.get('is_bearish'):
            bearish_days += 1

        # 黑天鹅响应
        if not is_stopped_out and day_idx >= 2:
            prev_temp = calc_market_signals(index_df, day_idx-2)['temperature']
            curr_temp = signals['temperature']
            if prev_temp > 50 and curr_temp < 15:
                logger.info(f"  🚨 {date.strftime('%Y-%m-%d')} 黑天鹅！快速减仓")
                for code in list(positions.keys()):
                    if code in stock_dfs and date in stock_dfs[code].index:
                        sell_shares = int(positions[code] * 0.60)
                        if sell_shares > 0:
                            cash += sell_shares * stock_dfs[code].loc[date, 'close']
                            positions[code] -= sell_shares
                            if positions[code] <= 0:
                                del positions[code]
                                del cost_basis[code]

        # 个股追踪止损
        if not is_stopped_out:
            for code in list(positions.keys()):
                if code in cost_basis and code in stock_dfs and date in stock_dfs[code].index:
                    current_price = stock_dfs[code].loc[date, 'close']
                    cost = cost_basis[code]
                    ret = (current_price - cost) / cost

                    if ret > 0.15:
                        high_key = code + '_high'
                        if high_key not in cost_basis:
                            cost_basis[high_key] = cost * 1.15
                        cost_basis[high_key] = max(cost_basis[high_key],
                                                    stock_dfs[code].loc[date, 'high']
                                                    if 'high' in stock_dfs[code].columns
                                                    else current_price)
                        if current_price < cost_basis[high_key] * 0.92:
                            shares = positions[code]
                            cash += shares * current_price
                            del positions[code]
                            del cost_basis[code]
                            if high_key in cost_basis: del cost_basis[high_key]
                            logger.info(f"  📈 {date.strftime('%Y-%m-%d')} 追踪止盈 {code}: +{ret*100:.1f}%")
                    elif ret < -0.12:
                        shares = positions[code]
                        cash += shares * current_price
                        del positions[code]
                        del cost_basis[code]
                        logger.info(f"  🛑 {date.strftime('%Y-%m-%d')} 止损 {code}: {ret*100:.1f}%")

        # 风控
        if not is_stopped_out and current_drawdown < MAX_DRAWDOWN_STOP:
            logger.info(f"  🚨 {date.strftime('%Y-%m-%d')} 回撤 {current_drawdown*100:.1f}% → 清仓")
            for c in list(positions.keys()):
                if c in stock_dfs and date in stock_dfs[c].index:
                    cash += positions[c] * stock_dfs[c].loc[date, 'close']
            positions.clear(); cost_basis.clear()
            is_stopped_out = True; peak_value = cash
            cooldown_until = date + timedelta(days=10)
            portfolio_value = cash
            current_market_position = 0.30

        if is_stopped_out:
            if cooldown_until and date < cooldown_until:
                daily_values.append({'date': date, 'value': portfolio_value, 'drawdown': current_drawdown})
                continue
            if signals['temperature'] > 50:
                logger.info(f"  ✅ 冷却结束 → 入场 (温度{signals['temperature']:.0f})")
                is_stopped_out = False; cooldown_until = None
                peak_value = portfolio_value
                current_market_position = 0.40
            else:
                daily_values.append({'date': date, 'value': portfolio_value, 'drawdown': current_drawdown})
                continue

        # 动态调仓
        if (day_idx - TRAIN_WINDOW) % REBALANCE_FREQ == 0 and not is_stopped_out:
            current_market_position = calc_position_level(current_market_position, signals)

            top_sectors = get_top_sectors(sector_stocks, stock_dfs, date, top_n=5)
            sector_info = []
            for sector, data, tier in top_sectors:
                sector_info.append(f"{sector}({tier}:5d={data['mom_5d']:.1f}%)")

            picks = select_stocks_from_sectors(top_sectors, sector_stocks, stock_dfs, date)
            target_codes = [p[0] for p in picks]

            if target_codes and signals['temperature'] > 25 and current_market_position > 0.01:
                total_value = cash + sum(
                    positions[c] * stock_dfs[c].loc[date, 'close']
                    for c in positions if c in stock_dfs and date in stock_dfs[c].index
                )
                target_value = total_value * current_market_position

                # 清仓重配
                for c in list(positions.keys()):
                    if c in stock_dfs and date in stock_dfs[c].index:
                        cash += positions[c] * stock_dfs[c].loc[date, 'close']
                    del positions[c]
                    if c in cost_basis: del cost_basis[c]

                per_stock = target_value / len(target_codes) if target_codes else 0
                for code in target_codes:
                    if code in stock_dfs and date in stock_dfs[code].index:
                        price = stock_dfs[code].loc[date, 'close']
                        if price > 0 and per_stock > 0:
                            shares = int(per_stock / price)
                            positions[code] = shares
                            cost_basis[code] = price
                            cash -= shares * price

                sector_log.append({
                    'date': date,
                    'sectors': sector_info,
                    'picks': [(p[0], p[2], f"{p[1]:.3f}") for p in picks],
                    'position': current_market_position,
                    'is_bearish': signals.get('is_bearish'),
                })

                bear_mark = "🐻熊市" if signals.get('is_bearish') else "🐂牛市"
                logger.info(f"  {date.strftime('%Y-%m-%d')} | 仓位:{current_market_position*100:.0f}% | "
                           f"温度:{signals['temperature']:.0f} | {bear_mark} | "
                           f"板块:{','.join(s[:6] for s in sector_info[:3])} | "
                           f"持有:{','.join(target_codes)}")

        # 记录
        portfolio_value = cash + sum(
            positions[c] * stock_dfs[c].loc[date, 'close']
            for c in positions if c in stock_dfs and date in stock_dfs[c].index
        )
        daily_values.append({'date': date, 'value': portfolio_value, 'drawdown': current_drawdown})

    # ============================================================
    # 分析
    # ============================================================
    pv_df = pd.DataFrame(daily_values)
    if pv_df.empty:
        logger.warning("无回测数据")
        return

    pv_df['daily_return'] = pv_df['value'].pct_change()
    dr = pv_df['daily_return'].dropna()

    total_return = pv_df['value'].iloc[-1] / pv_df['value'].iloc[0] - 1
    n = len(pv_df)
    annual = total_return * 252 / n
    sharpe = dr.mean()/dr.std()*np.sqrt(252) if dr.std()>0 else 0
    max_dd = pv_df['drawdown'].min()
    win_rate = (dr>0).sum()/(dr!=0).sum() if (dr!=0).sum()>0 else 0

    bh_ret = index_df['close'].iloc[-1]/index_df['close'].iloc[0]-1
    bh_annual = bh_ret * 252 / len(index_df)

    # GPU计算：如果指数在MA60上方 → buy & hold 同期收益
    dates_above_ma60 = []
    for day_idx in range(TRAIN_WINDOW, len(dates)):
        sig = calc_market_signals(index_df, day_idx)
        if not sig.get('is_bearish', True):
            dates_above_ma60.append(dates[day_idx])

    bullish_ratio = len(dates_above_ma60) / max(1, len(dates) - TRAIN_WINDOW) * 100

    # 输出
    logger.info(f"\n{'='*70}")
    logger.info(f"V13 — V12.1 增强版 回测结果")
    logger.info(f"{'='*70}")
    logger.info(f"回测期: {START_DATE} → {dates[-1].strftime('%Y-%m-%d')} ({n} 个交易日)")
    logger.info(f"初始: ¥{INIT_CAPITAL:,.0f} → 最终: ¥{pv_df['value'].iloc[-1]:,.0f}")
    logger.info(f"总收益: {total_return*100:.2f}%")
    logger.info(f"年化: {annual*100:.2f}% | 夏普: {sharpe:.2f} | 最大回撤: {max_dd*100:.1f}% | 胜率: {win_rate*100:.1f}%")
    logger.info(f"同期指数: {bh_annual*100:.1f}% | 超额: {(annual-bh_annual)*100:.1f}%")
    logger.info(f"🐂牛市占比: {bullish_ratio:.0f}% | 🐻熊市天数: {bearish_days}/{n}")
    logger.info(f"板块轮动日志: {len(sector_log)} 次调仓")

    # 年度分解
    pv_df['year'] = pv_df['date'].dt.year
    logger.info(f"年度收益:")
    for yr, group in pv_df.groupby('year'):
        yr_ret = group['value'].iloc[-1] / group['value'].iloc[0] - 1
        logger.info(f"  {yr}: {yr_ret*100:+.1f}%")

    # 板块出现频次
    sector_freq = {}
    for sl in sector_log:
        for s in sl['sectors']:
            name = s.split('(')[0]
            sector_freq[name] = sector_freq.get(name, 0) + 1
    top_freq = sorted(sector_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info(f"最强板块(调仓频次): {', '.join(f'{k}×{v}' for k,v in top_freq)}")

    if sector_log:
        last = sector_log[-1]
        logger.info(f"\n最近调仓({last['date'].strftime('%Y-%m-%d')}):")
        for s in last['sectors']:
            logger.info(f"  → {s}")
        logger.info(f"  选股: {last['picks']}")
        logger.info(f"  仓位: {last['position']*100:.0f}% | {'🐻熊市' if last.get('is_bearish') else '🐂牛市'}")

    ok = annual > 0.15 and sharpe > 0.8 and max_dd > -0.20
    logger.info(f"\n{'🟢 V13 达标' if ok else '🔴 V13 未达标'}")

    logger.info(f"\n📊 对比参考:")
    logger.info(f"  V12.1 (2025-2026): 年化 55.9% 夏普 1.69 回撤 -15.0% 胜率 52.0%")
    logger.info(f"  V12 (2025-2026):   年化 55.5% 夏普 1.59 回撤 -17.2% 胜率 51.7%")

    return {
        'annual': annual, 'sharpe': sharpe, 'max_dd': max_dd,
        'win_rate': win_rate, 'bh_annual': bh_annual, 'excess': annual - bh_annual,
        'sector_log': sector_log, 'sector_freq': sector_freq,
        'bullish_ratio': bullish_ratio,
    }


if __name__ == "__main__":
    growth_backtest_v13()
