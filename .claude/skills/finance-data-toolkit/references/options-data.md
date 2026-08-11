# 期权数据抓取模块

**模块路径：** `finance_toolkit/data_fetching/options_fetcher.py`

## 概述

期权数据抓取器覆盖A股期权、商品期权、股指期权等数据源，提供期权列表、期权链、希腊字母、隐含波动率、持仓量等数据。

## 数据源

| 数据源 | 覆盖内容 | 频率限制 |
|--------|----------|----------|
| 东方财富 | 期权列表、期权链、希腊字母 | 中 |
| 新浪期权 | 期权链数据 | 低 |
| 中金所 | 股指期权数据 | 低 |
| 上期所/大商所/郑商所 | 商品期权数据 | 低 |

## API 端点

### 1. 获取期权列表

```python
from finance_toolkit.data_fetching.options_fetcher import OptionsFetcher

fetcher = OptionsFetcher()
options = fetcher.fetch_option_list(market='all')
```

**请求：**
- URL: `https://push2.eastmoney.com/api/qt/clist/get`
- 参数: `fs=m:90+t:2` (期权市场)

**返回字段：**
| 字段 | 说明 |
|------|------|
| f12 | 代码 |
| f14 | 名称 |
| f2 | 标的代码 |
| f3 | 类型(C/P) |
| f4 | 行权价 |
| f15 | 到期日 |
| f5 | 最新价 |
| f6 | 涨跌幅 |
| f7 | 成交量 |
| f16 | 持仓量 |
| f17 | 隐含波动率 |

### 2. 获取期权链

```python
chain = fetcher.fetch_option_chain(underlying='510050', expiry='202401')
```

**返回结构：**
```json
{
  "underlying": "510050",
  "calls": [...],
  "puts": [...],
  "update_time": "2026-08-10T23:00:00"
}
```

### 3. 获取希腊字母

```python
greeks = fetcher.fetch_option_greeks(option_code='510050C2401M02600')
```

**返回字段：**
| 字段 | 说明 |
|------|------|
| delta | Delta值 |
| gamma | Gamma值 |
| theta | Theta值 |
| vega | Vega值 |
| rho | Rho值 |
| iv | 隐含波动率 |

### 4. 获取成交量排名

```python
ranking = fetcher.fetch_option_volume_ranking(market='all')
```

### 5. 获取持仓变化

```python
oi_change = fetcher.fetch_oi_change(underlying='510050', days=5)
```

## 便捷函数

```python
from finance_toolkit.data_fetching.options_fetcher import (
    fetch_options,
    fetch_option_chain_data,
    fetch_option_greeks_data
)

# 获取期权列表
options = fetch_options(market='all')

# 获取期权链
chain = fetch_option_chain_data(underlying='510050')

# 获取希腊字母
greeks = fetch_option_greeks_data(option_code='510050C2401M02600')
```

## 错误处理

| 错误类型 | 原因 | 处理建议 |
|----------|------|----------|
| DataFetchError | 网络请求失败 | 重试或使用代理 |
| 空数据 | 接口返回空 | 检查标的代码是否正确 |
| 格式错误 | API返回格式变化 | 更新解析逻辑 |

## 使用示例

```python
import asyncio
from finance_toolkit.data_fetching.options_fetcher import OptionsFetcher

async def main():
    fetcher = OptionsFetcher()
    
    # 获取50ETF期权列表
    options = await fetcher.fetch_option_list('sh')
    print(f"找到 {len(options)} 个期权合约")
    
    # 获取期权链
    chain = await fetcher.fetch_option_chain('510050')
    print(f"看涨: {len(chain['calls'])} 个, 看跌: {len(chain['puts'])} 个")

asyncio.run(main())
```

---

*文档版本：v1.0*  
*最后更新：2026-08-10*
