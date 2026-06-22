# 存储体系设计与文件布局

> 本文说明 mini_agent 的存储设计理念、作用域分层模型，以及各子系统写入的具体文件位置。

---

## 1. 设计理念

### 核心原则：数据的位置由其作用域决定

存储体系的根本出发点是一个问题：**这份数据属于谁、生命周期有多长？**

不同的数据有不同的归属层次——有些知识在所有项目之间通用，有些只和当前项目相关，有些只在本次对话中有意义，有些只和某个具体的子任务有关。将数据存放在错误的层次会带来两类问题：

- **太宽**（project 级数据存成 global）：噪声积累，检索时干扰加大
- **太窄**（session 级数据混入 global）：价值无法复用，跨 session 的知识流失

因此 mini_agent 按四个作用域层次组织所有持久化数据，各层级物理位置清晰分离。

### 作用域层次

```
Global（用户级）                  ~/.agent/
  └── Workdir（项目级）           <project_root>/.agent/
        └── Session（会话级）     <project_root>/.agent/sessions/<session_id>/
              └── Task（任务级）  <project_root>/.agent/sessions/<session_id>/tasks/<task_id>/
```

| 层次 | 物理根路径 | 生命周期 | 代表性数据 |
|------|-----------|---------|-----------|
| **Global** | `~/.agent/` | 用户存续期 | 跨项目通用经验、全局技能库 |
| **Workdir** | `<project_root>/.agent/` | 项目存续期 | 项目知识、权限配置、工具缓存 |
| **Session** | `…/.agent/sessions/<id>/` | 单次对话 | 对话历史、LLM 调试日志、统计 |
| **Task** | `…/sessions/<id>/tasks/<tid>/` | 单个 SubAgent 执行 | 实时输出、事件流、任务结果 |

---

## 2. 完整目录结构

```
~/.agent/                                      # [Global] 全局数据（W3，Stage 5）
├── memory.jsonl                               # 全局记忆（跨项目通用经验）
├── self_profile.json                          # Agent 自我画像（5.1）
├── projects_index.json                        # 已知项目注册表（5.2）
├── cross_project_index.json                   # 跨项目规律模式（5.3）
├── activity_log.jsonl                         # 全局活动日志 + session_metrics（5.4/6.3）
└── skills/                                    # 全局技能库（可选）
    └── <skill-name>/SKILL.md

<project_root>/
├── agent_config.json                          # 项目配置（可 git 提交）
│
└── .agent/                                    # [Workdir] 项目私有数据
    ├── memory.jsonl                           # 项目记忆（当前项目特有知识）
    ├── permissions.json                       # 权限白名单/黑名单
    ├── agent_api.key                          # HTTP API token（gitignore）
    │
    ├── project.json                           # 项目身份证 + 环境指纹（W2/4.1）
    ├── timeline.jsonl                         # session 时间线（W2/4.2）
    ├── work_index.json                        # 工作线索索引（W2/4.3）
    ├── open_threads.json                      # 跨 session 待处理线索池（W2/4.4）
    ├── knowledge_index.json                   # 结构化知识索引（W2/4.5）
    ├── phase_g_rhythm.json                    # Phase G 节奏治理时间戳（Stage 8/8.5）
    │
    ├── cache/
    │   └── tool_cache.json                    # 工具调用结果缓存
    │
    └── sessions/                              # [Session] 所有会话目录
        └── <session_id>/                      # 每个 session 一个目录
            ├── meta.json                      # 元信息（model、stats、summary）
            ├── history.json                   # 完整对话历史（messages 数组）
            ├── llm_debug.jsonl                # LLM 请求/响应调试日志
            ├── memory_delta.jsonl             # 本次 session 产生的记忆条目
            ├── plan_snapshot.json             # ExecutionPlan 持久化快照（W1）
            ├── traces.jsonl                   # 时序性能追踪（Stage 6/6.1）
            │
            └── tasks/                         # [Task] 所有子任务目录
                └── <task_id>/                 # 每个 SubAgent 任务一个目录
                    ├── output.log             # 实时输出（tab 切换数据源）
                    ├── events.jsonl           # 生命周期事件（状态变更、重试）
                    ├── result.json            # 任务完成结果（token 统计等）
                    └── manifest.json          # 任务全生命周期叙事文件（W1）
```

---

## 3. 路径管理：`storage/paths.py`

所有路径都通过 `AgentPaths` 统一获取，不在各模块中拼接字符串。

```python
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(project_root=Path.cwd())

# Workdir 级
paths.workdir_memory          # <root>/.agent/memory.jsonl
paths.permissions             # <root>/.agent/permissions.json

# Session 级
paths.session_dir(sid)        # <root>/.agent/sessions/<sid>/
paths.session_history(sid)    # <root>/.agent/sessions/<sid>/history.json
paths.session_meta(sid)       # <root>/.agent/sessions/<sid>/meta.json
paths.session_llm_debug(sid)  # <root>/.agent/sessions/<sid>/llm_debug.jsonl
paths.session_memory_delta(sid) # <root>/.agent/sessions/<sid>/memory_delta.jsonl
paths.session_plan_snapshot(sid) # <root>/.agent/sessions/<sid>/plan_snapshot.json

# Task 级
paths.task_dir(sid, tid)      # <root>/.agent/sessions/<sid>/tasks/<tid>/
paths.task_output(sid, tid)   # …/tasks/<tid>/output.log
paths.task_events(sid, tid)   # …/tasks/<tid>/events.jsonl
paths.task_result(sid, tid)   # …/tasks/<tid>/result.json
paths.task_manifest(sid, tid) # …/tasks/<tid>/manifest.json

# Global 级
paths.global_memory           # ~/.agent/memory.jsonl

# W2 Workdir 知识层（Stage 4）
paths.workdir_project_meta()       # <root>/.agent/project.json
paths.workdir_timeline()           # <root>/.agent/timeline.jsonl
paths.workdir_work_index()         # <root>/.agent/work_index.json
paths.workdir_open_threads()       # <root>/.agent/open_threads.json
paths.workdir_knowledge_index()    # <root>/.agent/knowledge_index.json

# W3 Global 知识层（Stage 5）
paths.global_self_profile()        # ~/.agent/self_profile.json
paths.global_projects_index()      # ~/.agent/projects_index.json
paths.global_cross_project_index() # ~/.agent/cross_project_index.json
paths.global_activity_log()        # ~/.agent/activity_log.jsonl

# Session 级（Stage 6 新增）
paths.session_traces(sid)          # <root>/.agent/sessions/<sid>/traces.jsonl

# Phase G 节奏治理（Stage 8）
paths.workdir_dir() / "phase_g_rhythm.json"   # <root>/.agent/phase_g_rhythm.json
```

**为什么需要统一路径管理层：**

在没有统一管理之前，路径散落在各模块中：`test_result/subagent_debug.jsonl`（硬编码在 sub_agent.py）、`.claude/logs/llm_debug_*.jsonl`（日期分割，不同 session 混合）、`sessions/`（直接在项目根）、`agent_permissions.json`（根目录散落）。

`AgentPaths` 使得任何路径变更只需修改一个文件，各模块不再需要感知目录结构。

---

## 4. 各子系统的存储说明

### 4.1 Session 持久化

**负责模块**：`session.py` → `SessionManager`

**存储位置**：`<project_root>/.agent/sessions/<session_id>/`

Session 使用目录格式而非单文件，原因是不同内容有不同的访问频率和清理策略：

- **`meta.json`** — 元信息，结构稳定，列表展示时只需读这一个文件，不必加载完整历史
- **`history.json`** — 完整对话历史（messages 数组），体积可能很大，按需加载

```json
// meta.json
{
  "id": "a3f28b1c",
  "title": "修复 JWT 过期验证 bug",
  "created_at": "2026-06-08T14:30:22",
  "updated_at": "2026-06-08T15:12:44",
  "provider": "anthropic",
  "model": "claude-opus-4-5",
  "stats": {
    "turns": 8,
    "input_tokens": 12400,
    "output_tokens": 3200,
    "tool_calls": 14
  },
  "summary": "定位并修复了 auth.py 中 JWT 过期校验逻辑使用了错误的 timedelta 单位..."
}
```

```json
// history.json — 顶层为数组
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": [...]},
  ...
]
```

**向后兼容**：旧格式的 `sessions/*.json` / `*.jsonl` 单文件仍可被 `SessionManager.load()` 读取，新 session 一律写目录格式。

**session_id 命名规则**：8 位随机 hex（如 `a3f28b1c`），由 `uuid4().hex[:8]` 生成。

---

### 4.2 记忆系统（Memory）

**负责模块**：`perception/memory_store.py` + `perception/memory_factory.py`

记忆系统有三个存储位置，对应三个不同的作用域：

#### 全局记忆：`~/.agent/memory.jsonl`

存储**跨项目通用经验**，即对所有项目都有参考价值的知识。

典型内容：
- 通用编程模式和注意事项（"Python 的 `timedelta` 参数单位容易混淆"）
- 常用工具的使用技巧
- 个人工作习惯和偏好

#### 项目记忆：`<project_root>/.agent/memory.jsonl`

存储**当前项目特有知识**，只对本项目有意义。

典型内容：
- 代码库的架构认知（"这个项目的认证逻辑在 `auth/jwt.py`"）
- 已解决的 bug 模式（"修复了 `auth.py` 中 JWT 过期校验的 timedelta 单位错误"）
- 项目特有工具和配置的使用方式

#### session 记忆增量：`…/sessions/<id>/memory_delta.jsonl`

本次 session 产生的记忆条目的**审计副本**。格式与 `memory.jsonl` 相同，仅用于追溯"这次对话生成了哪些记忆"。

**scope 字段决定写入位置**：

```python
# MemoryEntry.scope 决定写到哪个文件
entry = MemoryEntry(
    session_id="a3f28b1c",
    summary="...",
    scope="project"   # 或 "global"
)

# agent.py 中的路由逻辑：
if entry.scope == "global" and self._global_memory:
    self._global_memory.add(entry)   # → ~/.agent/memory.jsonl
else:
    self._memory.add(entry)          # → <project>/.agent/memory.jsonl
# 同时追加审计副本：
self._append_memory_delta(entry)     # → sessions/<id>/memory_delta.jsonl
```

**scope 判断原则**：

| 记忆内容 | scope |
|---------|-------|
| "Python `datetime.timedelta` 不能直接加减 `int`" | `global` |
| "修复了 `auth.py` line 42 的 JWT 校验 bug" | `project` |
| "这个项目用 PostgreSQL，连接字符串在 `config/db.py`" | `project` |
| "处理 Unicode 文件名需要 `errors='surrogateescape'`" | `global` |

**检索时合并两级**：

```python
from mini_agent.perception.memory_factory import merge_search

# 同时检索项目记忆和全局记忆，项目记忆优先
memories = merge_search(
    project_backend=self._memory,
    global_backend=self._global_memory,
    query="如何处理 JWT 过期",
    k=5,
)
```

---

### 4.3 LLM 调试日志

**负责模块**：`llm/debug_logger.py` → `LLMDebugLogger`

**启用条件**：`--debug-llm` CLI 参数 或 `LLM_DEBUG=1` 环境变量

**存储位置**：`<project_root>/.agent/sessions/<session_id>/llm_debug.jsonl`

每个 session 独立一个文件（而非旧版的按日期汇总），原因：

- 调试时关心的是"这次对话的请求"，不是"今天所有对话的请求"
- 按 session 隔离后，可以在 session 目录整体删除，不影响其他 session 的调试记录
- 不需要在日志里再打 session_id 字段过滤

**文件格式**：JSONL，每行一条记录，`event` 字段区分 `request` / `response` / `error`。

```jsonc
// 请求记录
{"seq": 1, "ts": "2026-06-08T14:32:01+00:00", "event": "request",
 "provider": "anthropic", "model": "claude-opus-4-5",
 "request": {"raw": {...}, "actual": {...}}}

// 响应记录
{"seq": 1, "ts": "2026-06-08T14:32:03+00:00", "event": "response",
 "duration_ms": 1842,
 "response": {"raw": {...}, "processed": {...}, "usage": {...}}}
```

**初始化时机**：`Agent._init_session()` 创建 session_id 后，立即通过 `init_debug_logger_for_session()` 将 logger 重新绑定到 session 目录：

```python
# agent.py 内部调用
init_debug_logger_for_session(
    cfg=existing_logger.cfg,
    project_root=self.cfg.project_root,
    session_id=self._session.id,
)
```

**fallback 路径**：若 session 尚未建立（如进程启动阶段），日志写入 `.agent/logs/llm_debug_<YYYYMMDD>.jsonl`。

---

### 4.4 SubAgent 任务文件

**负责模块**：`orchestrator/sub_agent.py` → `SubAgent`

**存储位置**：`<project_root>/.agent/sessions/<session_id>/tasks/<task_id>/`

每个 SubAgent 任务对应独立目录，包含三个文件：

#### `output.log` — 实时输出

SubAgent 运行过程中每一行输出都追加到此文件，格式为带时间戳的纯文本：

```
[14:32:01] Starting task: 分析认证模块
[14:32:01] Config: model=default, max_turns=10
[14:32:02] Agent built, running turn...
[14:32:05] Turn completed, output length: 843 chars
[14:32:05] Done. Tokens: 2341↑ 612↓, turns=3
```

此文件是**实现 tab 切换查看功能的数据源**（参见 SubAgent 输出查看机制），可在 task 执行过程中 `tail -f` 实时跟踪。

#### `events.jsonl` — 生命周期事件

记录 task 从创建到结束的所有关键状态变化，每行一条 JSON：

```jsonc
{"ts": 1749385921.3, "task_id": "abc123", "event": "sub_agent_start",   "details": {"task_name": "分析认证模块"}}
{"ts": 1749385921.4, "task_id": "abc123", "event": "acquiring_semaphore","details": {"label": "abc123 分析认证模块"}}
{"ts": 1749385921.5, "task_id": "abc123", "event": "semaphore_acquired", "details": {}}
{"ts": 1749385921.5, "task_id": "abc123", "event": "run_body_start",     "details": {"model": null, "max_turns": 10}}
{"ts": 1749385921.6, "task_id": "abc123", "event": "building_agent",     "details": {}}
{"ts": 1749385922.1, "task_id": "abc123", "event": "agent_built",        "details": {}}
{"ts": 1749385922.1, "task_id": "abc123", "event": "running_turn",       "details": {}}
{"ts": 1749385925.8, "task_id": "abc123", "event": "turn_completed",     "details": {"output_len": 843}}
{"ts": 1749385925.8, "task_id": "abc123", "event": "done",               "details": {"input_tokens": 2341, "output_tokens": 612, "tool_calls": 5, "turns": 3}}
```

标准事件类型：

| event | 触发时机 |
|-------|---------|
| `sub_agent_start` | SubAgent 线程启动 |
| `queued` | 等待 semaphore（并发槽位满） |
| `acquiring_semaphore` | 尝试获取槽位 |
| `semaphore_acquired` | 获得槽位，准备执行 |
| `run_body_start` | 开始执行（状态切换为 RUNNING） |
| `building_agent` | 开始构建 Agent 实例 |
| `agent_built` | Agent 构建完成 |
| `running_turn` | 开始调用 `run_turn()` |
| `turn_completed` | `run_turn()` 正常返回 |
| `llm_retry` | LLM 调用失败，准备重试 |
| `llm_retry_exhausted` | 重试次数耗尽 |
| `done` | 任务成功完成 |
| `failed` / `error` | 任务失败（含完整 traceback） |
| `cancelled_while_queued` | 在排队期间被取消 |
| `cancelled` | 任务被取消 |

#### `result.json` — 任务完成结果

任务正常完成时写入，记录最终统计数据：

```json
{
  "task_id": "abc123",
  "status": "done",
  "started_at": 1749385921.5,
  "finished_at": 1749385925.8,
  "input_tokens": 2341,
  "output_tokens": 612,
  "tool_calls": 5,
  "turns": 3,
  "output_len": 843
}
```

#### `manifest.json` — 任务全生命周期叙事文件（W1）

与 `events.jsonl`（被动记录每个状态变化时间点）和 `result.json`（仅终态统计）不同，`manifest.json` 是**主动写入**的结构化叙事，回答"这个任务的目标是什么、现在进展到哪、做出了哪些关键决策、最后留下了什么未解决的问题"。

- **任务创建时**（`SubAgent.__init__` 获得 `session_id` 后）立即写入一份初始版本，`goal`/`acceptance_criteria` 取自 `Task` 的对应字段（`goal` 为空时回退到 `prompt`）
- **执行过程中**由 agent 主动调用 `update_task_progress` 工具更新 `progress` 块和 `decision_log`（不是从 `events.jsonl` 被动推导）
- **任务结束时**（`DONE`/`FAILED`/`CANCELLED` 三种终态统一处理）补写 `outcome` 块

```jsonc
{
  "id": "abc123",
  "name": "修复 token 预算计算溢出问题",
  "initiator": "agent",
  "goal": "修复 token 预算计算溢出问题",
  "acceptance_criteria": ["所有现有单测通过"],
  "context_snapshot": {
    "related_files": [], "related_lessons": [],
    "parent_goal_id": null, "parent_task_id": null
  },
  "progress": {
    "current_step": "写新测试",
    "steps_done": ["定位根因", "修改计算逻辑"],
    "steps_remaining": ["写新测试", "跑测试套件"],
    "blockers": [],
    "last_updated": 1781856272.88
  },
  "decision_log": [
    {"at": 1781856272.9, "decision": "选择修改 _calc_budget() 而非 _trim_history()",
     "rationale": "", "alternatives_considered": []}
  ],
  "outcome": null   // 任务结束后才会有值，见下方
}
```

任务结束后 `outcome` 字段示例：

```jsonc
"outcome": {
  "status": "done",
  "summary": "修复完成……（result.output 的前 500 字符）",
  "artifacts": [],
  "unresolved": ["还有一个 edge case 未覆盖"],
  "lessons_generated": [],
  "token_cost": {"input": 100, "output": 50}
}
```

**对应工具**：`update_task_progress(task_id, current_step, steps_done, steps_remaining, blockers, note)`，详见 [Plan 与 Task 机制说明](plan-and-task-guide.md)。

**容错策略**：`manifest.json` 的所有写入操作（`TaskRecord.write_manifest()`）在异常时静默失败并返回 `None`，不会因为磁盘问题中断任务本身的执行。

#### `plan_snapshot.json` — ExecutionPlan 持久化快照（W1）

**负责模块**：`orchestrator/plan.py` → `ExecutionPlan`

**存储位置**：`<project_root>/.agent/sessions/<session_id>/plan_snapshot.json`（注意：与 `manifest.json` 不同，这个文件在 session 目录下，不在某个具体 task 目录下，因为一个 ExecutionPlan 对应一整个 session 的工作计划，可能驱动多个 SubAgent 任务）

`ExecutionPlan` 在内存中本是纯结构，但为了让长任务在 session 意外中断（进程崩溃、被杀死）后能够续跑，每次 `PlanTask` 状态变更（`add`/`start`/`complete`/`fail`）都会自动同步写入这个文件：

```json
{
  "goal": "完成 Phase A 基础设施清债",
  "created_at": 1781856272.46,
  "last_updated": 1781856272.46,
  "tasks": [
    {"id": "t1", "title": "history 条目加 _type 字段", "status": "done",
     "result": "已完成，见 commit abc123"},
    {"id": "t2", "title": "SubAgent 输出去截断", "status": "running", "result": ""},
    {"id": "t3", "title": "config.py 拆分", "status": "pending", "result": ""}
  ]
}
```

**恢复时机**：Agent 在 `_bind_session_extras()` 中绑定 session 时（无论是新建 session、`load_session()` 续接已有 session，还是 `new_session()` 开新对话），都会检测该路径下文件是否存在：

- 存在 → `try_restore_plan()` 解析并恢复为当前活跃 `ExecutionPlan`（中断时仍处于 `RUNNING` 状态的任务会被忠实保留为 `RUNNING`，由调用方决定是重新执行还是标记失败）
- 不存在 → 静默跳过，agent 以"无活跃 plan"状态正常启动，不阻塞流程

**注意**：恢复出来的 `PlanTask` 只保证 `id`/`title`/`status`/`result` 四个核心字段准确（精简 schema），不包含 `description`/`depends_on`/`parent_id`/`source` 等展示用字段，足以支持"恢复 DONE 步骤、从第一个非终态步骤继续"这一最低要求。

---

### 4.5 权限配置

**负责模块**：`permissions.py` → `PermissionGuard`

**存储位置**：`<project_root>/.agent/permissions.json`

记录用户选择"始终允许"或"始终拒绝"的工具调用规则，跨 session 持久生效。

```json
{
  "allow_list": [
    {"tool_name": "bash",       "path_prefix": ""},
    {"tool_name": "write_file", "path_prefix": "src/"}
  ],
  "denied_tools": ["delete_file"]
}
```

**`path_prefix` 的作用**：空字符串表示对该工具的所有调用放行；非空时只放行操作目标路径以此前缀开头的调用（精细控制，避免过宽授权）。

此文件可以提交到 git，方便团队共享权限配置（例如在 CI 环境中预设 `auto_approve` 相关规则）。

---

### 4.6 工具缓存

**负责模块**：`perception/tool_cache.py`

**启用条件**：`--tool-cache` 或 `tool_cache_enabled: true`

**存储位置**：`<project_root>/.agent/cache/tool_cache.json`

缓存 `read_file`、`web_search` 等工具在 session 内的调用结果，避免重复读取。此目录可以整体删除，不影响任何持久化数据。

---

### 4.7 HTTP API Token

**负责模块**：`api/auth.py`

**存储位置**：`<project_root>/.agent/agent_api.key`

启动 HTTP 服务时自动生成并保存，重启后复用（避免 token 变化导致客户端失效）。文件权限设为 `0600`，避免其他用户读取。此文件应加入 `.gitignore`。

---

### 4.5 W2 Workdir 知识层（Stage 4）

> 详见 [W2/W3 知识层指南](self-evolution-stage4-5-guide.md)

| 文件 | 路径方法 | 写入时机 | 读取时机 |
|------|---------|---------|---------|
| `project.json` | `workdir_project_meta()` | session 启动时（`ensure_project_meta`） | session 启动时注入 context |
| `timeline.jsonl` | `workdir_timeline()` | session 结束时追加 | session 启动时注入最近 N 条 |
| `work_index.json` | `workdir_work_index()` | 工具写入 / session end 时更新 | session 启动时扫描 open threads |
| `open_threads.json` | `workdir_open_threads()` | session end 导入未解决问题 | session 启动时注入 high-priority 线索 |
| `knowledge_index.json` | `workdir_knowledge_index()` | `update_knowledge` 工具调用时 | 按需检索 |

所有 W2 文件使用**原子替换写入**（`tmp → fsync → rename`），与 `memory.jsonl` 的追加写入模式不同。

### 4.6 W3 Global 知识层（Stage 5）

> 详见 [W2/W3 知识层指南](self-evolution-stage4-5-guide.md)

| 文件 | 路径方法 | 写入时机 |
|------|---------|---------|
| `self_profile.json` | `global_self_profile()` | session 结束时更新画像 |
| `projects_index.json` | `global_projects_index()` | 首次进入新项目时注册；session end 时刷新 `last_active_at` |
| `cross_project_index.json` | `global_cross_project_index()` | `scan_cross_project_patterns()` 扫描后合并写入（通常由 Phase G 8.4 或手动触发）|
| `activity_log.jsonl` | `global_activity_log()` | session 结束追加两行：`session_end`（主活动记录）+ `session_metrics`（Stage 6.3 异常检测基线）|

`activity_log.jsonl` 是**仅追加**的流水账，不做截断，长期使用后体积会持续增长。建议定期归档或按年/月分割。

### 4.7 时序追踪（Stage 6）

> 详见 [观察性系统指南](observability-guide.md)

| 文件 | 路径方法 | 写入时机 | 大小预估 |
|------|---------|---------|---------|
| `traces.jsonl` | `session_traces(sid)` | `run_turn` 每个阶段结束后追加 | ~1–5 KB/session（3–5 行/turn）|

`traces.jsonl` 随 session 目录存活，不会自动清理。`/diagnostics` 端点只读当前 session 的 traces，不扫描历史。长期项目可随 sessions 目录整体归档。

### 4.8 Phase G 节奏治理（Stage 8）

| 文件 | 位置 | 写入时机 |
|------|------|---------|
| `phase_g_rhythm.json` | `<root>/.agent/` | Phase G 每次运行后（记录 `_last_run_at`），以及每个提案发出后（记录 `prune:<name>` / `promote:<id>` 时间戳）|

此文件很小（纯时间戳字典），可以 git 提交（便于跨机器同步提案冷却状态），也可加入 `.gitignore`（允许每台机器独立触发）。

---

## 5. `.gitignore` 策略

```gitignore
# 运行时产物，不提交
.agent/sessions/       # 对话历史（含 llm_debug.jsonl / tasks/ / traces.jsonl）
.agent/cache/          # 可安全清除的缓存
.agent/logs/           # fallback 日志

# 敏感文件，不提交
.agent/agent_api.key

# 持久化数据，按需选择：
# .agent/memory.jsonl           # 项目记忆，通常不提交（个人知识）
# .agent/permissions.json       # 权限配置，团队项目建议提交
# .agent/project.json           # 项目身份证，通常不提交
# .agent/open_threads.json      # 个人工作线索，通常不提交
# .agent/phase_g_rhythm.json    # Phase G 冷却状态，按需决定
```

---

## 6. 数据清理参考

| 数据 | 清理方式 | 影响 |
|------|---------|------|
| 单个 session | `rm -rf .agent/sessions/<id>/` | 丢失该次对话历史、调试日志、`plan_snapshot.json`、`traces.jsonl`，该 session 的计划无法续跑 |
| 所有 session | `rm -rf .agent/sessions/` | 丢失所有对话历史，记忆不受影响；`/diagnostics` 性能分组将返回空 |
| 项目记忆 | `rm .agent/memory.jsonl` | 丢失项目级知识，全局记忆不受影响 |
| 全局记忆 | `rm ~/.agent/memory.jsonl` | 丢失跨项目通用经验 |
| 工具缓存 | `rm -rf .agent/cache/` | 下次运行重新构建缓存，无数据损失 |
| 单个 task 的 manifest | `rm .../tasks/<tid>/manifest.json` | 丢失该任务的进度叙事，不影响任务本身已完成的工作或 `result.json` |
| W2 知识层 | `rm .agent/project.json .agent/timeline.jsonl .agent/open_threads.json` | 丢失项目历史上下文，下次 session 重新初始化；`knowledge_index.json` 建议手动保留 |
| W3 Global 知识层 | `rm ~/.agent/self_profile.json ~/.agent/projects_index.json` | 丢失自我画像和项目注册表，不影响记忆与技能；Phase G 晋升候选需重新积累 |
| Phase G 节奏记录 | `rm .agent/phase_g_rhythm.json` | 重置所有提案冷却期，Phase G 下次运行将重新对所有候选提案 |
| 全局活动日志 | `rm ~/.agent/activity_log.jsonl` | 清空异常检测基线，需重新积累 10+ 条 session_metrics 记录后才能恢复异常检测能力 |
| 所有项目数据 | `rm -rf .agent/` | 完全重置，相当于全新项目；不影响 `~/.agent/` 全局数据 |

---

## 7. 相关文档

- [记忆管理指南](./memory-management-guide.md) — 记忆检索算法、后端扩展
- [SubAgent 机制说明](./subagent-mechanism.md) — 并发任务执行原理
- [Plan 与 Task 机制说明](./plan-and-task-guide.md) — `manifest.json`/`plan_snapshot.json` 的写入时机与对应工具
- [W2/W3 知识层指南](self-evolution-stage4-5-guide.md) — Workdir/Global 知识层详细说明
- [观察性系统指南](observability-guide.md) — `traces.jsonl` 格式与 `/diagnostics` 端点
- [Phase G 后台循环指南](self-evolution-phase-g-guide.md) — `phase_g_rhythm.json` 与节奏治理
- [配置指南](./config-guide.md) — 完整配置字段说明
- [权限管理](./permission-guide.md) — 权限系统详细说明

---

*最后更新：2026-06（新增 `plan_snapshot.json`、`manifest.json`，见 [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) Stage 0.2）*
