# 统一数据源抽象接口规范 v3.0

> 版本：v3.0
> 创建时间：2026-08-14
> 适用范围：股票、债券、基金、加密货币、外汇、期货、大宗商品

---

## 一、核心设计原则

### 1.1 设计目标

1. **统一契约**：所有数据输出标准化为 `FinanceData`，无论数据源
2. **多源路由**：自动按优先级降级，单一数据源故障不影响整体可用
3. **熔断保护**：异常数据源自动熔断，避免雪崩效应
4. **类型安全**：完整类型注解，IDE 友好，静态检查通过
5. **可扩展**：新增数据源只需实现 `BaseFetcher` 协议

### 1.2 架构层次

```
┌─────────────────────────────────────────────┐
│           用户层 (User API)                  │
│  DataFetcher.fetch_quote() / fetch_kline()   │
├─────────────────────────────────────────────┤
│           路由层 (Router)                    │
│  MultiSourceFetcher (优先级降级 + 熔断)       │
├─────────────────────────────────────────────┤
│           实现层 (Implementations)           │
│  AKShareFetcher / EastMoneyFetcher / ...     │
├─────────────────────────────────────────────┤
│           契约层 (Contracts)                 │
│  FinanceData / BaseFetcher / DataType        │
└─────────────────────────────────────────────┘
```

---

## 二、核心数据契约

### 2.1 FinanceData 统一数据类

**文件位置**：`finance_toolkit/core.py`

```python
@dataclass
class FinanceData:
    """统一金融数据契约 - 所有模块输出标准化为此格式"""
    
    source: str                    # 数据源标识: akshare/eastmoney/sina/tencent/netease/coingecko/binance
    data_type: str                 # 数据类型（见 DataType 枚举）
    symbol: str                    # 标的代码（标准化格式）
    timestamp: str                 # 数据时间戳 (ISO 8601, UTC)
    payload: Dict[str, Any]        # 业务载荷（见各类型 schema）
    raw: Optional[Dict] = None     # 原始响应（调试用）
    meta: Optional[Dict] = None    # 元信息：请求耗时、重试次数、代理IP等
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)
    
    def get(self, key: str, default: Any = None) -> Any:
        """便捷获取 payload 中的字段"""
        return self.payload.get(key, default)
```

### 2.2 DataType 枚举体系

**文件位置**：`finance_toolkit/data_source_config.py`

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

### 2.3 DataSourceMeta 数据源元数据

```python
@dataclass
class DataSourceMeta:
    """数据源元数据"""
    name: str                        # 唯一标识（如 'akshare'）
    display_name: str                # 显示名称（如 'AKShare'）
    type: DataSourceType             # 类型枚举
    base_url: str                    # API基础URL
    is_free: bool = True             # 是否免费
    requires_auth: bool = False      # 是否需要认证
    rate_limit: int = 10             # 每秒请求次数限制
    timeout: float = 30.0            # 超时(秒)
    priority: int = 0                # 优先级（越小越高）
    supported_types: List[DataType]  # 支持的数据类型
    api_key: Optional[str] = None
    proxy: Optional[str] = None
    custom_headers: Dict[str, str] = field(default_factory=dict)
```

### 2.4 内置数据源优先级表

| 数据源 | 优先级 | 免费 | 支持领域 | 认证要求 |
|--------|--------|------|---------|---------|
| AKShare | 1 | ✅ | 全领域 | 无 |
| 东方财富 | 2 | ✅ | 股票/基金/债券/指数 | 无 |
| 新浪财经 | 3 | ✅ | 股票/期货/外汇/加密货币 | 无 |
| 腾讯财经 | 4 | ✅ | 股票/ETF/指数 | 无 |
| CoinGecko | 5 | ✅ | 加密货币 | 无（需代理） |
| Binance | 6 | ✅ | 加密货币 | 无（需代理） |
| Yahoo Finance | 7 | ✅ | 股票/加密货币/外汇/商品 | 无（需代理） |
| FRED | 8 | ✅ | 宏观数据 | API Key |

---

## 三、标的代码标准化

### 3.1 标准格式定义

| 领域 | 标准格式 | 示例 |
|------|---------|------|
| A股股票 | `{代码}.{交易所}` | `600000.SH`, `000001.SZ`, `688001.SH` |
| ETF | 纯数字代码 | `510300`, `159915` |
| 债券 | `{代码}.BOND` | `113000.BOND` |
| 加密货币 | `{符号}-{计价币}` | `BTC-USDT`, `ETH-USD` |
| 期货 | `{品种}{合约月份}` | `IF2406`, `AU2412`, `CU2501` |
| 外汇 | `{货币对}` | `USDCNY`, `EURUSD`, `USDJPY` |

### 3.2 格式转换函数

```python
# 位于 finance_toolkit/interface.py

def to_standard_symbol(code: str) -> str:
    """转换为标准格式: 600000 -> 600000.SH"""
    code = code.strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('60', '68', '90')):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'

def to_akshare_symbol(code: str) -> str:
    """转换为AKShare格式: 600000.SH -> 600000"""
    return code.split('.')[0]

def to_sina_symbol(code: str) -> str:
    """转换为新浪格式: 600000.SH -> sh600000"""
    code = code.split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'
```

---

## 四、统一接口规范

### 4.1 BaseFetcher 接口定义

```python
# 位于 finance_toolkit/data_fetching/fetcher_base.py

class BaseFetcher(ABC):
    """所有数据抓取器的统一抽象基类"""
    
    @classmethod
    @abstractmethod
    def get_source_name(cls) -> str:
        """数据源名称标识（小写，如 'akshare', 'coingecko'）"""
        pass
    
    @classmethod
    @abstractmethod
    def get_supported_types(cls) -> List[str]:
        """支持的数据类型列表"""
        pass
    
    @abstractmethod
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[FinanceData]:
        """
        统一入口方法 - 根据 data_type 分派到具体实现
        
        Args:
            data_type: 数据类型（如 'quote', 'kline', 'crypto_quote'）
            symbols: 标的代码列表
            **kwargs: 其他参数
        
        Returns:
            List[FinanceData]
        """
        pass
    
    @abstractmethod
    def health_check(self) -> bool:
        """健康检查"""
        pass
    
    def get_priority(self) -> int:
        """优先级（越小越高），默认根据支持类型数量计算"""
        return len(self.get_supported_types())
    
    def get_capabilities(self) -> Dict[str, Any]:
        """获取数据源能力描述"""
        return {
            'source': self.get_source_name(),
            'supported_types': self.get_supported_types(),
            'priority': self.get_priority(),
        }
```

### 4.2 MultiSourceFetcher 多源路由

```python
class MultiSourceFetcher(BaseFetcher):
    """
    多数据源聚合 Fetcher - 自动按优先级降级
    
    使用方式：
        router = DataSourceRouter()
        results = router.fetch('crypto_quote', ['BTC', 'ETH'])
    """
    
    def __init__(self, default_source: str = 'akshare', 
                 failure_threshold: int = 5,
                 reset_timeout: float = 60.0):
        self.default_source = default_source
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._registered_fetchers: Dict[str, BaseFetcher] = {}
    
    def register(self, fetcher: BaseFetcher):
        """注册数据源抓取器"""
        ...
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[FinanceData]:
        """
        统一 fetch 入口 - 自动按优先级降级
        
        降级逻辑：
        1. 检查指定数据源是否存在且未熔断
        2. 按 SOURCE_PRIORITY 配置遍历数据源
        3. 首次成功即返回
        4. 全部失败则抛出 FallbackError
        """
        ...
```

### 4.3 全局路由实例

```python
# 位于 finance_toolkit/data_fetching/fetcher_base.py

# 创建默认路由（一次性调用）
def create_default_router() -> MultiSourceFetcher:
    router = MultiSourceFetcher(default_source='akshare')
    router.register(AKShareFetcher())
    router.register(EastMoneyFetcher())
    router.register(CryptoFetcher())
    router.register(BondFetcher())
    router.register(FundFetcher())
    router.register(ForexFetcher())
    router.register(FuturesFetcher())
    return router

# 全局单例路由
_global_router = create_default_router()
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

# 熔断器配置
CB_FAILURE_THRESHOLD = 5        # 触发熔断的失败次数
CB_RESET_TIMEOUT = 60.0         # 熔断重置等待时间(秒)
```

---

## 七、使用示例

```python
from finance_toolkit.interface import DataFetcher
from finance_toolkit.core import FinanceData

# 创建数据获取器
fetcher = DataFetcher(default_source='akshare')

# 获取股票行情
quotes = fetcher.fetch_quote(['600000.SH', '000001.SZ'])
for q in quotes:
    print(f"{q.symbol}: {q.get('close')} ({q.get('change_pct'):+.2f}%)")

# 获取债券收益率
yields = fetcher.fetch_bond_yield()
print(f"10年期国债收益率: {yields[0].get('yield_10y'):.2f}%")

# 获取基金净值
navs = fetcher.fetch_fund_nav(['000001.FUND', '110011.FUND'])
for nav in navs:
    print(f"{nav.symbol}: 净值={nav.get('unit_nav')}")

# 获取加密货币行情
cryptos = fetcher.fetch_crypto_quote(['BTC-USDT', 'ETH-USD'])
for c in cryptos:
    print(f"{c.symbol}: ${c.get('price'):,.2f}")

# 使用全局路由
from finance_toolkit.data_fetching.fetcher_base import _global_router as router
result = router.fetch('crypto_quote', ['BTC', 'ETH'])
```

---

## 八、错误处理规范

```python
class FinanceError(Exception):
    """金融数据抓取基础异常"""
    pass

class SourceUnavailableError(FinanceError):
    """数据源不可用"""
    pass

class SourceRateLimitedError(FinanceError):
    """数据源限流"""
    pass

class DataQualityError(FinanceError):
    """数据质量问题"""
    pass

class CircuitBreakerError(FinanceError):
    """熔断器触发"""
    pass

class FallbackError(FinanceError):
    """所有数据源均失败"""
    pass
```

---

## 九、模块文件清单

| 文件 | 职责 | 状态 |
|------|------|------|
| `core.py` | FinanceData 统一契约 | ✅ 已实现 |
| `interface.py` | DataFetcher 高层API | ✅ 已实现 |
| `data_source_config.py` | DataType枚举 + DataSourceMeta | ✅ 已实现 |
| `data_fetching/fetcher_base.py` | BaseFetcher + MultiSourceFetcher | ✅ 已实现 |
| `data_fetching/crypto_fetchers.py` | 加密货币多源获取 | ✅ 已实现 |
| `data_fetching/crypto_fetcher.py` | 加密货币数据模型 | ✅ 已实现 |
| `data_fetching/bond_fetcher.py` | 债券数据获取 | ✅ 已实现 |
| `data_fetching/fund_fetcher.py` | 基金数据获取 | ✅ 已实现 |
| `data_fetching/futures_fetcher.py` | 期货数据获取 | ✅ 已实现 |
| `data_fetching/forex_fetcher.py` | 外汇数据获取 | ✅ 已实现 |
| `data_fetching/extended_fetchers.py` | 扩展数据获取 | ✅ 已实现 |
| `models/crypto_models.py` | 加密货币数据模型 | ✅ 已实现 |
| `models/bond_models.py` | 债券数据模型 | ✅ 已实现 |
| `models/fund_models.py` | 基金数据模型 | ✅ 已实现 |
| `models/futures_models.py` | 期货数据模型 | ✅ 已实现 |
| `models/forex_models.py` | 外汇数据模型 | ✅ 已实现 |
| `models/stock_models.py` | 股票数据模型 | ✅ 已实现 |
| `specs/unified-interface-spec.md` | 本规范文档 | ✅ 新建 |

---

*规范版本：v3.0*
*更新日期：2026-08-14*
