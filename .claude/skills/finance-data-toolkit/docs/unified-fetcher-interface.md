# 统一数据抓取接口规范 v2.0

## 概述

本文档定义了股票、债券、基金、期货四类金融产品的统一数据抓取接口规范，覆盖：
- 统一的数据模型标准
- 统一的数据抓取接口
- 统一的数据质量验证
- 统一的符号格式转换

## 一、符号格式规范

### 1.1 内部标准格式

| 产品类型 | 格式 | 示例 |
|---------|------|------|
| 股票 | `{代码}.{交易所}` | `600000.SH`, `000001.SZ` |
| 债券 | `{代码}.BOND` | `127045.BOND` |
| 基金 | `{代码}.FUND` | `159915.FUND` |
| 期货 | `{代码}.FUT` | `CU2401.FUT` |

### 1.2 交易所代码

- `.SH`: 上海证券交易所
- `.SZ`: 深圳证券交易所
- `.BJ`: 北京证券交易所

### 1.3 格式转换函数

```python
# 内部格式 -> 数据源格式
from finance_toolkit.interface import (
    to_standard_symbol,   # 600000 -> 600000.SH
    to_sina_symbol,       # 600000.SH -> sh600000
    to_eastmoney_symbol,  # 600000.SH -> sh600000
    to_akshare_symbol,    # 600000.SH -> 600000
)
```

## 二、数据模型规范

### 2.1 统一基类: FinanceData

```python
@dataclass
class FinanceData:
    source: str                    # 数据源标识
    data_type: str                 # 数据类型
    symbol: str                    # 标的代码
    timestamp: str                 # 时间戳 (ISO 8601)
    payload: Dict[str, Any]        # 数据载荷
    raw: Optional[Dict] = None     # 原始响应
    meta: Optional[Dict] = None    # 元数据
```

### 2.2 股票数据模型

| 模型类 | 数据类型 | 说明 |
|--------|---------|------|
| StockQuote | `quote` | 实时行情 |
| KLine | `kline` | 历史K线 |
| StockFinancial | `financial` | 财务报表 |
| Dividend | `dividend` | 分红数据 |
| NorthboundFlow | `northbound` | 北向资金 |

### 2.3 债券数据模型

| 模型类 | 数据类型 | 说明 |
|--------|---------|------|
| BondYield | `bond_yield` | 国债收益率曲线 |
| BondQuote | `bond_quote` | 债券行情 |
| ConvertibleBond | `convertible` | 可转债 |
| BondInfo | `bond_info` | 债券基本信息 |

### 2.4 基金数据模型

| 模型类 | 数据类型 | 说明 |
|--------|---------|------|
| FundNav | `fund_nav` | 基金净值 |
| FundHolding | `fund_holding` | 基金持仓 |
| FundRank | `fund_rank` | 基金排行 |
| FundInfo | `fund_info` | 基金信息 |

### 2.5 期货数据模型

| 模型类 | 数据类型 | 说明 |
|--------|---------|------|
| FuturesQuote | `futures_quote` | 期货行情 |
| FuturesKLine | `futures_kline` | 期货K线 |
| FuturesPosition | `futures_position` | 持仓数据 |
| FuturesInfo | `futures_info` | 合约信息 |

## 三、统一接口规范

### 3.1 DataFetcher 类接口

```python
class DataFetcher:
    # 股票
    def fetch_quote(self, symbols: List[str], source: str = None) -> List[FinanceData]
    def fetch_kline(self, symbol: str, period: str, start: str, end: str = None, adjust: str = 'qfq', source: str = None) -> List[Dict]
    def fetch_financial(self, symbol: str, source: str = None) -> List[FinanceData]
    def fetch_dividend(self, symbol: str, source: str = None) -> List[FinanceData]
    
    # 债券
    def fetch_bond(self, symbol: str, data_type: str = 'yield', source: str = None) -> List[FinanceData]
    
    # 基金
    def fetch_fund(self, symbol: str, data_type: str = 'nav', source: str = None) -> List[FinanceData]
    
    # 期货
    def fetch_futures(self, symbol: str, data_type: str = 'quote', source: str = None) -> List[FinanceData]
    
    # 批量操作
    def fetch_all(self, symbols: List[str], data_types: List[str] = None) -> Dict[str, List[FinanceData]]
```

### 3.2 数据源优先级

```python
SOURCE_PRIORITY = {
    'quote': ['akshare', 'tencent', 'sina', 'eastmoney', 'netease'],
    'kline': ['akshare', 'sina'],
    'financial': ['akshare', 'tushare'],
    'fund': ['akshare', 'eastmoney'],
    'bond': ['akshare', 'eastmoney'],
    'futures': ['akshare', 'eastmoney'],
}
```

## 四、数据来源映射

### 4.1 股票数据来源

| 数据类型 | akshare | 东方财富 | 新浪财经 | 腾讯财经 |
|---------|---------|---------|---------|---------|
| 实时行情 | ✓ | ✓ | ✓ | ✓ |
| K线数据 | ✓ | ✓ | ✓ | - |
| 财务数据 | ✓ | ✓ | - | - |
| 分红数据 | ✓ | ✓ | - | - |
| 北向资金 | ✓ | ✓ | - | - |

### 4.2 债券数据来源

| 数据类型 | akshare | 东方财富 |
|---------|---------|---------|
| 收益率曲线 | ✓ | ✓ |
| 债券行情 | ✓ | ✓ |
| 可转债 | ✓ | ✓ |
| 基本信息 | ✓ | ✓ |

### 4.3 基金数据来源

| 数据类型 | akshare | 东方财富 |
|---------|---------|---------|
| ETF行情 | ✓ | ✓ |
| ETF K线 | ✓ | - |
| LOF行情 | ✓ | - |
| 场外基金净值 | ✓ | ✓ |
| 基金持仓 | ✓ | ✓ |
| 基金排行 | ✓ | ✓ |

### 4.4 期货数据来源

| 数据类型 | akshare | 东方财富 |
|---------|---------|---------|
| 期货行情 | ✓ | ✓ |
| 期货K线 | ✓ | ✓ |
| 持仓数据 | ✓ | ✓ |
| 合约信息 | ✓ | - |

## 五、错误处理规范

### 5.1 异常类型

| 异常类 | 触发条件 |
|-------|---------|
| SourceUnavailableError | 数据源不可用 |
| SourceRateLimitedError | 数据源限流 |
| DataNotFoundError | 数据不存在 |
| DataQualityError | 数据质量不达标 |
| CircuitBreakerError | 熔断器打开 |
| FallbackError | 降级失败 |

### 5.2 重试策略

- 最大重试次数: 3次
- 重试间隔: 指数退避 (1s, 2s, 4s)
- 熔断阈值: 连续5次失败
- 熔断恢复: 60秒后尝试

## 六、使用示例

### 6.1 基本使用

```python
from finance_toolkit.interface import DataFetcher, fetch_all

# 创建实例
fetcher = DataFetcher(default_source='akshare')

# 获取股票行情
quotes = fetcher.fetch_quote(['600000.SH', '000001.SZ'])

# 获取基金净值
nav = fetcher.fetch_fund('159915.FUND', data_type='nav')

# 获取债券收益
yield_curve = fetcher.fetch_bond('*', data_type='yield')

# 获取期货行情
futures = fetcher.fetch_futures('CU2401.FUT', data_type='quote')

# 批量获取
all_data = fetcher.fetch_all(['600000.SH'], data_types=['quote', 'kline'])
```

### 6.2 工厂函数使用

```python
from finance_toolkit.models import (
    create_stock_quote, create_bond_yield,
    create_fund_nav, create_futures_quote
)

# 创建数据对象
quote = create_stock_quote(symbol='600000.SH', name='浦发银行', close=10.5)
yield_curve = create_bond_yield(date='2026-08-12', yield_10y=2.55)
```

## 七、版本历史

| 版本 | 日期 | 变更说明 |
|-----|------|---------|
| v1.0 | 2026-08-01 | 初始版本，支持股票数据 |
| v1.1 | 2026-08-08 | 增加债券、基金数据支持 |
| v2.0 | 2026-08-12 | 统一接口规范，增加期货数据支持 |
