#!/usr/bin/env python3
"""
L2 — 板块新闻情绪采集脚本
用 news-search 查询各板块最新新闻 → 输出 sector_sentiment.json

每天开盘前跑一次，为 V14 提供板块情绪加权因子。
"""
import sys, os, json, time
sys.path.insert(0, '/Users/shj/skills')
from iwencai_api import query

SECTORS = [
    "半导体", "通信", "AI", "算力", "传媒",
    "光伏", "新能源", "新能源车",
    "电力", "基建", "煤炭",
    "军工",
    "医药",
    "消费",
    "金融",
]

OUTPUT_FILE = "/Users/shj/quantfly/data/sector_sentiment.json"

# 情绪关键词 → 分数映射
POSITIVE_KW = ["突破", "利好", "大增", "超预期", "政策支持", "获批", "加速", "新高", "放量", "增长"]
NEGATIVE_KW = ["下跌", "利空", "减持", "监管", "调查", "亏损", "暴雷", "退市", "违约", "制裁", "下滑"]

def score_news(news_items):
    """对新闻列表做简单情绪评分"""
    if not news_items:
        return 0.0
    
    pos, neg = 0, 0
    for item in news_items:
        text = json.dumps(item, ensure_ascii=False).lower()
        for kw in POSITIVE_KW:
            if kw in text:
                pos += 1
        for kw in NEGATIVE_KW:
            if kw in text:
                neg += 1
    
    total = pos + neg
    if total == 0:
        return 0.0
    
    # [−1, +1] 范围
    return (pos - neg) / total


def main():
    print(f"L2 板块情绪采集: {len(SECTORS)} 个行业")
    sentiment = {}

    for sector in SECTORS:
        r = query("hithink-common-query",
                  f"{sector} 行业 新闻 最新动态",
                  limit=5)
        datas = r.get('datas', [])
        s = score_news(datas)
        sentiment[sector] = s
        print(f"  {sector}: sentiment={s:+.2f} ({len(datas)} articles)")

        time.sleep(0.3)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(sentiment, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存 {len(sentiment)} 个行业情绪 → {OUTPUT_FILE}")
    print(f"   正面: {', '.join(k for k,v in sentiment.items() if v > 0.2)}")
    print(f"   负面: {', '.join(k for k,v in sentiment.items() if v < -0.2)}")


if __name__ == '__main__':
    main()
