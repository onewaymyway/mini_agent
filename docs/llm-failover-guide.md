# LLM 多配置故障转移 & 多 Key 轮转指南

## 概述

mini-agent 提供两层互补的容错机制，解决 LLM 调用的可靠性问题：

| 机制 | 解决的问题 | 触发时机 |
|------|-----------|---------|
| **ApiKeyPool** 多 key 轮转 | 单 provider 的 API key 频率限制 | 默认按“在途请求最少”分摊到各 key（`least_loaded`）；遇到 `RateLimitError` 立即切换，并把限流状态跨进程共享 |
| **LLMClientPool** 配置 fallback | provider 完全不可用、跨 provider 降级；账号级 403 | 当前配置所有重试用完仍失败；或遇到 `LLMPermanentError`（401/403） |

两层机制覆盖的调用范围见 [覆盖范围与后台调用](#覆盖范围与后台调用)：主对话、后台摘要/画像/记忆回填/压缩、cron 与 Objective 子 Agent、以及 `override_*` 调用，都会经过同一条 fallback 链。

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

#### `least_loaded`（最少在途优先，默认）

每次请求从**未冷却**的 key 中选择当前**在途请求最少**的一把；在途数相同时选择**最久未被使用**的。
并发任务（主对话、子任务、cron、外部项目）会自然分摊到全部 key 上，不再集中在第一把。

```
任务 A 请求: key-A（在途 0 → 1）
任务 B 请求: key-B（key-A 在途 1，key-B 在途 0）
任务 C 请求: key-C
任务 A 完成: key-A 在途归 0，下一次请求会优先选中它
```

每次调用都会登记一个**租约**，调用结束（无论成功或失败）即释放。租约 900 秒后自动失效，
进程崩溃遗留的租约不会永久占用负载。遇到 `key_switch_on` 中的错误时，被限流的 key 进入冷却，
冷却期间不再被选中。

适合场景：绝大多数场景，尤其是多任务、多进程并发。状态跨进程共享，见 [跨进程共享状态](#跨进程共享状态)。

#### `passive`（被动切换）

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

> **注意**：`LLMPermanentError`（HTTP 401/403：key 失效、账号被限制/封禁）无论 `key_switch_on`
> 怎么写都会触发换 key —— 同一把 key 重试不会成功，池里有其它 key 时应立即换 key；
> 所有 key 都被冷却后才会 fallback 到下一个 provider，且不会在失效 key 上做退避重试。
> 但如果 403 是**账号级**的（如 `We've detected unusual activity on your account`），
> 同一账号下的所有 key 都会被拒绝，换 key 无效，必须切换到**不同 provider** 的条目，
> 参见 [同一 provider 多条目与账号级 403](#同一-provider-多条目与账号级-403)。
> `llm_fallback_on` / `key_switch_on` 按类名匹配且**包含父类**：写 `LLMProviderError`
> 同样会命中其子类（如 `LLMPermanentError`）；`LLMContextWindowError` 除外（需触发 compact，不切换）。

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

## 跨进程共享状态

`least_loaded` 的在途计数与冷却状态写入文件，同一主项目下的所有进程共享：

```
<主项目根>/.agent/llm_key_state.json        状态
<主项目根>/.agent/llm_key_state.json.lock   文件锁
```

**哪些调用共享这份状态**：主对话、cron 与 Objective 子任务、orchestrator 的 SubAgent、
LLMHelper 旁路调用，以及以子进程运行的外部项目——只要它们解析到同一个主项目根。

**主项目根的解析顺序**：

1. 外部项目（根目录存在 `project.yaml`）：先读取环境变量 `MINI_AGENT_MAIN_PROJECT_ROOT`，
   再查外部项目注册表（`~/.agent/external_projects.json` 中的 `main_project_root`）。
2. 主项目：就是它自己的项目根。
3. 解析不到主项目根时（如嵌入式使用、测试）：退化为进程内内存状态，不跨进程。

> ⚠️ **外部项目需要先注册**。未注册的外部项目会解析不到主项目根，状态写入它自己目录下的
> `.agent/llm_key_state.json`，与主项目**不共享**限流状态。这种情况下它仍能正常轮转，只是看不到主项目的在途负载。
> 当前 daemon 不会设置 `MINI_AGENT_MAIN_PROJECT_ROOT`，因此外部项目目前依赖注册表。

**安全与可靠性**：

- 文件里只保存 key 的 SHA-256 摘要（16 位），**不含明文 key**。
- 读写期间持有文件锁（POSIX `flock`，Windows `msvcrt`），写入先写临时文件再原子替换。
- 文件损坏或无法解析时按空状态处理，不影响调用本身。

**已知边界**：

- 状态按 **key** 记录，不区分模型。同一把 key 上不同模型的限流会相互影响。
- 租约超过 900 秒仍未释放时会被视为失效，只影响负载统计，不影响正在进行的调用。
- 要重置全部轮转状态，在相关进程停止后删除上述文件即可。

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
| `key_rotation` | string | `"least_loaded"` | key 轮转策略（`least_loaded` / `passive` / `round_robin`）。`least_loaded` 状态跨进程共享；`passive` / `round_robin` 仅在当前进程内生效 |
| `key_switch_on` | list | `["LLMRateLimitError"]` | 触发 key 切换的错误类名称 |
| `key_cooldown` | float | 60.0 | key 冷却时间（秒） |

### fallback 触发条件（`llm_fallback_on`）

控制什么错误会触发切换到下一条配置。默认值：

```json
{
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"]
}
```

未在 `providers.json` 中配置 `llm_fallback_on` 时，内置默认值为 `LLMRateLimitError`、`LLMTimeoutError`、`LLMProviderError`、`LLMPermanentError`（403 等永久错误也会切换）。

> ⚠️ **显式配置会覆盖默认值**。如果你写了 `llm_fallback_on`，请确保包含 `LLMPermanentError`，或至少包含它的父类 `LLMProviderError`（按父类名匹配，会命中子类），否则 403 不会切换到下一条配置。推荐直接写明：`["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError", "LLMPermanentError"]`。

`LLMConfigError`（认证失败）默认**不**触发 fallback，因为换一套配置通常无法解决认证问题（除非两套配置使用不同 provider）。若需要，可显式加入：

```json
{
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError", "LLMConfigError"]
}
```

---

## 覆盖范围与后台调用

fallback 链只有在调用真正经过 `LLMClientPool` 时才有效。下面是各类调用的覆盖情况（修复后）：

| 调用方 | 入口 | 是否走 fallback 链 |
|--------|------|-------------------|
| 主对话循环 | `agent._llm.chat_with_retry` / `_call_llm` → `call_with_pool` | ✅ |
| 会话摘要、用户画像、记忆回填、压缩、反思、主动推荐等后台任务 | 持有 pool 托管 client 的 `chat_with_retry(...)` | ✅（基类转发给 pool） |
| LLMHelper 旁路调用（judge、ensemble、目标拆解、路由判定…） | `LLMHelper.chat/ask` → `call_with_pool` | ✅ |
| `override_model` / `override_provider` 调用 | 先用临时 client；失败后回落到默认链 | ✅（失败时回落） |
| cron 任务子 Agent | `cron_agent_bridge` → `attach_fallback_pool` | ✅（修复前为单条链） |
| Objective 子 Agent | `objective_agent_bridge` → `attach_fallback_pool` | ✅（修复前为单条链） |
| SubAgent（orchestrator） | `sub_agent` → `attach_fallback_pool` | ✅（修复前为单条链） |

### 实现要点

- **基类转发**：`LLMClient.chat_with_retry()`（`llm/base.py`）发现 client 被 pool 托管（`client._fallback_pool` 已设置）时，直接转发给 `LLMClientPool.chat_with_retry()`，由 pool 执行重试与 fallback。调用方不需要知道 pool 的存在。
- **不会递归**：pool 内部调用的是 `client.chat()`，不会再回到 `chat_with_retry`。
- **子 Agent 的首选配置不变**：`attach_fallback_pool(agent, cfg, llm_cfg)` 把 `llm_cfg`（任务指定的 model 等覆盖项）作为首选，再把 `llm_fallback_chain` 中其余条目接在后面，因此不会改变任务原本使用的模型。
- **子任务的多 key 池**：`build_pool_with_primary`（cron / Objective / SubAgent 共用）会让首选配置“认领”链上持有其 api_key 的多 key 池，因此子任务同样享有 key 轮转；被认领的条目不会在备选中重复出现。此前这些子任务只使用第一把 key。
- **旧的单链行为**：只有在 `llm_fallback_chain` 为空时，才退化为只含主配置的单条链。

> 如果你在新代码里仍然看到 `LLMConfig.from_app_config(cfg)` + `create_client()` 直接发起调用，那是绕过 fallback 的信号，应改用 `LLMHelper` 或 `attach_fallback_pool`。

---

## 同一 provider 多条目与账号级 403

同一 provider 下只换 key 的多个条目，**不能**解决账号级限制：

```json
{
  "llm_fallback_chain": [
    { "provider": "agnes", "model": "agnes-2.5-flash", "api_key": "key-1" },
    { "provider": "agnes", "model": "agnes-2.5-flash", "api_key": "key-2" }
  ]
}
```

如果 `key-1` 和 `key-2` 属于同一个账号，403 `unusual activity` 会同时拒绝两把 key。修复前，这种配置会让日志出现 `agnes/agnes-2.5-flash → agnes/agnes-2.5-flash` 的假切换，请求仍然失败。

**正确做法**：至少在链上加入一个**不同 provider**（或不同账号）的备用条目：

```json
{
  "llm_fallback_chain": [
    { "provider": "agnes", "model": "agnes-2.5-flash", "api_key": "key-1" },
    { "provider": "anthropic", "model": "claude-haiku-4-5", "api_key": "sk-ant-..." }
  ],
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError", "LLMPermanentError"]
}
```

加载时的自动检查（`LLMClientPool.from_config`）：

| 情况 | 行为 |
|------|------|
| provider、model、base_url、key **完全相同**的重复条目 | 自动忽略，并打印 `忽略重复条目` 警告 |
| 同 provider、同 model、同 base_url，只有 key 不同 | 保留（多 key 可以应对限流），但打印警告：账号级 403 换 key 无效，应加入不同 provider 的条目 |

---

## 重复条目与日志 label

`ProviderEntry.label` 现在带上 key 后缀，格式为 `provider/model#末 4 位 key`，例如 `agnes/agnes-2.5-flash#9f3c`。这样同 model、不同 key 的条目在日志里可以区分，不会再出现看似没有切换的 `a/m → a/m`。没有 key 的条目（如本地模型）不带后缀。

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
| `openrouter` | `OPENROUTER_API_KEY` |
| `agnes` | `AGNES_API_KEY` |

在 fallback chain 的条目中省略 `api_key` / `api_keys` 时，自动从对应环境变量读取。

### 自动注入机制

`load_config()` 在解析完 `providers.json` 后，会立即调用 `inject_env_from_providers()`，
将文件中配置的 `api_key` / `api_keys[0]` **自动写入**对应的标准环境变量（如 `AGNES_API_KEY`、`NVIDIA_API_KEY`）。

**规则：**
- 只补充**当前进程中尚不存在**的环境变量，不覆盖用户已手动 `export` 的值
- 注入顺序：先遍历 `llm_fallback_chain`，再遍历 `providers` 块；同一 provider 先遇到的 key 优先
- 实现位置：`src/mini_agent/llm/client_pool.py` 中的 `inject_env_from_providers()`

**效果：**只需在 `providers.json` 中配置一次 key，各 provider 实现、第三方库、子进程工具均可通过标准环境变量读取，无需重复传参。

```json
// providers.json 示例 — 配置 agnes 后，AGNES_API_KEY 自动可用
{
  "providers": {
    "agnes": { "api_key": "agnes-xxx" },
    "nvidia": { "api_key": "nvapi-xxx" }
  }
}
```

---

## 用户可见的切换提示

发生 key 切换时，状态栏和日志会打印：

```
⚠ [key-switch] ...ant-aaa → ...ant-bbb (LLMRateLimitError)
```

发生配置 fallback 时：

```
⚠ [llm-fallback] anthropic/claude-opus-4-7#k2b9 → openai/gpt-4o#7a1d (LLMRateLimitError: 429 Too Many Requests)
```

label 中的 `#xxxx` 是 key 的末 4 位，用于区分同一 provider/model 下的不同 key。

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

后台任务（摘要 / 画像 / 回填 / cron 子 Agent 等）
  └── client.chat_with_retry()  （pool 托管的 client 会转发给 pool）
        └── LLMClientPool.chat_with_retry() → call_with_pool()   同上流程
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
        key_pool = ApiKeyPool(keys=cfg_dict["api_keys"], rotation="least_loaded")  # 默认策略；跨进程共享需传入 store，见 _key_store_for
    entries.append(ProviderEntry(config=llm_cfg, client=create_client(llm_cfg),
                                 key_pool=key_pool))

pool = LLMClientPool(entries=entries)

# 从 AppConfig 自动构建（推荐）
pool = LLMClientPool.from_config(cfg)
```

---

## 运行时查看与切换

### 查看当前配置的所有模型

```
/provider models
```

列出 fallback chain 中所有已配置的条目，标记当前正在使用的：

```
Configured models  (active: anthropic/claude-opus-4-7)

   #  Provider     Model                         Keys  Rotation
   ─────────────────────────────────────────────────────────────
●  1  anthropic    claude-opus-4-7               2     passive
○  2  openai       gpt-4o                        1     passive

  ● = currently active · Keys = number of API keys in rotation
```

- `●` 标记当前活跃条目（`_current_idx`，可能因 fallback 已切到非第一条）
- `Keys` 列显示该条目配置的 API key 数量
- `Rotation` 列显示轮转策略（`passive` / `round_robin` / `—`）

### 运行时切换 provider

```
/provider switch openai gpt-4o
```

调用 `agent.switch_to_provider_default(provider, model)`：

1. 先在当前 `LLMClientPool` 的 fallback chain 中查找匹配的条目：
   - 给了 `model`：要求 provider + model 都匹配；
   - 不给 `model`（`/provider switch openai`）：使用该 provider 在 chain 中
     出现的**第一条**，即该 provider 的"默认模型"。
   - 命中后直接切换 `_current_idx`，复用该条目早已就绪的 client，**不会**
     重建，也**不会**丢弃 fallback chain 中的其他条目。
2. 若 chain 中完全没有该 provider 的条目：从标准环境变量（如
   `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 等）解析 api key，构造一条全新的
   `LLMConfig`，创建 client 后作为新条目追加进 chain 并激活。

`/provider models` 之后再次确认：fallback chain 的其余条目仍然保留，只是
`_current_idx` 指向了新的活跃条目。

### 切换模型（保持当前 provider，除非该模型属于另一 provider）

```
/model claude-haiku-4-5
```

调用 `agent.switch_model(model)`：

1. 先在 fallback chain 中查找模型名匹配的已配置条目——若找到，直接切换
   过去，连带使用其对应的 provider 与 api key（不重建 client）。
2. 若 chain 中没有这个模型，则在**当前 provider** 下用新的模型名构造一条
   新的 `LLMConfig`（沿用当前 api_key / base_url 等其余字段），创建新
   client，作为新条目追加进 chain 并激活。

这保证了 `/model` 不再只是改一个不会被实际读取的 `cfg.model` 字符串——
后续真正发出的 LLM 调用（`agent._call_llm` → `LLMClientPool.call_with_pool`
→ `LLMClientPool.current_client`）会使用切换后的 client。

### `/model` vs `/provider switch` 的区别

| | `/model <name>` | `/provider switch <name> [model]` |
|---|---|---|
| 改变的对象 | `LLMClientPool` 当前激活条目（`_current_idx`）+ 对应 client | 同上 |
| 查找范围 | 按 model 名在 fallback chain 中查找 | 按 provider（+可选 model）在 fallback chain 中查找 |
| 找不到时 | 在**当前 provider** 下新建条目 | 解析对应 provider 的环境变量 api key，新建条目 |
| API key | 命中已有条目则带着该条目的 key；新建条目则沿用当前 key | 命中已有条目则带着该条目的 key；新建条目则从环境变量重新读取 |
| LLMClientPool | fallback chain 始终保留，只追加/切换，不坍缩 | 同上 |
| 对话历史 | 保留 | 保留 |

> **注**：`Agent` 上还保留着更底层的 `switch_provider(llm_config)` 方法（供编程/测试场景直接传入完整 `LLMConfig` 使用），它会把整个 `LLMClientPool` 重建为单条链，丢弃原有 fallback chain。`/model` 与 `/provider switch` 这两个 CLI 命令不会调用它，而是调用 `switch_model()` / `switch_to_provider_default()`，二者都不会丢弃 fallback chain 中的其他条目。

### daemon 模式下的可观测性（看板 / API）

`/provider models` 是 CLI 内的命令，只在本地直跑或已连接的 REPL 里可用。
daemon 模式下，`LLMClientPool.snapshot()` / `ApiKeyPool.snapshot()` 这两个
早就实现好的方法此前完全没有被任何 HTTP 端点暴露过——daemon 在后台因为
某个 provider 频繁触发限流而不断切 key/切配置时，看板用户没有任何渠道
知道这件事正在发生，只能等到所有 fallback 都耗尽、彻底报错的那一刻才
会注意到（详见 `next_doc/kanban_perception_gaps_improvement_plan.md`
方向 B.1 的排查记录）。

现在新增只读端点 `GET /v1/self/llm_pool_status`，返回：

```json
{
  "entries": [
    {"label": "anthropic/claude-opus-4-7", "active": true,
     "keys": [{"key_suffix": "...abcd", "available": true,
               "cooldown_remaining": 0.0, "fail_count": 1}]},
    {"label": "openai/gpt-4o", "active": false}
  ],
  "current": 0,
  "switched_from_preferred": false,
  "enabled": true
}
```

- `switched_from_preferred`：`current != 0` 的简化标记，即当前是否已经
  不在 fallback chain 的第一条（首选）配置上——这是用户最想第一眼看到
  的信号，不需要自己数第几个 entry 是 active。
- 没有配置 `llm_fallback_chain`（只用单一配置）或 agent 未就绪时，
  `enabled` 为 `false`，其余字段为空，不是错误。

看板"🧠 自我状态"Tab 新增"🔀 LLM 故障转移状态"区块直接展示这份数据；
`switched_from_preferred=true` 时还会出现在顶栏"⚠️ 系统状态哨兵"聚合面板
里（见 `docs/kanban-dashboard-guide.md`）。这一步不涉及任何新增持久化，
纯粹是把已经在内存里的状态读出来展示。

### API Key 轮转状态（看板）

看板"🧠 自我状态"tab 中，"🔀 LLM 故障转移状态"下方新增"🔄 API Key 轮转状态"区块：

- 顶部指标：Key 总数、可用数、在途请求数、冷却中数量；全部冷却时给出错误提示，部分冷却时给出警告。
- 每个多 key 池一张表：key 末尾 8 位、可用/冷却状态、在途请求数、失败次数、冷却剩余秒数、最近使用时间。
- `least_loaded` 池标注为"🔗 跨进程共享"，在途数来自共享状态文件，因此子任务、cron、外部项目的请求都能看到；
  `passive` / `round_robin` 池标注为"⚠️ 仅本进程"，在途数显示为 `—`。

数据来源是只读端点 `GET /v1/self/key_rotation_status`。该端点不记录历史，只反映当前时刻的状态；
"最近一小时切换次数"等趋势信息请参考上方的调用统计。

### 轻量调用计数（方向 B.2）

在 B.1 只读暴露状态之外，"这个 daemon 今天到底调用了多少次 LLM、大概
花了多少 token"这类最基础的量级问题此前完全答不出来——`llm/
debug_logger.py` 需要手动设置 `LLM_DEBUG=1` 才会记录，且落盘的是完整
请求/响应正文（为调试排障设计，不是为统计设计）。

新增 `llm/call_stats.py`，跟调试日志是两套独立的东西：**默认开启**，
每次调用只记数字（provider/model/输入输出 token 数/耗时/结果分类：
成功/失败/key 切换/配置切换），不含任何请求/响应正文。

**挂载点（已统一）**：计数只在 `LLMClientPool.call_with_pool()` 一处记录，
所有 LLM 调用（主对话、LLMHelper、后台任务、子 Agent、外部项目、skill）都经过它，
因此不会重复计数。旧版本的两处挂载点（`_call_llm` 与 `LLMHelper.chat`）已移除记录逻辑。

写入策略是"攒批"，不是每次调用都落盘：内存缓冲区攒够 10 条或超过 30
秒未落盘才真正写一次文件，降低密集工具循环场景下的 I/O 开销。

**文件有界与接口性能（2026-11 修复超时）**：

- 每累计 5000 条追加记录，后台线程自动压缩一次，超过 7 天的原始记录压缩为每日汇总行。修复前压缩函数从未被调用，文件会无限增长。
- `GET /v1/self/llm_call_stats` 从文件末尾倒序读取，只读取查询窗口内的记录，耗时只与窗口大小有关，与历史总量无关（约 50MB 的文件，7 天查询约 0.1 秒）。
- 接口在线程池中执行，且 `series` 与 `breakdown` 只读取一次文件。
- 倒序读取假设文件大致按时间追加（批量刷盘的乱序幅度远小于 1 小时余量），这对本项目的写入方式成立。记录失败
（包括拿不到 project_root 之类的边界情况）一律静默忽略——调用计数是
锦上添花的可观测性增强，绝不能因为这里出问题影响真正的 LLM 调用主
链路，这一点在两处挂载点和 `call_stats.record_call()` 内部都做了三层
兜底。

原始逐条记录只保留最近 7 天（`_RAW_WINDOW_DAYS`），更早的记录会被
`compact_call_stats_storage()` 压缩成按天求和的汇总行，避免文件无限
增长——调用方式跟 `evolution/growth_advisor.py::compact_health_trend_
storage()` 一致，但聚合语义不同（健康度快照是"取当天最新一条"，调用
计数是"当天全部记录求和"，因为次数/token 数天然需要累加而不是取某个
时间点的瞬时值）。当前实现**没有**接一个自动定期调用压缩函数的调度点
（不像 `growth_health_trend` 挂在 `run_daily_cycle()` 上），查询接口
`call_stats_series()` 本身在内存里重新聚合、不依赖压缩是否发生过，
所以晚一点压缩不影响展示正确性，只是文件会先涨到一定大小再被压缩——
这是刻意的取舍：本期先验证有没有人真的关心这份数据，暂不新增一个
调度点。

新增只读端点 `GET /v1/self/llm_call_stats?days=7`，返回按天聚合的
序列。看板"🧠 自我状态"Tab 新增"📊 LLM 调用统计"区块（调用次数/失败数
柱状图 + 当日汇总指标）。

---

### 调用来源与项目拆分

每条记录带两个标签，看板可以按它们拆分：

| 字段 | 取值 | 含义 |
|------|------|------|
| `source` | `agent` / `helper` / `skill` / `background` / `cron` / `objective` / `subagent` | 谁发起的调用 |
| `origin` | `main` / `external:<项目目录名>` | 调用发生在哪个项目 |

来源的确定规则（优先级从高到低）：

1. 调用点用 `call_stats.call_source("skill")` 显式标记的来源（外层标记优先，`call_source_if_unset` 不覆盖外层）。
   - `capability_call` 工具（skill 入口）会把其中所有 LLM 调用标记为 `skill`；
   - `LLMHelper.chat()` 默认标记为 `helper`（judge、ensemble、目标拆解、路由判定等）；
   - `LLMClientPool.chat_with_retry()` 默认标记为 `background`（摘要、画像、记忆回填、压缩、反思等）。
2. 没有标记时，使用 pool 的默认来源：主 Agent 为 `agent`，cron / Objective / SubAgent 子 Agent 分别为 `cron` / `objective` / `subagent`（由 `attach_fallback_pool(..., source=...)` 设置）。

`origin` 由 cfg 所属项目决定：

- 主项目：`main`，写入主项目 `.agent/llm_call_stats.jsonl`；
- 外部项目（存在 `project.yaml`）：`external:<项目目录名>`，**写入主项目的计数文件**（通过 `MINI_AGENT_MAIN_PROJECT_ROOT` 或外部项目注册表解析主项目根），因此主看板可以看到外部项目的调用。主项目根解析不到时，退回写入外部项目自己的 `.agent/`，数据不丢，但主看板看不到。

已知限制：

- contextvar 不跨线程传播。skill 内部另起线程运行的子 Agent，其调用会使用它自己 pool 的默认来源（`subagent`），而不是 `skill`。
- 每日压缩（超过 7 天）后的汇总行没有来源信息，在 `by_source` / `by_origin` 中归入 `legacy`。

### 兼容性

- 旧版本写入的记录没有 `source` / `origin` 字段，读取时归入 `legacy`，不报错、不丢统计。
- `GET /v1/self/llm_call_stats` 的 `series` 字段口径不变；新增的 `breakdown` 字段（`by_source` / `by_origin`）是纯新增，旧前端忽略即可。
- 外部项目在旧版本下写入各自 `.agent/llm_call_stats.jsonl` 的历史数据**不会被迁移**到主项目，仍留在原位置。
- 看板在后端不返回 `breakdown` 时整块跳过，不报错。

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `providers.json` | **敏感配置**（API key），加入 .gitignore，不提交 |
| `providers.json.example` | 配置模板，可提交到 git 供团队参考 |
| `src/mini_agent/llm/client_pool.py` | ApiKeyPool、ProviderEntry、LLMClientPool 实现；`build_pool_with_primary`、`attach_fallback_pool` |
| `src/mini_agent/llm/key_state.py` | `least_loaded` 的共享状态：租约、冷却、文件后端（跨进程）与内存后端 |
| `src/mini_agent/config.py` | `_load_providers_config`、`_merge_providers_into_chain`、`load_config` |
| `src/mini_agent/agent/llm_control.py` | `_call_llm` 通过 `LLMClientPool.call_with_pool` 调用，并同步 `cfg.model` / `cfg.llm_provider` |
| `src/mini_agent/llm/base.py` | `LLMClient.chat_with_retry`：pool 托管时转发给 pool |
| `src/mini_agent/llm/service.py` | `LLMHelper`；`_chat_override` 失败时回落到默认链 |
| `src/mini_agent/evolution/cron_agent_bridge.py` / `objective_agent_bridge.py` / `orchestrator/sub_agent.py` | 子 Agent 构造后调用 `attach_fallback_pool` 接入 fallback 链 |
| `docs/retry-backoff-guide.md` | 重试退避策略（与本机制配合使用） |
| `src/mini_agent/perception/sentinel.py` | `read_llm_pool_snapshot()`：把 `snapshot()` 转成 `GET /v1/self/llm_pool_status` 的响应结构 |
| `src/mini_agent/llm/call_stats.py` | 轻量调用计数（方向 B.2）：攒批写入 + 按天聚合 + 降采样压缩 |
| `next_doc/kanban_perception_gaps_improvement_plan.md` | 方向 B.1（故障转移状态暴露）/ B.2（调用计数）设计与排查记录 |

---

## 变更记录

**2026-11：修复 fallback 未生效的几处路径**

- 后台调用（摘要、画像、记忆回填、压缩等）原先直接持有 provider client 调用 `chat_with_retry`，403/限流时既不换 key 也不换 provider。现在由基类转发给 pool。
- cron 与 Objective 子 Agent、SubAgent 原先只用单个 client，整条 `llm_fallback_chain` 被忽略。现在通过 `attach_fallback_pool` 接入。
- `override_*` 调用失败后原先直接抛出，现在回落到默认链。
- `from_config` 忽略完全相同的重复条目；同 provider/model 只换 key 的条目会打警告。
- `ProviderEntry.label` 增加 key 后缀，日志可区分同 model 的不同 key。
- 文档修正：显式写 `llm_fallback_on` 时需包含 `LLMPermanentError`，否则 403 不会切换。

**2026-11：调用计数按来源 / 项目拆分**

- 计数统一挂在 `LLMClientPool.call_with_pool()`，移除了 `_call_llm` 与 `LLMHelper.chat` 中的重复记录。
- 新增 `source` / `origin` 字段与 `breakdown` 返回值，旧数据兼容（归入 `legacy`）。
- override 临时 client 的调用也会计数（修复前不计入）。
- 外部项目的调用写入主项目计数文件，origin 标记为 `external:<项目名>`。
- skill 入口（`capability_call`）标记为 `skill`；后台摘要、画像、回填等标记为 `background`；子 Agent 标记为 `cron` / `objective` / `subagent`。
- 修复看板"读取失败：ReadTimeout"：接口改为窗口读取 + 线程池执行 + 单次读取；文件由自动压缩保持有界。

**2026-11：least_loaded 轮转与跨进程共享**

- 新增 `key_rotation: "least_loaded"`（在途请求最少优先），并设为默认；`passive` / `round_robin` 保持原有行为。
- 每把 key 的在途计数（租约）与冷却状态写入 `<主项目根>/.agent/llm_key_state.json`，同一主项目下的所有进程共享，文件仅保存 key 摘要。
- 修复子任务（cron / Objective / SubAgent）只使用第一把 key 的问题：`build_pool_with_primary` 现在认领链上的多 key 池。
- 外部项目的 LLM 调用接入同一套机制（需已注册，见“跨进程共享状态”）。

**2026-11：看板增加 API Key 轮转状态区块**

- 新增只读端点 `GET /v1/self/key_rotation_status` 与看板区块，展示每个多 key 池的 key 级状态（least_loaded 池读取跨进程共享状态）。
- `least_loaded` 状态快照新增 `last_used` 字段。

