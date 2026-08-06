# Browser-CDP 错误分类与重试策略

> 版本: 1.0.0
> 最后更新: 2026-08-06
> 状态: 待评审

## 1. 概述

本文档定义了 browser-cdp skill 的错误分类标准和重试策略，旨在建立可靠的网站操作能力，支持更多领域的网站抓取和浏览。

### 1.1 设计目标

- **可恢复性优先**: 区分可恢复错误和不可恢复错误
- **渐进式重试**: 根据错误类型采用不同的重试策略
- **熔断保护**: 防止连续失败导致资源浪费
- **可观测性**: 完整的错误日志和告警机制

### 1.2 错误分类体系

基于 `src/reliability/error.py` 中的 `ErrorCategory` 枚举，错误分为以下类别：

| 分类 | 错误类型 | 可恢复性 | 处理策略 |
|------|----------|----------|----------|
| CONNECTION | CDP连接断开 | ✅ 可恢复 | 重建连接 + 重试 |
| TIMEOUT | 超时错误 | ✅ 可恢复 | 重试（1次）或降级 |
| ELEMENT | 元素相关 | ✅ 可恢复 | 重新扫描元素或等待 |
| NAVIGATION | 页面导航 | ✅ 可恢复 | 重试导航 |
| CONTENT | 验证码 | ❌ 不可恢复 | 停止 + 通知用户 |
| PERMISSION | 反爬拦截 | ❌ 不可恢复 | 停止 + 通知用户 |
| UNKNOWN | 未知错误 | 视情况 | 记录日志 + 最多重试1次 |

## 2. 错误类型详解

### 2.1 CONNECTION - CDP连接问题

**错误类型**: `CDPConnectionLostError`

**触发场景**:
- Chrome DevTools Protocol 连接断开
- WebSocket 连接中断
- 浏览器进程崩溃

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
2. 如果连接失败，检查浏览器进程状态
3. 必要时重启浏览器实例

### 2.2 TIMEOUT - 超时错误

**错误类型**:
- `CDPCommandTimeoutError` - CDP命令超时
- `NetworkIdleTimeoutError` - networkidle等待超时
- `SmartWaitDegradedError` - 智能等待降级

**触发场景**:
- 页面加载超时
- 元素等待超时
- CDP命令执行超时

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
- 首次超时: 重试1次
- 连续超时: 切换到简单等待策略
- 多次超时: 记录日志并继续执行（不阻塞）

### 2.3 ELEMENT - 元素相关错误

**错误类型**:
- `ElementNotFoundError` - 元素未找到
- `ElementIndexInvalidError` - 元素编号无效

**触发场景**:
- 选择器匹配不到元素
- 动态内容导致元素索引变化
- 页面结构变化

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

## 2.4 NAVIGATION - 页面导航错误

**错误类型**: `NavigationTimeoutError`

**触发场景**:
- 页面跳转超时
- 目标URL不可达
- 重定向循环

**重试策略**:
```python
{
    "max_retries": 3,
    "base_delay": 2.0,
    "backoff_strategy": "exponential_jitter",
    "circuit_breaker": True
}
```

## 2.5 CONTENT - 内容相关错误

**错误类型**: `CaptchaDetectedError`

**触发场景**:
- 检测到验证码
- 需要人工验证

**处理策略**:
- ❌ **不可恢复**
- 立即停止操作
- 记录详细日志（截图、URL、时间）
- 通知用户人工干预

## 2.6 PERMISSION - 权限/拦截错误

**错误类型**: `BlockedByAntiBotError`

**触发场景**:
- 被反爬机制拦截
- IP被封禁
- 浏览器指纹检测

**处理策略**:
- ❌ **不可恢复**
- 停止当前操作
- 尝试更换代理
- 调整请求头和行为模式
- 通知用户

## 2.7 UNKNOWN - 未知错误

**触发场景**:
- 未分类的异常
- 意外错误

**处理策略**:
- 记录完整错误信息
- 最多重试1次
- 如果再次失败，抛出异常并通知用户

## 3. 重试策略配置

### 3.1 操作类型默认配置

基于 `RetryConfig.OPERATION_DEFAULTS`:

| 操作类型 | 最大重试次数 | 基础延迟 | 熔断器 |
|----------|-------------|----------|--------|
| cdp_command | 5 | 1.0s | ✅ 启用 |
| element_find | 3 | 0.5s | ❌ 禁用 |
| navigation | 3 | 2.0s | ✅ 启用 |
| screenshot | 2 | 1.0s | ❌ 禁用 |
| input_click | 3 | 1.0s | ❌ 禁用 |

### 3.2 退避策略

支持四种退避策略:

1. **FIXED**: 固定延迟
   ```python
   delay = min(base_delay, max_delay)
   ```

2. **LINEAR**: 线性增长
   ```python
   delay = min(base_delay * attempt, max_delay)
   ```

3. **EXPONENTIAL**: 指数增长
   ```python
   delay = min(base_delay ** attempt, max_delay)
   ```

4. **EXPONENTIAL_JITTER**: 指数增长 + 随机抖动（推荐）
   ```python
   delay = min(base_delay ** attempt, max_delay) * (0.5 + random.random())
   # 抖动范围: 50% ~ 150%
   ```

### 3.3 熔断器机制

**状态转换**:
```
closed → open (连续失败达到阈值)
open → half_open (等待恢复超时)
half_open → closed (试探成功)
half_open → open (试探失败)
```

**配置参数**:
- `failure_threshold`: 触发熔断的连续失败次数（默认5次）
- `recovery_timeout`: 恢复超时时间（默认30秒）

## 4. 告警规则

基于 `config/alert_rules.json`:

| 规则ID | 名称 | 条件 | 阈值 | 严重性 | 冷却时间 |
|--------|------|------|------|--------|----------|
| retry_failure_rate | 重试失败率过高 | > | 30% | warning | 10分钟 |
| connection_loss_rate | 连接丢失率过高 | > | 20% | error | 5分钟 |
| error_count | 错误数量过多 | > | 50次 | warning | 5分钟 |
| circuit_breaker_trips | 熔断器频繁触发 | > | 5次 | error | 10分钟 |
| operation_duration | 操作耗时过长 | > | 300秒 | warning | 5分钟 |
| captcha_detected | 检测到验证码 | > | 3次 | critical | 30分钟 |
| anti_bot_detected | 被反爬机制拦截 | > | 1次 | critical | 60分钟 |

## 5. 最佳实践

### 5.1 错误处理流程

```
操作执行
    ↓
捕获异常
    ↓
判断错误类型
    ↓
┌─────────────────────────────────────┐
│ 可恢复错误 → 检查重试次数           │
│   ├─ 有重试次数 → 计算退避延迟      │
│   │              → 记录日志         │
│   │              → 执行重试         │
│   └─ 重试耗尽 → 触发熔断器          │
│                → 记录告警           │
│                → 返回错误           │
├─────────────────────────────────────┤
│ 不可恢复错误 → 记录详细日志         │
│              → 触发告警             │
│              → 停止操作             │
│              → 通知用户             │
└─────────────────────────────────────┘
```

### 5.2 日志记录规范

**必须记录的信息**:
- 错误类型和分类
- 操作类型（cdp_command, element_find等）
- 重试次数和延迟
- 错误详情（选择器、URL、状态码等）
- 时间戳

**示例日志**:
```
[2026-08-06 05:19:32] WARNING [navigation] Retry 1/3 after 2.0s: NavigationTimeoutError(url='https://example.com', timeout=30.0)
[2026-08-06 05:19:34] WARNING [navigation] Retry 2/3 after 4.0s: NavigationTimeoutError(url='https://example.com', timeout=30.0)
[2026-08-06 05:19:38] ERROR [navigation] All 3 retries exhausted
```

### 5.3 网站适配建议

针对不同网站类型，建议调整重试策略:

| 网站类型 | 特点 | 建议重试次数 | 建议延迟 |
|----------|------|-------------|----------|
| 新闻网站 | 静态内容，加载快 | 2-3次 | 1-2s |
| 电商网站 | 动态加载，可能有反爬 | 3-5次 | 2-3s |
| 招聘网站 | 需要登录，结构复杂 | 3次 | 1-2s |
| 社交媒体 | 大量动态内容 | 3-5次 | 2-4s |
| 学术网站 | 相对稳定 | 2-3次 | 1-2s |

### 5.4 性能优化建议

1. **避免过度重试**: 设置合理的最大重试次数，避免无限重试
2. **使用指数退避**: 减少服务器压力，提高成功率
3. **启用熔断器**: 防止雪崩效应
4. **监控重试失败率**: 及时发现系统性问题

## 6. 测试覆盖

相关测试文件:
- `tests/unit/test_reliability_retry.py` - 重试框架单元测试
- `tests/unit/test_reliability_error.py` - 错误分类单元测试
- `tests/unit/test_reliability_metrics.py` - 指标监控测试
- `tests/unit/test_reliability_health.py` - 健康检查测试

## 7. 评审检查清单

- [ ] 错误分类是否完整覆盖所有场景
- [ ] 重试策略是否合理（次数、延迟、退避方式）
- [ ] 熔断器配置是否适当
- [ ] 告警规则是否合理
- [ ] 日志格式是否统一
- [ ] 测试覆盖是否充分
- [ ] 文档是否清晰易懂

## 8. 更新历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| 1.0.0 | 2026-08-06 | 初始版本，定义错误分类和重试策略 |

---

**评审人**: ___________
**评审日期**: ___________
**评审结果**: ☐ 通过  ☐ 需修改  ☐ 驳回
