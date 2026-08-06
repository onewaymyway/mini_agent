# Browser-CDP 错误分类与处理规范

> 版本: 2.0.0
> 最后更新: 2026-08-07
> 状态: 待评审
> 关联代码: `src/reliability/error.py`, `src/reliability/retry.py`, `src/reliability/logging.py`

---

## 1. 概述

本文档定义了 browser-cdp skill 的错误分类标准和错误处理规范，是建立可靠网站操作能力的基础。所有错误处理逻辑均基于 `src/reliability/` 模块实现。

### 1.1 设计原则

| 原则 | 说明 |
|------|------|
| **可恢复性优先** | 区分可恢复错误和不可恢复错误，避免不必要的失败 |
| **渐进式重试** | 根据错误类型采用不同的重试策略（次数、延迟、退避方式） |
| **熔断保护** | 防止连续失败导致资源浪费和雪崩效应 |
| **可观测性** | 完整的错误日志、指标收集和告警机制 |
| **分类清晰** | 7 大类错误，每类有明确的触发场景和处理策略 |

### 1.2 错误分类总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        错误分类体系                              │
├──────────────┬──────────────┬──────────────┬───────────────────┤
│   CONNECTION │    TIMEOUT   │    ELEMENT   │   NAVIGATION      │
│   (可恢复)    │   (可恢复)    │   (可恢复)    │    (可恢复)        │
│   CDP连接断开 │   超时错误    │   元素相关    │   页面导航         │
│   WebSocket断│   智能等待降级│   选择器失效  │   重定向循环       │
│   浏览器崩溃  │              │   动态内容变化│                  │
├──────────────┼──────────────┼──────────────┼───────────────────┤
│    CONTENT   │  PERMISSION  │    UNKNOWN   │                   │
│   (不可恢复)  │  (不可恢复)   │   (视情况)    │                   │
│   验证码      │   反爬拦截    │   未分类异常  │                   │
│   人工验证    │   IP封禁     │   意外错误    │                   │
└──────────────┴──────────────┴──────────────┴───────────────────┘
```

---

## 2. 错误分类详解

### 2.1 CONNECTION - CDP 连接问题

**错误类型**: `CDPConnectionLostError`

**触发场景**:
- Chrome DevTools Protocol 连接断开
- WebSocket 连接中断
- 浏览器进程意外崩溃
- 端口被占用或释放

**可恢复性**: ✅ 可恢复

**重试策略**:
```python
{
    "max_retries": 5,
    "base_delay": 1.0,
    "backoff_strategy": "exponential_jitter",
    "circuit_breaker": True,
    "circuit_breaker_threshold": 5,
    "circuit_breaker_recovery": 30.0
}
```

**恢复动作**:
1. 尝试重新连接 CDP
2. 检查浏览器进程状态
3. 必要时重启浏览器实例
4. 记录连接恢复日志

**日志示例**:
```
[2026-08-07T10:00:00] WARNING [connection] Retry 1/5 after 1.0s: CDPConnectionLostError(details={'url': 'https://example.com'})
[2026-08-07T10:00:02] WARNING [connection] Retry 2/5 after 2.1s: CDPConnectionLostError(details={'url': 'https://example.com'})
[2026-08-07T10:00:05] INFO [connection] Connection restored successfully
```

---

### 2.2 TIMEOUT - 超时错误

**错误类型**:
- `CDPCommandTimeoutError` - CDP 命令执行超时
- `NetworkIdleTimeoutError` - networkidle 等待超时
- `SmartWaitDegradedError` - 智能等待所有策略失败

**触发场景**:
- 页面加载超时
- 元素等待超时
- CDP 命令执行超时
- 网络请求挂起

**可恢复性**: ✅ 可恢复

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 2.0,
    "backoff_strategy": "exponential",
    "circuit_breaker": True
}
```

**降级策略**:
| 阶段 | 行为 |
|------|------|
| 首次超时 | 重试 1 次 |
| 连续超时 | 切换到简单等待策略 |
| 多次超时 | 记录日志并继续执行（不阻塞） |

**日志示例**:
```
[2026-08-07T10:00:00] WARNING [timeout] Retry 1/3 after 2.0s: CDPCommandTimeoutError(command='Runtime.evaluate', timeout=30.0)
[2026-08-07T10:00:04] WARNING [timeout] Retry 2/3 after 4.0s: NetworkIdleTimeoutError(timeout=15.0, pending_requests=3)
[2026-08-07T10:00:12] ERROR [timeout] All 3 retries exhausted, degraded to simple wait
```

---

### 2.3 ELEMENT - 元素相关错误

**错误类型**:
- `ElementNotFoundError` - 元素未找到
- `ElementIndexInvalidError` - 元素编号无效

**触发场景**:
- CSS/XPath 选择器匹配不到元素
- 动态内容导致元素索引变化
- 页面结构变化
- 元素尚未渲染完成

**可恢复性**: ✅ 可恢复

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 0.5,
    "backoff_strategy": "linear",
    "circuit_breaker": False
}
```

**恢复动作**:
1. 重新扫描页面元素
2. 尝试备用选择器
3. 等待页面稳定后重试
4. 记录元素查找失败详情

**日志示例**:
```
[2026-08-07T10:00:00] WARNING [element] Retry 1/3 after 0.5s: ElementNotFoundError(selector='#submit-btn', strategy='css')
[2026-08-07T10:00:01] WARNING [element] Retry 2/3 after 1.0s: ElementNotFoundError(selector='#submit-btn', strategy='css')
[2026-08-07T10:00:03] INFO [element] Element found with fallback selector '.btn-submit'
```

---

### 2.4 NAVIGATION - 页面导航错误

**错误类型**: `NavigationTimeoutError`

**触发场景**:
- 页面跳转超时
- 目标 URL 不可达
- 重定向循环
- DNS 解析失败

**可恢复性**: ✅ 可恢复

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 2.0,
    "backoff_strategy": "exponential_jitter",
    "circuit_breaker": True
}
```

**恢复动作**:
1. 检查 URL 有效性
2. 清除页面缓存后重试
3. 尝试备用 URL
4. 记录导航失败详情

**日志示例**:
```
[2026-08-07T10:00:00] WARNING [navigation] Retry 1/3 after 2.0s: NavigationTimeoutError(url='https://example.com/page', timeout=30.0)
[2026-08-07T10:00:04] WARNING [navigation] Retry 2/3 after 4.1s: NavigationTimeoutError(url='https://example.com/page', timeout=30.0)
[2026-08-07T10:00:12] ERROR [navigation] All 3 retries exhausted for url='https://example.com/page'
```

---

### 2.5 CONTENT - 内容相关错误

**错误类型**: `CaptchaDetectedError`

**触发场景**:
- 检测到验证码（图形验证码、滑块验证等）
- 需要人工验证才能继续
- 安全验证页面

**可恢复性**: ❌ 不可恢复

**处理策略**:
1. 立即停止当前操作
2. 记录详细日志（截图、URL、时间戳）
3. 触发告警通知用户
4. 等待人工干预后重试

**日志示例**:
```
[2026-08-07T10:00:00] CRITICAL [content] Captcha detected on https://example.com/search
[2026-08-07T10:00:00] CRITICAL [content] Screenshot saved: logs/screenshots/captcha_20260807_100000.png
[2026-08-07T10:00:00] CRITICAL [content] Alert triggered: captcha_detected threshold=3, current=1
```

---

### 2.6 PERMISSION - 权限/拦截错误

**错误类型**: `BlockedByAntiBotError`

**触发场景**:
- 被反爬机制拦截
- IP 被封禁
- 浏览器指纹检测
- 行为分析异常

**可恢复性**: ❌ 不可恢复

**处理策略**:
1. 停止当前操作
2. 尝试更换代理
3. 调整请求头和行为模式
4. 通知用户人工确认

**日志示例**:
```
[2026-08-07T10:00:00] CRITICAL [permission] Anti-bot mechanism detected on https://example.com
[2026-08-07T10:00:00] CRITICAL [permission] Details: {'detected_by': 'behavior_analysis', 'ip': 'xxx.xxx.xxx.xxx'}
[2026-08-07T10:00:00] CRITICAL [permission] Alert triggered: anti_bot_detected threshold=1, current=1
```

---

### 2.7 UNKNOWN - 未知错误

**触发场景**:
- 未分类的异常
- 意外错误
- 第三方库异常

**可恢复性**: 视情况

**处理策略**:
1. 记录完整错误信息（类型、消息、堆栈）
2. 最多重试 1 次
3. 如果再次失败，抛出异常并通知用户
4. 归类到相应错误类型

**日志示例**:
```
[2026-08-07T10:00:00] ERROR [unknown] Unexpected error: TypeError('NoneType is not subscriptable')
[2026-08-07T10:00:00] ERROR [unknown] Traceback: ...
[2026-08-07T10:00:01] ERROR [unknown] Retry 1/1 failed, giving up
```

---

## 3. 错误分类规则表

| 分类 | 错误类型 | 可恢复性 | 最大重试 | 基础延迟 | 退避策略 | 熔断器 |
|------|----------|----------|----------|----------|----------|--------|
| CONNECTION | CDPConnectionLostError | ✅ | 5 | 1.0s | exponential_jitter | ✅ |
| TIMEOUT | CDPCommandTimeoutError | ✅ | 3 | 2.0s | exponential | ✅ |
| TIMEOUT | NetworkIdleTimeoutError | ✅ | 3 | 2.0s | exponential | ✅ |
| TIMEOUT | SmartWaitDegradedError | ✅ | 3 | 2.0s | exponential | ✅ |
| ELEMENT | ElementNotFoundError | ✅ | 3 | 0.5s | linear | ❌ |
| ELEMENT | ElementIndexInvalidError | ✅ | 3 | 0.5s | linear | ❌ |
| NAVIGATION | NavigationTimeoutError | ✅ | 3 | 2.0s | exponential_jitter | ✅ |
| CONTENT | CaptchaDetectedError | ❌ | 0 | - | - | ❌ |
| PERMISSION | BlockedByAntiBotError | ❌ | 0 | - | - | ❌ |
| UNKNOWN | 其他异常 | 视情况 | 1 | 1.0s | fixed | ❌ |

---

## 4. 重试策略配置

### 4.1 操作类型默认配置

基于 `RetryConfig.OPERATION_DEFAULTS`:

| 操作类型 | 最大重试次数 | 基础延迟 | 最大延迟 | 退避策略 | 熔断器 |
|----------|-------------|----------|----------|----------|--------|
| cdp_command | 5 | 1.0s | 30.0s | exponential_jitter | ✅ |
| element_find | 3 | 0.5s | 10.0s | linear | ❌ |
| navigation | 3 | 2.0s | 30.0s | exponential_jitter | ✅ |
| screenshot | 2 | 1.0s | 10.0s | fixed | ❌ |
| input_click | 3 | 1.0s | 10.0s | linear | ❌ |

### 4.2 退避策略详解

| 策略 | 公式 | 适用场景 |
|------|------|----------|
| FIXED | `min(base_delay, max_delay)` | 简单操作，如截图 |
| LINEAR | `min(base_delay * attempt, max_delay)` | 元素查找 |
| EXPONENTIAL | `min(base_delay ** attempt, max_delay)` | 超时类错误 |
| EXPONENTIAL_JITTER | `min(base_delay ** attempt, max_delay) * (0.5 + random())` | CDP 连接，避免雪崩 |

### 4.3 熔断器机制

**状态转换**:
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

**配置参数**:
- `failure_threshold`: 触发熔断的连续失败次数（默认 5 次）
- `recovery_timeout`: 恢复超时时间（默认 30 秒）

---

## 5. 日志记录规范

### 5.1 必须记录的信息

| 字段 | 说明 | 示例 |
|------|------|------|
| 时间戳 | ISO 8601 格式 | `2026-08-07T10:00:00` |
| 日志级别 | DEBUG/INFO/WARNING/ERROR/CRITICAL | `WARNING` |
| 错误分类 | CONNECTION/TIMEOUT/ELEMENT 等 | `connection` |
| 操作类型 | cdp_command/element_find 等 | `cdp_command` |
| 重试次数 | 当前重试次数/最大次数 | `1/5` |
| 延迟时间 | 本次重试延迟（秒） | `1.0` |
| 错误详情 | 选择器/URL/状态码等 | `selector='#btn'` |
| 堆栈信息 | 完整异常堆栈 | `Traceback...` |

### 5.2 日志级别使用规范

| 级别 | 使用场景 | 示例 |
|------|----------|------|
| DEBUG | 详细调试信息 | 重试延迟计算、选择器匹配过程 |
| INFO | 正常操作记录 | 操作开始/结束、连接恢复 |
| WARNING | 可恢复错误 | 重试中、降级策略触发 |
| ERROR | 不可恢复错误 | 重试耗尽、熔断器触发 |
| CRITICAL | 严重错误 | 验证码、反爬拦截 |

### 5.3 日志格式示例

**结构化日志（JSON）**:
```json
{
  "timestamp": "2026-08-07T10:00:00",
  "level": "WARNING",
  "logger": "browser_cdp.reliability.retry",
  "message": "[navigation] Retry 1/3 after 2.0s: NavigationTimeoutError(url='https://example.com', timeout=30.0)",
  "module": "retry",
  "function": "retry_operation",
  "line": 234,
  "data": {
    "operation": "navigation",
    "attempt": 1,
    "max_retries": 3,
    "delay": 2.0,
    "error_type": "NavigationTimeoutError",
    "error_message": "Navigation to 'https://example.com' timed out after 30.0s"
  }
}
```

---

## 6. 告警规则

基于 `config/alert_rules.json`:

| 规则ID | 名称 | 条件 | 阈值 | 严重性 | 冷却时间 | 通知方式 |
|--------|------|------|------|--------|----------|----------|
| retry_failure_rate | 重试失败率过高 | > | 30% | warning | 10分钟 | log, webhook |
| connection_loss_rate | 连接丢失率过高 | > | 20% | error | 5分钟 | log, webhook, email |
| error_count | 错误数量过多 | > | 50次 | warning | 5分钟 | log |
| circuit_breaker_trips | 熔断器频繁触发 | > | 5次 | error | 10分钟 | log, webhook |
| operation_duration | 操作耗时过长 | > | 300秒 | warning | 5分钟 | log |
| captcha_detected | 检测到验证码 | > | 3次 | critical | 30分钟 | log, webhook, email |
| anti_bot_detected | 被反爬机制拦截 | > | 1次 | critical | 60分钟 | log, webhook, email |

---

## 7. 错误处理流程图

```
操作执行
    ↓
捕获异常
    ↓
判断错误类型
    ↓
┌─────────────────────────────────────────────────────────────┐
│ 可恢复错误 → 检查重试次数                                    │
│   ├─ 有重试次数 → 计算退避延迟                              │
│   │              → 记录 WARNING 日志                        │
│   │              → 执行重试                                 │
│   │              → 成功 → 记录 INFO 日志，重置熔断器计数    │
│   │              → 失败 → 记录 ERROR 日志                   │
│   └─ 重试耗尽 → 触发熔断器（如启用）                        │
│                → 记录 ERROR 日志                            │
│                → 返回错误结果                               │
├─────────────────────────────────────────────────────────────┤
│ 不可恢复错误 → 记录 CRITICAL 日志                           │
│              → 触发告警（如验证码、反爬）                    │
│              → 停止操作                                     │
│              → 通知用户                                     │
└─────────────────────────────────────────────────────────────┘
    ↓
记录指标（metrics）
    ↓
返回结果
```

---

## 8. 网站适配建议

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

## 9. 最佳实践

### 9.1 错误处理流程

1. **捕获异常**: 使用 try-except 捕获所有操作异常
2. **分类错误**: 调用 `categorize_error()` 确定错误类型
3. **判断可恢复性**: 调用 `is_retryable()` 判断是否可重试
4. **执行重试**: 调用 `retry_operation()` 或 `retry_operation_async()`
5. **记录日志**: 使用 `OperationLogger` 记录结构化日志
6. **触发告警**: 调用告警系统处理严重错误

### 9.2 性能优化建议

1. **避免过度重试**: 设置合理的最大重试次数，避免无限重试
2. **使用指数退避**: 减少服务器压力，提高成功率
3. **启用熔断器**: 防止雪崩效应
4. **监控重试失败率**: 及时发现系统性问题
5. **合理设置超时**: 根据网站特点调整超时时间

### 9.3 代码使用示例

```python
from src.reliability.retry import retry_operation, RetryConfig
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

## 10. 测试覆盖

相关测试文件:
- `tests/unit/test_reliability_error.py` - 错误分类单元测试
- `tests/unit/test_reliability_retry.py` - 重试框架单元测试
- `tests/unit/test_reliability_metrics.py` - 指标监控测试
- `tests/unit/test_reliability_health.py` - 健康检查测试
- `tests/unit/test_reliability_logging.py` - 日志系统测试
- `tests/unit/test_reliability_middleware.py` - 中间件测试

---

## 11. 评审检查清单

- [ ] 错误分类是否完整覆盖所有场景（7 大类）
- [ ] 重试策略是否合理（次数、延迟、退避方式）
- [ ] 熔断器配置是否适当（阈值、恢复时间）
- [ ] 告警规则是否合理（阈值、冷却时间、通知方式）
- [ ] 日志格式是否统一（JSON 结构化）
- [ ] 测试覆盖是否充分（单元测试覆盖率 > 80%）
- [ ] 文档是否清晰易懂
- [ ] 代码与文档是否一致

---

## 12. 更新历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| 1.0.0 | 2026-08-06 | 初始版本，定义错误分类和重试策略 |
| 2.0.0 | 2026-08-07 | 完善错误处理规范，增加日志记录和告警规则 |

---

**评审人**: ___________
**评审日期**: ___________
**评审结果**: ☐ 通过  ☐ 需修改  ☐ 驳回

---

## 附录 A: 错误类型速查表

| 错误类名 | 分类 | 可恢复 | 最大重试 | 基础延迟 |
|----------|------|--------|----------|----------|
| CDPConnectionLostError | CONNECTION | ✅ | 5 | 1.0s |
| CDPCommandTimeoutError | TIMEOUT | ✅ | 3 | 2.0s |
| NetworkIdleTimeoutError | TIMEOUT | ✅ | 3 | 2.0s |
| SmartWaitDegradedError | TIMEOUT | ✅ | 3 | 2.0s |
| ElementNotFoundError | ELEMENT | ✅ | 3 | 0.5s |
| ElementIndexInvalidError | ELEMENT | ✅ | 3 | 0.5s |
| NavigationTimeoutError | NAVIGATION | ✅ | 3 | 2.0s |
| CaptchaDetectedError | CONTENT | ❌ | 0 | - |
| BlockedByAntiBotError | PERMISSION | ❌ | 0 | - |

---

## 附录 B: 退避策略速查表

| 策略 | 公式 | 适用场景 |
|------|------|----------|
| FIXED | `min(base, max)` | 截图等简单操作 |
| LINEAR | `min(base * n, max)` | 元素查找 |
| EXPONENTIAL | `min(base^n, max)` | 超时类错误 |
| EXPONENTIAL_JITTER | `min(base^n, max) * jitter` | CDP 连接 |

---

## 附录 C: 告警规则速查表

| 规则ID | 阈值 | 严重性 | 冷却时间 |
|--------|------|--------|----------|
| retry_failure_rate | > 30% | warning | 10分钟 |
| connection_loss_rate | > 20% | error | 5分钟 |
| error_count | > 50次 | warning | 5分钟 |
| circuit_breaker_trips | > 5次 | error | 10分钟 |
| operation_duration | > 300秒 | warning | 5分钟 |
| captcha_detected | > 3次 | critical | 30分钟 |
| anti_bot_detected | > 1次 | critical | 60分钟 |
