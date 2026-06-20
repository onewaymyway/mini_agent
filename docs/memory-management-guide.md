# 记忆管理指南

## 概述

mini-agent 的记忆管理系统提供跨 session 的长期记忆能力，支持会话历史的持久化存储和相关记忆检索。系统采用可扩展架构，支持多种后端存储实现。

记忆条目分两类：
- **summary 型**（原有）：session 结束时的整体摘要，`entry_type="summary"`
- **lesson 型**（2026-06 新增，Stage 1）：从失败、纠正、反思中提炼出的具体可复用经验，`entry_type="lesson"`，详见本文档[「Lesson Memory」](#lesson-memory)一节

## 核心架构

### 分层设计

```
┌─────────────────────────────────────────────┐
│           ContextBuilder / Agent            │
│         (使用记忆的调用层)                   │
└─────────────────┬───────────────────────────┘
                  │ 依赖抽象接口
                  ▼
┌─────────────────────────────────────────────┐
│            MemoryBackend (ABC)              │
│         统一接口定义（抽象基类）              │
│  - add(entry)                               │
│  - search(query, k)                         │
│  - search_by_tag(tag)                       │
│  - count (property)                         │
└─────────────────┬───────────────────────────┘
                  │ 工厂创建
                  ▼
┌─────────────────────────────────────────────┐
│         MemoryFactory (create_...)          │
│         根据配置创建后端实例                  │
└─────────────────┬───────────────────────────┘
                  │ 返回具体实现
                  ▼
┌─────────────────────────────────────────────┐
│           MemoryStore (local)               │
│    JSONL 存储 + TF-IDF 检索 + 时间衰减       │
└─────────────────────────────────────────────┘
```

### 组件说明

#### 1. `MemoryBackend` (abstract)

定义记忆系统的统一接口，所有后端实现都必须继承此类。

**位置**: `src/mini_agent/perception/memory_base.py`

**核心方法**:
- `add(entry: MemoryEntry) -> None` - 持久化记忆
- `search(query: str, k: int) -> list[MemoryEntry]` - 关键词检索
- `search_by_tag(tag: str) -> list[MemoryEntry]` - 标签过滤
- `count -> int` - 条目总数

#### 2. `MemoryStore` (local backend)

本地 JSONL 存储实现，使用 TF-IDF + 时间衰减算法进行记忆检索。

**位置**: `src/mini_agent/perception/memory_store.py`

**存储格式**:
```json
{"session_id": "...", "summary": "...", "key_outcomes": [...], "tags": [...], "model": "...", "created_at": 1234567890, "entry_id": "abc123", "entry_type": "summary"}
```

lesson 型条目额外携带以下字段（2026-06 新增，详见[「Lesson Memory」](#lesson-memory)）：
```json
{"entry_type": "lesson", "trigger": "...", "outcome": "...", "root_cause": "...", "suggested_action": "...", "confidence": 0.6, "occurrence_count": 1, "source": "self_reflection"}
```
两类条目存储在同一个 `memory.jsonl` 文件里，按 `entry_type` 区分，互不干扰。

**检索算法**:
1. **分词策略**:
   - 英文：按单词切分，过滤停用词
   - 中文：双字 + 三字 n-gram（如"数据库" → ["数据", "据库", "数据库"]）

2. **TF-IDF 评分**:
   - `TF = 词频 / 总词数`
   - `IDF = log((N + 1) / (df + 1)) + 1`
   - 综合分数 = TF × IDF

3. **时间衰减**:
   - 衰减系数 λ = ln(2) / 30（半衰期 30 天）
   - 最终分数 = TF-IDF × exp(-λ × age_days)

4. **容量管理**:
   - 默认最多 500 条记忆
   - 超出时淘汰最旧的条目
   - 使用原子写入（tmp + rename）保证数据一致性

#### 3. `MemoryFactory`

根据配置创建对应后端实例的工厂类。

**位置**: `src/mini_agent/perception/memory_factory.py`

**内置后端**:
- `"local"` — MemoryStore（默认，无外部依赖）

**扩展点**（预留）:
- `"chroma"` — 向量检索（需要 chromadb）
- `"redis"` — 跨进程共享（需要 redis-py）
- `"sqlite"` — 关系型存储

## 配置方式

### 配置文件 (`agent_config.json`)

```json
{
  "memory_enabled": true,
  "memory_backend": "local",
  "memory_store_path": ".agent/memory.jsonl",
  "memory_top_k": 3,
  "memory_decay_half_life_days": 30.0,
  "memory_max_entries": 500,
  "lesson_rules_enabled": true,
  "lesson_fail_threshold": 3,
  "correction_detection_enabled": true
}
```

### 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory_enabled` | bool | `false` | 是否启用记忆系统 |
| `memory_backend` | string | `"local"` | 后端类型（local/chroma/redis） |
| `memory_store_path` | string | `.agent/memory.jsonl` | 存储文件路径 |
| `memory_top_k` | int | `3` | 检索返回的最大条目数 |
| `memory_decay_half_life_days` | float | `30.0` | 时间衰减半衰期（天） |
| `memory_max_entries` | int | `500` | 记忆条目上限 |
| `lesson_rules_enabled` | bool | `true` | 规则触发（连续失败/拒绝重试成功）总开关，2026-06 新增 |
| `lesson_fail_threshold` | int | `3` | 同一工具连续失败 ≥ N 次触发 lesson，2026-06 新增 |
| `correction_detection_enabled` | bool | `true` | 人类反馈纠正检测总开关，2026-06 新增 |

以上三个 lesson 相关字段仅支持通过 `agent_config.json` 配置，暂无对应的 CLI 显式参数。

### 代码中使用

```python
from mini_agent.config import load_config
from mini_agent.perception.memory_factory import create_memory_backend

# 1. 加载配置
cfg = load_config(memory_enabled=True, memory_top_k=5)

# 2. 创建后端实例
backend = create_memory_backend(cfg)

# 3. 添加记忆
from mini_agent.perception.memory_store import MemoryEntry
entry = MemoryEntry(
    session_id="session_123",
    summary="使用 TF-IDF 进行记忆检索",
    key_outcomes=["中文分词改用 n-gram", "加入时间衰减因子"],
    tags=["记忆系统", "TF-IDF", "中文处理"],
    model="claude-opus-4-5"
)
backend.add(entry)

# 4. 检索记忆
results = backend.search("如何处理中文文本", k=3)
for entry in results:
    print(f"{entry.summary} (score: {entry.age_days} days old)")

# 5. 按标签过滤
tagged = backend.search_by_tag("记忆系统")
```

## 记忆内容结构

### MemoryEntry 数据类

```python
@dataclass
class MemoryEntry:
    session_id: str           # 来源会话 ID
    summary: str              # 会话摘要（核心内容，lesson 型条目通常为空）
    key_outcomes: list[str]   # 关键结论列表（lesson 型条目通常为空）
    tags: list[str]           # 自动提取的标签
    model: str                # 使用的模型名称
    created_at: float         # 创建时间戳
    entry_id: str             # 唯一标识符（自动生成）
    scope: str = "project"    # "project" | "global"

    # ── Lesson Memory 扩展字段（2026-06，全部带默认值，summary 型条目零迁移成本）──
    entry_type: str = "summary"      # "summary" | "lesson" | "capability_map"
    trigger: str = ""                # 触发场景描述（lesson 专属）
    outcome: str = ""                # 实际发生了什么（lesson 专属）
    root_cause: str = ""             # 根因，如有（lesson 专属）
    suggested_action: str = ""       # 下次该怎么做（lesson 专属）
    confidence: float = 0.5          # 0-1，可信度（lesson 专属）
    occurrence_count: int = 1        # 同类 lesson 重复出现次数（lesson 专属）
    source: str = "self_reflection"  # "self_reflection" | "human_feedback" | "revert_record"
```

`to_search_text()` 对 lesson 型条目（`entry_type="lesson"`）会额外拼接
`trigger`/`outcome`/`root_cause`/`suggested_action` 四个字段，否则这些信息
无法被关键词检索命中。summary 型条目的检索文本拼接逻辑保持不变。

### 记忆生成策略

建议由 Agent 在 session 结束时调用 `add()`，传入：

1. **summary**: 会话的核心目标和最终结果
2. **key_outcomes**: 具体的技术结论、配置参数、代码模式
3. **tags**: 自动从关键术语提取（如文件名、技术名词、错误类型）

示例记忆内容：
```python
MemoryEntry(
    session_id="20260608-abc123",
    summary="修复中文打印乱码问题，使用 UTF-8 编码并配置 console 字体",
    key_outcomes=[
        "在 Windows 上需要使用 chcp 65001 设置控制台为 UTF-8",
        "Python print 默认使用 GBK，需要显式指定 encoding='utf-8'",
        "设置 SUPPORTED_OS=Windows 以启用平台特定逻辑"
    ],
    tags=["中文", "乱码", "UTF-8", "Windows", "编码问题"],
    model="claude-opus-4-5"
)
```

## Lesson Memory

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 1（Phase B），2026-06 落地。

summary 型条目回答"这次 session 做了什么"，lesson 型条目回答更具体的
"什么场景下、出了什么问题、下次该怎么做"。四条独立的写入路径，**任意一条
触发都会立即写入记忆，不需要等 session 结束**（SessionEnd 反思是唯一例外）：

| 写入路径 | 触发条件 | `source` | `confidence` | 实现位置 |
|---|---|---|---|---|
| 规则一：连续失败 | 同一工具连续失败 ≥ N 次（默认 3，可配置） | `self_reflection` | 0.6 | `perception/lesson_rules.py` |
| 规则二：拒绝后重试成功 | 权限拒绝后 10 分钟内同工具重新调用成功 | `self_reflection` | 0.6 | `perception/lesson_rules.py` |
| SessionEnd 反思 | session 真正结束时（REPL 退出），LLM 反思最后若干轮对话 | `self_reflection` | 由 LLM 给出 | `agent.py::_reflect_and_save_lessons` |
| 人类反馈纠正 | 用户输入命中纠正性短语（"不对"/"应该是"/"that's wrong" 等） | `human_feedback` | 0.85 | `perception/correction_detector.py` |
| `(e)dit` 审批编辑 | 用户在审批时编辑了命令/参数后批准 | `human_feedback` | 0.85 | `permissions.py` + `agent.py::_on_edit_detected` |

四条路径的 `confidence` 分层是有意设计：人类明确给出的反馈（纠正短语、
直接编辑）可信度最高（0.85），规则触发次之（0.6，纯规则判断，不调用 LLM，
但缺乏对"为什么失败"的真正理解），SessionEnd 反思的 confidence 由 LLM
自行给出（无固定值，体现"模型对自己反思结论的把握程度"）。

### 规则触发（LessonRuleEngine）

不依赖 LLM，纯规则判断，零延迟、零 token 成本。两条规则的状态（连续失败计数、
待观察的拒绝事件）保存在 `LessonRuleEngine` 实例里，由 `ToolExecutor` 持有，
随 session 生命周期存在：

```python
from mini_agent.perception.lesson_rules import LessonRuleEngine

engine = LessonRuleEngine(session_id="sess1", model="claude-opus-4-5", fail_threshold=3)

# 每次工具调用结果产生后调用 observe()
entry = engine.observe(
    tool_name="bash",
    tool_input={"command": "..."},
    allowed=True,            # 本次调用是否通过权限检查
    result_str="[error: ...]",
    is_error=True,           # 由 is_tool_error() 判断后传入
)
if entry is not None:
    memory_backend.add(entry)
```

**规则一（连续失败）**：每个连续失败区间只生成一次 lesson（达到阈值后再失败不
重复生成），直到下次该工具成功调用才重置计数，允许下一个失败区间再次触发。
不同工具的失败计数互相独立。

**规则二（拒绝后重试成功）**：权限拒绝事件会被记录为"待观察"，若 10 分钟内
（`_DENIAL_RETRY_WINDOW_SECONDS`）同一工具被重新调用且成功，判定为"agent
调整方式后纠错成功"，生成 lesson；超出窗口或重试仍失败则不触发。每个待观察
事件只消费一次。

`is_tool_error()`（原 `agent.py` 的 `_is_tool_error`，2026-06 迁移至此处供
`tool_executor.py` 共享，避免循环依赖）综合判断错误前缀（`[error`、
`Traceback` 等）、非零 exit code、常见异常类名三类特征。

### SessionEnd 反思

`agent.trigger_session_end()` 在 REPL 真正退出时（`EOFError` / `exit` /
`quit` / `/exit` / `/quit`）被调用，同步执行（进程即将退出，后台线程没有
意义），内部做好异常隔离，反思失败只打印警告、不阻塞退出：

1. 触发 `SessionEnd` hook（详见 [hooks 指南](hooks.md)）
2. 若 `cfg.memory.enabled`，用 `is_turn_boundary()` 精确截取最后若干轮
   用户意图轮次 + `tool_stats` 摘要，跑一次轻量 LLM 调用
   （prompt 见 `prompts/system/session_reflection.md` +
   `prompts/user/session_reflection_request.md`）
3. LLM 按要求返回 JSON 数组（每个元素含 `trigger`/`outcome`/`root_cause`/
   `suggested_action`/`confidence`），解析后逐条写入记忆，最多 `max_lessons`
   条（默认 5）

容错处理：模型偶尔会用 markdown 代码块围栏包裹响应，会被自动剥离；解析失败或
返回非数组时静默降级为 0 条，不抛异常；`confidence` 字段会被裁剪到 [0, 1]。
若 session 中没有任何用户轮次也没有工具调用统计，直接跳过，不浪费一次
LLM 调用。

### 人类反馈纠正检测

规则式短语匹配（`perception/correction_detector.py::detect_correction`），
不调用 LLM 分类。覆盖中英文约 30 条纠正句式（"不对"/"应该用 X 而不是 Y"/
"下次记得"/"that's wrong"/"next time remember to" 等），挂载在
`run_turn()` 的 `append_user()` 之后，对每条新的用户输入做检测。

设计取舍是"宁可漏检，不可误判"——独立的"应该是"/"should be"这类短语被
有意排除（容易误判成普通技术陈述句），只保留与否定词/对比词共现的精确
模式（如"应该用 X 而不是 Y"、"should use X instead, not Y"）。检测只在
消息前 300 字符内进行，避免长消息中段偶然出现纠正性词汇被误判。

```python
from mini_agent.perception.correction_detector import detect_correction, make_correction_lesson_fields

if detect_correction(user_message):
    fields = make_correction_lesson_fields(
        correction_text=user_message,
        prior_action="agent 刚执行了 write_file 覆盖原文件",  # 上一轮 assistant 做了什么
    )
    # fields = {"trigger": "...", "outcome": "...", "root_cause": "",
    #           "suggested_action": "...", "confidence": 0.85, "source": "human_feedback"}
```

### `(e)dit` 审批编辑接入

用户在审批 `(e)dit` 时修改了命令/参数，这个动作本身就是高质量人类反馈，
不依赖 `detect_correction()` 的短语匹配——"用户主动编辑了 agent 提议的
操作"这件事本身就是明确的纠正信号。`PermissionGuard` 检测到编辑后记录到
`last_edit`，由 `ToolExecutor` 在 `guard.check()` 返回后查询并通过回调
转交 `Agent._on_edit_detected()` 处理：写入一条 `_type="user_correction"`
的 history 消息（详见 [history 类型化设计](history-typed-design.md)）+
生成一条 `source="human_feedback"` 的记忆条目。详见
[权限管理指南](permission-guide.md#edit-与-lesson-memory-的接入2026-06-stage-15)。

### 检索 lesson 条目

lesson 型条目和 summary 型条目存储在同一文件、共用同一套检索接口，
区别只在 `entry_type` 字段：

```python
results = backend.search("bash 命令权限问题", k=3)
for entry in results:
    if entry.entry_type == "lesson":
        print(f"[lesson] {entry.trigger} → {entry.suggested_action} (confidence={entry.confidence})")
    else:
        print(f"[summary] {entry.summary}")
```

## 可扩展性设计

### v2 可扩展性重构要点

1. **接口与实现分离**:
   - `MemoryBackend` 是纯抽象接口，不依赖任何具体存储
   - Agent 代码只依赖接口，完全解耦具体实现

2. **工厂模式**:
   - `MemoryFactory` 负责根据配置创建后端
   - 新增后端只需两步：
     1. 实现 `MemoryBackend` 接口
     2. 在 `_REGISTRY` 中注册

3. **配置模块化**:
   - `MemoryConfig` 独立配置记忆系统
   - 添加新参数不影响其他模块

### 添加自定义后端示例

```python
# 1. 创建自定义后端
class ChromaMemoryBackend(MemoryBackend):
    def __init__(self, cfg: AppConfig):
        import chromadb
        self._client = chromadb.PersistentClient(path=".chroma")
        self._collection = self._client.get_or_create_collection("memories")

    def add(self, entry: MemoryEntry) -> None:
        self._collection.add(
            documents=[entry.to_search_text()],
            metadatas=[{"session_id": entry.session_id, "tags": entry.tags}],
            ids=[entry.entry_id]
        )

    def search(self, query: str, k: int = 3) -> list[MemoryEntry]:
        results = self._collection.query(query_texts=[query], n_results=k)
        # 转换结果为 MemoryEntry 列表
        ...

    def search_by_tag(self, tag: str) -> list[MemoryEntry]:
        # Chroma 的过滤查询
        ...

    @property
    def count(self) -> int:
        return self._collection.count()

# 2. 注册后端
from mini_agent.perception.memory_factory import register_memory_backend

register_memory_backend("chroma", lambda cfg: ChromaMemoryBackend(cfg))

# 3. 在配置中使用
{
  "memory_backend": "chroma"
}
```

## 相关文档

- [系统架构总览](./system-overview.md) - Agent 整体架构
- [配置指南](./config-guide.md) - 完整配置说明
- [并发与任务编排](./plan-and-task-guide.md) - 多任务管理
- [history 类型化设计](./history-typed-design.md) - `is_turn_boundary()` 与 `user_correction` 类型
- [权限管理指南](./permission-guide.md) - `(e)dit` 审批编辑如何接入 lesson 系统
- [hooks 指南](./hooks.md) - `SessionEnd` 事件触发机制
- [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) - Stage 1 完整需求背景

## 常见问题

**Q: 为什么中文分词使用 n-gram 而不是逐字？**

A: 逐字切分会导致复合词（如"数据库"）被拆成独立的单字，每个单字的 IDF 权重极低，导致 TF-IDF 检索效果差。n-gram 保留了词语边界，能正确匹配"数据库"→["数据","据库","数据库"]。

**Q: 时间衰减的作用是什么？**

A: 记忆的价值随时间降低。指数衰减确保最近的记忆在检索时权重更高，防止旧记忆持续干扰当前上下文。

**Q: 记忆文件为什么会增长？**

A: 每次 session 结束都会添加新的记忆条目（summary 型 + 可能的 SessionEnd 反思 lesson）。规则触发和人类反馈路径也会随时追加 lesson 条目。超过 `max_entries`（默认 500）会自动淘汰最旧的条目。

**Q: 如何备份记忆？**

A: 记忆文件是 JSONL 格式，直接复制 `.agent/memory.jsonl` 即可。恢复时替换同名文件。

**Q: lesson 条目和 summary 条目可以分开存储吗？**

A: 当前版本不支持，两者共用同一个 `memory.jsonl` 文件和检索接口，靠 `entry_type` 字段区分。如果需要分开管理，可以在检索结果上按 `entry_type` 过滤。

**Q: 为什么我关掉了 bash 三次失败也没看到 lesson？**

A: 检查 `lesson_rules_enabled` 是否为 `true`（默认开启）以及 `memory_enabled` 是否为 `true`——规则触发引擎依赖 `cfg.memory.enabled`，记忆系统未启用时不会创建 `LessonRuleEngine` 实例。

**Q: 纠正检测会不会误判我的正常对话？**

A: 设计上"宁可漏检，不可误判"，已排除了"应该是"/"should be"这类高误报模式，只保留与否定词/对比词共现的精确句式。仍有极低概率误判（如纯技术讨论中出现"should use X instead"），可接受，因为即使误判生成的 lesson 内容本身也通常是合理的技术建议。

---

*最后更新：2026-06（新增「Lesson Memory」章节，对应 self_evolution_implementation_plan.md Stage 1）*
