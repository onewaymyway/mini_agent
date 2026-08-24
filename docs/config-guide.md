# 配置系统指南

> 说明 mini-agent 的配置架构、子配置块、加载优先级与扩展方式。

> **模块布局（2026-06 起）**：配置代码已从单文件 `config.py` 拆分为 `config/` 包
> （`models.py` / `loader.py` / `prompt_builder.py`，见 [代码结构说明](code-structure-guide.md#23-核心层原根目录模块)），
> 但对外 import 路径完全不变——本文档中所有 `from mini_agent.config import ...` 写法继续有效。

---

## 1. 架构概览

配置系统采用**子配置块（Feature Config）**架构：`AppConfig` 主体只持有核心字段，各功能域通过独立的子配置类聚合。

```
AppConfig
├── model / api_key / project_root ...  ← 核心字段（直接持有）
├── memory:     MemoryConfig            ← 记忆功能
├── compress:   CompressConfig          ← 历史压缩
├── tool_trim:  ToolTrimConfig          ← 工具结果截断（含原始输出留存 + 可选 LLM 智能摘要，详见 [工具结果原始留存与智能摘要指南](tool-result-raw-store-and-smart-summary-guide.md)）
├── skill:      SkillConfig             ← Skill 系统
├── perception: PerceptionConfig        ← 感知（扫描/监听/缓存/token）
├── session:    SessionConfig           ← Session 持久化
├── debug:      DebugConfig             ← 调试日志
├── http:       HttpConfig              ← HTTP API 服务
├── retry:      RetryConfig             ← LLM 调用重试
├── ensemble:   EnsembleConfig          ← 多结果合并取优（Best-of-N）
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
    backend: str = "local"             # "local" | "hybrid" | "chroma" | "redis"（"hybrid" 为新增）
    store_path: Optional[Path] = None  # None = <project_root>/.agent/memory.jsonl
    global_enabled: bool = True
    global_top_k: int = 2
    top_k: int = 3
    decay_half_life_days: float = 30.0
    max_entries: int = 500

    # ── Lesson Memory 扩展（2026-06，Stage 1）──
    lesson_rules_enabled: bool = True       # 规则触发总开关
    lesson_fail_threshold: int = 3          # 同一工具连续失败 ≥ N 次触发 lesson
    correction_detection_enabled: bool = True  # 人类反馈纠正检测总开关

    # ── 记忆语义检索（混合 TF-IDF + 本地离线 Embedding）──
    embedding_enabled: bool = False          # [默认关闭] 唯一总开关
    embedding_model: str = "bge-small-zh-v1.5"
    embedding_model_cache_dir: Optional[Path] = None  # None = ~/.agent/models/
    embedding_tfidf_weight: float = 0.5
    embedding_weight: float = 0.5
    embedding_top_n: int = 20

    # ── 记忆巩固：淘汰前先归纳 ──
    consolidation_enabled: bool = True
    consolidation_min_group_size: int = 3
```

| 字段 | 说明 |
|------|------|
| `backend` | 指定记忆后端实现，对应 `memory_factory.py` 注册表中的 key，`"hybrid"` 会在 `embedding_enabled=True` 时启用语义检索，否则等价于 `"local"` |
| `decay_half_life_days` | 时间衰减半衰期，30 天后旧记忆检索分数衰减 50% |
| `max_entries` | 超出后先尝试归纳（见 `consolidation_enabled`），归纳失败再淘汰最旧条目并重写文件 |
| `lesson_rules_enabled` | 关闭后 `_init_components()` 不会创建 `LessonRuleEngine`，规则触发完全不生效 |
| `lesson_fail_threshold` | 仅影响"连续失败"规则；"拒绝后重试成功"规则不受此参数影响 |
| `correction_detection_enabled` | 关闭后 `run_turn()` 跳过纠正短语检测，但不影响 `(e)dit` 接入（后者走独立路径） |
| `embedding_enabled` | [默认关闭] 本地离线 embedding 语义检索总开关。为 `False` 时不 import 任何推理依赖（`onnxruntime`/`tokenizers`），行为与改造前完全一致 |
| `embedding_model` | 内置候选：`"bge-small-zh-v1.5"`（默认，中文场景）/ `"embedding-gemma-300m"`（多语言）；也可填用户自定义本地模型目录路径 |
| `embedding_tfidf_weight` / `embedding_weight` | 混合检索合并权重，默认 5:5 |
| `consolidation_enabled` | 淘汰前是否尝试把即将丢弃的相似 lesson 归纳成一条 `consolidated_lesson`，而不是直接物理删除。失败静默降级为原有淘汰行为 |
| `consolidation_min_group_size` | 至少聚类到这么多条才触发归纳，避免小样本过度抽象 |

`lesson_rules_enabled`/`lesson_fail_threshold`/`correction_detection_enabled`
仅支持 JSON 配置文件，暂无对应 CLI 参数。详见
[记忆管理指南](memory-management-guide.md#lesson-memory) 中 Lesson Memory 完整说明，
以及 [四项优先改进指南](four-priority-improvements-guide.md) 中语义检索/记忆巩固的完整用法。

### CompressConfig

```python
@dataclass
class CompressConfig:
    enabled: bool = False
    threshold: float = 0.7             # token 占用率超过此值触发压缩
    strategy: str = "compact_with_skills"  # 压缩策略名，"compact_with_skills" 为特殊值（见下），
                                            # 其余对应 history/compression.py 注册表
    forget_orphan_tool_results: bool = False

    # ── SelectiveStrategy 专用 ──
    selective_weights: dict = None
    selective_min_user_turns: int = 3

    # ── 触发器开关（2026-07 新增，见 history/triggers.py，均默认关闭）──
    turn_count_trigger_enabled: bool = False
    max_turns_before_compact: int = 20
    tool_call_count_trigger_enabled: bool = False
    max_tool_calls_before_compact: int = 50
    topic_shift_detection: str = "off"          # "off" | "heuristic" | "llm"
    topic_shift_keyword_overlap_threshold: float = 0.15
    redundancy_detection_enabled: bool = False
    redundancy_tool_result_ratio: float = 0.6
    compact_cooldown_turns: int = 3
    require_confirmation: bool = False

    # ── compact_mechanism_improvement_plan.md P0/P1/P2（2026-07 三次更新，均默认关闭）──
    goal_aware_weighting_enabled: bool = False
    decision_extraction_on_compact_with_skills_enabled: bool = False
    decision_recall_tool_enabled: bool = True
    safe_point_gating_enabled: bool = False
    composite_intensity_enabled: bool = False
    composite_intensity_threshold: float = 1.2
    audit_enabled: bool = False
    audit_compact_reasons: list = field(default_factory=lambda: [
        "topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep",
    ])
    audit_async: bool = True
```

| 字段 | 说明 |
|------|------|
| `strategy` | `"compact_with_skills"`（默认，与手动 `/compact` 一致：LLM 摘要 + skill 重附，见 [compact-design.md](compact-design.md)）/ `"turn_aligned"` / `"llm_summary"` / `"sliding_window"` / `"selective"`（轻量可插拔策略，走 `HistoryManager.auto_compress()` 旧路径）/ 自定义注册名 |
| `forget_orphan_tool_results` | 压缩后是否剔除保留段中无对应 tool_use 的 tool_result |
| `turn_count_trigger_enabled` / `max_turns_before_compact` | 距上次 compact 满 N 轮自动触发（常规维护性压缩，建议策略 `selective`） |
| `tool_call_count_trigger_enabled` / `max_tool_calls_before_compact` | 距上次 compact 累计 N 次工具调用自动触发 |
| `topic_shift_detection` | `"off"` 不检测；`"heuristic"` 用关键词重合度+切换语关键词，无额外 LLM 调用；`"llm"` 在 heuristic 命中后追加一次小模型调用二次确认（判断依据是自上次 compact 以来的**全部用户输入 + compact 摘要**，而不只是相邻一轮，见 [compact-design.md](compact-design.md)）。命中后建议策略为 `compact_with_skills`（与手动 `/compact` 一致）。两档均内置续接短语白名单（如"继续"/"continue"/"好的"等整句匹配，以及"继续想办法解决XXX"这类以续接词开头的长句前缀豁免）和短文本豁免（当前消息关键词数 <2 时不参与重合度判断），避免短回复被误判为话题切换 |
| `topic_shift_min_budget_pct` | token 占用率门槛（默认 `0.2`）：仅当已启用 token 占用率估算（`ctx.budget_pct > 0`）且当前占用率低于此值时，直接跳过话题切换检测（含 heuristic 和 LLM 两档），因为历史本来就短，压缩收益很小，还可能白白多花一次 LLM 二次确认调用。设为 `0` 关闭该门槛（旧行为）。未启用 token 占用率估算时不受影响 |
| `topic_shift_keyword_overlap_threshold` | 相邻两条用户消息关键词重合度低于此值视为疑似话题切换 |
| `redundancy_detection_enabled` / `redundancy_tool_result_ratio` | `tool_result` 消息占比超过此值时触发（历史信息冗余），建议策略 `selective` |
| `compact_cooldown_turns` | compact 后这么多轮内，除 token 硬阈值外的其他触发器不生效，防止反复触发 |
| `require_confirmation` | `False`（默认）全自动静默压缩；`True` 触发后先询问用户 y/n，拒绝则本次跳过 |
| `goal_aware_weighting_enabled`（P0-A） | goal_mode 卡住恢复触发 compact 时，是否把未通过的验收标准作为 hint 传给 `compact_with_skills()`，让摘要优先保留目标相关信息 |
| `decision_extraction_on_compact_with_skills_enabled`（P0-B） | compact 摘要是否额外要求 LLM 附带结构化决策候选块，解析后入队沉淀（复用 `wiki/decision_writer.py` 巩固循环） |
| `decision_recall_tool_enabled` | 是否注册 `recall_decisions` 只读工具（检索 P0-B 沉淀下来的决策，工具本身始终注册，关闭时功能层面禁用） |
| `safe_point_gating_enabled`（P1-A） | 非 token 硬阈值的触发命中，若落在有副作用工具调用链条中间（`bash`/`write_file` 等），是否挂起等到安全点再执行 |
| `composite_intensity_enabled` / `composite_intensity_threshold`（P1-B） | 硬触发均未命中时，是否把各触发器的"接近阈值程度"求和，达到阈值也视为命中一次 `composite_intensity` 软触发 |
| `audit_enabled` / `audit_compact_reasons` / `audit_async`（P2-A） | deep compact（默认仅话题切换/卡住恢复）完成后是否异步执行一次 LLM 质量自检，发现遗漏信息时追加历史条目 + 写 `activity_digest.jsonl` |

> 各触发器相互独立、可任意组合开启；P0/P1/P2 六项改造的设计取舍和实施状态详见
> [Compact 设计文档](compact-design.md)（"Compact 机制主动化改进"一节）
> 与源设计文档 `next_doc/compact_mechanism_improvement_plan.md`。

> 平坦 key 映射：`compact_goal_aware_weighting_enabled` / `compact_decision_extraction_enabled` /
> `compact_decision_recall_tool_enabled` / `compact_safe_point_gating_enabled` /
> `compact_composite_intensity_enabled` / `compact_composite_intensity_threshold` /
> `compact_audit_enabled` / `compact_audit_reasons`（JSON 数组）/ `compact_audit_async`。

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
    artifact_auto_detect_enabled: bool = False  # 产出物自动侦测，默认关闭
```

注意：`tool_cache_max_entries` 是 v2 新增字段，控制 `ToolResultCache` 的 LRU 容量上限。
`artifact_auto_detect_enabled` 打开后，`write_file`/`create_file`/`patch_file(_simple)`/
`bash` 成功执行时会自动扫描文档/图片类产出并登记到产出物看板，详见
[产出物看板指南](artifacts-dashboard-guide.md)。

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
    keyword_activation_enabled: bool = False  # 关键词自动激活（默认关闭）
```

`keyword_activation_enabled`：控制每轮用户输入是否触发 `auto_activate()` 的关键词扫描。
默认关闭，避免 skill 被意外拉起；可在 `agent_config.json` 中设为 `true` 静态开启，
也可在运行时通过 `/skill autoload on|off` 动态切换。

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

### EnsembleConfig

控制"多结果合并取优"（Best-of-N）功能：对同一任务获取多个候选结果，再综合评判出最优结果。

```python
@dataclass
class EnsembleConfig:
    mode: str = "off"                  # off | manual | auto | always
    granularity: str = "both"          # llm_call | subagent | both
    n: int = 3                         # 候选数
    execution: str = "parallel"        # serial | parallel
    max_concurrency: int = 3
    judge_strategy: str = "llm_judge"  # llm_judge | first_success | vote | merge
    judge_model: Optional[str] = None  # None = 复用主模型
    early_stop_on_consensus: bool = True
    max_extra_cost_ratio: float = 2.0
```

| 字段 | 说明 |
|------|------|
| `mode` | `off` 完全关闭；`manual` 仅显式调用工具时触发；`auto` 框架自行判断（规则层+模型自判层）；`always` 强制触发（调试用） |
| `granularity` | 限制可用粒度：`llm_call`（同输入多次调用）/ `subagent`（多 SubAgent 不同上下文）/ `both` |
| `n` | 候选数量 |
| `execution` | `serial`（可提前停止，省成本）/ `parallel`（速度快） |
| `judge_strategy` | 评判策略；`verifiable` 类任务会被 `classify_task_type()` 自动覆盖为 `first_success` |
| `early_stop_on_consensus` | 串行模式下，候选已通过校验或已有多数共识时提前停止 |

**JSON 配置示例：**

```json
{
  "ensemble_mode": "manual",
  "ensemble_granularity": "both",
  "ensemble_n": 3,
  "ensemble_execution": "parallel",
  "ensemble_judge_strategy": "llm_judge"
}
```

**CLI 运行时调整：**

```
/ensemble mode auto
/ensemble granularity subagent
```

详见 [多结果合并取优指南](ensemble-best-of-n-guide.md)。

### LLM Fallback Chain（多配置故障转移 + 多 Key 轮转）

API key 等敏感配置存放在独立的 **`providers.json`** 中（已加入 `.gitignore`），与普通配置分离。

```
项目根目录/
├── agent_config.json      ← 通用配置，可提交 git
├── providers.json         ← API key & fallback chain，加入 .gitignore ❌
└── providers.json.example ← 模板，可提交 git
```

`providers.json` 支持两个顶层字段：

```json
{
  "llm_fallback_chain": [
    {
      "provider": "anthropic",
      "model": "claude-opus-4-7",
      "api_keys": ["sk-ant-aaa", "sk-ant-bbb"],
      "key_rotation": "passive"
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

AppConfig 新增字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llm_fallback_chain` | list | `[]` | 配置链，空时退化为单条主配置 |
| `llm_fallback_on` | list\|null | `null`（使用内置默认值） | 触发 fallback 的错误类名称集合 |

CLI 参数：

```bash
mini-agent --providers-config /secure/path/providers.json
```

详见 [LLM 多配置故障转移指南](llm-failover-guide.md)。

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
  "auto_compress_strategy": "compact_with_skills",
  "compact_turn_count_trigger_enabled": true,
  "compact_max_turns": 20,
  "compact_tool_call_count_trigger_enabled": true,
  "compact_max_tool_calls": 50,
  "compact_topic_shift_detection": "heuristic",
  "compact_redundancy_detection_enabled": true,
  "compact_require_confirmation": false,
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

### WorkdirKnowledgeConfig（Stage 4 W2）

```python
@dataclass
class WorkdirKnowledgeConfig:
    enabled: bool = True
    timeline_inject_limit: int = 5          # 注入最近 N 条 timeline 记录
    work_thread_relation_days: float = 7.0  # N 天内活跃的 work_thread 才关联 session
    open_threads_inject_limit: int = 5      # 最多注入几条 high-priority open_thread
```

控制 W2 Workdir 知识层（`project.json` / `timeline.jsonl` / `open_threads.json` / `knowledge_index.json`）的维护与 context 注入。
详见 [W2/W3 知识层指南](self-evolution-stage4-5-guide.md)。

### GlobalKnowledgeConfig（Stage 5 W3）

```python
@dataclass
class GlobalKnowledgeConfig:
    enabled: bool = True
    dormant_after_days: float = 30.0        # 超过此天数未活跃的项目标记为休眠
    activity_log_inject_limit: int = 5      # 注入最近 N 条 activity_log 条目
```

控制 W3 Global 知识层（`self_profile.json` / `projects_index.json` / `cross_project_index.json` / `activity_log.jsonl`）的维护与 context 注入。
详见 [W2/W3 知识层指南](self-evolution-stage4-5-guide.md)。

### GenerativeCapabilityConfig（`generative-capability` skill 三档机制）

```python
@dataclass
class GenerativeCapabilityConfig:
    skill_tier_max_turns: int = 40                    # <=0 视为显式关闭 SKILL 档
    skill_upgrade_enabled: bool = True
    skill_upgrade_success_threshold: int = 3
    skill_upgrade_retry_cooldown_seconds: int = 3600
```

控制 `generative-capability` 类型 skill（如 `.claude/skills/browser-site-
scraper/`）script→skill→explore 三档执行机制里 SKILL 档（`PlaybookRunner`
参照 playbook 执行）的默认回合预算、以及 SKILL 档证明可靠后自动升级蒸馏
为 `script.py` 的开关/门槛/冷却期。这是全局默认值，单个 skill 可以在自己
的 `capability.yaml -> skill_tier` 下用同名字段覆盖（skill 级优先级更高）。
`skill_tier_max_turns<=0`（无论是全局还是 skill 级配置）会让该范围内
`capability_call` 不再注入 `playbook_repo`/`skill_runner`，SKILL 档回退
为静默跳过（等价于该字段引入前的行为）。详见
[skill-system-guide.md 3.8 节"三档 member 执行机制"](skill-system-guide.md#38-generative-capability-skill按需调用的领域能力包阶段七新增)，
背景与实施记录见
[generative_capability_three_tier_improvement_plan.md 第5节](../next_doc/generative_capability_three_tier_improvement_plan.md)。

### ObservabilityConfig（Stage 6）

```python
@dataclass
class ObservabilityConfig:
    enabled: bool = True
    tracing_enabled: bool = True       # 是否写入 traces.jsonl（独立开关）
    anomaly_k_sigma: float = 3.0       # 异常检测触发阈值（value > mean + k*std）
    anomaly_min_samples: int = 10      # 至少需要多少条历史记录才启用异常检测
```

| 字段 | 说明 |
|------|------|
| `enabled` | 整体开关；关闭后 `tracing_enabled` 也同时失效 |
| `tracing_enabled` | 只关闭 traces.jsonl 写入，不影响 `/diagnostics` 端点与异常检测 |
| `anomaly_k_sigma` | 越小越灵敏（建议 2.5~3.5），3.0 是保守默认值 |
| `anomaly_min_samples` | 样本不足时不运行异常检测，避免误报（新项目初期） |

对应的便捷属性：`cfg.observability_enabled` / `cfg.tracing_enabled`。
详见 [观察性系统指南](observability-guide.md)。

### GoalModeConfig（Goal 模式）

```python
@dataclass
class GoalModeConfig:
    enabled: bool = False
    spec_builder_model: Optional[str] = None
    spec_builder_provider: Optional[str] = None
    spec_builder_mode: str = "auto"  # llm | agent | auto
    spec_builder_agent_allowed_tools: list = field(default_factory=lambda: [
        "skill_list", "list_workflows", "show_workflow",
        "read_file", "list_dir", "tree_summary", "grep", "glob",
    ])
    spec_builder_agent_allowed_tool_groups: list = field(default_factory=list)
    spec_builder_agent_max_turns: int = 6
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None
    judge_tools_enabled: bool = False
    judge_allowed_tools: list = field(default_factory=lambda: ["bash", "read_file", "grep", "glob"])
    judge_allowed_tool_groups: list = field(default_factory=list)
    judge_yes_mode: bool = False
    judge_max_turns: int = 40
    max_rounds: int = 20
    max_total_compacts: int = 10
    consecutive_same_feedback_limit: int = 3
    same_feedback_similarity_threshold: float = 0.9
    judge_show_prompt: bool = False
    judge_show_raw_output: bool = False
    persist_state: bool = True
    auto_resume_prompt: bool = True
```

| 字段 | 说明 |
|------|------|
| `enabled` | 整体开关，关闭时 `/goal` 命令报错提示未启用 |
| `spec_builder_model` / `spec_builder_provider` | GoalSpecBuilder（验收标准协商）用的模型，`null` = 复用主 `cfg.model` |
| `spec_builder_mode` | **[SYS-GOAL-MODE-DUAL-PATH]** 验收标准生成路径：`"llm"` 始终单轮裸 LLM 调用（不挂工具）；`"agent"` 始终构造只读受限 Agent（先查证项目里实际的 skill/workflow 定义再生成）；`"auto"`（默认）先用规则判断目标是否涉及项目内部信息，命中则走 agent，未命中则走 llm（llm 输出里若自报 `needs_project_context: true` 会再升级到 agent 重跑一次）。也可以在 `/goal` 命令上用 `--mode=` 临时覆盖，见 [Goal 模式指南](goal-mode-guide.md#验收标准生成路径) |
| `spec_builder_agent_allowed_tools` / `spec_builder_agent_allowed_tool_groups` | 仅命中 agent 路径时生效的只读工具白名单，默认只包含 `skill_list`/`list_workflows`/`show_workflow`/`read_file`/`list_dir`/`tree_summary`/`grep`/`glob`——刻意不包含 `bash` 或任何写操作 |
| `spec_builder_agent_max_turns` | agent 路径允许的最大轮次（先探索 skill/workflow 再产出 JSON），默认 `6`，远小于主 Agent 的轮次预算 |
| `judge_model` / `judge_provider` | GoalJudge 用的模型，`null` = 复用主 `cfg.model` |
| `judge_tools_enabled` | GoalJudge 是否挂载工具自己验证验收标准，默认关闭（最小权限原则） |
| `judge_allowed_tools` / `judge_allowed_tool_groups` | `judge_tools_enabled=true` 时的工具白名单 |
| `judge_yes_mode` | 仅当 `judge_tools_enabled=true` 时生效：是否真实执行工具调用（`--yes` 全放行），默认仍强制 sandbox 拦截 |
| `judge_max_turns` | **[BUGFIX]** GoalJudge 单次核查内部允许跑的最大轮次上限，默认 `40`。此前硬编码为"不挂工具 2 / 挂工具 6"且不可配置，挂工具场景很容易在验证命令（如 `pytest`）跑完前就撞顶，导致空输出、被迫保守判 `CONTINUE`；现在统一改为读这个配置项 |
| `max_rounds` | 外层循环轮次上限 |
| `max_total_compacts` | 单次 goal 执行期间最多允许几次 compact |
| `consecutive_same_feedback_limit` | 连续 N 轮反馈高度雷同即判定"卡住"提前终止 |
| `same_feedback_similarity_threshold` | 判定"雷同"的相似度阈值（`difflib.SequenceMatcher`） |
| `judge_show_prompt` | 打印发给 GoalJudge 的完整输入 prompt，排查判定依据用（默认关闭，内容较长） |
| `judge_show_raw_output` | **[BUGFIX]** 打印 GoalJudge 返回的原始 JSON 判定结果，排查解析/判定依据用。同时修复了"GoalJudge 撞到 `judge_max_turns` 仍未产出最终文本时被当成成功返回空字符串"的问题，现在会显式识别并附带明确原因注入反馈 |
| `persist_state` | 是否在轮次边界落盘 `goal_state.json`，供异常中断恢复 |
| `auto_resume_prompt` | 启动 REPL 时若检测到未完成的 goal 是否主动提示 |

只支持从 `agent_config.json` 的 `goal_mode: {...}` 块读取，暂无对应 CLI 参数。
详见 [Goal 模式指南](goal-mode-guide.md)。

### TurnJudgeConfig

```python
@dataclass
class TurnJudgeConfig:
    enabled: bool = False
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None
    max_auto_rounds: int = 3
    judge_show_prompt: bool = False
    history_window: int = 6
```

| 字段 | 说明 |
|------|------|
| `enabled` | 总开关，默认关闭，不影响任何现有行为 |
| `judge_model` / `judge_provider` | TurnJudge 用的模型，`null` = 复用主 `cfg.model`（建议用更便宜/更快的模型，高频触发点） |
| `max_auto_rounds` | 连续自动接管次数上限，防止死循环；判定为 `NEED_USER` 或达到上限后清零 |
| `judge_show_prompt` | 打印发给 TurnJudge 的完整输入 prompt，排查判定依据用 |
| `history_window` | 供 TurnJudge 参考的最近历史消息条数 |

只支持从 `agent_config.json` 的 `turn_judge: {...}` 块读取，暂无对应 CLI 参数。
详见 [轮次守门员指南](turn-judge-guide.md)。

### PrivacyConfig

```python
@dataclass
class PrivacyConfig:
    enabled: bool = True
    secrets: list = field(default_factory=list)   # [{"name": str, "value": str}]
    auto_env_patterns: Optional[list] = None      # None = 使用内置默认模式
    placeholder_prefix: str = "SECRET"
    verbose: bool = False
```

| 字段 | 说明 |
|------|------|
| `enabled` | 总开关；`False` 时使用空操作实现，零开销 |
| `secrets` | 显式指定的隐私值列表 |
| `auto_env_patterns` | 自动采集环境变量的正则模式列表；`None` = 内置默认；`[]` = 禁用 |
| `placeholder_prefix` | 占位符前缀，默认生成 `{{SECRET_1}}` |
| `verbose` | 启动时打印已注册条目摘要（不含真实值） |

详见 [隐私信息保护机制说明](privacy-guard-guide.md)。

### `hot_reload_interval_s`（AppConfig 直接字段）

```python
hot_reload_interval_s: float = 2.0  # Skills/Agent Profiles 热重载轮询间隔（秒）
```

详见 [热重载机制说明](hot-reload-guide.md)。

### `notepad_enabled`（AppConfig 直接字段）

```python
notepad_enabled: bool = True  # 记事本功能总开关，默认开启
```

`agent_config.json` 中配置：

```json
{
  "notepad_enabled": false
}
```

关闭后：不再向 system prompt 注入记事本块，也不再为当前 session 创建/加载
`notepad.json`；`notepad_*` 工具调用会返回错误提示（工具本身仍注册在全局
registry 中，与其它 `xxx_enabled` 开关的既有取舍一致——只是功能层面禁用，
不会让模型看不到工具定义）。详见 [记事本机制说明](notepad-guide.md)。

### `recall_history_enabled` / `recall_history_mode`（AppConfig 直接字段，P2-B）

```python
recall_history_enabled: bool = False   # recall_from_raw_history 工具总开关，默认关闭
recall_history_mode: str = "keyword"   # "keyword"（已实现）| "embedding"（预留，未实现）
```

`agent_config.json` 中配置：

```json
{
  "recall_history_enabled": true,
  "recall_history_mode": "keyword"
}
```

开启后，agent 可以主动调用 `recall_from_raw_history(query, max_results=5)`
按关键词在当前 session 的完整 raw history（含已被 compact 掉的片段）里检索
原始内容，用于"隐约记得处理过某事但当前上下文已经找不到细节"的场景。工具本身
**始终**注册在全局 registry 中（与 `notepad_enabled` 一致的取舍），关闭时调用
直接返回错误提示，不做任何检索。详见 [Compact 设计文档](compact-design.md#p2-b-raw-history-按需找回工具)。

### `max_turns_on_limit` / `max_turns_hard_limit`（AppConfig 直接字段）

```python
max_turns: int = 50              # 单次 run_turn 内，模型-工具循环的常规轮次预算
max_turns_on_limit: str = "stop" # "stop" | "continue" | "compact_continue"
max_turns_hard_limit: int = 250  # 250 = 50 * 5，continue/compact_continue 策略下的硬性总轮次上限
```

`agent_config.json` 中配置：

```json
{
  "max_turns": 50,
  "max_turns_on_limit": "compact_continue",
  "max_turns_hard_limit": 250
}
```

撞到 `max_turns` 轮次预算时（终端会打印 `Reached max turns (N).` 警告），
`max_turns_on_limit` 决定接下来怎么做：

| 取值 | 行为 |
| --- | --- |
| `"stop"`（默认，原有行为不变） | 直接结束本轮 `run_turn`，把控制权交还用户/调用方 |
| `"continue"` | 不压缩历史，直接把预算续上（`_turns_budget` 追加一个 `max_turns`）并注入一条模拟的"继续"消息，agent 不中断地往下跑；历史会持续增长，务必配合 token 阈值触发器（见 [Compact 设计文档](compact-design.md)）一起用，否则容易把 context 撑爆 |
| `"compact_continue"` | 先调用 `compact_with_skills()` 强制压缩一次历史（不等 token/轮次触发器命中），压缩成功后再注入"继续"消息续跑，行为与 `[AUTO-COMPACT-CONTINUE]` 的自动续接一致 |

无论选哪种非 `"stop"` 策略，续跑的总轮次都受 `max_turns_hard_limit` 兜底
（默认 `max_turns * 5`），达到硬顶后仍会退回 `"stop"` 的行为，避免长时间
自动化任务失控地无限循环下去。`daemon`/自主任务场景建议用
`"compact_continue"`；交互式 CLI 场景保持默认 `"stop"` 即可，让用户自己
决定是否要继续。

> 这是唯一**不属于** `AppConfig` 加载流程的配置块——单独落盘在
> `<project_root>/behavior_config.json`，跟 `agent_config.json` 放在
> 同一级目录（缺省用 `Path.cwd()`），由 `perception/behavior/config.py`
> 管理，通过 `/behavior` 命令或 `/v1/perception/toggle` 接口读写，不走
> `--config` / 环境变量这套加载优先级，也不会被 `agent_config.json`
> 里的字段覆盖。总开关和全部子开关默认 `False`。采集到的原始事件/
> 分析摘要仍然落盘在 `~/.agent/behavior/`（跨项目共享）。

```python
@dataclass
class BehaviorConfig:
    enabled: bool = False
    active_window_enabled: bool = False
    idle_enabled: bool = False
    browser_report_enabled: bool = False
    mobile_report_enabled: bool = False
    clipboard_meta_enabled: bool = False
    cdp_browser_enabled: bool = False
    git_activity_enabled: bool = False
    terminal_command_enabled: bool = False
    now_playing_enabled: bool = False
    app_lifecycle_enabled: bool = False
    daily_analysis_enabled: bool = False
    daily_analysis_hour: int = 22
    # ... 详见 docs/behavior-perception-guide.md
```

详见 [用户行为感知系统指南](behavior-perception-guide.md)。

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

### AutonomyConfig（好奇心评分 / 自主探索排序权重）

```python
@dataclass
class AutonomyConfig:
    novelty_weight: float = 0.5           # urgency + novelty_weight * novelty 排序权重
    exploration_min_calls_threshold: int = 2   # total_calls 低于此值视为"几乎未探索"
    already_explored_cooldown_days: float = 30.0
```

| 字段 | 说明 |
|------|------|
| `novelty_weight` | `SoftGoalDeriver` 对候选 Goal 排序时，`urgency`（确定性问题的紧急度）与 `novelty`（好奇心/信息增益评分）的合并权重。设为 `0` 等价于改造前纯 `urgency` 排序（回归行为） |
| `exploration_min_calls_threshold` | `capability_map` 中 `total_calls` 低于此阈值的能力视为"几乎未探索"，novelty 分数按 `1 / (1 + total_calls)` 计算 |
| `already_explored_cooldown_days` | 同一能力领域最近探索过（见 `activity_digest.jsonl` 中 `type="exploration_result"` 记录）则大幅降权，避免探索预算被重复消耗在同一领域 |

详见 [四项优先改进指南](four-priority-improvements-guide.md#方案三自主探索好奇心评分)。

> `AutonomyConfig` 还承载了另外四组彼此独立的字段（各自可单独开关）：
> `fairness_*`（[Goal 执行公平性调度配置](goal-execution-fairness-config.md)）、
> `objective_isolated_context_enabled`/`objective_isolated_*`（自主任务独立
> 上下文）、`guardian_mode_enabled`/`guardian_*`（看护模式 GuardianRunner）——
> 后两组详见 [Daemon 自主任务错误状态识别与恢复指南](daemon-autonomous-state-recovery-guide.md)；
> `objective_persistent_worker_enabled`/`objective_persistent_worker_*`
> （目标级持久 Worker）、`scheduler_heartbeat_enabled`/
> `scheduler_heartbeat_*`（调度心跳独立化）——详见
> [Daemon 执行模型与调度心跳指南](daemon-execution-model-guide.md)。

---

## 6. 添加新功能配置

> **2026-08 起的新规范**：新增配置字段/配置块统一走
> `config/param_registry.py` 里的注册表驱动机制，不再逐个手写
> `config/loader.py` 里的 `XxxConfig(field=int(_x.get(...)), ...)`
> 构造代码。完整规范、决策树（"我该走 nested block 还是 flat CLI
> 参数？"）、常见坑，见 **[参数系统指南](param-system-guide.md)**——
> 本节只给一个最短路径示例。

**绝大多数新参数**（只需要能在 `agent_config.json` 里配置，不需要
`--xxx` 命令行覆盖）走"嵌套 block"机制，两步搞定：

**步骤一**：在 `config/models.py` 中新建（或复用已有）子配置类

```python
@dataclass
class MyFeatureConfig:
    enabled: bool = False
    param_a: int = 10
    param_b: str = "default"
```

**步骤二**：在 `AppConfig` 中加入子块引用，并在
`config/param_registry.py::_build_nested_blocks()` 里注册一行

```python
# models.py
@dataclass
class AppConfig:
    ...
    my_feature: MyFeatureConfig = field(default_factory=MyFeatureConfig)

# param_registry.py::_build_nested_blocks()
NestedBlockSpec("my_feature", _m.MyFeatureConfig),
```

到此为止——**不需要再碰 `loader.py`**。`agent_config.json` 里
`"my_feature": {"param_a": 20}` 会被 `load_all_nested_blocks()` 通用地
读出来并做类型转换，`config_catalog.py`（看板配置 UI）也会自动展示
新字段。给已有 block（如 `autonomy`/`tech_radar`）加新字段更简单：
只需要步骤一（加 dataclass 字段），连步骤二都不用做。

只有当新参数确实需要 `--xxx` 命令行覆盖时，才走
[参数系统指南](param-system-guide.md) 里的 `ParamSpec` 机制；新增的子
配置类如需被 `config/__init__.py` 重导出（供外部 `from mini_agent.config
import MyFeatureConfig` 使用），记得同时把类名加入 `__init__.py` 的
import 列表和 `__all__`。

---

## 7. 相关文档

- [参数系统指南](param-system-guide.md) — **新增任何配置参数前必读**：nested block vs flat CLI 参数的统一注册与解析机制
- [代码结构说明](code-structure-guide.md) — `config/` 包的文件拆分与职责边界
- [MCP 集成指南](mcp-guide.md) — MCP 外部工具服务的架构、配置与扩展方式
- [系统设计概述](system-overview.md) — 整体架构与各子系统关系
- [记忆管理指南](memory-management-guide.md) — `MemoryConfig` 新增字段的完整使用场景（Lesson Memory）
- [四项优先改进指南](four-priority-improvements-guide.md) — `MemoryConfig`（embedding/consolidation）与 `AutonomyConfig` 新字段的完整使用场景
- [Goal 执行公平性调度配置](goal-execution-fairness-config.md) — `AutonomyConfig` 的 `fairness_*` 字段（P1-P5）完整说明
- [Daemon 自主任务错误状态识别与恢复指南](daemon-autonomous-state-recovery-guide.md) — `FormatCorrectionConfig.result_sanity_check_enabled` 与 `AutonomyConfig` 的 `objective_isolated_*`/`guardian_*` 字段完整说明
- [多结果合并取优指南](ensemble-best-of-n-guide.md) — `EnsembleConfig` 的完整使用场景与架构说明
- [Goal 模式指南](goal-mode-guide.md) — `GoalModeConfig` 的完整使用场景与架构说明
- [轮次守门员指南](turn-judge-guide.md) — `TurnJudgeConfig` 的完整使用场景与架构说明
- [用户行为感知系统指南](behavior-perception-guide.md) — `BehaviorConfig`（独立配置文件，不属于 `AppConfig`）的完整设计与使用

---

*最后更新：2026-08（引入 `config/param_registry.py` 统一嵌套 block 加载机制，`config/loader.py` 里 10 个嵌套配置块改为通用加载，详见 [参数系统指南](param-system-guide.md)）*
