# -*- encoding: utf-8 -*-
"""
V14 — V13 + 三层新闻/情绪增强
================================
V13 = 牛熊切换 + 板块动量 + 质量/情绪因子
V14 新增:
  L3 基本面刷新：问财实时拉取最新 ROE/净利增速/营收增速
  L2 板块新闻情绪：行业新闻情绪加权到板块动量评分
  L1 个股新闻过滤：调仓前排除负面新闻个股（仅实盘）

🐂 沪深300 > MA60 → 全仓进攻
🐻 沪深300 < MA60 → 仓位压缩至 30%
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
logger = logging.getLogger("Growth_v14")

START_DATE = "2023-01-01"
TRAIN_WINDOW = 60
REBALANCE_FREQ = 10
TOP_N = 4
INIT_CAPITAL = 1_000_000
MAX_DRAWDOWN_STOP = -0.15

# 🆕 V14 运行模式
LIVE_MODE = False   # True=实盘模式(启用L1个股新闻过滤+问财API), False=回测模式
L2_ENABLED = True   # L2 板块情绪加权
L3_ENABLED = True   # L3 最新基本面数据

TREND_FILTER_MA = 60
TREND_FILTER_MAX_POS = 0.30
BULL_MAX_POS = 1.0

# ============================================================
# 🆕 V14: 加载增强数据
# ============================================================
# L3 — 最新基本面
FUNDAMENTALS_LATEST = {}
fund_latest_path = Path(__file__).parent / "data" / "fundamentals_latest.json"
if L3_ENABLED and fund_latest_path.exists():
    with open(fund_latest_path) as f:
        FUNDAMENTALS_LATEST = json.load(f)
    logger.info(f"🆕 L3 最新基本面: {len(FUNDAMENTALS_LATEST)}只 (Q1 2026)")
else:
    logger.info("L3 最新基本面: 未加载")

# L2 — 板块新闻情绪
SECTOR_SENTIMENT = {}
sector_sent_path = Path(__file__).parent / "data" / "sector_sentiment.json"
if L2_ENABLED and sector_sent_path.exists():
    with open(sector_sent_path) as f:
        SECTOR_SENTIMENT = json.load(f)
    logger.info(f"🆕 L2 板块情绪: {len(SECTOR_SENTIMENT)} 行业")

# 继承 V12.1 的时间感知基本面（回测用）
FUNDAMENTALS_2024 = {}
FUNDAMENTALS_2025 = {}
fund_2024_path = Path(__file__).parent / "data" / "fundamentals_2024.json"
fund_2025_path = Path(__file__).parent / "data" / "fundamentals_2025.json"
if fund_2024_path.exists():
    with open(fund_2024_path) as f:
        FUNDAMENTALS_2024 = json.load(f)
if fund_2025_path.exists():
    with open(fund_2025_path) as f:
        FUNDAMENTALS_2025 = json.load(f)

FUND_SWITCH_DATE = pd.Timestamp("2026-04-01")

def get_fundamentals(code, date):
    """V14 增强：优先用 L3 最新数据，否则降级到时间感知数据"""
    # L3 优先：如果交易日期接近现在，用最新基本面
    if L3_ENABLED and date >= pd.Timestamp("2026-04-01"):
        latest = FUNDAMENTALS_LATEST.get(code, {})
        if latest:
            return latest
    # 降级：时间感知
    if date >= FUND_SWITCH_DATE:
        return FUNDAMENTALS_2025.get(code, {})
    return FUNDAMENTALS_2024.get(code, {})

def get_sector_sentiment(sector):
    """L2: 获取板块新闻情绪分 [-1, 1]"""
    if not L2_ENABLED or not SECTOR_SENTIMENT:
        return 0.0
    return SECTOR_SENTIMENT.get(sector, 0.0)

# ============================================================
# 指数数据获取
# ============================================================
def get_index_data_robust(days=1200):
    import requests as req
    # QMT
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
    except Exception:
        pass
    # Tencent fallback
    try:
        r = req.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                    params={'param': f'sh000001,day,,,{days},qfq'},
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
    except Exception:
        pass
    return pd.DataFrame()

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

# ============================================================
# 数据获取
# ============================================================
def fetch_stock_data():
    import requests as req
    logger.info(f"获取 {len(ALL_STOCKS)} 只股票K线 (QMT)...")
    stock_dfs = {}
    for code in ALL_STOCKS:
        try:
            clean = code.split(".")[0]
            r = req.get(f"http://10.6.98.168:8765/data/kline",
                        params={"code": clean, "period": "1d", "count": 800}, timeout=15)
            candles = r.json().get("candles", [])
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
    sector_stocks = {}
    for etf_code, info in ETF_SECTOR_MAP.items():
        s = info['sector']
        if s not in sector_stocks:
            sector_stocks[s] = set()
        for stock in info['stocks']:
            if stock in stock_dfs:
                sector_stocks[s].add(stock)
    logger.info(f"板块动量覆盖: {len(sector_stocks)} 个行业")
    return sector_stocks

# ============================================================
# 板块动量信号
# ============================================================
def calc_sector_momentum(sector_stocks, stock_dfs, date, window=20):
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
            # 🆕 L2: 板块新闻情绪加权（±10%微调）
            l2_bonus = get_sector_sentiment(sector) * 0.10
            comp += l2_bonus
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
# 质量 + 情绪因子
# ============================================================
def quality_score(code, date):
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
    f = get_fundamentals(code, date)
    if not f:
        return True
    roe = f.get('roe', 10)
    pg = f.get('profit_g', 10)
    return not (roe < 5 or pg < -20)

def sentiment_score(df, idx):
    if idx < 20:
        return 0.0
    volume = df['volume'].iloc[idx-20:idx+1].values
    turn_ratio = np.mean(volume[-5:]) / np.mean(volume[-20:]) if np.mean(volume[-20:]) > 0 else 1
    return 0.05 if turn_ratio > 2.0 else (0.02 if turn_ratio > 1.5 else 0.0)

# ============================================================
# 🆕 L1: 个股新闻过滤（仅实盘模式）
# ============================================================
def stock_news_filter(candidates):
    """
    L1: 调仓前个股负面新闻检查
    仅在 LIVE_MODE=True 时生效
    """
    if not LIVE_MODE:
        return candidates  # 回测跳过

    try:
        import sys
        sys.path.insert(0, '/Users/shj/quantfly/scripts')
        from stock_news_filter import filter_stocks as _filter

        # candidates = [(code, name), ...]
        # We only have codes here, so pass without names
        pairs = [(c, '') for c, _, _, _ in candidates]
        safe, bad = _filter(pairs, delay=0.3)
        if bad:
            logger.info(f"  🆕 L1 过滤: {len(bad)}只负面 → {[(c, kw) for c, kw in bad]}")

        # Return only safe candidates
        safe_set = set(safe)
        return [(c, s, t, sec) for c, s, t, sec in candidates if c in safe_set]
    except Exception as e:
        logger.warning(f"L1 过滤失败 (fallthrough): {e}")
        return candidates

# ============================================================
# 动态选股
# ============================================================
def select_stocks_from_sectors(top_sectors, sector_stocks, stock_dfs, date, top_n=TOP_N):
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
    picks = stock_scores[:top_n + 2]  # 多选2只，为L1过滤留余量

    # 🆕 L1: 个股新闻过滤
    picks = stock_news_filter(picks)

    return picks[:top_n]

# ============================================================
# 仓位管理（V13 牛熊切换）
# ============================================================
def calc_market_signals(index_df, current_idx):
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
    temperature = signals['temperature']
    volume_signal = signals['volume_signal']
    volatility = signals['volatility']
    is_bearish = signals.get('is_bearish', False)

    if temperature < 20 and volume_signal > 0.5:
        return 0.00
    if volatility > 70:
        return max(0.05, current_position * 0.5)
    if temperature < 25:
        return 0.00
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

    if is_bearish:
        return min(base, TREND_FILTER_MAX_POS)
    return min(base, BULL_MAX_POS)

# ============================================================
# 主回测
# ============================================================
def growth_backtest_v14():
    logger.info("=" * 70)
    logger.info("V14 — V13 + L3最新基本面 + L2板块情绪 + L1个股过滤")
    logger.info(f"LIVE={LIVE_MODE} L2={L2_ENABLED} L3={L3_ENABLED}")
    logger.info("=" * 70)

    index_df = get_index_data_robust(days=1200)
    if not index_df.empty:
        index_df = index_df[index_df.index >= START_DATE]
    if index_df.empty:
        logger.error("指数为空"); return

    stock_dfs = fetch_stock_data()
    if len(stock_dfs) < 20:
        logger.error("股票数据不足"); return

    sector_stocks = build_sector_momentum_from_stocks(stock_dfs)

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
    bearish_days = 0
    l1_filtered_total = 0

    for day_idx, date in enumerate(dates):
        if day_idx < TRAIN_WINDOW:
            continue

        portfolio_value = cash
        for code, shares in positions.items():
            if code in stock_dfs and date in stock_dfs[code].index:
                portfolio_value += shares * stock_dfs[code].loc[date, 'close']

        if portfolio_value > peak_value:
            peak_value = portfolio_value
        current_drawdown = (portfolio_value - peak_value) / peak_value

        signals = calc_market_signals(index_df, day_idx)
        if signals.get('is_bearish'):
            bearish_days += 1

        # 黑天鹅
        if not is_stopped_out and day_idx >= 2:
            prev_temp = calc_market_signals(index_df, day_idx-2)['temperature']
            if prev_temp > 50 and signals['temperature'] < 15:
                logger.info(f"  🚨 {date.strftime('%Y-%m-%d')} 黑天鹅！")
                for code in list(positions.keys()):
                    if code in stock_dfs and date in stock_dfs[code].index:
                        sell_shares = int(positions[code] * 0.60)
                        if sell_shares > 0:
                            cash += sell_shares * stock_dfs[code].loc[date, 'close']
                            positions[code] -= sell_shares
                            if positions[code] <= 0:
                                del positions[code]; del cost_basis[code]

        # 追踪止损
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
                            cash += positions[code] * current_price
                            del positions[code]; del cost_basis[code]
                            if high_key in cost_basis: del cost_basis[high_key]
                    elif ret < -0.12:
                        cash += positions[code] * current_price
                        del positions[code]; del cost_basis[code]

        # 风控
        if not is_stopped_out and current_drawdown < MAX_DRAWDOWN_STOP:
            logger.info(f"  🚨 {date.strftime('%Y-%m-%d')} 回撤{current_drawdown*100:.1f}% → 清仓")
            for c in list(positions.keys()):
                if c in stock_dfs and date in stock_dfs[c].index:
                    cash += positions[c] * stock_dfs[c].loc[date, 'close']
            positions.clear(); cost_basis.clear()
            is_stopped_out = True; peak_value = cash
            cooldown_until = date + timedelta(days=10)
            current_market_position = 0.30

        if is_stopped_out:
            if cooldown_until and date < cooldown_until:
                daily_values.append({'date': date, 'value': portfolio_value, 'drawdown': current_drawdown})
                continue
            if signals['temperature'] > 50:
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
                    'date': date, 'sectors': sector_info,
                    'picks': [(p[0], p[2], f"{p[1]:.3f}") for p in picks],
                    'position': current_market_position,
                    'is_bearish': signals.get('is_bearish'),
                })
                bear_mark = "🐻" if signals.get('is_bearish') else "🐂"
                logger.info(f"  {date.strftime('%Y-%m-%d')} | 仓位:{current_market_position*100:.0f}% | "
                           f"温度:{signals['temperature']:.0f} | {bear_mark} | "
                           f"持有:{','.join(target_codes)}")

        portfolio_value = cash + sum(
            positions[c] * stock_dfs[c].loc[date, 'close']
            for c in positions if c in stock_dfs and date in stock_dfs[c].index
        )
        daily_values.append({'date': date, 'value': portfolio_value, 'drawdown': current_drawdown})

    # 分析
    pv_df = pd.DataFrame(daily_values)
    if pv_df.empty:
        logger.warning("无回测数据"); return

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

    bullish_ratio = (n - bearish_days) / n * 100 if n > 0 else 0

    logger.info(f"\n{'='*70}")
    logger.info(f"V14 — V13 + 三层增强 回测结果")
    logger.info(f"{'='*70}")
    logger.info(f"回测期: {START_DATE} → {dates[-1].strftime('%Y-%m-%d')} ({n} 个交易日)")
    logger.info(f"初始: ¥{INIT_CAPITAL:,.0f} → 最终: ¥{pv_df['value'].iloc[-1]:,.0f}")
    logger.info(f"总收益: {total_return*100:.2f}%")
    logger.info(f"年化: {annual*100:.2f}% | 夏普: {sharpe:.2f} | 最大回撤: {max_dd*100:.1f}% | 胜率: {win_rate*100:.1f}%")
    logger.info(f"同期指数: {bh_annual*100:.1f}% | 超额: {(annual-bh_annual)*100:.1f}%")
    logger.info(f"🐂牛市占比: {bullish_ratio:.0f}% | 🐻熊市: {bearish_days}/{n}")
    logger.info(f"板块轮动日志: {len(sector_log)} 次调仓")

    pv_df['year'] = pv_df['date'].dt.year
    logger.info(f"年度收益:")
    for yr, group in pv_df.groupby('year'):
        yr_ret = group['value'].iloc[-1] / group['value'].iloc[0] - 1
        logger.info(f"  {yr}: {yr_ret*100:+.1f}%")

    sector_freq = {}
    for sl in sector_log:
        for s in sl['sectors']:
            name = s.split('(')[0]
            sector_freq[name] = sector_freq.get(name, 0) + 1
    top_freq = sorted(sector_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info(f"最强板块: {', '.join(f'{k}×{v}' for k,v in top_freq)}")

    if sector_log:
        last = sector_log[-1]
        logger.info(f"\n最近调仓({last['date'].strftime('%Y-%m-%d')}):")
        for s in last['sectors']:
            logger.info(f"  → {s}")
        logger.info(f"  选股: {last['picks']}")

    logger.info(f"\n🆕 V14 增强状态: L1={'✅实盘' if LIVE_MODE else '⏸回测'} "
               f"L2={'✅' if L2_ENABLED else '⏸'} L3={'✅' if L3_ENABLED else '⏸'}")
    ok = annual > 0.15 and sharpe > 0.8 and max_dd > -0.20
    logger.info(f"{'🟢 V14 达标' if ok else '🔴 V14 未达标'}")


if __name__ == "__main__":
    growth_backtest_v14()
