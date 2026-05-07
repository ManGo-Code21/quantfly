# -*- encoding: utf-8 -*-
"""
产业-板块映射 — 产业名 → 东方财富板块 → 成分股
"""
import requests
import logging
import time
from typing import Optional

logger = logging.getLogger("IndustryMapper")

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_em_session = requests.Session()
_em_session.trust_env = False

# 产业名 → 东方财富板块代码
INDUSTRY_SECTOR_MAP = {
    "AI大模型":   {"板块": "AI概念",   "板块代码": "BK0800"},
    "半导体":     {"板块": "半导体",   "板块代码": "BK0880"},
    "机器人":     {"板块": "机器人",   "板块代码": "BK0900"},
    "稀土永磁":   {"板块": "稀土永磁", "板块代码": "BK0899"},
    "脑机接口":   {"板块": "脑机接口", "板块代码": "BK0894"},
    "商业航天":   {"板块": "商业航天", "板块代码": "BK0716"},
    "智能电网":   {"板块": "智能电网", "板块代码": "BK0924"},
    "量子计算":   {"板块": "量子计算", "板块代码": "BK0885"},
    "新能源车":   {"板块": "新能源车", "板块代码": "BK0900"},
}

_cache = {}
_cache_time = {}
_CACHE_TTL = 300  # 5分钟缓存


def get_sector_list() -> list[str]:
    """列出所有支持的产业"""
    return list(INDUSTRY_SECTOR_MAP.keys())


def get_eastmoney_sector_stocks(industry: str) -> list[tuple]:
    """
    获取产业对应的东方财富板块成分股

    Args:
        industry: 产业名 (如 "AI大模型")

    Returns:
        List of (code, name) tuples, 按涨跌幅降序
    """
    if industry not in INDUSTRY_SECTOR_MAP:
        logger.warning(f"未知产业: {industry}")
        return []

    bk_info = INDUSTRY_SECTOR_MAP[industry]
    bk_code = bk_info["板块代码"]

    # 检查缓存
    now = time.time()
    if industry in _cache and _cache_time.get(industry, 0) > now - _CACHE_TTL:
        return _cache[industry]

    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1,
            "pz": 500,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": f"b:{bk_code}",
            "fields": "f12,f14,f2,f3,f20",
        }
        r = _em_session.get(url, params=params, headers=EM_HEADERS, timeout=10)
        data = r.json()
        stocks = data.get("data", {}).get("diff", [])

        result = []
        for s in stocks:
            code = str(s.get("f12", ""))
            name = str(s.get("f14", ""))
            if code and name:
                result.append((code, name))

        # 缓存
        _cache[industry] = result
        _cache_time[industry] = now

        logger.info(f"[{industry}] 获取 {len(result)} 只成分股 (板块: {bk_code})")
        return result

    except Exception as e:
        logger.error(f"获取板块成分股失败 [{industry}]: {e}")
        return []
