# 统一数据源抽象接口规范 v3.0

> 版本：v3.0
> 创建时间：2026-08-14
> 适用范围：股票、债券、基金、加密货币、外汇、期货、大宗商品

---

## 一、核心数据契约

### 1.1 FinanceData 统一数据类

```python
# 位于 finance_toolkit/core.py（已实现）
@dataclass
class FinanceData:
    source: str                    # 数据源标识
    data_type: str                 # 数据类型
    symbol: str                    # 标的代码
    timestamp: str                 # ISO 8601 UTC
    payload: Dict[str, Any]        # 业务载荷
    raw: Optional[Dict] = None     # 原始响应
    meta: Optional[Dict] = None    # 元信息
```

### 1.2 DataType 枚举（data_source_config.py）

| 领域 | 枚举值 | 说明 |
|------|--------|------|
| **股票** | `stock_quote`, `stock_kline`, `stock_financial`, `stock_dividend`, `stock_lhb`, `stock_northbound`, `stock_basic`, `stock_sector`, `stock_capital_flow` |
| **债券** | `bond_yield`, `bond_convertible`, `bond_corporate`, `bond_government` |
| **基金** | `fund_etf_quote`, `fund_etf_kline`, `fund_lof_quote`, `fund_open_nav`, `fund_holdings`, `fund_rank`, `fund_list` |
| **加密货币** | `crypto_quote`, `crypto_kline`, `crypto_rank`, `crypto_trending`, `crypto_orderbook` |
| **外汇** | `forex_quote`, `forex_cny`, `forex_historical` |
| **期货** | `future_quote`, `future_kline`, `option_quote`, `option_greeks` |
| **指数** | `index_quote`, `index_kline` |
| **商品** | `commodity_quote`, `commodity_futures`, `commodity_gold`, `commodity_crude`, `commodity_dxy` |
| **宏观** | `macro_gdp`, `macro_cpi`, `macro_pmi`, `macro_interest_rate`, `macro_money_supply`, `macro_exchange_rate`, `macro_unemployment`, `macro_trade` |

---

## 二、标的代码标准化

### 2.1 格式规范

| 领域 | 标准格式 | 示例 |
|------|---------|------|
| A股股票 | `{代码}.{交易所}` | `600000.SH`, `000001.SZ`, `688001.SH` |
| ETF | `{代码}` 或 `{前缀}{代码}` | `510300`, `sh510300`, `sz159915` |
| 债券 | `{代码}.BOND` | `113000.BOND` |
| 加密货币 | `{符号}-{计价币}` | `BTC-USDT`, `ETH-USD` |
| 期货 | `{品种}{合约月份}` | `IF2406`, `AU2412`, `CU2501` |
| 外汇 | `{货币对}` | `USDCNY`, `EURUSD`, `USDJPY` |

### 2.2 转换函数

```python
# 已实现于 finance_toolkit/interface.py
def to_standard_symbol(code: str) -> str        # → '{代码}.{交易所}'
def to_akshare_symbol(code: str) -> str         # → 纯代码数字
def to_sina_symbol(code: str) -> str            # → 'sh600000'
def to_eastmoney_symbol(code: str) -> str       # → 'sh600000'
```

---

## 三、数据源配置标准

### 3.1 DataSourceMeta 结构

```python
@dataclass
class DataSourceMeta:
    name: str                        # 唯一标识
    display_name: str                # 显示名称
    type: DataSourceType             # 类型枚举
    base_url: str                    # API基础URL
    is_free: bool = True             # 是否免费
    requires_auth: bool = False      # 是否需要认证
    rate_limit: int = 10             # 每秒请求限制
    timeout: float = 30.0            # 超时(秒)
    priority: int = 0                # 优先级（越小越高）
    supported_types: List[DataType]  # 支持的数据类型
    api_key: Optional[str] = None
    proxy: Optional[str] = None
    custom_headers: Dict[str, str] = field(default_factory=dict)
```

### 3.2 内置数据源优先级

| 数据源 | 优先级 | 免费 | 支持领域 |
|--------|--------|------|---------|
| AKShare | 1 | ✅ | 股票/债券/基金/加密货币/期货/宏观/外汇 |
| 东方财富 | 2 | ✅ | 股票/基金/债券/指数 |
| 新浪财经 | 3 | ✅ | 股票/期货/外汇/加密货币 |
| 腾讯财经 | 4 | ✅ | 股票/ETF/指数 |
| CoinGecko | 5 | ✅ | 加密货币（需代理） |
| Binance | 6 | ✅ | 加密货币（需代理） |
| Yahoo Finance | 7 | ✅ | 股票/加密货币/外汇/商品（需代理） |
| FRED | 8 | ✅ | 宏观数据（需API Key） |

---

## 四、统一接口规范

### 4.1 BaseFetcher 接口定义

```python
class BaseFetcher(ABC):
    """所有数据抓取器必须实现的接口"""
    
    @classmethod
    @abstractmethod
    def get_source_name(cls) -> str: ...
    
    @classmethod
    @abstractmethod
    def get_supported_types(cls) -> List[str]: ...
    
    @abstractmethod
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[FinanceData]: ...
    
    @abstractmethod
    def health_check(self) -> bool: ...
```

### 4.2 多数据源路由

```python
class MultiSourceFetcher(BaseFetcher):
    """自动按优先级降级路由"""
    
    def __init__(self, default_source='akshare'):
        self.default_source = default_source
        self._registered_fetchers: Dict[str, BaseFetcher] = {}
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
    
    def register(self, fetcher: BaseFetcher): ...
    def fetch(self, data_type, symbols, **kwargs) -> List[FinanceData]:
        # 1. 检查熔断器状态
        # 2. 按优先级遍历数据源
        # 3. 首次成功即返回，失败自动降级
        ...
```

### 4.3 全局路由入口

```python
# create_default_router() 在 fetcher_base.py 中已实现
# 注册: AKShareFetcher, EastMoneyFetcher, CryptoFetcher,
#        BondFetcher, FundFetcher, ForexFetcher, FuturesFetcher

_global_router = create_default_router()

# 便捷调用
from finance_toolkit.data_fetching.fetcher_base import _global_router as router
router.fetch('crypto_quote', ['BTC', 'ETH'])
```

---

## 五、各领域数据 Schema

### 5.1 股票实时行情 (stock_quote)

```python
QUOTE_SCHEMA = {
    'required': ['code', 'name', 'price', 'change_pct', 'volume', 'amount'],
    'optional': ['open', 'high', 'low', 'pre_close', 'turnover_rate',
                 'pe_ratio', 'pb_ratio', 'total_mv', 'circ_mv'],
}
```

### 5.2 债券收益率 (bond_yield)

```python
BOND_YIELD_SCHEMA = {
    'required': ['date', 'yield_1y', 'yield_5y', 'yield_10y'],
    'optional': ['yield_2y', 'yield_3y', 'yield_7y', 'yield_30y'],
}
```

### 5.3 基金净值 (fund_open_nav)

```python
FUND_NAV_SCHEMA = {
    'required': ['fund_code', 'nav_date', 'unit_nav', 'accum_nav'],
    'optional': ['change_pct', 'fund_name', 'fund_type'],
}
```

### 5.4 加密货币行情 (crypto_quote)

```python
CRYPTO_QUOTE_SCHEMA = {
    'required': ['symbol', 'price', 'market_cap', 'volume_24h'],
    'optional': ['price_change_24h', 'high_24h', 'low_24h', 'circulating_supply', 'source'],
}
```

### 5.5 期货行情 (future_quote)

```python
FUTURES_QUOTE_SCHEMA = {
    'required': ['symbol', 'name', 'last_price', 'change_pct', 'volume', 'open_interest'],
    'optional': ['open', 'high', 'low', 'prev_close'],
}
```

### 5.6 外汇行情 (forex_quote)

```python
FOREX_QUOTE_SCHEMA = {
    'required': ['code', 'name', 'price'],
    'optional': ['change_pct', 'high', 'low', 'date', 'time'],
}
```

---

## 六、并发与限流配置

```python
# 推荐默认配置
CONCURRENCY_LIMIT = 5           # 同一数据源并发请求数
REQUEST_TIMEOUT = 30            # 单请求超时(秒)
RETRY_TIMES = 3                 # 失败重试次数
RETRY_BACKOFF = [1, 2, 5]       # 指数退避(秒)
RATE_LIMIT_PER_MIN = 60         # 每分钟最大请求数
PROXY_ROTATION = True           # 是否轮换代理
```

---

## 七、统一使用示例

```python
from finance_toolkit.core import FinanceData
from finance_toolkit.data_fetching.fetcher_base import _global_router as router

# 获取加密货币行情（自动降级）
result = router.fetch('crypto_quote', ['BTC', 'ETH'])
for data in result:
    print(f"{data.symbol}: {data.get('price')}")

# 获取债券收益率曲线
result = router.fetch('bond_yield', [])
print(f"10年期国债: {result[0].get('yield_10y'):.2f}%")

# 获取基金净值
result = router.fetch('fund_open_nav', ['000001'])
for data in result:
    print(f"{data.get('fund_code')}: 净值={data.get('unit_nav')}")

# 获取期货行情
result = router.fetch('future_quote', ['IF2406'])
```

---

## 八、已知问题与待修复

| 编号 | 问题 | 状态 | 优先级 |
|------|------|------|--------|
| #1 | crypto_fetcher.py 末尾截断未完整 | ⚠️ 待修复 | 高 |
| #2 | fund_fetcher.py 语法错误（行251-265混合了两段代码） | ⚠️ 待修复 | 高 |
| #3 | fetcher_base.py 引用了不存在的模块 | ⚠️ 待修复 | 中 |
| #4 | 外汇数据通过 HTTP 直接调用，缺少 akshare 封装 | ℹ️ 待完善 | 低 |

---

*规范版本：v3.0*
*更新日期：2026-08-14*
