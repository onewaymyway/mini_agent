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

### 轻量调用计数（方向 B.2）

在 B.1 只读暴露状态之外，"这个 daemon 今天到底调用了多少次 LLM、大概
花了多少 token"这类最基础的量级问题此前完全答不出来——`llm/
debug_logger.py` 需要手动设置 `LLM_DEBUG=1` 才会记录，且落盘的是完整
请求/响应正文（为调试排障设计，不是为统计设计）。

新增 `llm/call_stats.py`，跟调试日志是两套独立的东西：**默认开启**，
每次调用只记数字（provider/model/输入输出 token 数/耗时/结果分类：
成功/失败/key 切换/配置切换），不含任何请求/响应正文。挂载点是
`agent/llm_control.py::_call_llm()`（主对话循环）和 `llm/service.py::
LLMHelper.chat()`（judge/ensemble/目标拆解等场景），这是仅有的两处
`LLMClientPool.call_with_pool()` 调用点，覆盖了系统里发生的所有 LLM
调用。

写入策略是"攒批"，不是每次调用都落盘：内存缓冲区攒够 10 条或超过 30
秒未落盘才真正写一次文件，降低密集工具循环场景下的 I/O 开销。记录失败
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

## 相关文件

| 文件 | 说明 |
|------|------|
| `providers.json` | **敏感配置**（API key），加入 .gitignore，不提交 |
| `providers.json.example` | 配置模板，可提交到 git 供团队参考 |
| `src/mini_agent/llm/client_pool.py` | ApiKeyPool、ProviderEntry、LLMClientPool 实现 |
| `src/mini_agent/config.py` | `_load_providers_config`、`_merge_providers_into_chain`、`load_config` |
| `src/mini_agent/agent.py` | `_call_llm` 通过 `LLMClientPool` 调用 |
| `docs/retry-backoff-guide.md` | 重试退避策略（与本机制配合使用） |
| `src/mini_agent/perception/sentinel.py` | `read_llm_pool_snapshot()`：把 `snapshot()` 转成 `GET /v1/self/llm_pool_status` 的响应结构 |
| `src/mini_agent/llm/call_stats.py` | 轻量调用计数（方向 B.2）：攒批写入 + 按天聚合 + 降采样压缩 |
| `next_doc/kanban_perception_gaps_improvement_plan.md` | 方向 B.1（故障转移状态暴露）/ B.2（调用计数）设计与排查记录 |
