# 请求速率控制模块（rate_limiter.py）

> 令牌桶/漏桶/固定窗口三种算法、指数退避重试、熔断器模式。

---

## 1. 核心功能

| 功能 | 说明 |
|------|------|
| 令牌桶算法 | 平滑限流，支持突发流量 |
| 漏桶算法 | 恒定速率输出 |
| 固定窗口 | 简单计数限流 |
| 指数退避重试 | 失败后自动重试 |
| 熔断器 | 连续失败后暂停，超时恢复 |

---

## 2. 快速开始

```python
from src.core.rate_limiter import get_rate_limiter, RateLimitAlgorithm

# 获取全局限流器
limiter = get_rate_limiter()

# 设置令牌桶算法，每秒 2 个请求，突发上限 5 个
limiter.set_algorithm(RateLimitAlgorithm.TOKEN_BUCKET, rate=2.0, burst=5)

# 执行请求前获取令牌
if limiter.acquire():
    await do_request()
else:
    await asyncio.sleep(limiter.get_retry_after())
```

---

## 3. 算法对比

| 算法 | 特点 | 适用场景 |
|------|------|----------|
| `TOKEN_BUCKET` | 允许突发，平滑限流 | 大多数场景 |
| `LEAKY_BUCKET` | 恒定速率输出 | 严格限速 |
| `FIXED_WINDOW` | 简单计数 | 低精度需求 |

---

## 4. API 参考

### 4.1 RateLimitConfig

```python
@dataclass
class RateLimitConfig:
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.TOKEN_BUCKET
    rate: float = 1.0          # 每秒请求数
    burst: int = 10            # 突发上限
    max_retries: int = 3       # 最大重试次数
    base_delay: float = 1.0    # 基础延迟（秒）
    circuit_breaker_threshold: int = 5  # 熔断阈值
    recovery_timeout: float = 30.0     # 恢复超时（秒）
```

### 4.2 RateLimiter

| 方法 | 说明 |
|------|------|
| `set_algorithm(algo, **kwargs)` | 设置算法和参数 |
| `acquire()` | 获取令牌，返回 bool |
| `get_retry_after()` | 获取需等待时间 |
| `execute(func, *args, **kwargs)` | 带重试执行函数 |
| `record_success()` | 记录成功 |
| `record_failure()` | 记录失败 |
| `get_state()` | 获取当前状态 |

### 4.3 全局函数

```python
limiter = get_rate_limiter()
set_rate_limiter(limiter)
reset_rate_limiter()
```

---

## 5. 使用示例

### 5.1 基础限流

```python
from src.core.rate_limiter import get_rate_limiter, RateLimitAlgorithm

limiter = get_rate_limiter()
limiter.set_algorithm(RateLimitAlgorithm.TOKEN_BUCKET, rate=2.0, burst=5)

for url in urls:
    if limiter.acquire():
        fetch(url)
    else:
        time.sleep(limiter.get_retry_after())
```

### 5.2 带重试执行

```python
async def fetch_with_retry(url):
    async def _fetch():
        async with session.get(url) as resp:
            return await resp.json()
    
    return await limiter.execute(_fetch)
```

### 5.3 熔断器模式

```python
limiter = get_rate_limiter()
limiter.set_algorithm(
    RateLimitAlgorithm.TOKEN_BUCKET,
    rate=1.0,
    circuit_breaker_threshold=5,
    recovery_timeout=10.0
)

# 连续失败 5 次后熔断，10 秒后自动恢复
```

---

## 6. 注意事项

- 全局限流器是单例，所有模块共享同一配置
- 调用 `reset_rate_limiter()` 可重置为默认配置
- 熔断器状态会跨请求累积，需根据业务调整阈值
