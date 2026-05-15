#!/usr/bin/env python3
"""
L3 — 基本面数据刷新脚本
用问财 hithink-finance-query 拉取全部持仓股的最新 ROE/净利增速/营收增速
→ 输出 fundamentals_latest.json
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/Users/shj/skills')
from iwencai_api import query

# 全部持仓股（来自 V13 ETF_SECTOR_MAP 展平去重）
ALL_STOCKS = sorted(set([
    '688041','002415','688111','688256','688008','300474','603501','688981',
    '000063','600050','300308','002281','300502',
    '002230','603019','300496','000977',
    '300383','603881','002587',
    '300418','002602','002517','002558',
    '601012','002129','688223','300274','688390',
    '300750','002594','601799','002460','688005',
    '600900','601985','600886','600089','002350','601179','600550',
    '601800','601390','601668','600585',
    '601088','600188','601225','600985',
    '600879','600150','688066','600760',
    '600276','300760','688180','688185','603259',
    '000333','000651','002508',
    '600519','000858','002304','000568',
    '002714','300498','002157',
    '601888','600754',
    '601398','601939','600036','000001',
    '600030','601066','601881','300059',
    '601318',
    '000002','600048','001979',
]))

BATCH_SIZE = 8   # 每批8只，控制API频率
OUTPUT_FILE = "/Users/shj/quantfly/data/fundamentals_latest.json"

# 技能检测到的日期后缀（最新报告期）
def parse_finance(datas):
    """解析 finance-query 返回的财务数据"""
    out = {}
    for d in datas:
        code = d.get('股票代码', '').split('.')[0]
        if not code:
            continue
        
        # 找最新报告期的 ROE / 净利增速 / 营收增速
        roe = None
        profit_g = None
        revenue_g = None
        for key, val in d.items():
            if '净资产收益率' in key:
                try:
                    roe = float(val)
                except:
                    pass
            elif '归母净利润同比增长率' in key:
                try:
                    profit_g = float(val)
                except:
                    pass
            elif '营业收入同比增长率' in key:
                try:
                    revenue_g = float(val)
                except:
                    pass
        
        if roe is not None or profit_g is not None:
            out[code] = {
                'roe': roe if roe is not None else 0,
                'profit_g': profit_g if profit_g is not None else 0,
                'revenue_g': revenue_g if revenue_g is not None else 0,
                'name': d.get('股票简称', code),
            }
    return out


def main():
    print(f"L3 基本面刷新: {len(ALL_STOCKS)} 只股票")
    all_fundamentals = {}
    failed = []

    for i in range(0, len(ALL_STOCKS), BATCH_SIZE):
        batch = ALL_STOCKS[i:i + BATCH_SIZE]
        query_text = ' '.join(batch[:6])  # 第一批6只
        query_text += ' ROE 净利润增长率 营业收入增长率'

        r = query('hithink-finance-query', query_text, limit=len(batch))
        datas = r.get('datas', [])
        if datas:
            parsed = parse_finance(datas)
            all_fundamentals.update(parsed)
            print(f"  [{i+1}-{min(i+BATCH_SIZE, len(ALL_STOCKS))}] "
                  f"OK: {len(datas)} results, parsed {len(parsed)} stocks")
        else:
            err = r.get('error', 'no data')
            print(f"  [{i+1}-{min(i+BATCH_SIZE, len(ALL_STOCKS))}] FAIL: {err}")
            failed.extend(batch)

        if i + BATCH_SIZE < len(ALL_STOCKS):
            time.sleep(0.5)  # 频率控制

    # 保存
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_fundamentals, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存 {len(all_fundamentals)} 只股票 → {OUTPUT_FILE}")
    if failed:
        print(f"⚠️  失败 {len(failed)} 只: {failed[:10]}")
    
    # 展示示例
    for code in ['688041', '002415', '300750', '600519']:
        if code in all_fundamentals:
            d = all_fundamentals[code]
            print(f"  {code} {d.get('name', '')}: ROE={d['roe']:.1f}% "
                  f"净利增速={d['profit_g']:.1f}% 营收增速={d['revenue_g']:.1f}%")


if __name__ == '__main__':
    main()
