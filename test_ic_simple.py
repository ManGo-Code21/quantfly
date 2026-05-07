#!/usr/bin/env python3
"""快速IC测试脚本"""
import requests
import pandas as pd
import numpy as np
import time

FEATURE_COLS = [
    'ret5', 'ret10', 'ret20', 'vol_std20', 'vol_ratio', 'price_std20',
    'high_low_ratio', 'turn_rate', 'ret_skew', 'vol_skew',
    'pct_chg', 'amplitude', 'close_ma20_ratio',
    'ret_vs_ma5', 'ret_vs_ma10', 'vol_stability',
    'ret_accel', 'vol_growth', 'vol_momentum', 'rsi14',
]

EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://quote.eastmoney.com/',
}
EM_HIST = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'

def get_kline_em(code, count=200):
    secid = f'1.{code}' if code.startswith(('6','9')) else f'0.{code}'
    params = {
        'secid': secid,
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': '101', 'fqt': '1', 'beg': '0', 'end': '20500101', 'lmt': count,
    }
    try:
        r = requests.get(EM_HIST, params=params, headers=EM_HEADERS, timeout=5)
        klines = r.json().get('data', {}).get('klines', [])
        records = []
        for k in klines:
            p = k.split(',')
            records.append({
                'date': pd.to_datetime(p[0]),
                'open': float(p[1]), 'high': float(p[2]),
                'low': float(p[3]), 'close': float(p[4]),
                'volume': int(p[5]),
            })
        return pd.DataFrame(records).set_index('date').sort_index()
    except:
        return pd.DataFrame()

def _calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return float(100 - 100 / (1 + avg_gain / avg_loss))

def calc_features(df, future_n=5):
    if df is None or len(df) < 30:
        return pd.DataFrame()
    close = df['close'].astype(float).values
    volume = df['volume'].astype(float).values
    high = df['high'].astype(float).values
    low = df['low'].astype(float).values
    rows = []
    for i in range(21, len(df) - future_n):
        c = close[:i+1]; v = volume[:i+1]; h = high[:i+1]; l = low[:i+1]
        ret5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
        ret10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
        ret20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0
        future_ret = (close[i + future_n] / c[-1] - 1) if i + future_n < len(close) else 0
        rows.append({
            'date': df.index[i],
            'ret5': ret5, 'ret10': ret10, 'ret20': ret20,
            'vol_std20': float(np.std(v[-20:]) / (np.mean(v[-20:]) + 1)),
            'vol_ratio': v[-1] / (np.mean(v[-5:]) + 1),
            'price_std20': float(np.std(c[-20:]) / (np.mean(c[-20:]) + 1)),
            'high_low_ratio': (h[-1] - l[-1]) / (c[-1] + 0.01),
            'turn_rate': v[-1] / (v[-20:].sum() / 20 + 1) if v[-20:].sum() > 0 else 0,
            'ret_skew': float(pd.Series(c[-20:]).skew()) if len(c) >= 20 else 0,
            'vol_skew': float(pd.Series(v[-20:]).skew()) if len(v) >= 20 else 0,
            'pct_chg': (c[-1] / c[-2] - 1) * 100 if len(c) > 1 else 0,
            'amplitude': ((h[-1] - l[-1]) / (c[-1] + 0.01)) * 100,
            'close_ma20_ratio': c[-1] / (np.mean(c[-20:]) + 0.01),
            'ret_vs_ma5': c[-1] / (np.mean(c[-5:]) + 0.01) if len(c) >= 5 else 1,
            'ret_vs_ma10': c[-1] / (np.mean(c[-10:]) + 0.01) if len(c) >= 10 else 1,
            'vol_stability': float(np.std(v[-10:]) / (np.mean(v[-10:]) + 1)),
            'ret_accel': ret5 - ret10,
            'vol_growth': np.mean(v[-5:]) / (np.mean(v[-20:]) + 1),
            'vol_momentum': np.mean(v[-3:]) / (np.mean(v[-10:-3]) + 1) if len(v) >= 10 else 1,
            'rsi14': _calc_rsi(c, 14),
            'future_ret': future_ret,
        })
    return pd.DataFrame(rows)

if __name__ == "__main__":
    # 测试获取10只股票
    stocks = ['000001', '000002', '000004', '000005', '000006', '000007', '000008', '000009', '000010', '000011']
    all_features = []
    for code in stocks:
        df = get_kline_em(code, count=250)
        if not df.empty:
            feats = calc_features(df)
            if not feats.empty:
                feats['code'] = code
                all_features.append(feats)
        time.sleep(0.1)

    if all_features:
        df_all = pd.concat(all_features, ignore_index=True)
        for col in FEATURE_COLS + ['future_ret']:
            if col in df_all.columns:
                df_all[col] = df_all[col].replace([np.inf, -np.inf], np.nan).fillna(0)
        
        # 计算截面IC
        daily_ics = {col: [] for col in FEATURE_COLS}
        for date, group in df_all.groupby('date'):
            if len(group) < 5:
                continue
            future = group['future_ret'].values
            for col in FEATURE_COLS:
                vals = group[col].values
                mask = np.isfinite(vals) & np.isfinite(future)
                if mask.sum() < 3:
                    continue
                corr = np.corrcoef(vals[mask], future[mask])[0, 1]
                if np.isfinite(corr):
                    daily_ics[col].append(corr)
        
        print('\n=== 10只股票 因子IC快速测试 ===')
        print(f'总记录数: {len(df_all)}')
        print(f'\n{"因子":<18} {"IC均值":>8} {"IC标准差":>8} {"IR":>7} {"胜率":>6}')
        print('-' * 50)
        for col in FEATURE_COLS:
            ic_list = daily_ics[col]
            if ic_list:
                ic_arr = np.array(ic_list)
                print(f'{col:<18} {np.mean(ic_arr):>+8.4f} {np.std(ic_arr):>8.4f} {np.mean(ic_arr)/(np.std(ic_arr)+1e-10):>+7.3f} {np.mean(ic_arr>0)*100:>5.0f}%')
    else:
        print('获取数据失败')