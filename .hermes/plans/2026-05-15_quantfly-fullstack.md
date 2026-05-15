# QuantFly 项目完善计划

> 2026-05-15 | 基于现有 v0.2.0 代码库

## 现状评估

| 模块 | 已有 | 缺失 |
|------|------|------|
| **策略交易** | V13策略 (`v13_live.py`, `v13_trader.py`), 回测11个变体 | MiniQMT集成不稳定，无风控面板 |
| **行情数据** | QMT K线/分钟/Tick/资金流向API (`data.py` 13端点) | ✅ 完善 |
| **选股** | 板块动量/资金流/基本面/风控过滤 (7个filter) | ✅ 完善 |
| **热点/新闻** | 新闻爬虫+情感分析+Gate1/2验证 | ❌ 无盘中监测面板，无情绪指数 |
| **交易分析** | 板块RSRS动量 | ❌ 无持仓面板，无板块热力图，无资金流向总览 |
| **前端/UI** | 静态文件挂载 | ❌ 无Dashboard页面 |

## 目标架构

```
┌─────────────────────────────────────────────────┐
│                   QuantFly API                   │
│                  (127.0.0.1:8765)                │
├──────────────┬──────────────┬───────────────────┤
│  策略交易     │  交易分析     │  情绪与监测        │
│ /strategy/*  │ /dashboard/* │ /sentiment/*      │
│ /trade/*     │ /flow/*      │ /monitor/*        │
├──────────────┴──────────────┴───────────────────┤
│              v13_trader.py (独立进程)             │
│     V13策略 → 信号 → MiniQMT xtquant 下单        │
└─────────────────────────────────────────────────┘
```

---

## Phase 1: 策略交易核心 (今天)

### 1.1 v13_trader.py 完善
- [x] 导入 v13_live + xtquant ✅
- [ ] 修复交易连接（Windows直接运行）
- [ ] 添加 `--dry-run` 模式（输出信号不下单）
- [ ] 添加持仓对比逻辑（当前 vs 目标，精确调仓）
- [ ] 风控：单日亏损>5%暂停交易
- [ ] 交易日志写入 SQLite

**文件**: `v13_trader.py`

### 1.2 策略 API 端点
- `POST /strategy/signal` — 获取V13信号（同步）
- `GET  /strategy/status` — 策略运行状态
- `GET  /strategy/positions` — 当前持仓+盈亏

**文件**: `quantfly/api/routes/strategy.py` (新建)

---

## Phase 2: 交易分析面板 (今天-明天)

### 2.1 持仓分析 API
- `GET /dashboard/positions` — 实时持仓（代码/数量/成本/现价/盈亏%/仓位%）
- `GET /dashboard/equity` — 权益曲线（日级）

**文件**: `quantfly/api/routes/dashboard.py` (新建)

### 2.2 板块量能分析
- `GET /dashboard/sectors` — 板块热力图数据
  - 30+板块的：涨跌幅/成交额/资金净流入/RSRS评分/排名
- `GET /dashboard/sector/{name}` — 单板块详情（成分股表现）

**文件**: `quantfly/screener/sector_dashboard.py` (新建)

### 2.3 资金流向总览
- `GET /flow/overview` — 全市场资金流向
  - 北向资金（沪股通+深股通）
  - 主力/散户净流入
  - 行业资金流入TOP10/流出TOP10
- `GET /flow/stock/{code}` — 个股资金流向

**文件**: `quantfly/api/routes/flow.py` (新建，封装现有 capital_flow_filter）

### 2.4 热点板块雷达
- `GET /dashboard/hot` — 实时热点检测
  - 涨幅TOP板块 + 量比 + 涨停数
  - 基于现有 `hot_topics/monitor.py` 增强

**文件**: 增强 `quantfly/hot_topics/monitor.py`

---

## Phase 3: 情绪与新闻 (明天)

### 3.1 市场情绪指数
- `GET /sentiment/index` — 综合情绪评分 (0-100)
  - 成分：涨跌比 + 量比 + 北向 + 涨停数 + 新闻情感
  - 权重可配置
- `GET /sentiment/history` — 历史情绪曲线

**文件**: `quantfly/sentiment/market_sentiment.py` (新建)

### 3.2 新闻监测
- `GET /sentiment/news` — 最新财经新闻（带情感标签）
- `GET /sentiment/news/{stock}` — 个股相关新闻
- `GET /sentiment/breaking` — 突发新闻告警

**文件**: 增强 `quantfly/hot_topics/news_sentiment.py` + `quantfly/api/routes/sentiment.py` (新建)

### 3.3 盘中异动监测
- `GET /monitor/alerts` — 实时异动告警
  - 涨停/跌停/放量/急拉/急跌
  - 基于 QMT 全推数据
- `GET /monitor/summary` — 盘面概览（涨跌家数/涨停数/跌停数）

**文件**: `quantfly/monitor/intraday.py` (新建)

---

## Phase 4: 前端Dashboard (后天)

### 4.1 单页Dashboard
HTML5页面，4个卡片面板：
1. **策略状态卡** — 当前信号/仓位/模式(🐂/🐻)/下次调仓
2. **板块热力图卡** — 30+板块颜色热力
3. **资金流向卡** — 北向/主力/行业TOP10
4. **情绪+异动卡** — 情绪指数/涨停数/最新告警

**文件**: `quantfly/ui/static/dashboard.html` (新建)

---

## 文件清单

```
quantfly/
├── v13_trader.py                     # [改] 加 --dry-run, 风控, 日志
├── quantfly/
│   ├── api/
│   │   ├── main.py                   # [改] 注册新路由
│   │   └── routes/
│   │       ├── strategy.py           # [新] 策略API
│   │       ├── dashboard.py          # [新] 持仓/板块/热点
│   │       ├── flow.py               # [新] 资金流向
│   │       ├── sentiment.py          # [新] 情绪+新闻
│   │       └── monitor.py            # [新] 异动监测
│   ├── screener/
│   │   └── sector_dashboard.py       # [新] 板块仪表盘
│   ├── sentiment/
│   │   ├── __init__.py               # [新]
│   │   └── market_sentiment.py       # [新] 市场情绪指数
│   ├── monitor/
│   │   ├── __init__.py               # [新]
│   │   └── intraday.py              # [新] 盘中异动
│   └── ui/
│       └── static/
│           └── dashboard.html        # [新] 前端面板
```

## 执行顺序

```
1. v13_trader.py 完善 (--dry-run, 风控, 日志)
2. strategy.py API (信号/状态/持仓)
3. dashboard.py API (持仓/板块/热点)
4. flow.py API (资金流向)
5. market_sentiment.py (情绪指数)
6. sentiment.py + monitor.py API (新闻+异动)
7. dashboard.html (前端)
8. main.py 注册全部路由
```

## 验证标准

- `python v13_trader.py --dry-run` 输出完整信号+调仓计划
- `curl 127.0.0.1:8765/dashboard/positions` 返回持仓JSON
- `curl 127.0.0.1:8765/sentiment/index` 返回情绪分数
- 浏览器打开 dashboard.html 显示4个面板卡片
