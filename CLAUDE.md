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
