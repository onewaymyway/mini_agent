# API接口详细参考

> 版本：v3.0
> 最后更新：2026-08-18

---

## 目录

1. [统一接口规范](#1-统一接口规范)
2. [A股数据接口](#2-a股数据接口)
3. [指数数据接口](#3-指数数据接口)
4. [基金数据接口](#4-基金数据接口)
5. [ETF数据接口](#5-etf数据接口)
6. [期货数据接口](#6-期货数据接口)
7. [债券数据接口](#7-债券数据接口)
8. [外汇数据接口](#8-外汇数据接口)
9. [加密货币数据接口](#9-加密货币数据接口)
10. [板块数据接口](#10-板块数据接口)
11. [宏观数据接口](#11-宏观数据接口)
12. [新闻与社区数据接口](#12-新闻与社区数据接口)

---

## 1. 统一接口规范

### 1.1 基础请求格式

所有数据请求使用统一的异步接口：

```python
from finance_toolkit import FinanceToolkit

async def fetch_data():
    toolkit = FinanceToolkit()
    
    # 方式一：直接指定数据源
    data = await toolkit.fetch(
        source='akshare',
        symbol='000001',
        data_type='quote'
    )
    
    # 方式二：使用多源融合（自动降级）
    data = await toolkit.fetch_multisource(
        symbol='000001',
        data_type='quote'
    )
    
    return data
```

### 1.2 请求参数规范

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | str | 否 | 数据源标识：akshare/tushare/eastmoney/sina/yahoo |
| `symbol` | str | 是 | 标的代码，如 000001、600000.SH、BTC-USDT |
| `data_type` | str | 是 | 数据类型：quote/kline/financial等 |
| `start` | datetime | 否 | 开始日期（K线类数据） |
| `end` | datetime | 否 | 结束日期（K线类数据） |
| `period` | str | 否 | K线周期：1m/5m/15m/30m/60m/day/week |
| `count` | int | 否 | 返回条数限制 |

### 1.3 响应数据格式

```json
{
  "source": "akshare",
  "data_type": "quote",
  "symbol": "000001.SZ",
  "timestamp": "2026-08-18T04:00:00Z",
  "payload": {
    "symbol": "000001.SZ",
    "name": "平安银行",
    "close": 12.35,
    "open": 12.28,
    "high": 12.42,
    "low": 12.25,
    "pre_close": 12.28,
    "volume": 123456789,
    "amount": 1523456789,
    "change_pct": 0.57,
    "change_amt": 0.07,
    "turnover": 0.12,
    "pe_ttm": 5.23,
    "pb": 0.45,
    "total_mv": 234567890000
  },
  "raw": null,
  "meta": {
    "request_time_ms": 125,
    "retry_count": 0,
    "proxy_used": false,
    "cache_hit": false
  }
}
```

---

## 2. A股数据接口

### 2.1 实时行情 (quote)

**适配器**: `StockAdapter`, `AKShareAdapter`

**支持的数据源**: AKShare、东方财富、新浪财经

```python
# 获取单只股票行情
async for data in adapter.fetch(['000001'], 'quote'):
    print(data.payload['close'])

# 批量获取
async for data in adapter.fetch(['000001', '600000', '300750'], 'quote'):
    print(data.symbol, data.payload['name'])
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 股票代码（标准化格式） |
| name | str | 股票名称 |
| close | float | 最新价 |
| open | float | 今开 |
| high | float | 最高 |
| low | float | 最低 |
| pre_close | float | 昨收 |
| volume | int | 成交量（手） |
| amount | float | 成交额（元） |
| change_pct | float | 涨跌幅（%） |
| change_amt | float | 涨跌额 |
| turnover | float | 换手率（%） |
| pe_ttm | float | 市盈率TTM |
| pb | float | 市净率 |
| total_mv | float | 总市值（元） |
| circ_mv | float | 流通市值（元） |

**底层API**:
- AKShare: `ak.stock_zh_a_spot_em()`
- 东方财富: `https://push2.eastmoney.com/api/qt/stock/get`
- 新浪: `https://hq.sinajs.cn/list=sh000001,sz000001`

### 2.2 K线数据 (kline)

```python
async for data in adapter.fetch(
    symbols=['000001'],
    data_type='kline',
    start=datetime(2024, 1, 1),
    end=datetime(2024, 12, 31),
    period='day'
):
    # data.payload['date'], data.payload['close']...
    pass
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| date | str | 交易日期 |
| open | float | 开盘价 |
| close | float | 收盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| volume | int | 成交量（手） |
| amount | float | 成交额 |
| change_pct | float | 涨跌幅 |
| amplitude | float | 振幅 |

### 2.3 财务数据 (financial)

```python
# 利润表
async for data in adapter.fetch(['000001'], 'financial', financial_type='income'):
    pass

# 资产负债表
async for data in adapter.fetch(['000001'], 'financial', financial_type='balance'):
    pass

# 现金流量表
async for data in adapter.fetch(['000001'], 'financial', financial_type='cashflow'):
    pass
```

### 2.4 龙虎榜 (lhb)

```python
async for data in adapter.fetch(['000001'], 'lhb'):
    # data.payload['reason'] - 上榜原因
    # data.payload['buy_amount'] - 买入金额
    # data.payload['sell_amount'] - 卖出金额
    pass
```

### 2.5 北向资金 (northbound)

```python
async for data in adapter.fetch(['000001'], 'northbound'):
    # data.payload['buy_amount'] - 北向买入金额
    # data.payload['sell_amount'] - 北向卖出金额
    # data.payload['net_flow'] - 净流入
    pass
```

---

## 3. 指数数据接口

### 3.1 指数行情 (index_quote)

```python
async for data in adapter.fetch(['000001'], 'index_quote'):
    pass
```

**支持的指数代码**:
- 000001 - 上证指数
- 399001 - 深证成指
- 399006 - 创业板指
- 000688 - 科创50
- 000300 - 沪深300
- 000905 - 中证500

### 3.2 成分股查询 (constituents)

```python
async for data in adapter.fetch(['000300'], 'constituents'):
    # data.payload['symbol'] - 成分股代码
    # data.payload['weight'] - 权重
    pass
```

---

## 4. 基金数据接口

### 4.1 基金净值 (fund_nav)

```python
async for data in adapter.fetch(['159915'], 'fund_nav'):
    # data.payload['nav'] - 单位净值
    # data.payload['accumulated_nav'] - 累计净值
    pass
```

### 4.2 基金持仓 (fund_holdings)

```python
async for data in adapter.fetch(['159915'], 'fund_holdings'):
    # data.payload['stock_code'] - 股票代码
    # data.payload['stock_name'] - 股票名称
    # data.payload['shares'] - 持仓数量
    # data.payload['nav_ratio'] - 占净值比
    pass
```

### 4.3 基金排行 (fund_rank)

```python
async for data in adapter.fetch(['all'], 'fund_rank', rank_type='yearly'):
    # rank_type: yearly/monthly/quarterly
    pass
```

---

## 5. ETF数据接口

### 5.1 ETF行情 (etf_quote)

```python
async for data in adapter.fetch(['510300.SH'], 'etf_quote'):
    pass
```

**常见ETF代码**:
- 510300.SH - 沪深300ETF
- 510500.SH - 中证500ETF
- 159915.SZ - 创业板ETF
- 588000.SH - 科创50ETF

### 5.2 ETF规模 (etf_size)

```python
async for data in adapter.fetch(['510300.SH'], 'etf_size'):
    # data.payload['size'] - 基金规模（亿元）
    pass
```

---

## 6. 期货数据接口

### 6.1 期货行情 (futures_quote)

```python
async for data in adapter.fetch(['CU2412'], 'futures_quote'):
    pass
```

**合约代码格式**: 品种字母 + 年月，如 CU2412（铜）、AU2412（黄金）

### 6.2 持仓量 (futures_position)

```python
async for data in adapter.fetch(['CU2412'], 'futures_position'):
    # data.payload['position'] - 持仓量
    pass
```

---

## 7. 债券数据接口

### 7.1 收益率曲线 (bond_yield)

```python
async for data in adapter.fetch(['all'], 'bond_yield', start='20240101', end='20241231'):
    # data.payload['maturity'] - 期限
    # data.payload['yield'] - 收益率
    pass
```

### 7.2 可转债 (convertible)

```python
async for data in adapter.fetch(['all'], 'convertible'):
    pass
```

---

## 8. 外汇数据接口

### 8.1 实时汇率 (forex_quote)

```python
# 使用中国银行
async for data in boc_adapter.fetch(['USD/CNY'], 'forex_quote'):
    pass

# 使用AKShare
async for data in akshare_forex_adapter.fetch(['USD/CNY'], 'forex_quote'):
    pass
```

**货币对代码**:
- USD/CNY - 美元/人民币
- EUR/CNY - 欧元/人民币
- GBP/CNY - 英镑/人民币
- JPY/CNY - 日元/人民币
- HKD/CNY - 港币/人民币

---

## 9. 加密货币数据接口

### 9.1 Binance适配器

```python
async for data in binance_adapter.fetch(['BTC-USDT'], 'crypto_quote'):
    # data.payload['price'] - 最新价
    # data.payload['change_24h'] - 24小时涨跌幅
    # data.payload['volume_24h'] - 24小时成交量
    pass
```

### 9.2 CoinGecko适配器

```python
async for data in coingecko_adapter.fetch(['bitcoin'], 'crypto_quote'):
    pass

# 全球币种排行
async for data in coingecko_adapter.fetch([], 'crypto_rank', limit=100):
    pass
```

**支持的币种**:
- BTC, ETH, USDT, BNB, SOL, XRP, ADA, DOGE, DOT, MATIC等

---

## 10. 板块数据接口

### 10.1 板块行情 (sector_quote)

```python
async for data in adapter.fetch(['all'], 'sector_quote'):
    pass
```

### 10.2 板块资金流向 (sector_fundflow)

```python
async for data in adapter.fetch(['all'], 'sector_fundflow'):
    # data.payload['net_flow'] - 净流入
    pass
```

---

## 11. 宏观数据接口

### 11.1 经济指标 (macro)

```python
async for data in macro_scraper.fetch(['GDP'], 'macro', start='20230101'):
    pass

# 支持的经济指标:
# GDP, CPI, PMI, M2, 社融, 外汇储备
```

---

## 12. 新闻与社区数据接口

### 12.1 财经新闻 (news)

```python
async for data in news_scraper.fetch([], 'news', keyword='AI'):
    # data.payload['title'] - 标题
    # data.payload['content'] - 内容摘要
    # data.payload['publish_time'] - 发布时间
    pass
```

### 12.2 股吧情绪 (guba)

```python
async for data in guba_scraper.fetch(['000001'], 'guba_sentiment'):
    # data.payload['sentiment_score'] - 情绪评分
    # data.payload['comment_count'] - 评论数
    pass
```

---

## 附录A: 错误码说明

| 错误码 | 说明 | 处理建议 |
|--------|------|----------|
| 401 | 认证失败 | 检查Token配置 |
| 403 | 权限不足 | 检查数据源权限 |
| 429 | 请求过于频繁 | 降低请求频率 |
| 500 | 服务端错误 | 稍后重试 |
| TIMEOUT | 请求超时 | 检查网络/代理 |
| CIRCUIT_BREAKER | 熔断器开启 | 等待恢复 |

## 附录B: 代理配置

```yaml
proxy:
  enabled: true
  pool_size: 10
  auto_switch: true
  retry_on_timeout: 3
```
