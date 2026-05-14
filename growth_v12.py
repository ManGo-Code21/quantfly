# -*- encoding: utf-8 -*-
"""
V12 — 动态板块动量轮动 + 趋势波段 + 波动率过滤
====================================================
核心升级（vs V11）：
1. 动态板块发现：每日扫描30+行业ETF动量，不用固定股票池
2. 三层信号：启动信号(5日) → 加速信号(10日) → 高潮信号(20日)
3. 动态选股：在最强ETF对应的行业里选龙头股
4. 仓位管理：继承V11的黑天鹅/波动率过滤/追踪止盈

数据源：ETF日线(akshare fund_etf_hist_em) + 个股K线(get_kline_em)
"""
import numpy as np
import pandas as pd
import akshare as ak
import logging
from datetime import timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Growth_v12")

from train_ranker import get_kline_em
from timing_model import get_index_data

START_DATE = "2025-05-14"
TRAIN_WINDOW = 60
REBALANCE_FREQ = 10  # 10天调仓（V11的15天，V12更灵活）
TOP_N = 4
INIT_CAPITAL = 1_000_000
MAX_DRAWDOWN_STOP = -0.15

# ============================================================
# ETF → 行业股票映射（30+行业ETF，覆盖科技/周期/国防/消费/医药/金融）
# ============================================================
ETF_SECTOR_MAP = {
    # 科技（细分为芯片/通信/AI/云计算/游戏）
    'sh512760': {'name': '芯片ETF', 'sector': '半导体', 'stocks': ['688041','002415','688111','688256','688008','300474','603501','688981']},
    'sz159995': {'name': '芯片ETF深', 'sector': '半导体', 'stocks': ['688041','002415','688111','688256','688008','300474','603501','688981']},
    'sh515050': {'name': '通信ETF', 'sector': '通信', 'stocks': ['000063','600050','300308','002281','300502']},
    'sh515980': {'name': '人工智能ETF', 'sector': 'AI', 'stocks': ['688111','002230','603019','300496','688256','000977','688041']},
    'sh516510': {'name': '云计算ETF', 'sector': '算力', 'stocks': ['000977','603019','300383','002230','002415','603881','002587']},
    'sz159869': {'name': '游戏ETF', 'sector': '传媒', 'stocks': ['300418','002602','002517','002558']},
    # 新能源
    'sh515790': {'name': '光伏ETF', 'sector': '光伏', 'stocks': ['601012','002129','688223','300274','688390']},
    'sh516160': {'name': '新能源ETF', 'sector': '新能源', 'stocks': ['300750','601012','002594','300274','688005']},
    'sh516390': {'name': '新能源车ETF', 'sector': '新能源车', 'stocks': ['300750','002594','601799','002460']},
    'sh516880': {'name': '光伏ETF银华', 'sector': '光伏', 'stocks': ['601012','002129','688223','300274']},
    # 周期/电力/基建（电网设备在这里）
    'sh561560': {'name': '电力ETF', 'sector': '电力', 'stocks': ['600900','601985','600886','600089','002350','601179','600550']},
    'sh516970': {'name': '基建ETF', 'sector': '基建', 'stocks': ['601800','601390','601668','600585']},
    'sh515220': {'name': '煤炭ETF', 'sector': '煤炭', 'stocks': ['601088','600188','601225','600985']},
    # 国防/军工
    'sh512660': {'name': '军工ETF', 'sector': '军工', 'stocks': ['600879','600150','688066','600760']},
    'sh512670': {'name': '国防ETF', 'sector': '军工', 'stocks': ['600879','600150','688066','600760']},
    'sh512710': {'name': '军工龙头', 'sector': '军工', 'stocks': ['600879','600150','688066']},
    # 医药
    'sh512010': {'name': '医药ETF', 'sector': '医药', 'stocks': ['600276','300760','688180','688185','603259']},
    'sz159883': {'name': '医疗器械ETF', 'sector': '医药', 'stocks': ['300760','688180','603259']},
    # 消费
    'sz159996': {'name': '家电ETF', 'sector': '消费', 'stocks': ['000333','000651','002508']},
    'sz159928': {'name': '消费ETF', 'sector': '消费', 'stocks': ['600519','000858','002304','000568']},
    'sh512690': {'name': '酒ETF', 'sector': '消费', 'stocks': ['600519','000858','002304','000568']},
    'sz159865': {'name': '养殖ETF', 'sector': '消费', 'stocks': ['002714','300498','002157']},
    'sz159766': {'name': '旅游ETF', 'sector': '消费', 'stocks': ['601888','600754']},
    # 金融
    'sh512800': {'name': '银行ETF', 'sector': '金融', 'stocks': ['601398','601939','600036','000001']},
    'sh512880': {'name': '证券ETF', 'sector': '金融', 'stocks': ['600030','601066','601881','300059']},
    'sh510230': {'name': '金融ETF', 'sector': '金融', 'stocks': ['601318','600036','000001']},
    'sh512200': {'name': '房地产ETF', 'sector': '金融', 'stocks': ['000002','600048','001979']},
}

# 去重ETF代码
ETF_CODES = list(ETF_SECTOR_MAP.keys())

# 行业去重映射
SECTOR_TO_ETF = {}
for code, info in ETF_SECTOR_MAP.items():
    s = info['sector']
    if s not in SECTOR_TO_ETF:
        SECTOR_TO_ETF[s] = []
    SECTOR_TO_ETF[s].append(code)

# 展平所有股票
ALL_STOCKS = list(set(
    code for info in ETF_SECTOR_MAP.values() for code in info['stocks']
))

# ============================================================
# 数据获取
# ============================================================
def fetch_stock_data():
    """获取所有股票历史日线"""
    logger.info(f"获取{len(ALL_STOCKS)}只股票K线...")
    stock_dfs = {}
    for code in ALL_STOCKS:
        try:
            df = get_kline_em(code, count=300)
            if not df.empty and len(df[df.index >= START_DATE]) >= 40:
                stock_dfs[code] = df[df.index >= START_DATE]
        except:
            pass
    logger.info(f"  有效股票: {len(stock_dfs)} 只")
    return stock_dfs


def build_sector_momentum_from_stocks(stock_dfs):
    """
    用各板块成分股的加权平均涨跌幅 代替 ETF 动量
    Mac上ETF数据不可靠，直接用个股算板块动量更准确
    """
    # 按 sector 分组
    sector_stocks = {}
    for etf_code, info in ETF_SECTOR_MAP.items():
        s = info['sector']
        if s not in sector_stocks:
            sector_stocks[s] = set()
        for stock in info['stocks']:
            if stock in stock_dfs:
                sector_stocks[s].add(stock)

    logger.info(f"板块动量覆盖: {len(sector_stocks)}个行业")
    for s, stocks in sorted(sector_stocks.items()):
        logger.debug(f"  {s}: {len(stocks)}只")

    return sector_stocks


# ============================================================
# 板块动量信号（用板块内个股加权平均）
# ============================================================
def calc_sector_momentum(sector_stocks, stock_dfs, date, window=20):
    """计算所有板块的指定窗口动量（成分股等权平均）"""
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
            momentum[sector] = np.mean(returns) * 100  # 等权平均，转百分比
    return momentum


def get_top_sectors(sector_stocks, stock_dfs, date, top_n=5, windows=[5, 10, 20]):
    """
    三层信号体系：
    1. 5日动量 → 启动信号（权重0.5）
    2. 10日动量 → 加速信号（权重0.3）
    3. 20日动量 → 趋势确认（权重0.2）
    """
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
# 动态选股（从强势ETF对应的股票池中选）
# ============================================================
def select_stocks_from_sectors(top_sectors, sector_stocks, stock_dfs, date, top_n=TOP_N):
    """在最强板块对应的股票池中选个股"""
    stock_scores = []
    seen_codes = set()

    for sector, data, tier in top_sectors:
        candidates = list(sector_stocks.get(sector, set()))
        for code in candidates:
            if code in seen_codes:
                continue
            seen_codes.add(code)

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

            stock_vol = np.std(np.diff(np.log(close[-21:]))) * np.sqrt(252) * 100 if len(close)>=21 else 0
            vol_penalty = max(0, (stock_vol - 50) * 0.005)

            sector_bonus = data['score'] / 100

            final = momentum*0.40 + trend*0.30 + vol_score*0.15 - vol_penalty + sector_bonus
            stock_scores.append((code, final, tier, sector))

    stock_scores.sort(key=lambda x: x[1], reverse=True)
    return stock_scores[:top_n]


# ============================================================
# 仓位管理（继承V11）
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
    }


def calc_position_level(current_position, signals):
    """动态仓位控制（继承V11）"""
    temperature = signals['temperature']
    volume_signal = signals['volume_signal']
    volatility = signals['volatility']

    if temperature < 20 and volume_signal > 0.5:
        return 0.00
    if volatility > 70:
        return max(0.05, current_position * 0.5)
    if temperature < 25:
        return 0.00
    if temperature > 60 and volume_signal > 0 and volatility < 50:
        if current_position < 0.5:
            return min(1.0, current_position + 0.30)
        elif current_position < 1.0:
            return min(1.0, current_position + 0.20)
    if temperature < 40:
        return max(0.05, current_position - 0.25)
    return current_position


# ============================================================
# 主回测
# ============================================================
def growth_backtest_v12():
    logger.info("=" * 70)
    logger.info("V12 动态板块动量轮动 + 趋势波段 + 波动率过滤")
    logger.info("=" * 70)

    # 获取数据
    index_df = get_index_data(days=400)
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
    position_history = {}

    trades = []
    daily_values = []
    sector_log = []  # 记录每次调仓时的板块信号

    peak_value = INIT_CAPITAL
    is_stopped_out = False
    cooldown_until = None
    current_market_position = 0.5

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

        # 黑天鹅响应
        if not is_stopped_out and day_idx >= 2:
            prev_temp = calc_market_signals(index_df, day_idx-2)['temperature']
            curr_temp = signals['temperature']
            if prev_temp > 50 and curr_temp < 15:
                logger.info(f"  🚨 {date.strftime('%Y-%m-%d')} 黑天鹅！快速减仓")
                for code in list(positions.keys()):
                    if code in stock_dfs and date in stock_dfs[code].index:
                        sell_ratio = 0.60
                        sell_shares = int(positions[code] * sell_ratio)
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

        # 动态调仓：板块扫描 → 选股
        if (day_idx - TRAIN_WINDOW) % REBALANCE_FREQ == 0 and not is_stopped_out:
            current_market_position = calc_position_level(current_market_position, signals)

            # 板块动量扫描
            top_sectors = get_top_sectors(sector_stocks, stock_dfs, date, top_n=5)

            # 记录板块信号
            sector_info = []
            for sector, data, tier in top_sectors:
                sector_info.append(f"{sector}({tier}:5d={data['mom_5d']:.1f}%)")

            # 选股
            picks = select_stocks_from_sectors(top_sectors, sector_stocks, stock_dfs, date)
            target_codes = [p[0] for p in picks]

            if target_codes and signals['temperature'] > 25:
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
                    'position': current_market_position
                })

                logger.info(f"  {date.strftime('%Y-%m-%d')} | 仓位:{current_market_position*100:.0f}% | "
                           f"温度:{signals['temperature']:.0f} | 波:{signals['volatility']:.0f} | "
                           f"板块:{','.join(s[:8] for s in sector_info[:3])} | "
                           f"持有:{','.join(target_codes)}")

        # 记录
        portfolio_value = cash + sum(
            positions[c] * stock_dfs[c].loc[date, 'close']
            for c in positions if c in stock_dfs and date in stock_dfs[c].index
        )
        daily_values.append({'date': date, 'value': portfolio_value, 'drawdown': current_drawdown})

    # 分析
    pv_df = pd.DataFrame(daily_values)
    if pv_df.empty:
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

    logger.info(f"\n{'='*70}")
    logger.info(f"V12 动态板块动量轮动 结果")
    logger.info(f"{'='*70}")
    logger.info(f"初始: ¥{INIT_CAPITAL:,.0f} → 最终: ¥{pv_df['value'].iloc[-1]:,.0f}")
    logger.info(f"年化: {annual*100:.2f}% | 夏普: {sharpe:.2f} | 回撤: {max_dd*100:.1f}% | 胜率: {win_rate*100:.1f}%")
    logger.info(f"同期指数: {bh_annual*100:.1f}% | 超额: {(annual-bh_annual)*100:.1f}%")
    logger.info(f"板块轮动日志: {len(sector_log)}次调仓")

    # 板块出现频次统计
    sector_freq = {}
    for sl in sector_log:
        for s in sl['sectors']:
            name = s.split('(')[0]
            sector_freq[name] = sector_freq.get(name, 0) + 1
    top_freq = sorted(sector_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info(f"最强板块(调仓频次): {', '.join(f'{k}×{v}' for k,v in top_freq)}")

    # 最近一次调仓（最新板块信号）
    if sector_log:
        last = sector_log[-1]
        logger.info(f"\n最近调仓({last['date'].strftime('%Y-%m-%d')}):")
        for s in last['sectors']:
            logger.info(f"  → {s}")
        logger.info(f"  选股: {last['picks']}")

    logger.info(f"{'='*70}")

    ok = annual>0.15 and sharpe>0.8 and max_dd>-0.20 and win_rate>0.50
    logger.info("🟢 达标" if ok else "🔴 未达标")

    return {
        'annual': annual, 'sharpe': sharpe, 'max_dd': max_dd,
        'win_rate': win_rate, 'bh_annual': bh_annual, 'excess': annual - bh_annual,
        'sector_log': sector_log, 'sector_freq': sector_freq,
    }


if __name__ == "__main__":
    growth_backtest_v12()
