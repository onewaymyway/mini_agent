# 实时行情数据抓取模块

**模块路径：** `finance_toolkit/data_fetching/realtime_fetcher.py`

## 概述

实时行情数据抓取器覆盖A股、港股、美股实时行情，提供指数行情、涨幅榜、跌幅榜、市场概况等数据。

## 数据源

| 数据源 | 覆盖内容 | 频率限制 |
|--------|----------|----------|
| 新浪财经 | 实时行情 | 低 |
| 东方财富 | 行情API | 中 |

## API 端点

### 1. 获取A股实时行情

```python
from finance_toolkit.data_fetching.realtime_fetcher import RealtimeFetcher

fetcher = RealtimeFetcher()
quotes = fetcher.fetch_a_stock_quote(codes=['sh600000', 'sz000001'])
```

### 2. 获取港股实时行情

```python
hk_quotes = fetcher.fetch_hk_stock_quote(codes=['00700', '00941'])
```

### 3. 获取美股实时行情

```python
us_quotes = fetcher.fetch_us_stock_quote(codes=['AAPL', 'GOOGL'])
```

### 4. 获取指数实时行情

```python
index_quotes = fetcher.fetch_index_quote(codes=['sh000001', 'sz399001'])
```

### 5. 获取市场概况

```python
summary = fetcher.fetch_market_summary()
```

### 6. 获取涨幅榜

```python
gainers = fetcher.fetch_top_gainers(market='a', limit=20)
```

### 7. 获取跌幅榜

```python
losers = fetcher.fetch_top_losers(market='a', limit=20)
```

## 便捷函数

```python
from finance_toolkit.data_fetching.realtime_fetcher import (
    fetch_realtime_quote,
    fetch_market_summary,
    fetch_top_gainers
)

# 获取实时行情
quotes = fetch_realtime_quote(codes=['sh600000', 'sz000001'])

# 获取市场概况
summary = fetch_market_summary()

# 获取涨幅榜
gainers = fetch_top_gainers(limit=20)
```

## 返回数据结构

```json
{
  "code": "sh600000",
  "name": "浦发银行",
  "price": 10.50,
  "open": 10.40,
  "high": 10.60,
  "low": 10.35,
  "prev_close": 10.45,
  "volume": 125000000,
  "amount": 1312500000,
  "change": 0.05,
  "change_pct": 0.48,
  "time": "2026-08-10 15:00:00",
  "type": "a_stock",
  "update_time": "2026-08-10T23:00:00"
}
```

## 错误处理

| 错误类型 | 原因 | 处理建议 |
|----------|------|----------|
| DataFetchError | 网络请求失败 | 重试或使用代理 |
| 空数据 | 接口返回空 | 检查股票代码是否正确 |
| 格式错误 | API返回格式变化 | 更新解析逻辑 |

## 使用示例

```python
from finance_toolkit.data_fetching.realtime_fetcher import RealtimeFetcher

fetcher = RealtimeFetcher()

# 获取A股实时行情
quotes = fetcher.fetch_a_stock_quote(['sh600000', 'sz000001', 'sh601318'])
for q in quotes:
    print(f"{q['name']}: {q['price']} ({q['change_pct']}%)")

# 获取市场概况
summary = fetcher.fetch_market_summary()
print(f"上证指数: {summary['sh000001']['price']}")
print(f"深证成指: {summary['sz399001']['price']}")

# 获取涨幅榜
gainers = fetcher.fetch_top_gainers(limit=10)
for g in gainers:
    print(f"{g['name']}: +{g['change_pct']}%")
```

## 注意事项

1. 实时行情数据更新频率较高，建议控制请求频率
2. 港股和美股数据可能有延迟
3. 部分接口需要代理才能访问

---

*文档版本：v1.0*  
*最后更新：2026-08-10*
