# 日K数据下载机制文档

## 概述

本模块提供 A 股全市场日K数据的下载、存储与查询能力，支撑 `run_local_screener.py` 本地选股引擎。

---

## 核心组件

### 1. `stock_watch/daily_kline_db.py`

SQLite 本地数据库，负责：
- 建表与索引（`ensure_schema()`）
- 单只增量更新（`update_stock()`）
- 批量更新（`update_batch()`）
- 全市场更新（`update_all_market()`）
- 历史查询（`get_kline()`）

数据库路径：`data/kline.db`（SQLite，WAL 模式）

### 2. `entrypoints/run_local_screener.py`

本地多因子选股引擎，读取 `data/kline.db` 计算技术指标并输出 Top N 候选股。

### 3. `stock_watch/indicators.py`

6 类技术指标信号计算（MA/MACD/RSI/KDJ/布林带/量能）。

---

## 数据源策略

三层兜底，自动降级：

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| 1 | 东方财富 API（urllib 直连） | 绕过系统代理冲突 |
| 2 | 东方财富 API（CDP 浏览器） | 可处理动态页面 |
| 3 | akshare | 开源库，最稳定兜底 |

**当前状态**：akshare 在部分环境下因系统代理（HTTP_PROXY）导致连接被断，需检查代理配置。

---

## 使用方法

### 命令行

```bash
# 查看数据库状态
cd external_projects/stock_watch
python stock_watch/daily_kline_db.py --info

# 更新单只标的\python stock_watch/daily_kline_db.py --symbol 300364

# 批量更新\python stock_watch/daily_kline_db.py --batch 300364 300418 603533

# 全市场更新（首次需下载约5000只标的的历史K线，耗时较长）
python stock_watch/daily_kline_db.py --all
```

### Python API

```python
from stock_watch.daily_kline_db import DailyKlineDB

with DailyKlineDB() as db:
    # 查看状态
    info = db.table_info()
    print(info)  # {symbol_count, total_rows, latest_date, db_path}

    # 全市场增量更新（每次只拉新增交易日数据）
    db.update_all_market()

    # 批量更新指定标的
    db.update_batch(["300364", "603533"])

    # 查询K线
    df = db.get_kline("300364", days=120)
```

### 运行选股

```bash
# 算法池选股
python entrypoints/run_local_screener.py --pool-type algo --top-n 10

# 手动池选股
python entrypoints/run_local_screener.py --pool-type manual --top-n 10

# 指定标的
python entrypoints/run_local_screener.py --codes 300364 603533 600519
```

---

## 增量更新策略

| 场景 | 行为 |
|------|------|
| 标的无数据 | 首次拉取 max_days_back（默认3000日）历史 |
| 标的有数据 | 仅拉取 `last_date + 1` 到今天的增量 |
| 数据库已有当日数据 | 跳过，返回0行 |

**注意**：数据库不会重新拉全量历史，随运行天数自然增长覆盖历史。

---

## 故障处理

### akshare 连接失败

症状：`RemoteDisconnected('Remote end closed connection without response')`

原因：Windows 系统代理（HTTP_PROXY/HTTPS_PROXY）干扰 requests 库。

解决：
```python
# 方案1：设置环境变量后重启Python
import os
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

# 方案2：使用 urllib 直连（已内置在 daily_kline_db.py 中）
```

### 东方财富 API 失败

症状：`Expecting value: line 1 column 1 (char 0)`

原因：网络超时或 IP 被封。

解决：等待几分钟后重试，`daily_kline_db.py` 会自动降级到 akshare。

### K线数据列名不一致

akshare 不同版本列名可能为中文或英文（如 `'收盘'` vs `'close'`）。
`_normalize_kline_rows()` 使用 `.get()` 容错，缺失字段默认填 0。

---

## 数据库结构

```sql
CREATE TABLE kline (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    amount    REAL,
    change_pct REAL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX idx_kline_symbol ON kline(symbol);
CREATE INDEX idx_kline_symbol_date ON kline(symbol, date DESC);
```

---

## 测试

```bash
cd external_projects/stock_watch
python -m pytest tests/ -v
```

当前共 43 个测试，全部通过。