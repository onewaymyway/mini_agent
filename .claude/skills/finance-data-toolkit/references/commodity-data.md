# 大宗商品数据抓取模块

**模块路径：** `finance_toolkit/data_fetching/commodity_fetcher.py`

## 概述

大宗商品数据抓取器覆盖有色金属、贵金属、能源、农产品等数据源，提供实时价格、交易所数据等。

## 数据源

| 数据源 | 覆盖内容 | 频率限制 |
|--------|----------|----------|
| 新浪财经 | 期货实时行情 | 低 |
| 上海期货交易所 | 铜、铝、锌、铅、镍、锡 | 低 |
| 大连商品交易所 | 大豆、玉米、棉花 | 低 |
| 郑州商品交易所 | 小麦、白糖、PTA | 低 |
| LME | 伦敦金属交易所数据 | 低 |
| CME | 芝加哥商品交易所数据 | 低 |

## API 端点

### 1. 获取有色金属价格

```python
from finance_toolkit.data_fetching.commodity_fetcher import CommodityFetcher

fetcher = CommodityFetcher()
metals = fetcher.fetch_metals()
```

**覆盖品种：** 铜、铝、锌、铅、镍、锡

### 2. 获取贵金属价格

```python
precious = fetcher.fetch_precious_metals()
```

**覆盖品种：** 黄金、白银、铂金、钯金

### 3. 获取能源价格

```python
energy = fetcher.fetch_energy()
```

**覆盖品种：** WTI原油、布伦特原油、天然气、燃料油

### 4. 获取农产品价格

```python
agriculture = fetcher.fetch_agriculture()
```

**覆盖品种：** 大豆、玉米、小麦、棉花、白糖、豆油、棕榈油

### 5. 获取期货合约详情

```python
contract = fetcher.fetch_futures_contract(exchange='shfe', symbol='cu2401')
```

### 6. 获取持仓数据

```python
oi = fetcher.fetch_futures_oi(exchange='shfe', symbol='cu')
```

### 7. 获取LME价格

```python
lme = fetcher.fetch_lme_prices()
```

### 8. 获取CME价格

```python
cme = fetcher.fetch_cme_prices()
```

## 便捷函数

```python
from finance_toolkit.data_fetching.commodity_fetcher import (
    fetch_metals_prices,
    fetch_precious_metals_prices,
    fetch_energy_prices,
    fetch_agriculture_prices
)

# 获取有色金属价格
metals = fetch_metals_prices()

# 获取贵金属价格
precious = fetch_precious_metals_prices()

# 获取能源价格
energy = fetch_energy_prices()

# 获取农产品价格
agriculture = fetch_agriculture_prices()
```

## 返回数据结构

```json
{
  "code": "sf_cu",
  "name": "铜",
  "price": 68500.0,
  "open": 68200.0,
  "high": 68800.0,
  "low": 68100.0,
  "prev_close": 68300.0,
  "change": 200.0,
  "change_pct": 0.29,
  "volume": "125000手",
  "time": "2026-08-10 15:00:00",
  "type": "metal",
  "update_time": "2026-08-10T23:00:00"
}
```

## 错误处理

| 错误类型 | 原因 | 处理建议 |
|----------|------|----------|
| DataFetchError | 网络请求失败 | 重试或使用代理 |
| 空数据 | 接口返回空 | 检查品种代码是否正确 |
| 格式错误 | API返回格式变化 | 更新解析逻辑 |

## 使用示例

```python
from finance_toolkit.data_fetching.commodity_fetcher import CommodityFetcher

fetcher = CommodityFetcher()

# 获取贵金属价格
precious = fetcher.fetch_precious_metals()
for item in precious:
    print(f"{item['name']}: {item['price']} ({item['change_pct']}%)")

# 获取能源价格
energy = fetcher.fetch_energy()
for item in energy:
    print(f"{item['name']}: {item['price']}")
```

---

*文档版本：v1.0*  
*最后更新：2026-08-10*
