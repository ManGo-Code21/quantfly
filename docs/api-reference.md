# QuantFly 跨机器 QMT 数据 API 文档

> 版本: v0.2.0 | Windows: `10.6.98.168:8765` | 最后更新: 2026-05-06  
> Mac 通过 HTTP 调用 Windows QMT 数据，无需本地安装 xtquant

---

## 快速开始

```bash
# 所有端点均以 HTTP GET 访问（除 /live/factors 为 POST）
curl http://10.6.98.168:8765/live/health | jq .
curl "http://10.6.98.168:8765/data/quote?codes=000001,600000" | jq .
```

---

## 1. 健康检查

### `GET /live/health`

QMT 连接状态 + API 可用性。

**无参数。**

**返回:**
```json
{
  "status": "ok",           // "ok" | "degraded"
  "qmt_connected": true,    // QMT 数据服务是否可达
  "timestamp": 1778057825.75
}
```

---

## 2. 实时行情

### `GET /data/quote`

批量获取股票最新行情（基于最近日K线）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `codes` | string | Required | 逗号分隔，如 `000001,600000` |

限制: 最多 100 只。

**返回:**
```json
{
  "quotes": {
    "000001": {
      "date": "2026-05-06",
      "price": 11.36,
      "open": 11.50,
      "high": 11.55,
      "low": 11.30,
      "volume": 43210000,
      "amount": 489653000.0,
      "pre_close": 11.49,
      "pct_chg": -1.13
    }
  },
  "count": 1
}
```

---

## 3. K线数据

### `GET /data/kline`

日线或分钟K线。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `code` | string | Required | 股票代码，如 `000001` |
| `period` | string | `"1d"` | `1d` / `1m` / `5m` / `15m` / `30m` / `60m` |
| `count` | int | `100` | 返回数量，1~1000 |

**返回:**
```json
{
  "code": "000001",
  "period": "1d",
  "candles": [
    {
      "date": "2026-05-06",
      "open": 11.50,
      "high": 11.55,
      "low": 11.30,
      "close": 11.36,
      "volume": 43210000,
      "amount": 489653000.0
    }
  ],
  "count": 100
}
```

### `GET /data/minute`

分钟K线快捷接口，等同于 `/data/kline`（period 为分钟周期）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `code` | string | Required | 股票代码 |
| `period` | string | `"5m"` | `1m` / `5m` / `15m` / `30m` / `60m` |
| `count` | int | `240` | 返回数量，1~2000 |

---

## 4. 逐笔成交

### `GET /data/tick`

单只股票逐笔成交明细（Level1 tick 数据）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `code` | string | Required | 股票代码 |
| `count` | int | `100` | 返回数量，1~5000 |

**返回:**
```json
{
  "code": "000001",
  "ticks": [
    {
      "time": "14:50:00",
      "price": 11.36,
      "volume": 100,
      "amount": 1136.0,
      "type": "买"
    }
  ],
  "count": 100
}
```

---

## 5. 资金流向 ⭐

### `GET /data/money_flow`

个股日频资金流向 — 主力/超大单/大单/中单/小单净流入。

**多数据源 fallback:** 东方财富 → akshare（自动切换）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `codes` | string | Required | 逗号分隔，如 `000001,600000` |
| `days` | int | `5` | 返回天数，1~250 |

限制: 最多 50 只。

**返回:**
```json
{
  "money_flow": {
    "000001": [
      {
        "date": "2026-05-06",
        "main_net": 41053756.0,
        "small": -80057088.0,
        "medium": 39003328.0,
        "large": 37960464.0,
        "super_large": 3093292.0,
        "main_net_ratio": 2.97,
        "small_ratio": -5.8,
        "medium_ratio": 2.82,
        "large_ratio": 2.75,
        "super_large_ratio": 0.22,
        "close": 11.36,
        "pct_chg": -1.13
      }
    ]
  },
  "count": 1,
  "days": 5,
  "source": {"000001": "eastmoney"}
}
```

| 字段 | 单位 | 说明 |
|------|------|------|
| `main_net` | 元 | 主力净流入（超大单 + 大单） |
| `super_large` | 元 | 超大单净流入 |
| `large` | 元 | 大单净流入 |
| `medium` | 元 | 中单净流入 |
| `small` | 元 | 小单净流入 |
| `*_ratio` | % | 各档净占比 |
| `source` | — | 数据来源: `eastmoney` / `akshare` |

---

## 6. 板块信息

### `GET /data/sectors`

全部板块列表（行业、概念、指数等）。

**无参数。**

**返回:**
```json
{
  "sectors": ["沪深300", "中证500", "上证50", "AI大模型", "机器人概念", ...],
  "count": 500
}
```

### `GET /data/sector/stocks`

板块成分股列表。

| 参数 | 类型 | 说明 |
|------|------|------|
| `sector` | string | 板块名，如 `沪深300`、`中证500`、`AI大模型` |

**返回:**
```json
{
  "sector": "AI大模型",
  "stocks": ["000001.SZ", "600000.SH", ...],
  "count": 35
}
```

### `GET /data/index_weight`

指数成分股权重。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `index` | string | `"000300"` | 指数代码 |
| `date` | string | `""` | 日期 YYYYMMDD，空=最新 |

---

## 7. 财务数据

### `GET /data/financial`

批量获取估值指标和财务指标。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `codes` | string | Required | 逗号分隔 |
| `fields` | string | `""` | 逗号分隔，空=全部字段 |

限制: 最多 50 只。

**可用字段:**
`pe`, `pe_ttm`, `pb`, `ps`, `pcf`, `roe`, `roa`, `grossProfitMargin`, `netProfitMargin`, `totalMarketCap`, `floatMarketCap`, `operatingRevenue`, `netProfit`, `operatingRevenueYOY`, `netProfitYOY`, `goodwill`, `totalShares`, `floatShares`

**返回:**
```json
{
  "financial": {
    "000001": {"pe": 5.2, "pb": 0.65, "roe": 12.5, "totalMarketCap": 220000000000.0}
  },
  "count": 1
}
```

---

## 8. 交易日历

### `GET /data/calendar`

获取交易日列表和节假日。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `start` | string | 一年前 | 起始日期 YYYYMMDD |
| `end` | string | 今天 | 截止日期 YYYYMMDD |

**返回:**
```json
{
  "trading_days": 243,
  "dates": ["2026-04-01", "2026-04-02", ...],
  "holidays": ["2026-05-01", ...],
  "range": "20250101 ~ 20260506"
}
```

---

## 9. 因子计算

### `POST /live/factors`

同步获取 ≤30 只股票的截面因子值。

**Body:**
```json
{
  "codes": ["000001", "000002"],
  "days": 100
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `codes` | list | 股票代码，1~30 只 |
| `days` | int | 数据跨度（默认100） |

**返回:** 每只股票最新一日的 21 个量价因子 + 资金流因子。

---

## 10. 因子IC排名（异步）

### `GET /live/rank`

异步提交因子 IC 排名计算任务（500只×300天约2-3分钟）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `days` | int | `300` | 数据跨度，30~500 |
| `stocks` | int | `500` | 股票数量，50~500 |

**返回:** `{task_id, status:"queued", check_url, result_url}`

**流程:**
1. `GET /live/rank` → 获取 `task_id`
2. `GET /live/status/{task_id}` → 轮询进度
3. `GET /live/result/{task_id}` → 获取 IC 排名结果

---

## 架构说明

```
Mac (数据消费)
  │
  │ HTTP (10.6.98.168:8765)
  ▼
Windows (数据提供)
  ├─ FastAPI (uvicorn, port 8765)
  ├─ QMT xtdata (127.0.0.1:58610)
  │   └─ 行情/K线/Tick/板块/财务/日历
  └─ 东方财富 HTTP API (外部)
      └─ 资金流向 (fallback: akshare)
```

## 常见问题

**Q: 资金流向数据为空？**  
A: 自动 fallback 到 akshare。如果东方财富限流，`source` 字段会显示 `akshare`。

**Q: tick 数据字段缺失？**  
A: QMT tick 数据结构因版本而异。如果 `price` 字段为空，尝试 `lastPrice`。

**Q: 端口连不上？**  
A: Windows 防火墙需开放 8765：`netsh advfirewall firewall add rule name="QuantFly" dir=in action=allow protocol=TCP localport=8765`

**Q: Mac 如何用？**  
A: `curl http://10.6.98.168:8765/data/quote?codes=000001` 或在 Python 中用 `requests`。
