# IPO数据抓取模块

**模块路径：** `finance_toolkit/data_fetching/ipo_fetcher.py`

## 概述

IPO数据抓取器覆盖A股新股申购、上市、IPO日历、港股IPO、美股IPO等数据。

## 数据源

| 数据源 | 覆盖内容 | 频率限制 |
|--------|----------|----------|
| 东方财富数据中心 | A股IPO数据 | 低 |
| 同花顺 | IPO日历 | 低 |

## API 端点

### 1. 获取即将上市新股

```python
from finance_toolkit.data_fetching.ipo_fetcher import IPOFetcher

fetcher = IPOFetcher()
upcoming = fetcher.fetch_upcoming_ipo(market='a')
```

**支持市场：** a（A股）、hk（港股）、us（美股）

### 2. 获取近期上市新股

```python
listings = fetcher.fetch_new_listings(days=7)
```

### 3. 获取IPO日历

```python
calendar = fetcher.fetch_ipo_calendar(year=2024)
```

### 4. 获取新股申购详情

```python
subscribe = fetcher.fetch_stock_subscribe(code='688001')
```

## 便捷函数

```python
from finance_toolkit.data_fetching.ipo_fetcher import (
    fetch_upcoming_listings,
    fetch_ipo_calendar
)

# 获取即将上市新股
upcoming = fetch_upcoming_listings()

# 获取IPO日历
calendar = fetch_ipo_calendar()
```

## 返回数据结构

```json
{
  "code": "688001",
  "name": "测试股票",
  "market": "科创板",
  "trade_date": "2026-08-15",
  "issue_price": 50.00,
  "issue_pe": 25.5,
  "issue_size": 100000000,
  "subscribe_start": "2026-08-10",
  "subscribe_end": "2026-08-12",
  "update_time": "2026-08-10T23:00:00"
}
```

## 错误处理

| 错误类型 | 原因 | 处理建议 |
|----------|------|----------|
| DataFetchError | 网络请求失败 | 重试或使用代理 |
| 空数据 | 接口返回空 | 检查日期范围是否正确 |

## 使用示例

```python
from finance_toolkit.data_fetching.ipo_fetcher import IPOFetcher

fetcher = IPOFetcher()

# 获取即将上市新股
upcoming = fetcher.fetch_upcoming_ipo('a')
for stock in upcoming[:10]:
    print(f"{stock['code']} {stock['name']} - 上市日期: {stock['trade_date']}")

# 获取IPO日历
calendar = fetcher.fetch_ipo_calendar()
for item in calendar[:20]:
    print(f"{item['apply_date']}: {item['name']}")
```

---

*文档版本：v1.0*  
*最后更新：2026-08-10*
