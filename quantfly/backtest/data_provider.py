# -*- encoding: utf-8 -*-
"""
回测数据提供模块
从东方财富获取K线和实时行情
"""
import requests
import pandas as pd
import time
import logging
from typing import Optional

logger = logging.getLogger("Backtest.Data")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
EM_HIST_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/clist/get"


def get_kline_em(code: str, count: int = 100) -> pd.DataFrame:
    """东方财富K线"""
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": count,
    }
    try:
        r = requests.get(EM_HIST_URL, params=params, headers=EM_HEADERS, timeout=10)
        klines = r.json().get("data", {}).get("klines", [])
        records = []
        for k in klines:
            p = k.split(",")
            records.append({
                "date": pd.to_datetime(p[0]),
                "open": float(p[1]),
                "high": float(p[2]),
                "low": float(p[3]),
                "close": float(p[4]),
                "volume": int(p[5]),
            })
        return pd.DataFrame(records).set_index("date").sort_index()
    except Exception as e:
        logger.warning(f"获取K线失败 {code}: {e}")
        return pd.DataFrame()


def get_realtime_quotes(codes: list) -> pd.DataFrame:
    """批量获取实时行情"""
    if not codes:
        return pd.DataFrame()
    df_list = []
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        secids = []
        for c in batch:
            secid = f"1.{c}" if c.startswith(("6", "9")) else f"0.{c}"
            secids.append(secid)
        params = {
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2", "invt": "2",
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f12,f14",
            "secids": ",".join(secids),
        }
        try:
            r = requests.get(EM_QUOTE_URL, params=params, headers=EM_HEADERS, timeout=10)
            data = r.json().get("data", {}).get("diff", [])
            for item in data:
                df_list.append({
                    "code": str(item.get("f12", "")),
                    "name": item.get("f14", ""),
                    "change_pct": item.get("f3", 0),
                    "volume": item.get("f5", 0),
                    "amount": item.get("f6", 0),
                    "turn": item.get("f8", 0),
                })
        except Exception as e:
            logger.warning(f"获取实时行情失败: {e}")
        time.sleep(0.1)
    return pd.DataFrame(df_list) if df_list else pd.DataFrame()


def get_all_limit_up_codes() -> set:
    """获取涨停股代码集合"""
    try:
        params = {
            "pn": 1, "pz": 200,
            "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12",
            "filter": "f3=9.9",
        }
        r = requests.get(EM_QUOTE_URL, params=params, headers=EM_HEADERS, timeout=10)
        data = r.json().get("data", {})
        diff = data.get("diff", [])
        return {str(item.get("f12", "")) for item in diff}
    except:
        return set()
