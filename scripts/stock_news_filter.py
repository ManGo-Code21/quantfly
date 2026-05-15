#!/usr/bin/env python3
"""
L1 — 个股新闻过滤脚本
调仓前对候选股逐只查询新闻 → 返回安全名单

用途：V14 选股完成后、下单前，过滤掉有负面新闻的股票。
"""
import sys, os, json, time
sys.path.insert(0, '/Users/shj/skills')
from iwencai_api import query

# 负面关键词
NEGATIVE_KW = [
    "立案调查", "行政处罚", "监管函", "问询函", "ST", "*ST",
    "退市风险", "重大诉讼", "被冻结", "减持", "质押爆仓",
    "业绩变脸", "大幅下修", "预亏", "亏损", "暴雷",
    "商誉减值", "资产减值", "债务违约", "停产", "被制裁",
]


def scan_stock(code, name=""):
    """单只股票负面新闻检查"""
    query_text = f"{code} {name} 公告 新闻".strip()
    r = query("hithink-common-query", query_text, limit=5)
    datas = r.get('datas', [])

    negatives = []
    for item in datas:
        text = json.dumps(item, ensure_ascii=False)
        for kw in NEGATIVE_KW:
            if kw in text:
                negatives.append(kw)
                break

    return {
        'code': code,
        'safe': len(negatives) == 0,
        'negatives': negatives,
        'articles': len(datas),
    }


def filter_stocks(candidates, delay=0.3):
    """
    过滤候选股列表
    
    candidates: [(code, name), ...]
    返回: [safe_codes], [filtered_codes]
    """
    safe, filtered = [], []
    for code, name in candidates:
        result = scan_stock(code, name)
        if result['safe']:
            safe.append(code)
        else:
            filtered.append((code, result['negatives']))
            print(f"  ⚠️ {code} {name}: {', '.join(result['negatives'])}")
        time.sleep(delay)

    return safe, filtered


if __name__ == '__main__':
    # 测试
    test = [
        ('688041', '海光信息'),
        ('688256', '寒武纪'),
        ('300308', '中际旭创'),
    ]
    print("L1 个股过滤测试:")
    safe, bad = filter_stocks(test)
    print(f"  ✅ 安全: {safe}")
    print(f"  ❌ 过滤: {bad}")
