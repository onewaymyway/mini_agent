# LLM 多配置故障转移 & 多 Key 轮转指南

## 概述

mini-agent 提供两层互补的容错机制，解决 LLM 调用的可靠性问题：

| 机制 | 解决的问题 | 触发时机 |
|------|-----------|---------|
| **ApiKeyPool** 多 key 轮转 | 单 provider 的 API key 频率限制 | 遇到 `RateLimitError`，立即切换，无等待 |
| **LLMClientPool** 配置 fallback | provider 完全不可用、跨 provider 降级 | 当前配置所有重试用完仍失败 |

两层机制独立配置，可单独或组合使用。

---

## 配置文件分离

API key 等敏感信息存放在**独立的 `providers.json`** 中，与普通配置 `agent_config.json` 分离。

```
项目根目录/
├── agent_config.json      # 通用配置（模型、功能开关等）← 可提交到 git
├── providers.json         # Provider & API key 配置    ← 加入 .gitignore ❌
└── providers.json.example # 配置模板                   ← 可提交到 git
```

`providers.json` 在项目初始化时自动被加入 `.gitignore`，不会被意外提交。

---

## 快速上手

### 步骤一：复制模板

```bash
cp providers.json.example providers.json
```

### 步骤二：填写真实 key

```json
// providers.json
{
  "llm_fallback_chain": [
    {
      "provider": "anthropic",
      "model": "claude-opus-4-7",
      "api_keys": ["sk-ant-real-key-1", "sk-ant-real-key-2"],
      "key_rotation": "passive"
    },
    {
      "provider": "openai",
      "model": "gpt-4o",
      "api_key": "sk-openai-real-key"
    }
  ]
}
```

### 步骤三：启动（自动发现）

```bash
mini-agent   # 自动读取项目根目录的 providers.json
```

或显式指定路径：

```bash
mini-agent --providers-config /secure/path/providers.json
```

---

## providers.json 结构

### 方式一：直接写 fallback chain（推荐）

```json
{
  "llm_fallback_chain": [
    {
      "provider": "anthropic",
      "model": "claude-opus-4-7",
      "api_keys": ["sk-ant-aaa", "sk-ant-bbb"],
      "key_rotation": "passive",
      "key_switch_on": ["LLMRateLimitError"],
      "key_cooldown": 60
    },
    {
      "provider": "openai",
      "model": "gpt-4o",
      "api_key": "sk-openai-backup"
    }
  ],
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"]
}
```

### 方式二：providers 全局块（key 统一管理）

当多条 chain 条目共用同一 provider 的 key 时，用 `providers` 块统一管理，避免重复：

```json
{
  "providers": {
    "anthropic": {
      "api_keys": ["sk-ant-aaa", "sk-ant-bbb"],
      "key_rotation": "round_robin",
      "key_cooldown": 60
    },
    "openai": {
      "api_keys": ["sk-openai-111", "sk-openai-222"]
    }
  },
  "llm_fallback_chain": [
    { "provider": "anthropic", "model": "claude-opus-4-7" },
    { "provider": "anthropic", "model": "claude-haiku-4-5" },
    { "provider": "openai",    "model": "gpt-4o" }
  ]
}
```

`providers` 块中的设置会自动合并到 `llm_fallback_chain` 的对应条目中。chain 条目中**显式指定的字段优先级更高**，会覆盖全局设置。

### 优先级

```
CLI 参数  >  agent_config.json  >  providers.json chain[0]  >  环境变量  >  内置默认值
```

三个核心参数（`provider`、`model`、`api_key`）均遵循此顺序。这意味着：

- 有了 `providers.json`，**不需要设置 `ANTHROPIC_API_KEY` 等环境变量**
- 有了 `providers.json`，**`agent_config.json` 里可以不写 `model`、`provider`**，直接继承主配置的值
- `agent_config.json` 里的 `model`/`provider` 仍然可以覆盖 `providers.json` 的值（如需临时切换模型）

**最简配置示例**：只有 `providers.json`，`agent_config.json` 只写功能开关：

```json
// providers.json（含 key，不提交 git）
{
  "llm_fallback_chain": [
    { "provider": "anthropic", "model": "claude-opus-4-7", "api_key": "sk-ant-..." }
  ]
}

// agent_config.json（无敏感信息，可提交 git）
{
  "memory_enabled": true,
  "session_summary_enabled": true,
  "max_llm_calls": 4
}
```

---

## 多 Key 轮转（ApiKeyPool）

### 轮转策略

#### `passive`（被动切换，默认）

正常情况只使用当前 key；遇到触发条件才切换。

```
请求 1: key-A ✓
请求 2: key-A ✓
请求 3: key-A → RateLimitError → 立即切换到 key-B
请求 4: key-B ✓
请求 5: key-B ✓
```

适合场景：key 数量少（2-3个）、请求不密集。

#### `round_robin`（主动轮询）

每次请求自动轮转到下一个可用 key，均匀分摊 RPM 配额。

```
请求 1: key-A ✓
请求 2: key-B ✓
请求 3: key-C ✓
请求 4: key-A ✓  ← 循环
请求 5: key-B ✓
```

适合场景：key 数量多（3个以上）、请求密集，需要均匀利用配额。

### key 切换触发条件（`key_switch_on`）

默认只在 `LLMRateLimitError` 时切换。可扩展为其他错误类型：

```json
{
  "key_switch_on": ["LLMRateLimitError", "LLMConfigError"]
}
```

支持的错误类名：
- `LLMRateLimitError` — API 频率超限（默认包含）
- `LLMConfigError` — 认证失败（如 key 无效或已吊销）
- `LLMTimeoutError` — 请求超时
- `LLMProviderError` — 通用 provider 错误

### key 冷却时间（`key_cooldown`）

被切换的 key 进入冷却期，期间不会被使用。默认 60 秒。

```json
{
  "key_cooldown": 120
}
```

若所有 key 均在冷却中，等待最早恢复的 key，再触发整体配置的 fallback 逻辑。

---

## 配置 Fallback 链（LLMClientPool）

### fallback_chain 结构

`llm_fallback_chain` 是一个有序列表，**第一条是主配置**，后续条目依次为备用。

```json
{
  "llm_fallback_chain": [
    { /* 主配置，第一优先 */ },
    { /* 备用配置 1 */ },
    { /* 备用配置 2，最后兜底 */ }
  ]
}
```

若 `llm_fallback_chain` 为空或未配置，退化为只使用主配置（`ANTHROPIC_API_KEY` + 当前 model）的单条链，行为与旧版本完全兼容。

### 每条配置支持的字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | string | 主配置的 provider | provider 名称（anthropic / openai / deepseek 等） |
| `model` | string | 主配置的 model | 模型名称 |
| `api_key` | string | 对应 provider 的环境变量 | 单个 API key |
| `api_keys` | list | — | 多个 API key（启用 ApiKeyPool） |
| `base_url` | string | — | 自定义 API 地址（兼容 OpenAI 协议的第三方服务） |
| `max_tokens` | int | 主配置值 | 最大输出 token 数 |
| `temperature` | float | 0.0 | 温度参数 |
| `timeout` | int | 120 | 请求超时（秒） |
| `key_rotation` | string | `"passive"` | key 轮转策略（`passive` / `round_robin`） |
| `key_switch_on` | list | `["LLMRateLimitError"]` | 触发 key 切换的错误类名称 |
| `key_cooldown` | float | 60.0 | key 冷却时间（秒） |

### fallback 触发条件（`llm_fallback_on`）

控制什么错误会触发切换到下一条配置。默认值：

```json
{
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"]
}
```

`LLMConfigError`（认证失败）默认**不**触发 fallback，因为换一套配置通常无法解决认证问题（除非两套配置使用不同 provider）。若需要，可显式加入：

```json
{
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError", "LLMConfigError"]
}
```

---

## 环境变量 API Key 映射

各 provider 的默认环境变量：

| Provider | 环境变量 |
|----------|---------|
| `anthropic` / `claude` | `ANTHROPIC_API_KEY` |
| `openai` / `azure` | `OPENAI_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `moonshot` | `MOONSHOT_API_KEY` |
| `qwen` | `DASHSCOPE_API_KEY` |
| `groq` | `GROQ_API_KEY` |
| `together` | `TOGETHER_API_KEY` |
| `fireworks` | `FIREWORKS_API_KEY` |
| `nvidia` / `nim` | `NVIDIA_API_KEY` |

在 fallback chain 的条目中省略 `api_key` / `api_keys` 时，自动从对应环境变量读取。

---

## 用户可见的切换提示

发生 key 切换时，状态栏和日志会打印：

```
⚠ [key-switch] ...ant-aaa → ...ant-bbb (LLMRateLimitError)
```

发生配置 fallback 时：

```
⚠ [llm-fallback] anthropic/claude-opus-4-7 → openai/gpt-4o (LLMRateLimitError: 429 Too Many Requests)
```

---

## 完整两层调用流程

```
_call_llm()
  └── LLMClientPool.call_with_pool()
        ├── entry[0]: anthropic/claude-opus-4-7
        │     ApiKeyPool: [key-A, key-B]
        │       ├── 尝试 key-A → RateLimitError → key 切换到 key-B（立即，无等待）
        │       ├── 尝试 key-B → RateLimitError → 所有 key 冷却
        │       └── RetryPolicy 重试预算耗尽 → entry[0] 整体失败
        │
        └── entry[1]: openai/gpt-4o              ← llm_fallback_chain 切换
              ApiKeyPool: [key-X]
                └── 尝试 key-X → 成功 ✓
```

---

## 代码中使用

```python
from mini_agent.llm.client_pool import ApiKeyPool, LLMClientPool, ProviderEntry
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client

# 构建 fallback 链
entries = []
for cfg_dict in [
    {"provider": "anthropic", "model": "claude-opus-4-7",
     "api_keys": ["sk-ant-aaa", "sk-ant-bbb"]},
    {"provider": "openai", "model": "gpt-4o", "api_key": "sk-openai-xxx"},
]:
    llm_cfg = LLMConfig(provider=cfg_dict["provider"], model=cfg_dict["model"],
                        api_key=cfg_dict.get("api_key",""), ...)
    key_pool = None
    if len(cfg_dict.get("api_keys", [])) > 1:
        key_pool = ApiKeyPool(keys=cfg_dict["api_keys"], rotation="passive")
    entries.append(ProviderEntry(config=llm_cfg, client=create_client(llm_cfg),
                                 key_pool=key_pool))

pool = LLMClientPool(entries=entries)

# 从 AppConfig 自动构建（推荐）
pool = LLMClientPool.from_config(cfg)
```

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `providers.json` | **敏感配置**（API key），加入 .gitignore，不提交 |
| `providers.json.example` | 配置模板，可提交到 git 供团队参考 |
| `src/mini_agent/llm/client_pool.py` | ApiKeyPool、ProviderEntry、LLMClientPool 实现 |
| `src/mini_agent/config.py` | `_load_providers_config`、`_merge_providers_into_chain`、`load_config` |
| `src/mini_agent/agent.py` | `_call_llm` 通过 `LLMClientPool` 调用 |
| `docs/retry-backoff-guide.md` | 重试退避策略（与本机制配合使用） |
