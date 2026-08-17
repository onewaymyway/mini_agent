# 数据源扩展指南

> 版本：v3.0
> 目标：指导开发者添加新的金融数据源

---

## 目录

1. [快速开始](#1-快速开始)
2. [Scraper层实现](#2-scraper层实现)
3. [Adapter层实现](#3-adapter层实现)
4. [注册与发现](#4-注册与发现)
5. [测试规范](#5-测试规范)
6. [最佳实践](#6-最佳实践)

---

## 1. 快速开始

### 1.1 新增数据源三步走

1. **创建Scraper** - 实现原始数据抓取
2. **创建Adapter** - 封装并标准化输出
3. **注册适配器** - 添加到系统注册表

### 1.2 示例：添加"模拟数据源"

```python
# 步骤1: 创建 scrapers/simulated_scraper.py
from finance_toolkit.core import BaseScraper, FinanceData, register_scraper
from datetime import datetime
from typing import List, AsyncIterator

@register_scraper
class SimulatedScraper(BaseScraper):
    @property
    def source_name(self) -> str:
        return 'simulated'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline']
    
    async def fetch(self, symbols, data_type, start=None, end=None, **kwargs):
        for sym in symbols:
            yield FinanceData(
                source='simulated',
                data_type=data_type,
                symbol=sym,
                timestamp=datetime.utcnow().isoformat(),
                payload={'price': 100.0 + hash(sym) % 100}
            )
    
    async def health_check(self):
        from finance_toolkit.core import HealthResult, HealthStatus
        return HealthResult(status=HealthStatus.HEALTHY)
    
    async def close(self):
        pass
```

```python
# 步骤2: 创建 adapters/simulated_adapter.py
from .base_adapter import BaseAdapter
from .simulated_scraper import SimulatedScraper

class SimulatedAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._scraper = SimulatedScraper()
```

```python
# 步骤3: 注册到 adapters/__init__.py
from .simulated_adapter import SimulatedAdapter
ALL_ADAPTERS['simulated'] = SimulatedAdapter
```

---

## 2. Scraper层实现

### 2.1 必须实现的接口

```python
class BaseScraper(ABC):
    @property
    def source_name(self) -> str: ...  # 数据源唯一标识
    
    @property
    def supported_types(self) -> List[str]: ...  # 支持的数据类型
    
    async def fetch(self, symbols, data_type, start, end, **kwargs) -> AsyncIterator[FinanceData]: ...
    
    async def health_check(self) -> HealthResult: ...  # 健康检查
    
    async def close(self): ...  # 资源释放
```

### 2.2 关键设计要点

#### 2.2.1 异常处理

```python
async def fetch(self, symbols, data_type, start=None, end=None, **kwargs):
    try:
        # 实际抓取逻辑
        data = await self._do_fetch(symbols, data_type)
        for item in data:
            yield FinanceData(source=self.source_name, ...)
    except RateLimitError:
        logger.warning(f"Source {self.source_name} rate limited")
        # 触发熔断器
        raise
    except Exception as e:
        logger.error(f"Fetch failed: {e}")
        yield FinanceData(error=str(e), source=self.source_name)
```

#### 2.2.2 请求限流

```python
import asyncio
import time

class RateLimiter:
    def __init__(self, max_requests=10, per_second=1):
        self.max_requests = max_requests
        self.per_second = per_second
        self._timestamps = []
    
    async def acquire(self):
        while True:
            now = time.monotonic()
            # 清理过期时间戳
            self._timestamps = [t for t in self._timestamps if now - t < 1]
            
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return
            
            await asyncio.sleep(1.0 / self.per_second)
```

#### 2.2.3 重试机制

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=lambda e: isinstance(e, (TimeoutError, ConnectionError))
)
async def _do_fetch(self, symbols, data_type):
    # 实际请求
    return await self._fetch_impl(symbols, data_type)
```

---

## 3. Adapter层实现

### 3.1 必须实现的接口

```python
class BaseAdapter:
    @property
    def source_name(self) -> str: ...  # 数据源标识
    
    @property
    def supported_types(self) -> List[str]: ...  # 支持的数据类型
    
    async def initialize(self): ...  # 初始化连接
    
    async def health_check(self) -> HealthResult: ...  # 健康检查
    
    async def fetch(self, symbols, data_type, start, end, **kwargs) -> AsyncIterator[FinanceData]: ...
    
    async def close(self): ...  # 释放资源
    
    def get_adapter_info(self) -> dict: ...  # 返回元信息
    def get_stats(self) -> dict: ...  # 返回运行统计
```

### 3.2 字段映射

所有Adapter必须实现字段映射，将原始数据标准化：

```python
# 定义字段映射表
FIELD_MAP = {
    '原始字段名': '标准字段名',
    'price': 'close',
    'volume': 'volume',
    # ...
}

def _map_fields(raw_record: dict) -> dict:
    result = {}
    for src_key, val in raw_record.items():
        mapped_key = FIELD_MAP.get(src_key, src_key)
        result[mapped_key] = val
    return result
```

### 3.3 数据验证

```python
from finance_toolkit.exceptions import ValidationError

async def _validate(self, data: FinanceData) -> bool:
    """验证数据完整性"""
    required_fields = {'symbol', 'timestamp', 'payload'}
    if not all(field in data.__dict__ for field in required_fields):
        raise ValidationError("Missing required fields")
    
    # 业务规则验证
    if data.payload.get('price', 0) <= 0:
        raise ValidationError(f"Invalid price: {data.payload.get('price')}")
    
    return True
```

---

## 4. 注册与发现

### 4.1 自动发现机制

系统在 `adapters/__init__.py` 中使用自动发现：

```python
import pkgutil
import importlib

# 自动扫描并导入所有适配器
for importer, modname, ispkg in pkgutil.iter_modules(__path__):
    if modname.startswith('_') or modname == 'base_adapter':
        continue
    try:
        importlib.import_module(f'.{modname}', __name__)
    except ImportError as e:
        logger.warning(f"Failed to import adapter {modname}: {e}")
```

### 4.2 手动注册

如果需要特殊初始化，在 `__init__.py` 中手动注册：

```python
# 添加到 ALL_ADAPTERS 字典
ALL_ADAPTERS = {
    'akshare': AKShareAdapter,
    'eastmoney': EastMoneyAdapter,
    # ... 新添加的适配器
    'my_new_source': MyNewSourceAdapter,
}

# 添加到 __all__ 列表
__all__ = [
    # ... 现有导出
    'MyNewSourceAdapter',
    'create_my_new_source_adapter',
]
```

### 4.3 配置管理

在 `config/data_sources.yaml` 中添加配置：

```yaml
data_sources:
  my_new_source:
    enabled: true
    priority: 5           # 优先级（数字越小越高）
    rate_limit: 10        # 每秒最大请求数
    timeout: 30           # 超时时间（秒）
    retry_count: 3        # 重试次数
    cache_ttl: 300        # 缓存时间（秒）
    circuit_breaker:
      fail_threshold: 5   # 熔断阈值
      recovery_timeout: 60 # 恢复超时
    endpoints:
      quote: "https://api.example.com/quote"
      kline: "https://api.example.com/kline"
    auth:
      type: api_key       # 认证类型: none/api_key/oauth2
      key_name: "X-API-Key"
```

---

## 5. 测试规范

### 5.1 单元测试模板

```python
# tests/test_my_adapter.py
import pytest
import asyncio
from finance_toolkit.adapters.my_adapter import MyAdapter
from finance_toolkit.core import FinanceData

@pytest.mark.asyncio
async def test_my_adapter_health_check():
    adapter = MyAdapter()
    result = await adapter.health_check()
    assert result.is_healthy

@pytest.mark.asyncio
async def test_my_adapter_fetch_quote():
    adapter = MyAdapter()
    await adapter.initialize()
    
    results = []
    async for data in adapter.fetch(['TEST01'], 'quote'):
        results.append(data)
        assert data.source == 'my_adapter'
        assert data.symbol == 'TEST01'
        assert 'price' in data.payload
    
    assert len(results) > 0
    await adapter.close()
```

### 5.2 集成测试

```python
# tests/integration/test_data_sources.py
import pytest
from finance_toolkit import FinanceToolkit

@pytest.mark.integration
@pytest.mark.parametrize("source,expected_types", [
    ('akshare', ['quote', 'kline']),
    ('eastmoney', ['quote', 'lhb']),
    ('my_new_source', ['quote', 'kline']),
])
async def test_source_availability(source, expected_types):
    toolkit = FinanceToolkit()
    
    for data_type in expected_types:
        data = await toolkit.fetch(source=source, symbol='000001', data_type=data_type)
        assert data is not None
        assert data.source == source
```

---

## 6. 最佳实践

### 6.1 性能优化

1. **连接池**: 复用HTTP连接
2. **批量请求**: 合并多个symbol为单次请求
3. **异步IO**: 使用async/await避免阻塞
4. **缓存**: 对不变数据设置合理TTL

### 6.2 错误处理

1. **分级异常**: 区分网络错误、数据错误、认证错误
2. **优雅降级**: 主源失败时自动切换备用源
3. **熔断保护**: 防止雪崩效应
4. **日志记录**: 关键操作记录完整日志

### 6.3 安全考虑

1. **敏感信息**: API Key不要硬编码，使用环境变量
2. **输入验证**: 对用户输入进行严格校验
3. **资源限制**: 防止恶意请求耗尽资源
4. **数据脱敏**: 生产环境不输出敏感数据

### 6.4 文档规范

每个新增数据源必须包含：
- README.md 使用说明
- API文档（端点、参数、响应格式）
- 示例代码
- 故障排查指南

---

## 附录A: 现有数据源扩展状态

| 数据源 | 状态 | 可扩展性 | 备注 |
|--------|------|----------|------|
| AKShare | ✅ 已完善 | ⭐⭐⭐ | 免费、无需认证 |
| Tushare | ✅ 已完善 | ⭐⭐ | 需Token，专业数据 |
| 东方财富 | ✅ 已完善 | ⭐⭐⭐ | 北向资金、龙虎榜 |
| 新浪财经 | ✅ 已完善 | ⭐⭐ | 备用数据源 |
| Yahoo Finance | ✅ 已完善 | ⭐⭐ | 港股/美股 |
| Binance | ✅ 已完善 | ⭐⭐⭐ | 加密货币主流 |
| CoinGecko | ✅ 已完善 | ⭐⭐ | 全球币种覆盖 |
| 中国银行 | ✅ 已完善 | ⭐ | 外汇汇率 |

## 附录B: 建议新增数据源

| 优先级 | 数据源 | 用途 | 预计工作量 |
|--------|--------|------|------------|
| P0 | 同花顺iFinD | 专业机构数据 | 大 |
| P1 | 雪球 | 社区情绪 | 中 |
| P1 | 财联社 | 实时快讯 | 中 |
| P2 | 巨潮资讯 | 公告数据 | 小 |
| P2 | 上交所/深交所 | 官方数据 | 小 |
| P3 | Wind万得 | 机构级数据 | 大（需授权） |
