# 配置系统指南

> 说明 mini-agent 的配置架构、子配置块、加载优先级与扩展方式。

---

## 1. 架构概览

配置系统采用**子配置块（Feature Config）**架构：`AppConfig` 主体只持有核心字段，各功能域通过独立的子配置类聚合。

```
AppConfig
├── model / api_key / project_root ...  ← 核心字段（直接持有）
├── memory:     MemoryConfig            ← 记忆功能
├── compress:   CompressConfig          ← 历史压缩
├── tool_trim:  ToolTrimConfig          ← 工具结果截断
├── skill:      SkillConfig             ← Skill 系统
├── perception: PerceptionConfig        ← 感知（扫描/监听/缓存/token）
├── session:    SessionConfig           ← Session 持久化
├── debug:      DebugConfig             ← 调试日志
├── http:       HttpConfig              ← HTTP API 服务
├── retry:      RetryConfig             ← LLM 调用重试
└── mcp:        MCPConfig               ← MCP 外部工具服务
```

**设计原则**：新增功能只需新建子配置类，`AppConfig` 主体不变。

---

## 2. 子配置块详解

### MemoryConfig

```python
@dataclass
class MemoryConfig:
    enabled: bool = False
    backend: str = "local"             # "local" | "chroma" | "redis"（扩展点）
    store_path: Optional[Path] = None  # None = <project_root>/.agent/memory.jsonl
    top_k: int = 3
    decay_half_life_days: float = 30.0
    max_entries: int = 500
```

| 字段 | 说明 |
|------|------|
| `backend` | 指定记忆后端实现，对应 `memory_factory.py` 注册表中的 key |
| `decay_half_life_days` | 时间衰减半衰期，30 天后旧记忆检索分数衰减 50% |
| `max_entries` | 超出后淘汰最旧条目并重写文件 |

### CompressConfig

```python
@dataclass
class CompressConfig:
    enabled: bool = False
    threshold: float = 0.7             # token 占用率超过此值触发压缩
    strategy: str = "turn_aligned"     # 压缩策略名，对应 history/compression.py 注册表
    forget_orphan_tool_results: bool = False
```

| 字段 | 说明 |
|------|------|
| `strategy` | `"turn_aligned"`（默认）/ `"llm_summary"` / `"sliding_window"` / 自定义注册名 |
| `forget_orphan_tool_results` | 压缩后是否剔除保留段中无对应 tool_use 的 tool_result |

### PerceptionConfig

```python
@dataclass
class PerceptionConfig:
    project_scan_enabled: bool = False
    file_watch_enabled: bool = False
    tool_cache_enabled: bool = False
    tool_cache_max_entries: int = 256  # LRU 容量上限
    token_estimate_enabled: bool = False
    token_warn_threshold: float = 0.75
    tool_stats_enabled: bool = False
```

注意：`tool_cache_max_entries` 是 v2 新增字段，控制 `ToolResultCache` 的 LRU 容量上限。

### SkillConfig

```python
@dataclass
class SkillConfig:
    semantic_enabled: bool = False
    semantic_threshold: float = 0.72
    tracking_enabled: bool = False
    chunking_enabled: bool = False
    compact_budget: int = 25_000
    compact_per_skill: int = 5_000
    matcher: str = "keyword"           # "keyword" | "ngram" | "semantic"（扩展点）
```

### SessionConfig

```python
@dataclass
class SessionConfig:
    dir: Optional[Path] = None
    fmt: str = "json"                  # "json" | "jsonl"
    auto_save: bool = True
    summary_enabled: bool = False
    summary_min_turns: int = 4
    search_enabled: bool = False
    backend: str = "local"             # 预留扩展点
```

### MCPConfig

```python
@dataclass
class MCPConfig:
    servers: list[MCPServerConfig] = field(default_factory=list)

@dataclass
class MCPServerConfig:
    name: str                          # server 唯一标识
    transport: str = "stdio"           # "stdio" | "sse"
    command: str = ""                  # stdio 专用：可执行命令
    args: list[str] = field(...)       # stdio 专用：命令行参数
    env: dict[str, str] = field(...)   # stdio 专用：额外环境变量
    url: str = ""                      # sse 专用：SSE endpoint
    auto_approve: bool = False         # 此 server 所有工具免审批
    timeout: float = 10.0             # 连接与调用超时（秒）
    enabled: bool = True              # False 时跳过
```

`MCPConfig` 通过 `agent_config.json` 的 `mcp_servers` 数组配置（非平坦 key，使用嵌套对象数组）：

```json
{
  "mcp_servers": [
    {
      "name": "time_server",
      "transport": "stdio",
      "command": "python",
      "args": ["mcp_servers/time_server.py"],
      "auto_approve": true
    }
  ]
}
```

详见 [MCP 集成指南](mcp-guide.md)。

### RetryConfig

控制 LLM 调用的自动重试行为，包括重试次数、基础等待时长和退避策略。

```python
@dataclass
class RetryConfig:
    max_retries: int = 15          # 最大重试次数
    delay: float = 5.0             # 第一次重试的基础等待时间（秒）
    verbose: bool = True           # 是否打印重试日志
    backoff_mode: str = "fixed"    # "fixed" | "linear" | "exponential"
    backoff_step: float = 60.0     # linear: 每次递增秒数；exponential: 倍数（>1.0）
    backoff_max_delay: float = 0.0 # 等待时长上限（秒），0 = 不限制
```

**退避策略说明：**

| `backoff_mode` | 等待时长计算 | 典型场景 |
|----------------|-------------|---------|
| `fixed` | 每次固定 `delay` 秒（默认） | 一般偶发错误 |
| `linear` | `delay, delay+step, delay+2×step, …` | API 频率限制（429） |
| `exponential` | `delay, delay×step, delay×step², …` | 服务过载恢复 |

**JSON 配置示例：**

```json
{
  "llm_retry_max": 10,
  "llm_retry_delay": 10,
  "llm_retry_backoff_mode": "exponential",
  "llm_retry_backoff_step": 1.5,
  "llm_retry_backoff_max_delay": 300
}
```

**CLI 参数：**

```bash
mini-agent --retry-backoff linear --retry-backoff-step 60 --retry-backoff-max 300
```

详见 [LLM 重试退避策略指南](retry-backoff-guide.md)。

---

## 3. 配置加载优先级

```
JSON 配置文件  >  命令行参数  >  环境变量  >  内置默认值
```

> ⚠️ 注意：JSON 配置文件优先级**高于**命令行参数，与部分 CLI 工具的习惯相反。

### 3.1 JSON 配置文件

自动查找项目根目录下的 `agent_config.json`，或通过 `--config` 指定路径。

字段名与子配置块**字段同名**（使用平坦 key），`load_config` 内部组装为子配置块：

```json
{
  "model": "claude-opus-4-5",
  "memory_enabled": true,
  "memory_top_k": 5,
  "memory_backend": "local",
  "memory_decay_half_life_days": 30.0,
  "memory_max_entries": 500,
  "auto_compress_enabled": true,
  "auto_compress_strategy": "turn_aligned",
  "tool_cache_enabled": true,
  "tool_cache_max_entries": 256,
  "skill_tracking_enabled": true
}
```

### 3.2 环境变量

| 环境变量 | 对应字段 |
|---------|---------|
| `ANTHROPIC_API_KEY` | `api_key` |
| `CLAUDE_MODEL` | `model` |
| `LLM_PROVIDER` | `llm_provider` |
| `LLM_BASE_URL` | `llm_base_url` |
| `LLM_DEBUG` | `debug.llm_enabled` |
| `SESSION_DIR` | `session.dir` |
| `MAX_LLM_CALLS` | `max_llm_calls` |

---

## 4. 代码中使用配置

### 4.1 新写法（子配置块）

```python
# 推荐：通过子配置块访问，语义更清晰
if cfg.memory.enabled:
    backend = create_memory_backend(cfg)

if cfg.compress.enabled and cfg.compress.strategy == "llm_summary":
    ...

max_entries = cfg.perception.tool_cache_max_entries
decay = cfg.memory.decay_half_life_days
```

### 4.2 旧写法（仍可用）

```python
# 向后兼容：通过 @property 代理到子块，渐进迁移期间无需修改
if cfg.memory_enabled:        # 等价于 cfg.memory.enabled
    ...
if cfg.tool_cache_enabled:    # 等价于 cfg.perception.tool_cache_enabled
    ...
```

---

## 5. 构建自定义 AppConfig

```python
from mini_agent.config import (
    AppConfig, MemoryConfig, CompressConfig,
    SkillConfig, PerceptionConfig, load_config
)

# 方式一：直接构造（测试场景）
cfg = AppConfig(
    model="claude-opus-4-5",
    memory=MemoryConfig(enabled=True, top_k=5),
    compress=CompressConfig(enabled=True, strategy="llm_summary"),
    perception=PerceptionConfig(tool_cache_enabled=True, tool_cache_max_entries=512),
)

# 方式二：通过 load_config（生产场景，自动读取文件/环境变量）
cfg = load_config(
    memory_enabled=True,
    memory_top_k=5,
    auto_compress_enabled=True,
)
```

---

## 6. 添加新功能配置

新增功能只需三步，无需修改 `AppConfig` 主体：

**步骤一**：新建子配置类

```python
@dataclass
class MyFeatureConfig:
    enabled: bool = False
    param_a: int = 10
    param_b: str = "default"
```

**步骤二**：在 `AppConfig` 中加入子块引用

```python
@dataclass
class AppConfig:
    ...
    my_feature: MyFeatureConfig = field(default_factory=MyFeatureConfig)
```

**步骤三**：在 `load_config` 中从 JSON/CLI 组装

```python
my_feature_cfg = MyFeatureConfig(
    enabled=_fb("my_feature_enabled", None),
    param_a=_fn("my_feature_param_a", None, 10),
)
```

---

## 7. 相关文档

- [MCP 集成指南](mcp-guide.md) — MCP 外部工具服务的架构、配置与扩展方式
- [系统设计概述](system-overview.md) — 整体架构与各子系统关系

---

*最后更新：2026-06（新增 MCPConfig 子配置块）*
