# V13.1 — 风险平价权重 + 概念板块筛选

> 2026-05-16 | @ManGo | 基于国泰海通 ETF 策略框架启发

---

## 变更概要

### 1. V13 → V13.1：风险平价权重 (`growth_v13.py`)

**改动**：等权买入 → 波动率倒数加权（Risk Parity）

**位置**：`growth_v13.py` lines 128-162（新增函数）、lines 757-770（修改买入逻辑）

**核心逻辑**：
```python
# 旧：等权
per_stock = target_value / len(target_codes)

# 新：风险平价
vol = 20日年化波动率
weight_i = (1/vol_i) / Σ(1/vol_j)
alloc_i = target_value * weight_i
```

**开关**：`RISK_PARITY_ENABLED = True` 设 `False` 回退等权

**fallback**：不足2只有效波动率时自动退回等权

**预期效果**：
- 高波动标的（如科创板688xxx）少配 → 降低组合波动
- 低波动标的（如主板600xxx）多配 → 提升夏普比率
- 理论上不影响多空方向，纯优化资金分配

### 2. 概念板块自动筛选器 (`scripts/concept_screener.py`)

**来源**：从8期历史数据提炼的规律

**规则**：
- ✅ 成分股 10-80只 + 产业逻辑（供应链/技术主线）+ 跨季度≥2次
- ❌ 次新/ST、<10只、>200只、纯概念炒作

**产出**：`data/concept_screening/final_concept_pool.json`
- Tier 1（核心池 4个）：BC电池、MLOps概念、华为欧拉、华为盘古
- Tier 2（备选池 9个）：周期/医药类
- Tier 3（观察池 15个）：成分股超标但产业逻辑强

**已知问题**：10-80只规则可能过严，华为昇腾(105只)、MCU芯片(93只)等强产业链概念被排除

### 3. 风格轮动方案（设计完成，未编码）

**方案**：国证2000 vs 沪深300 相对强弱 → 偏好大小盘

**实现位置**（待编码）：
- 新增 `get_style_index_data()` — 获取沪深300+中证1000 K线
- 新增 `SECTOR_STYLE` 映射表
- 修改 `get_top_sectors()` 加入风格加成
- 修改 `growth_backtest_v13()` 主循环

**数据已验证**：沪深300 (push2his) ✅、中证1000 (mootdx) ✅

---

## 回测执行

**重要**：Mac 侧 QMT (10.6.98.168:8765) 不可达，回测需在 Windows 侧执行：

```bash
# Windows 上
cd D:\quantfly
python growth_v13.py
```

**对比测试**：
1. 先跑 `RISK_PARITY_ENABLED = True` → 记录结果
2. 改 `RISK_PARITY_ENABLED = False` → 对比等权baseline
3. 预期：夏普 +0.1~0.2，年化持平或略高

---

## Git 提交清单

| 文件 | 状态 | 说明 |
|------|------|------|
| `growth_v13.py` | M | V13.1 风险平价权重 |
| `scripts/concept_screener.py` | A | 概念板块筛选器 |
| `data/concept_screening/` | A | 筛选结果缓存 |
| `CHANGELOG_v13.1.md` | A | 本文档 |

⚠️ **Git Push 注意**：Mac 侧 keychain 失效，需从 Windows 侧 `git push`
