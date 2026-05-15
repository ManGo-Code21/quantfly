# QuantFly - A股量化交易框架

> AI驱动 + B/S架构 + 轻量化 + 热插拔模块

## 项目概述

**QuantFly**（蜻蜓量化）：轻量化A股量化框架，核心流程：
```
热点监控 → 产业映射 → 选股三原则 → 回测验证 → QMT下单
```

**验证成果**：38笔回测平均 +2.74%/笔，稀土永磁 +13%

## 技术栈

- **后端**：FastAPI + Python 3.10
- **前端**：HTML5 + Bootstrap + Lightweight-charts（K线图）
- **本地UI**：PyQt6（全功能界面）
- **数据库**：SQLite（回测记录）+ JSON（配置）
- **交易**：MiniQMT xtquant

## 目录结构

```
quantfly/
├── hot_topics/      # 热点监控模块
├── screener/        # 选股三原则
├── backtest/        # 回测引擎
├── trading/         # QMT下单
├── reports/         # 每日报告（飞书推送）
├── api/             # FastAPI 服务
├── ui/              # PyQt6 本地界面
└── tests/
```

## 工作流程

### 开发流程
1. **方案设计**：写 `docs/` 文档，Claude Code 审核
2. **编码实现**：Claude Code 执行代码编写
3. **验证测试**：运行脚本验证功能
4. **Git提交**：分支开发，主分支保护

### 定时任务
- 热点监控：9:15 / 13:00（cronjob）
- 选股扫描：9:25（cronjob）
- 回测验证：收盘后（cronjob）
- 报告推送：18:00（cronjob）

## 数据源

### 多源降级架构
```
1. mootdx (TCP:7709) → 行情K线/五档盘口 (最稳定, 无IP封禁)
2. QMT HTTP (10.6.98.168:8765) → 资金流向/持仓
3. akshare → 研报/北向/龙虎榜/财联社快讯
4. 新浪 hq.sinajs.cn → ETF实时行情
5. FIXED_STOCK_POOLS → 最终fallback
```

### a-stock-data 已安装 (simonlin1212/a-stock-data)
- **mootdx**: 日/周/月/分钟K线, 五档盘口46字段, 板块成分股
- **akshare**: 北向资金, 龙虎榜, 研报PDF, 解禁数据, 财联社快讯, F10资料, 公告

### IWencai SkillHub 已安装
- **CLI**: `iwencai-skillhub-cli` (~/.local/bin)
- **report-search**: 研报搜索 (v2.0.0) ✅
- **news-search**: 财经资讯搜索 (v1.0.0) ✅
- **announcement-search**: 公告搜索 (v1.0.0) ✅ 新增
- **环境变量**: IWENCAI_API_KEY, IWENCAI_BASE_URL 已配置在 ~/.zshrc
- ⚠️ 其余23个官方技能+社区技能需登录SkillHub后才能CLI安装

```python
# K线 (推荐首选 mootdx)
from mootdx.quotes import Quotes
client = Quotes.factory(market='std')
kline = client.bars(symbol='688981', frequency=9, offset=60)  # 9=日K

# 龙虎榜
import akshare as ak
lhb = ak.stock_sina_lhb_detail_daily(trade_date="20260513")

# 北向资金
north = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")

# 研报搜索 (IWencai)
curl -X POST https://openapi.iwencai.com/v1/comprehensive/search \
  -H "Authorization: Bearer $IWENCAI_API_KEY" \
  -d '{"channels":["report"],"app_id":"AIME_SKILL","query":"半导体行业"}'
```

## 策略体系（当前主力：V13）

### V13 — 牛熊切换 + 板块动量轮动 (主力实盘)
```
🐂 沪深300 > MA60 → 全仓 V12.1 (质量+情绪因子)
🐻 沪深300 < MA60 → 仓位压缩至 30%
```
**回测 (2023-2026)：年化 53.68% 夏普 1.91 回撤 -15.9% 超额 +37.6%**
- 回测: `growth_v13.py`
- 实盘: `v13_live.py` — MiniQMT 适配器，一键 `get_v13_signals()`
- 数据: QMT K线 + 2024/2025年报基本面 + Q1-2026最新

### V12.1 — 动态板块动量轮动 + 质量/情绪 (2026-05-14)
**回测 (2025-2026)：年化 55.91% 夏普 1.69 回撤 -15.0%**
- 脚本: `growth_v12_1.py`
- 30+行业板块动量、三层信号、质量预过滤

### V12 — 动态板块动量轮动 (基线)
**回测 (2025-2026)：年化 55.47% 夏普 1.59 回撤 -17.2%**
- 脚本: `growth_v12.py`

### V11 — 固定池趋势波段 (基线)
**回测：年化 13.08% 夏普 0.75 回撤 -15.1%**
- 脚本: `growth_v11.py`

## 代码规范

- 类型提示必须
- Docstring 必须
- 异常处理具体，不捕获通用Exception
- 日志级别：DEBUG/INFO/WARNING/ERROR

## 快捷命令

```bash
# 热点监控
python -m quantfly.hot_topics.monitor

# 选股扫描
python -m quantfly.screener.stock_picker

# 回测
python -m quantfly.backtest.engine

# 启动API
uvicorn quantfly.api.main:app --reload

# 启动PyQt界面
python -m quantfly.ui.main_window
```
