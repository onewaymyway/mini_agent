# Browser-CDP 重试策略设计文档

> 版本: 1.0.0
> 最后更新: 2026-08-07
> 状态: 待评审
> 关联代码: `src/reliability/retry.py`, `src/reliability/error.py`

---

## 1. 概述

本文档详细设计 browser-cdp skill 的重试策略，包括重试次数、间隔时间、退避算法，以及不适用的错误类型。

### 1.1 设计目标

- **智能重试**: 根据错误类型自动选择最优重试策略
- **资源保护**: 避免无效重试浪费系统资源
- **用户体验**: 快速恢复可恢复错误，及时通知不可恢复错误
- **可配置性**: 支持按操作类型和网站类型自定义策略

### 1.2 重试策略总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         重试策略决策树                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  错误发生                                                           │
│      ↓                                                              │
│  判断错误类型                                                       │
│      ↓                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ 可恢复错误    │  │ 不可恢复错误  │  │ 未知错误      │              │
│  │              │  │              │  │              │              │
│  │ • CONNECTION │  │ • CONTENT    │  │ • UNKNOWN    │              │
│  │ • TIMEOUT    │  │ • PERMISSION │  │              │              │
│  │ • ELEMENT    │  │              │  │              │              │
│  │ • NAVIGATION │  │              │  │              │              │
│  └──────┬───────┘  └──────────────┘  └──────────────┘              │
│         ↓                                                           │
│  检查熔断器状态                                                     │
│      ↓                                                              │
│  ┌──────────────┐  ┌──────────────┐                                │
│  │ 熔断器关闭    │  │ 熔断器打开    │                                │
│  │              │  │              │                                │
│  │ • 执行重试    │  │ • 等待恢复    │                                │
│  │ • 记录失败    │  │ • 抛出异常    │                                │
│  └──────┬───────┘  └──────────────┘                                │
│         ↓                                                           │
│  计算退避延迟                                                       │
│      ↓                                                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 退避策略选择:                                                │   │
│  │ • FIXED        - 固定延迟 (截图等简单操作)                   │   │
│  │ • LINEAR       - 线性增长 (元素查找)                         │   │
│  │ • EXPONENTIAL  - 指数增长 (超时类错误)                       │   │
│  │ • EXPONENTIAL_JITTER - 指数+抖动 (CDP连接)                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│         ↓                                                           │
│  执行重试                                                           │
│      ↓                                                              │
│  ┌──────────────┐  ┌──────────────┐                                │
│  │ 成功         │  │ 失败         │                                │
│  │              │  │              │                                │
│  │ • 记录成功    │  │ • 检查重试次数│                                │
│  │ • 重置熔断器  │  │ • 继续重试    │                                │
│  └──────────────┘  └──────────────┘                                │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 重试策略配置

### 2.1 操作类型默认配置

基于 `RetryConfig.OPERATION_DEFAULTS`:

| 操作类型 | 最大重试次数 | 基础延迟 | 最大延迟 | 退避策略 | 熔断器 | 适用场景 |
|----------|-------------|----------|----------|----------|--------|----------|
| cdp_command | 5 | 1.0s | 30.0s | exponential_jitter | ✅ | CDP 命令执行 |
| element_find | 3 | 0.5s | 10.0s | linear | ❌ | 元素查找 |
| navigation | 3 | 2.0s | 30.0s | exponential_jitter | ✅ | 页面导航 |
| screenshot | 2 | 1.0s | 10.0s | fixed | ❌ | 截图操作 |
| input_click | 3 | 1.0s | 10.0s | linear | ❌ | 输入点击 |

### 2.2 错误类型重试策略

#### 2.2.1 CONNECTION - CDP 连接问题

**错误类型**: `CDPConnectionLostError`

**重试策略**:
```python
{
    "max_retries": 5,
    "base_delay": 1.0,
    "max_delay": 30.0,
    "backoff_strategy": "exponential_jitter",
    "circuit_breaker": True,
    "circuit_breaker_threshold": 5,
    "circuit_breaker_recovery": 30.0
}
```

**策略说明**:
- 最大重试 5 次：CDP 连接问题可能临时发生，需要多次尝试
- 基础延迟 1.0s：快速响应，减少等待时间
- 指数退避 + 抖动：避免多个客户端同时重试导致雪崩
- 启用熔断器：连续失败 5 次后熔断，防止资源浪费

**退避延迟计算示例**:
| 重试次数 | 基础延迟 | 抖动后延迟 | 累计延迟 |
|----------|----------|------------|----------|
| 1 | 1.0s | 0.5s ~ 1.5s | 0.5s ~ 1.5s |
| 2 | 2.0s | 1.0s ~ 3.0s | 1.5s ~ 4.5s |
| 3 | 4.0s | 2.0s ~ 6.0s | 3.5s ~ 10.5s |
| 4 | 8.0s | 4.0s ~ 12.0s | 7.5s ~ 22.5s |
| 5 | 16.0s | 8.0s ~ 24.0s | 15.5s ~ 46.5s |

---

#### 2.2.2 TIMEOUT - 超时错误

**错误类型**:
- `CDPCommandTimeoutError`
- `NetworkIdleTimeoutError`
- `SmartWaitDegradedError`

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 2.0,
    "max_delay": 30.0,
    "backoff_strategy": "exponential",
    "circuit_breaker": True
}
```

**策略说明**:
- 最大重试 3 次：超时通常表示临时性问题，3 次足够
- 基础延迟 2.0s：超时后需要更长时间等待
- 指数退避：逐步增加等待时间
- 启用熔断器：防止持续超时导致资源耗尽

**降级策略**:
| 阶段 | 行为 | 说明 |
|------|------|------|
| 首次超时 | 重试 1 次 | 可能是临时网络波动 |
| 连续超时 | 切换到简单等待 | 放弃智能等待，使用固定延迟 |
| 多次超时 | 记录日志并继续 | 不阻塞后续操作 |

---

#### 2.2.3 ELEMENT - 元素相关错误

**错误类型**:
- `ElementNotFoundError`
- `ElementIndexInvalidError`

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 0.5,
    "max_delay": 10.0,
    "backoff_strategy": "linear",
    "circuit_breaker": False
}
```

**策略说明**:
- 最大重试 3 次：元素查找失败通常是临时性问题
- 基础延迟 0.5s：快速重试，减少等待
- 线性退避：简单可预测的重试间隔
- 不启用熔断器：元素查找失败不影响后续操作

**恢复动作**:
1. 重新扫描页面元素
2. 尝试备用选择器
3. 等待页面稳定后重试
4. 记录元素查找失败详情

---

#### 2.2.4 NAVIGATION - 页面导航错误

**错误类型**: `NavigationTimeoutError`

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 2.0,
    "max_delay": 30.0,
    "backoff_strategy": "exponential_jitter",
    "circuit_breaker": True
}
```

**策略说明**:
- 最大重试 3 次：导航失败可能是临时性问题
- 基础延迟 2.0s：导航需要更长时间
- 指数退避 + 抖动：避免多个客户端同时重试
- 启用熔断器：防止持续导航失败

---

#### 2.2.5 CONTENT - 内容相关错误

**错误类型**: `CaptchaDetectedError`

**重试策略**:
```python
{
    "max_retries": 0,
    "base_delay": 0.0,
    "backoff_strategy": "fixed",
    "circuit_breaker": False
}
```

**策略说明**:
- ❌ **不适用重试**：验证码需要人工干预
- 立即停止操作
- 记录详细日志（截图、URL、时间戳）
- 触发告警通知用户

---

#### 2.2.6 PERMISSION - 权限/拦截错误

**错误类型**: `BlockedByAntiBotError`

**重试策略**:
```python
{
    "max_retries": 0,
    "base_delay": 0.0,
    "backoff_strategy": "fixed",
    "circuit_breaker": False
}
```

**策略说明**:
- ❌ **不适用重试**：反爬拦截需要更换代理或调整策略
- 立即停止当前操作
- 尝试更换代理
- 调整请求头和行为模式
- 通知用户人工确认

---

#### 2.2.7 UNKNOWN - 未知错误

**重试策略**:
```python
{
    "max_retries": 1,
    "base_delay": 1.0,
    "backoff_strategy": "fixed",
    "circuit_breaker": False
}
```

**策略说明**:
- 最多重试 1 次：未知错误可能是系统性问题
- 记录完整错误信息
- 归类到相应错误类型

---

## 3. 退避算法详解

### 3.1 算法公式

| 策略 | 公式 | 适用场景 |
|------|------|----------|
| FIXED | `delay = min(base_delay, max_delay)` | 简单操作，如截图 |
| LINEAR | `delay = min(base_delay * attempt, max_delay)` | 元素查找 |
| EXPONENTIAL | `delay = min(base_delay ** attempt, max_delay)` | 超时类错误 |
| EXPONENTIAL_JITTER | `delay = min(base_delay ** attempt, max_delay) * (0.5 + random())` | CDP 连接 |

### 3.2 退避策略对比

```
延迟 (秒)
  |
30|                    EXPONENTIAL_JITTER
  |                    /
20|                   /
  |                  /
10|                 /    EXPONENTIAL
  |                /    /
  |               /    /
 5|              /    /
  |             /    /
 2|            /    /
  |           /    /
 1|----------/----/------ LINEAR
  |         /    /
  |        /    /
 0+-------/----/---------------- FIXED
  0  1  2  3  4  5  6  7  8  9  10
              重试次数
```

### 3.3 抖动因子说明

EXPONENTIAL_JITTER 策略在指数退避基础上添加随机抖动：

```python
delay = min(base_delay ** attempt, max_delay) * (0.5 + random.random())
# 抖动范围: 50% ~ 150%
```

**作用**:
- 避免多个客户端同时重试导致雪崩效应
- 提高重试成功率
- 减少服务器压力

---

## 4. 熔断器机制

### 4.1 状态转换

```
         ┌─────────────────────────────────────────┐
         │                                         │
         ▼                                         │
    ┌─────────┐    连续失败N次    ┌─────────┐      │
    │ CLOSED  │ ───────────────→ │  OPEN   │      │
    │  正常   │                  │  熔断   │      │
    └────┬────┘                  └────┬────┘      │
         │                            │           │
         │ 成功                       │ 等待M秒    │
         │ 试探成功                   ▼           │
         │                     ┌─────────┐       │
         └─────────────────────│HALF_OPEN│───────┘
                               │  试探   │
                               └─────────┘
```

### 4.2 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| failure_threshold | 5 | 触发熔断的连续失败次数 |
| recovery_timeout | 30.0 | 恢复超时时间（秒） |

### 4.3 熔断器状态说明

| 状态 | 说明 | 行为 |
|------|------|------|
| CLOSED | 正常状态 | 允许执行，记录成功/失败 |
| OPEN | 熔断状态 | 拒绝执行，等待恢复超时 |
| HALF_OPEN | 试探状态 | 允许一次试探，成功则关闭，失败则重新打开 |

---

## 5. 不适用的错误类型

### 5.1 不可恢复错误

以下错误类型**不适用重试**，应立即停止操作并通知用户：

| 错误类型 | 分类 | 原因 | 处理策略 |
|----------|------|------|----------|
| CaptchaDetectedError | CONTENT | 需要人工验证 | 停止 + 截图 + 告警 |
| BlockedByAntiBotError | PERMISSION | 被反爬机制拦截 | 停止 + 换代理 + 告警 |

### 5.2 重试不适用场景

| 场景 | 说明 | 建议 |
|------|------|------|
| 验证码 | 需要人工干预 | 停止操作，通知用户 |
| 反爬拦截 | IP 被封禁或行为异常 | 更换代理，调整策略 |
| 权限不足 | 需要登录或授权 | 提示用户登录 |
| 资源耗尽 | 系统资源不足 | 等待资源释放 |

---

## 6. 重试策略配置示例

### 6.1 使用默认配置

```python
from src.reliability.retry import retry_operation, RetryConfig

# 使用操作类型默认配置
config = RetryConfig.for_operation("navigation")
result = retry_operation(func=navigate, config=config, operation="navigation", url="https://example.com")
```

### 6.2 自定义配置

```python
from src.reliability.retry import RetryConfig, BackoffStrategy

# 自定义重试配置
config = RetryConfig(
    max_retries=5,
    backoff_strategy=BackoffStrategy.EXPONENTIAL_JITTER,
    base_delay=1.0,
    max_delay=30.0,
    circuit_breaker=True,
    circuit_breaker_threshold=5,
    circuit_breaker_recovery=30.0,
)
```

### 6.3 使用装饰器

```python
from src.reliability.retry import with_retry, BackoffStrategy

@with_retry(max_retries=3, backoff=BackoffStrategy.LINEAR, base_delay=0.5, operation="element_find")
def find_element(selector: str):
    # 元素查找逻辑
    pass
```

---

## 7. 网站适配建议

针对不同网站类型，建议调整重试策略:

| 网站类型 | 特点 | 建议重试次数 | 建议延迟 | 特殊处理 |
|----------|------|-------------|----------|----------|
| 新闻网站 | 静态内容，加载快 | 2-3次 | 1-2s | 无需特殊处理 |
| 电商网站 | 动态加载，可能有反爬 | 3-5次 | 2-3s | 启用熔断器 |
| 招聘网站 | 需要登录，结构复杂 | 3次 | 1-2s | 元素查找重试 |
| 社交媒体 | 大量动态内容 | 3-5次 | 2-4s | 智能等待策略 |
| 学术网站 | 相对稳定 | 2-3次 | 1-2s | 无需特殊处理 |
| 政府网站 | 可能有限流 | 3次 | 2-3s | 启用退避策略 |

---

## 8. 最佳实践

### 8.1 重试策略选择指南

| 场景 | 推荐策略 | 原因 |
|------|----------|------|
| CDP 连接断开 | exponential_jitter | 避免雪崩效应 |
| 页面超时 | exponential | 逐步增加等待时间 |
| 元素未找到 | linear | 快速重试，简单可预测 |
| 截图操作 | fixed | 简单操作，无需复杂退避 |
| 验证码/反爬 | 不重试 | 需要人工干预 |

### 8.2 性能优化建议

1. **避免过度重试**: 设置合理的最大重试次数
2. **使用指数退避**: 减少服务器压力
3. **启用熔断器**: 防止雪崩效应
4. **监控重试失败率**: 及时发现系统性问题
5. **合理设置超时**: 根据网站特点调整

### 8.3 代码使用示例

```python
from src.reliability.retry import retry_operation, RetryConfig, BackoffStrategy
from src.reliability.error import categorize_error, is_retryable
from src.reliability.logging import get_logger

logger = get_logger()

# 同步重试示例
config = RetryConfig.for_operation("navigation")
result = retry_operation(
    func=navigate_to_page,
    config=config,
    operation="navigation",
    url="https://example.com"
)

# 异步重试示例
import asyncio
result = await retry_operation_async(
    func=async_search,
    operation="search",
    query="python"
)

# 错误分类示例
try:
    do_something()
except Exception as e:
    category = categorize_error(e)
    if is_retryable(e):
        logger.warning(f"Retryable error: {category.value}")
    else:
        logger.critical(f"Non-retryable error: {category.value}")
```

---

## 9. 测试覆盖

相关测试文件:
- `tests/unit/test_reliability_retry.py` - 重试框架单元测试
- `tests/unit/test_reliability_error.py` - 错误分类单元测试
- `tests/unit/test_reliability_metrics.py` - 指标监控测试
- `tests/unit/test_reliability_health.py` - 健康检查测试

---

## 10. 评审检查清单

- [ ] 重试策略是否覆盖所有错误分类
- [ ] 重试次数是否合理（不过多也不过少）
- [ ] 退避算法是否适合对应场景
- [ ] 熔断器配置是否适当
- [ ] 不可恢复错误是否正确标记
- [ ] 文档是否清晰易懂
- [ ] 代码与文档是否一致

---

## 11. 更新历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| 1.0.0 | 2026-08-07 | 初始版本，定义重试策略和退避算法 |

---

**评审人**: ___________
**评审日期**: ___________
**评审结果**: ☐ 通过  ☐ 需修改  ☐ 驳回
