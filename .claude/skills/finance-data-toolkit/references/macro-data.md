# 宏观经济数据抓取模块

**模块路径：** `finance_toolkit/data_fetching/macro_fetcher.py`

## 概述

宏观经济数据抓取器覆盖CPI、PPI、GDP、PMI、利率、货币供应量、失业率等宏观经济指标。

## 数据源

| 数据源 | 覆盖内容 | 频率限制 |
|--------|----------|----------|
| 国家统计局 | CPI、PPI、GDP、PMI | 低 |
| 中国人民银行 | 利率、M2 | 低 |
| FRED | 美国CPI、失业率 | 低 |
| BLS | 美国失业率 | 低 |

## API 端点

### 1. 获取CPI数据

```python
from finance_toolkit.data_fetching.macro_fetcher import MacroFetcher

fetcher = MacroFetcher()
cpi = fetcher.fetch_cpi(country='cn')
```

**支持国家：** cn（中国）、us（美国）

### 2. 获取PPI数据

```python
ppi = fetcher.fetch_ppi(country='cn')
```

### 3. 获取GDP数据

```python
gdp = fetcher.fetch_gdp(country='cn')
```

### 4. 获取PMI数据

```python
pmi = fetcher.fetch_pmi(country='cn')
```

### 5. 获取利率数据

```python
rate = fetcher.fetch_interest_rate(country='cn')
```

### 6. 获取货币供应量(M2)

```python
m2 = fetcher.fetch_money_supply(country='cn')
```

### 7. 获取失业率

```python
unemployment = fetcher.fetch_unemployment(country='us')
```

## 便捷函数

```python
from finance_toolkit.data_fetching.macro_fetcher import (
    fetch_cpi,
    fetch_pmi,
    fetch_gdp
)

# 获取CPI数据
cpi = fetch_cpi(country='cn')

# 获取PMI数据
pmi = fetch_pmi(country='cn')

# 获取GDP数据
gdp = fetch_gdp(country='cn')
```

## 返回数据结构

```json
{
  "date": "2026-07",
  "value": 2.1,
  "type": "cpi",
  "country": "cn",
  "update_time": "2026-08-10T23:00:00"
}
```

## 错误处理

| 错误类型 | 原因 | 处理建议 |
|----------|------|----------|
| DataFetchError | 网络请求失败 | 重试或使用代理 |
| 空数据 | 接口返回空 | 检查国家代码是否正确 |
| 格式错误 | API返回格式变化 | 更新解析逻辑 |

## 使用示例

```python
from finance_toolkit.data_fetching.macro_fetcher import MacroFetcher

fetcher = MacroFetcher()

# 获取中国CPI数据
cpi = fetcher.fetch_cpi('cn')
for item in cpi[-12:]:  # 最近12个月
    print(f"{item['date']}: {item['value']}%")

# 获取美国失业率
unemployment = fetcher.fetch_unemployment('us')
print(f"失业率: {unemployment['rate']}%")
```

---

*文档版本：v1.0*  
*最后更新：2026-08-10*
