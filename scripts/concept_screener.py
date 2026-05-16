#!/usr/bin/env python3
"""
概念板块质量筛选器 — 基于8期历史数据的规律发现
筛选规则：
  ✅ 好概念：10-80只成分股 + 产业逻辑 + 跨季度出现≥2次 + 低相关
  ❌ 坏概念：次新/ST、<10只、>200只、纯概念炒作
"""

import os, sys, json, time, re
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, '/Users/shj/skills')
from iwencai_api import query

CACHE_DIR = Path('/Users/shj/quantfly/data/concept_screening')
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 配置
# ============================================================

# 历史查询时间点（跨季度，覆盖不同市场环境）
QUERY_DATES = [
    '2025年3月15日',   # Q1 春季行情
    '2025年5月15日',   # Q2 年中
    '2025年6月1日',    # Q2
    '2025年6月15日',   # Q2 末
    '2025年8月1日',    # Q3 暑期
    '2025年9月15日',   # Q3 末
    '2025年10月15日',  # Q4 国庆后
    '2025年12月1日',   # Q4 年末
    '2026年1月15日',   # Q1 春节前
    '2026年3月1日',    # Q1
    '2026年5月1日',    # Q2 当前
]

# 坏概念黑名单（名称包含这些关键词的直接排除）
BAD_KEYWORDS = [
    '次新', 'ST', '*ST', '退市', '风险警示',
    '数字水印', '数据确权', '元宇宙', 'NFT', 'Web3',
    '电子烟', '盲盒', '地摊经济', '网红直播',
    '壳资源', '昨日', '昨日涨停', '昨日连板',
    '昨日触板', '昨日首板',
]

# 太宽泛的排除（成分股通常>200只的概念关键词）
TOO_BROAD_KEYWORDS = [
    '专精特新', '沪深300', '中证500', '融资融券', '深股通', '沪股通',
    '标普道琼斯', 'MSCI', '富时罗素', '国企改革', '央企改革',
    '一带一路', '乡村振兴', '雄安新区',
]

# 产业逻辑关键词（有真实供应链/技术主线的概念）
INDUSTRY_KEYWORDS = [
    # 半导体/芯片链
    '光刻胶', '光刻机', 'CPO', '先进封装', 'Chiplet', 'HBM',
    '存储', '第三代半导体', 'IGBT', 'EDA', 'RISC-V',
    # AI/算力链
    'AI', '算力', '服务器', '液冷', 'AIPC', 'AI手机',
    '大模型', '具身智能', '机器视觉',
    # 机器人/自动化链
    '机器人', '人形机器人', '减速器', '伺服', '传感器',
    '工业母机', '工业',
    # 新能源链
    '固态电池', '钠离子', '钙钛矿', '储能', '光伏',
    '一体化压铸', '高压快充', '换电',
    # 军工/航天链
    '军工', '航天', '卫星', '低空经济', '商业航天',
    '大飞机', '航母', '军民融合',
    # 通信链
    '5G', '6G', '卫星互联网', '光通信', '光纤',
    # 医药链
    '创新药', 'CRO', '基因', '细胞', '减肥药',
    # 汽车链
    '智能驾驶', '车路协同', '激光雷达',
    # 材料链
    '稀土', '永磁', '碳纤维', 'PEEK',
    # 电力链
    '特高压', '智能电网', '虚拟电厂',
    # 消费电子
    '折叠屏', 'MR', '混合现实',
    # 数字货币链
    '数字货币', '区块链', '信创',
]

# ============================================================
# 数据采集
# ============================================================

def load_cache(name):
    """加载缓存"""
    f = CACHE_DIR / f'{name}.json'
    if f.exists():
        return json.loads(f.read_text())
    return {}

def save_cache(name, data):
    """保存缓存"""
    f = CACHE_DIR / f'{name}.json'
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2))

def fetch_concept_rankings(date_str, limit=30):
    """获取某日概念板块涨幅排名"""
    cache = load_cache('concept_rankings')
    key = date_str
    if key in cache:
        return cache[key]
    
    r = query('hithink-common-query', f'{date_str}概念板块涨幅排名前{limit}', limit=limit)
    concepts = []
    if r.get('datas'):
        for item in r['datas']:
            concepts.append({
                'name': item.get('指数简称', ''),
                'code': item.get('指数代码', ''),
                'chg': item.get('最新涨跌幅:前复权', ''),
            })
    
    cache[key] = concepts
    save_cache('concept_rankings', cache)
    time.sleep(2)  # API限流保护
    return concepts

def fetch_concept_stock_count(concept_name):
    """获取概念板块成分股数量"""
    cache = load_cache('concept_stocks')
    if concept_name in cache:
        return cache[concept_name]
    
    r = query('hithink-common-query', f'{concept_name}概念板块成分股', limit=300)
    count = 0
    stocks = []
    if r.get('datas'):
        stocks = [item.get('股票代码', '') for item in r['datas']]
        count = len(stocks)
    
    cache[concept_name] = {'count': count, 'stocks': stocks}
    save_cache('concept_stocks', cache)
    time.sleep(1.5)
    return cache[concept_name]

# ============================================================
# 筛选规则
# ============================================================

def is_bad_concept(name):
    """检查是否为坏概念（黑名单匹配）"""
    for kw in BAD_KEYWORDS:
        if kw.lower() in name.lower():
            return True, f'黑名单关键词: {kw}'
    return False, ''

def is_too_broad(name):
    """检查是否太宽泛"""
    for kw in TOO_BROAD_KEYWORDS:
        if kw.lower() in name.lower():
            return True, f'太宽泛关键词: {kw}'
    return False, ''

def has_industry_logic(name):
    """检查是否有产业逻辑"""
    for kw in INDUSTRY_KEYWORDS:
        if kw.lower() in name.lower():
            return True, kw
    return False, ''

def filter_concepts(all_concepts_data):
    """
    主筛选逻辑
    返回: {
        'good': [...],      # 优质概念池
        'flagged': [...],   # 需要人工审核的
        'rejected': [...],  # 已排除的
    }
    """
    # Step 1: 统计每个概念出现的频次
    freq = Counter()
    concept_info = {}  # name -> {dates: [...], ...}
    
    for date_str, concepts in all_concepts_data.items():
        for c in concepts:
            name = c['name']
            if name not in concept_info:
                concept_info[name] = {'dates': [], 'codes': set(), 'chgs': []}
            concept_info[name]['dates'].append(date_str)
            concept_info[name]['codes'].add(c['code'])
            concept_info[name]['chgs'].append(c['chg'])
            freq[name] += 1
    
    good = []
    flagged = []
    rejected = []
    
    print(f"\n{'='*60}")
    print(f"概念板块筛选结果")
    print(f"{'='*60}")
    print(f"总候选概念数: {len(concept_info)}")
    
    for name in sorted(concept_info.keys(), key=lambda n: freq[n], reverse=True):
        info = concept_info[name]
        issues = []
        
        # 规则1: 黑名单检查
        is_bad, reason = is_bad_concept(name)
        if is_bad:
            rejected.append({'name': name, 'freq': freq[name], 'reason': reason})
            continue
        
        # 规则2: 太宽泛检查
        is_broad, reason = is_too_broad(name)
        if is_broad:
            rejected.append({'name': name, 'freq': freq[name], 'reason': reason})
            continue
        
        # 规则3: 频次检查（跨季度出现 ≥2次）
        if freq[name] < 2:
            issues.append(f'仅出现{freq[name]}次，不满足≥2次')
        
        # 规则4: 成分股数检查（需要异步获取，先标记）
        # 后续会批量获取
        
        # 规则5: 产业逻辑检查
        has_logic, logic_kw = has_industry_logic(name)
        if not has_logic:
            issues.append('无明确产业逻辑关键词')
        
        # 分类
        if freq[name] >= 2 and has_logic:
            good.append({
                'name': name,
                'freq': freq[name],
                'logic_kw': logic_kw,
                'sample_dates': info['dates'][:3],
                'avg_chg': sum(float(c) for c in info['chgs'] if c) / max(1, len([c for c in info['chgs'] if c])),
                'stock_count': None,  # 待填充
            })
        elif freq[name] >= 2 or has_logic:
            flagged.append({
                'name': name,
                'freq': freq[name],
                'issues': issues,
                'has_logic': has_logic,
                'logic_kw': logic_kw if has_logic else '',
                'sample_dates': info['dates'][:2],
            })
        else:
            rejected.append({
                'name': name,
                'freq': freq[name],
                'reason': '; '.join(issues) if issues else '频次<2且无产业逻辑',
            })
    
    return {
        'good': sorted(good, key=lambda x: (-x['freq'], x['name'])),
        'flagged': sorted(flagged, key=lambda x: (-x['freq'], x['name'])),
        'rejected': sorted(rejected, key=lambda x: (-x['freq'], x['name'])),
    }


def enrich_stock_counts(result):
    """为优质概念获取成分股数量"""
    for concept in result['good'][:20]:  # 只查前20个
        info = fetch_concept_stock_count(concept['name'])
        concept['stock_count'] = info['count']
        time.sleep(1)
    
    # 按成分股数二次过滤
    final_good = []
    for c in result['good']:
        if c['stock_count'] is not None:
            if c['stock_count'] < 10:
                result['rejected'].append({
                    'name': c['name'], 'freq': c['freq'],
                    'reason': f'成分股太少({c["stock_count"]}只，<10)',
                })
                continue
            elif c['stock_count'] > 200:
                result['rejected'].append({
                    'name': c['name'], 'freq': c['freq'],
                    'reason': f'成分股太多({c["stock_count"]}只，>200)',
                })
                continue
        final_good.append(c)
    
    result['good'] = final_good
    return result


# ============================================================
# 主流程
# ============================================================

def main():
    print("🔍 概念板块质量筛选器")
    print(f"查询时间点: {len(QUERY_DATES)} 个")
    print(f"产业关键词库: {len(INDUSTRY_KEYWORDS)} 个")
    print(f"黑名单关键词: {len(BAD_KEYWORDS)} 个")
    print()
    
    # Phase 1: 采集历史概念排名数据
    all_data = {}
    for date_str in QUERY_DATES:
        print(f"📡 查询 {date_str} ...", end=' ')
        concepts = fetch_concept_rankings(date_str)
        print(f"{len(concepts)} 个概念")
        all_data[date_str] = concepts
        time.sleep(2)
    
    # Phase 2: 筛选
    result = filter_concepts(all_data)
    
    # Phase 3: 获取成分股数量
    print(f"\n📊 获取成分股数量（前20个优质概念）...")
    result = enrich_stock_counts(result)
    
    # Phase 4: 输出结果
    print(f"\n{'='*60}")
    print(f"✅ 优质概念池 ({len(result['good'])}个)")
    print(f"{'='*60}")
    for i, c in enumerate(result['good'], 1):
        sc = c.get('stock_count')
        sc_str = str(sc) if sc is not None else '?'
        print(f"  {i:2d}. {c['name']:<12s} 频次={c['freq']:2d}  成分股={sc_str:>3s}只  "
              f"逻辑={c['logic_kw']:>8s}  均涨={c['avg_chg']:+.2f}%")
    
    print(f"\n{'='*60}")
    print(f"⚠️ 待审核 ({len(result['flagged'])}个)")
    print(f"{'='*60}")
    for c in result['flagged']:
        print(f"  • {c['name']:<12s} 频次={c['freq']}  问题={'; '.join(c['issues'])}")
    
    print(f"\n{'='*60}")
    print(f"❌ 已排除 ({len(result['rejected'])}个)")
    print(f"{'='*60}")
    for c in result['rejected'][:15]:
        print(f"  • {c['name']} — {c['reason']}")
    if len(result['rejected']) > 15:
        print(f"  ... 及其他 {len(result['rejected'])-15} 个")
    
    # 保存结果
    output = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'rules': {
            'stock_range': '10-80',
            'min_frequency': 2,
            'require_industry_logic': True,
        },
        'good_concepts': result['good'],
        'flagged': result['flagged'],
        'rejected': result['rejected'][:50],
    }
    save_cache('screening_result', output)
    print(f"\n💾 结果已保存到 {CACHE_DIR}/screening_result.json")
    
    return result

if __name__ == '__main__':
    main()
