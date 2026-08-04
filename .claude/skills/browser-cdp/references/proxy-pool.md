# 代理池管理模块（proxy_pool.py）

> HTTP/SOCKS5 代理轮换、健康检查、自动故障转移、按健康度/轮询/随机策略选择。

---

## 1. 核心功能

| 功能 | 说明 |
|------|------|
| 多代理管理 | 支持 HTTP/SOCKS5 代理 |
| 健康检查 | 自动检测代理可用性 |
| 故障转移 | 健康代理耗尽时自动切换 |
| 多种策略 | 健康度/轮询/随机选择 |

---

## 2. 快速开始

```python
from src.core.proxy_pool import get_proxy_pool, ProxyInfo, ProxyType

# 获取全局代理池
pool = get_proxy_pool()

# 添加代理
pool.add_proxy(ProxyInfo(host="127.0.0.1", port=8080, proxy_type=ProxyType.HTTP))
pool.add_proxy(ProxyInfo(host="127.0.0.2", port=8081, proxy_type=ProxyType.SOCKS5))

# 按健康度选择代理
proxy = pool.get_proxy_by_health_score()
print(f"使用代理: {proxy.host}:{proxy.port}")
```

---

## 3. 选择策略

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| `health_score` | 优先选择健康度高的代理 | 代理质量差异大 |
| `round_robin` | 轮询选择 | 均匀分布请求 |
| `random` | 随机选择 | 避免规律性 |

---

## 4. API 参考

### 4.1 ProxyInfo

```python
@dataclass
class ProxyInfo:
    host: str
    port: int
    proxy_type: ProxyType = ProxyType.HTTP
    username: str = ""
    password: str = ""
    health_score: float = 1.0
    success_count: int = 0
    failure_count: int = 0
    is_active: bool = True
    
    def mark_success(self) -> None
    def mark_failure(self) -> None
```

### 4.2 ProxyPoolConfig

```python
@dataclass
class ProxyPoolConfig:
    rotation_strategy: str = "health_score"
    health_check_interval: float = 60.0
    failure_threshold: int = 3
```

### 4.3 ProxyPool

| 方法 | 说明 |
|------|------|
| `add_proxy(proxy)` | 添加单个代理 |
| `add_proxies(proxies)` | 批量添加代理 |
| `remove_proxy(host, port)` | 移除代理 |
| `get_proxy_by_health_score()` | 按健康度选择 |
| `get_proxy_by_round_robin()` | 轮询选择 |
| `get_proxy_by_random()` | 随机选择 |
| `get_next_proxy()` | 根据配置策略选择 |
| `get_stats()` | 获取统计信息 |

### 4.4 全局函数

```python
pool = get_proxy_pool()
set_proxy_pool(pool)
reset_proxy_pool()
```

---

## 5. 使用示例

### 5.1 基础用法

```python
from src.core.proxy_pool import get_proxy_pool, ProxyInfo, ProxyType

pool = get_proxy_pool()
pool.add_proxy(ProxyInfo(host="1.2.3.4", port=8080, proxy_type=ProxyType.HTTP))

proxy = pool.get_next_proxy()
# 使用 proxy 发起请求
```

### 5.2 自定义策略

```python
from src.core.proxy_pool import ProxyPool, ProxyPoolConfig

pool = ProxyPool(config=ProxyPoolConfig(rotation_strategy="round_robin"))
pool.add_proxy(ProxyInfo(host="1.2.3.4", port=8080))
pool.add_proxy(ProxyInfo(host="5.6.7.8", port=8080))

# 轮询选择
proxy1 = pool.get_next_proxy()
proxy2 = pool.get_next_proxy()  # 不同的代理
```

### 5.3 健康度管理

```python
proxy = pool.get_next_proxy()
try:
    response = fetch(proxy)
    proxy.mark_success()
except Exception:
    proxy.mark_failure()
    # 失败 3 次后代理自动标记为不活跃
```

---

## 6. 注意事项

- 全局代理池是单例，所有模块共享同一配置
- 调用 `reset_proxy_pool()` 可清空所有代理
- 代理失败次数达到阈值后自动标记为不活跃
- 健康度 = 成功次数 / (成功次数 + 失败次数)
