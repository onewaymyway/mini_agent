# 统一数据抓取接口规范

## 1. 概述

本文档定义了 finance-data-toolkit 的统一数据抓取接口规范，包括输入输出格式、重试策略、异常类型和容错机制。

## 2. 统一数据契约

### 2.1 FinanceData 数据类

```python
@dataclass
class FinanceData:
    """统一金融数据契约"""
    source: str                    # 数据源名称
    data_type: str                 # 数据类型
    symbol: str                    # 标的代码
    timestamp: str                 # 时间戳 (ISO 8601)
    payload: Dict[str, Any]        # 数据载荷
    raw: Optional[Dict] = None     # 原始数据
    meta: Optional[Dict] = None    # 元数据
```

### 2.2 数据类型枚举

| 类型 | 说明 | 示例 |
|------|------|------|
| `quote` | 实时行情 | 股票、基金、指数实时价格 |
| `kline` | K线数据 | 日K、周K、月K、分钟K |
| `financial` | 财务报表 | 资产负债表、利润表、现金流量表 |
| `dividend` | 分红数据 | 分红派息、配股信息 |
| `lhb` | 龙虎榜 | 龙虎榜交易数据 |
| `northbound` | 北向资金 | 沪股通、深股通资金流向 |
| `stock_basic` | 股票基础信息 | 代码、名称、行业、上市日期 |
| `fund_nav` | 基金净值 | 基金净值、累计净值 |
| `fund_holdings` | 基金持仓 | 基金持仓明细 |
| `bond_yield` | 债券收益率 | 国债收益率、企业债收益率 |
| `bond_quote` | 债券行情 | 债券实时行情 |
| `futures_quote` | 期货行情 | 期货实时行情 |
| `futures_kline` | 期货K线 | 期货历史K线 |
| `index_quote` | 指数行情 | 指数实时行情 |
| `index_kline` | 指数K线 | 指数历史K线 |
| `macro_gdp` | GDP数据 | 国内生产总值 |
| `macro_cpi` | CPI数据 | 居民消费价格指数 |
| `macro_pmi` | PMI数据 | 采购经理指数 |
| `forex_quote` | 外汇行情 | 汇率实时行情 |
| `crypto_quote` | 加密货币 | 加密货币行情 |
| `etf_quote` | ETF行情 | ETF实时行情 |
| `etf_kline` | ETFK线 | ETF历史K线 |
| `news` | 新闻资讯 | 财经新闻、公告 |
| `sentiment` | 情绪数据 | 市场情绪指标 |
| `social` | 社交数据 | 股吧帖子、评论 |

## 3. 统一接口定义

### 3.1 数据获取器基类

```python
class BaseFetcher:
    """数据获取器基类"""
    
    def __init__(self, source: str, config: Optional[Dict] = None):
        self.source = source
        self.config = config or {}
        self.retry_policy = RetryPolicy.from_config(self.config.get('retry', {}))
        self.circuit_breaker = CircuitBreaker(source, **self.config.get('circuit_breaker', {}))
    
    async def fetch(self, symbol: str, data_type: str, **kwargs) -> List[FinanceData]:
        """获取数据（抽象方法）"""
        raise NotImplementedError
    
    async def fetch_batch(self, symbols: List[str], data_type: str, **kwargs) -> List[FinanceData]:
        """批量获取数据"""
        results = []
        for symbol in symbols:
            results.extend(await self.fetch(symbol, data_type, **kwargs))
        return results
```

### 3.2 统一入口函数

```python
async def fetch_data(
    symbol: str,
    data_type: str,
    source: Optional[str] = None,
    params: Optional[Dict] = None,
    fallback: bool = True
) -> List[FinanceData]:
    """
    统一数据获取入口
    
    Args:
        symbol: 标的代码
        data_type: 数据类型
        source: 数据源（可选，默认自动选择）
        params: 额外参数
        fallback: 是否启用降级策略
    
    Returns:
        List[FinanceData]: 数据列表
    """
    pass
```

## 4. 重试策略

### 4.1 RetryPolicy 配置

```python
@dataclass
class RetryPolicy:
    """重试策略配置"""
    max_retries: int = 3                    # 最大重试次数
    backoff_factor: float = 1.0             # 退避因子
    backoff_max: float = 30.0               # 最大退避时间（秒）
    retryable_exceptions: Tuple[type, ...] = (
        ConnectionError,
        TimeoutError,
        SourceUnavailableError,
        SourceRateLimitedError,
    )
    jitter: bool = True                     # 是否启用随机抖动
    
    @classmethod
    def from_config(cls, config: Dict) -> 'RetryPolicy':
        """从配置字典创建重试策略"""
        pass
```

### 4.2 重试退避算法

```python
def calculate_backoff(attempt: int, policy: RetryPolicy) -> float:
    """
    计算退避时间
    
    算法：min(backoff_factor * 2^attempt, backoff_max)
    如果启用 jitter，则加上随机抖动
    """
    base_delay = policy.backoff_factor * (2 ** attempt)
    delay = min(base_delay, policy.backoff_max)
    
    if policy.jitter:
        delay *= random.uniform(0.5, 1.5)
    
    return delay
```

### 4.3 重试装饰器

```python
def retry_with_policy(policy: RetryPolicy):
    """
    带策略的重试装饰器
    
    使用示例：
        @retry_with_policy(RetryPolicy(max_retries=5, backoff_factor=2))
        async def fetch_data():
            ...
    """
    pass
```

## 5. 异常体系

### 5.1 异常层次结构

```
FinanceError (基础异常)
├── SourceError (数据源错误)
│   ├── SourceUnavailableError (数据源不可用)
│   ├── SourceRateLimitedError (数据源限流)
│   └── SourceAuthError (数据源认证失败)
├── DataError (数据错误)
│   ├── DataNotFoundError (数据未找到)
│   ├── DataQualityError (数据质量问题)
│   └── DataValidationError (数据校验失败)
├── CircuitBreakerError (熔断器触发)
├── FallbackError (降级失败)
└── ConfigError (配置错误)
```

### 5.2 异常处理最佳实践

```python
try:
    data = await fetch_data('600000.SH', 'quote')
except SourceUnavailableError as e:
    logger.warning(f"数据源 {e.source} 不可用: {e.reason}")
    # 尝试切换数据源
except DataQualityError as e:
    logger.warning(f"数据质量问题: {e.issues}")
    # 触发数据清洗流程
except CircuitBreakerError as e:
    logger.error(f"熔断器触发: {e.source}, 等待 {e.reset_after} 秒后恢复")
    # 等待熔断器恢复
except FallbackError as e:
    logger.error(f"所有数据源均失败: {e.errors}")
    # 返回缓存数据或抛出异常
```

## 6. 熔断器机制

### 6.1 状态机

```
CLOSED (正常) ──失败次数达到阈值──> OPEN (熔断)
     ^                                  |
     |                                  v
     +────成功试探──<──HALF_OPEN (半开) ──失败──+
                        |
                        v
                     CLOSED (恢复)
```

### 6.2 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `failure_threshold` | 5 | 触发熔断的失败次数 |
| `reset_timeout` | 60 | 熔断恢复时间（秒） |
| `half_open_max_calls` | 3 | 半开状态最大试探次数 |

## 7. 降级策略

### 7.1 FallbackManager

```python
class FallbackManager:
    """降级策略管理器"""
    
    def __init__(self, sources: List[Tuple[str, Callable]]):
        """
        Args:
            sources: 数据源列表 [(source_name, fetch_func), ...]
        """
        pass
    
    async def fetch(self, *args, **kwargs) -> Any:
        """
        按优先级尝试多个数据源
        
        Returns:
            第一个成功的数据源返回结果
        
        Raises:
            FallbackError: 所有数据源都失败时抛出
        """
        pass
```

### 7.2 默认数据源优先级

| 数据类型 | 优先级顺序 |
|----------|------------|
| 实时行情 | akshare → 腾讯 → 新浪 → 东方财富 |
| K线数据 | akshare → 新浪 → 腾讯 |
| 财务报表 | akshare → tushare |
| 基金数据 | akshare → 天天基金 |
| 债券数据 | akshare → 东方财富 |
| 期货数据 | akshare → 东方财富 |
| 指数数据 | akshare → 东方财富 |
| 宏观数据 | akshare → 国家统计局 |
| 外汇数据 | 腾讯 → 东方财富 |
| 加密货币 | 币安 → CoinGecko |
| ETF数据 | akshare → 东方财富 |

## 8. 输入输出格式

### 8.1 输入参数

```python
# 实时行情
fetch_realtime_quote(
    symbols: List[str],           # 股票代码列表
    source: str = 'akshare',      # 数据源
    fields: Optional[List[str]] = None  # 指定字段
) -> List[FinanceData]

# K线数据
fetch_kline(
    symbol: str,                  # 股票代码
    period: str = 'daily',        # daily/weekly/monthly/1m/5m/15m/30m/60m
    start: str = '20240101',      # 开始日期
    end: str = None,              # 结束日期
    adjust: str = 'qfq',          # qfq/hfq/不复权
    source: str = 'akshare'
) -> List[Dict]

# 财务报表
fetch_financial(
    symbol: str,
    report_type: str = 'all',     # balance_sheet/income_statement/cash_flow/all
    source: str = 'akshare'
) -> List[FinanceData]
```

### 8.2 输出格式

```python
# FinanceData 示例
FinanceData(
    source='akshare',
    data_type='quote',
    symbol='600000.SH',
    timestamp='2024-01-15T10:30:00Z',
    payload={
        'open': 10.50,
        'high': 10.80,
        'low': 10.40,
        'close': 10.70,
        'pre_close': 10.50,
        'volume': 1000000,
        'amount': 10500000.0,
        'change_pct': 1.90,
        'change_amt': 0.20,
    },
    meta={
        'quality_report': {...},
        'fetch_time_ms': 150,
    }
)
```

## 9. 性能要求

| 指标 | 要求 |
|------|------|
| 单次请求延迟 | < 2秒（P95） |
| 批量请求延迟 | < 5秒（100只股票） |
| 重试成功率 | > 95% |
| 熔断恢复时间 | < 60秒 |
| 内存占用 | < 100MB（1000只股票） |

## 10. 使用示例

### 10.1 基本用法

```python
from finance_toolkit.data_fetching import fetch_realtime_quote, fetch_kline

# 获取实时行情
quotes = fetch_realtime_quote(['600000.SH', '000001.SZ'])
for q in quotes:
    print(f"{q.symbol}: {q.payload['close']}")

# 获取K线数据
klines = fetch_kline('600000.SH', period='daily', start='20240101')
print(f"获取 {len(klines)} 条K线数据")
```

### 10.2 使用统一入口

```python
from finance_toolkit.data_fetching import fetch_data

# 统一入口获取数据
data = await fetch_data(
    symbol='600000.SH',
    data_type='quote',
    source='akshare',
    fallback=True
)
```

### 10.3 异常处理

```python
from finance_toolkit.exceptions import (
    SourceUnavailableError,
    DataQualityError,
    CircuitBreakerError,
)

try:
    data = await fetch_data('600000.SH', 'quote')
except SourceUnavailableError as e:
    print(f"数据源不可用: {e.source}")
except DataQualityError as e:
    print(f"数据质量问题: {e.issues}")
except CircuitBreakerError as e:
    print(f"熔断器触发，等待 {e.reset_after} 秒后恢复")
```

## 11. 配置示例

```python
# 全局配置
from finance_toolkit.config import FinanceConfig

config = FinanceConfig(
    retry=RetryPolicy(
        max_retries=5,
        backoff_factor=2.0,
        backoff_max=60.0,
    ),
    circuit_breaker={
        'failure_threshold': 5,
        'reset_timeout': 60,
    },
    fallback_order={
        'quote': ['akshare', 'tencent', 'sina', 'eastmoney'],
        'kline': ['akshare', 'sina', 'tencent'],
    }
)

# 设置全局配置
FinanceConfig.set_global(config)
```

## 12. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2024-01-15 | 初始版本，定义统一接口规范 |
