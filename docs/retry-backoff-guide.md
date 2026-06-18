# LLM 重试退避策略指南

## 概述

mini-agent 的 LLM 调用层内置了重试机制，当大模型返回空响应、发生网络异常、或 API 超时时会自动重试。

从 v0.x 起，重试等待时长支持三种**退避策略（Backoff Strategy）**：

| 策略 | 关键词 | 说明 |
|------|--------|------|
| 固定等待 | `fixed` | 每次重试等待相同时长（默认） |
| 线性递增 | `linear` | 每次等待在上次基础上增加固定秒数 |
| 指数递增 | `exponential` | 每次等待乘以固定倍数 |

---

## 快速上手

### 命令行参数

```bash
# 使用线性退避，初始等待 10s，每次 +60s，上限 300s
mini-agent --retry-backoff linear --retry-backoff-step 60 --retry-backoff-max 300

# 使用指数退避，初始等待 5s，每次 ×1.5，上限 120s
mini-agent --retry-backoff exponential --retry-backoff-step 1.5 --retry-backoff-max 120

# 固定等待（默认），每次等 30s
mini-agent --retry-backoff fixed
```

第一次重试的等待时长由全局参数 `--llm-retry-delay`（或配置文件 `llm_retry_delay`）决定，默认为 **5 秒**。

### 配置文件（agent_config.json）

```json
{
  "llm_retry_max": 10,
  "llm_retry_delay": 10,
  "llm_retry_backoff_mode": "exponential",
  "llm_retry_backoff_step": 1.5,
  "llm_retry_backoff_max_delay": 300
}
```

---

## 三种策略详解

### 1. fixed — 固定等待（默认）

每次重试等待时长固定不变，适合不清楚具体情况时的保守策略。

```
等待序列（delay=10s）：
  第1次: 10s
  第2次: 10s
  第3次: 10s
```

### 2. linear — 线性递增

等待时长随重试次数线性增长，适合"频率限制"类错误（如 API 的 429 Too Many Requests）。

**计算公式：**
```
delay(n) = initial + (n - 1) × step
```

```
示例（initial=10, step=60, max_delay=300）：
  第1次: 10s
  第2次: 70s
  第3次: 130s
  第4次: 190s
  第5次: 250s
  第6次: 300s（受 max_delay 限制）
```

CLI 参数：
- `--retry-backoff-step 60` → 每次增加 60 秒

### 3. exponential — 指数递增

等待时长按倍数增长，适合需要"让服务器充分恢复"的严重错误场景。

**计算公式：**
```
delay(n) = min(initial × multiplier^(n-1), max_delay)
```

```
示例（initial=5, multiplier=2.0, max_delay=300）：
  第1次:  5s
  第2次: 10s
  第3次: 20s
  第4次: 40s
  第5次: 80s
  第6次: 160s
  第7次: 300s（受 max_delay 限制）

示例（initial=10, multiplier=1.5）：
  第1次: 10.0s
  第2次: 15.0s
  第3次: 22.5s
  第4次: 33.8s
```

CLI 参数：
- `--retry-backoff-step 1.5` → 每次乘以 1.5（multiplier 必须 > 1.0）

---

## 参数参考

### CLI 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--retry-backoff` | `fixed\|linear\|exponential` | `fixed` | 退避策略模式 |
| `--retry-backoff-step` | float | `60.0` | linear: 每次递增秒数；exponential: 倍数（>1.0） |
| `--retry-backoff-max` | float | `0` | 等待时长上限（秒），0 = 不限制 |

以下参数与退避策略配合使用：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--llm-retry-max` | int | `15` | 最大重试次数 |
| `--llm-retry-delay` | float | `5.0` | 第一次重试的基础等待时间（秒） |

### 配置文件字段（agent_config.json）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llm_retry_max` | int | `15` | 最大重试次数 |
| `llm_retry_delay` | float | `5.0` | 第一次重试基础等待时长（秒） |
| `llm_retry_backoff_mode` | string | `"fixed"` | 退避策略模式 |
| `llm_retry_backoff_step` | float | `60.0` | linear: 步长；exponential: 倍数 |
| `llm_retry_backoff_max_delay` | float | `0.0` | 等待时长上限（0=不限） |

---

## 状态栏实时显示

重试等待期间，状态栏会显示当前倒计时和重试进度：

```
⏳ Retry 2/5  [████░░░░░░]  43.2s remaining
```

字段说明：
- `2/5` — 当前是第 2 次重试，最多重试 5 次
- `[████░░░░░░]` — 进度条（等待已消耗的比例）
- `43.2s remaining` — 本次等待剩余秒数

---

## 代码中使用

直接构造 `BackoffStrategy` 对象：

```python
from mini_agent.llm.retry import (
    FixedBackoff, LinearBackoff, ExponentialBackoff,
    RetryPolicy, default_retry_policy, parse_backoff,
    EmptyOutputCondition,
)

# 方式一：直接构造策略对象
policy = RetryPolicy(
    max_retries=5,
    backoff=ExponentialBackoff(initial=5.0, multiplier=1.5, max_delay=120.0),
    conditions=[EmptyOutputCondition()],
    retry_on_exception=True,
)

# 方式二：用 parse_backoff 从字符串构造（适合从配置读取）
backoff = parse_backoff(
    mode="linear",
    initial=10.0,
    step_or_multiplier=60.0,
    max_delay=300.0,
)
policy = default_retry_policy(max_retries=10, backoff=backoff)

# 运行
response = policy.call_with_retry(
    call_fn=lambda: llm.chat(messages),
    on_retry=lambda attempt, reason: print(f"retry {attempt}: {reason}"),
)
```

### parse_backoff 签名

```python
def parse_backoff(
    mode: str,                    # "fixed" | "linear" | "exponential"
    initial: float,               # 第一次重试的等待秒数
    step_or_multiplier: float,    # linear: 步长(s)；exponential: 倍数
    max_delay: float = 0.0,       # 上限（0=不限）
) -> BackoffStrategy: ...
```

### 向后兼容

旧代码传入 `retry_delay=N` 仍然有效，等价于 `backoff=FixedBackoff(N)`：

```python
# 旧写法（仍然有效）
policy = RetryPolicy(max_retries=3, retry_delay=10.0)

# 新写法（推荐）
policy = RetryPolicy(max_retries=3, backoff=FixedBackoff(10.0))
```

---

## 选择策略的建议

| 场景 | 推荐策略 | 示例配置 |
|------|----------|----------|
| 轻度偶发错误（默认） | `fixed` | `delay=5` |
| API 频率限制（429 错误） | `linear` | `delay=10, step=60, max=300` |
| 严重过载或服务不稳定 | `exponential` | `delay=5, step=2.0, max=300` |
| 后台长时间任务 | `exponential` | `delay=10, step=1.5, max=600` |

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/mini_agent/llm/retry.py` | BackoffStrategy 体系、RetryPolicy 核心实现 |
| `src/mini_agent/config.py` | RetryConfig 配置数据类 |
| `src/mini_agent/cli/parser.py` | CLI 参数定义 |
| `src/mini_agent/agent.py` | Agent 中 retry_policy 初始化 |
