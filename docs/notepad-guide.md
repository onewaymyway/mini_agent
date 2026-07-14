# 记事本（Notepad）机制说明

mini-agent 的记事本是一个**常驻 system prompt 的持久便签**，供 Agent 在执行任务过程中
记录关键信息、关键结果、注意事项，防止在多轮工具调用或 history compact 之后遗忘。

**补充阅读**：
- [Compact 设计文档](compact-design.md) — 记事本与 history compact 的联动（第「与记事本的联动」节）
- [存储路径管理指南](storage-paths-guide.md) — `session_notepad(sid)` 路径定义
- [Prompt 管理指南](prompts-guide.md) — `system/notepad.md` 在 system prompt 组装链路中的位置
- [Commands & Tools 参考](commands-and-tools-reference.md) — `/notepad` 命令与 `notepad_*` 工具速查表
- [配置系统指南](config-guide.md) — `notepad_enabled` 开关字段说明

---

## 1. 核心概念

### 为什么需要记事本

Agent 在长任务中会经历多轮工具调用、多次 history compact。普通对话历史一旦被压缩，
细节（具体文件路径、某个中间结果、用户的一句强调）就只能靠 LLM 生成的摘要保留，
容易丢失或走样。记事本解决的正是这个问题：

- **常驻可见**：每一轮 LLM 调用，记事本的完整内容都会出现在 system prompt 的固定位置。
- **不受 compact 影响**：记事本不是对话历史的一部分，`history/compression.py` 和
  `agent/compaction.py` 的任何压缩路径都不会读取或改写它。
- **持久化**：落盘到对应 session 目录下的 `notepad.json`，session 恢复后自动加载。
- **agent 主导取舍**：不做自动截断——内容的增、删、改、总结全部由 agent 通过工具主动完成。

### 与 Plan（执行计划）的区别

| | 记事本 | Plan（执行计划） |
|---|---|---|
| 用途 | 零散的关键信息/结果/注意事项 | 结构化的任务分解与进度跟踪 |
| 结构 | 扁平列表（id + content + tag） | 任务树（parent/depends_on/status） |
| 典型内容 | "配置文件在 `/etc/x.conf`"、"接口有分页，注意 `page` 参数" | "任务 A 依赖任务 B，B 已完成，结果是……" |
| 详见 | 本文档 | [Plan 与 Task 机制说明](plan-and-task-guide.md) |

两者可以同时使用：Plan 跟踪"做到哪一步"，记事本记录"过程中学到/发现的事实"。

---

## 2. 数据模型

```python
# tools/notepad.py

@dataclass
class NotepadEntry:
    id: str                  # 6 位随机 hex，如 "7e22da"
    content: str              # 条目内容
    tag: Optional[str]        # 可选分类，如 "fact" / "result" / "caution" / "todo"
    created_at: str
    updated_at: str
```

`NotepadStore` 持有某个 session 的全部条目（`dict[id, NotepadEntry]` + 插入顺序列表），
提供增删改查和落盘方法，风格上与 `orchestrator/plan.py::ExecutionPlan` 一致：
纯数据结构 + 简单方法，不依赖框架其他部分。

---

## 3. 内置工具（`tools/notepad.py`）

| 工具 | 参数 | 说明 |
|------|------|------|
| `notepad_add` | `content`, `tag?` | 新增一条记事，返回分配的 id |
| `notepad_update` | `id`, `content` | 修改已有条目内容 |
| `notepad_remove` | `id` | 删除一条 |
| `notepad_list` | — | 列出全部条目及 id（一般无需调用——记事本内容已常驻 system prompt；仅在条目很多、需要精确 id 时使用） |
| `notepad_summarize` | `replace_ids`, `new_content`, `tag?` | 把多条条目合并为一条，用于瘦身 |

所有工具 `requires_approval=False`（不需要用户批准），每次调用后立即原子落盘。

### 使用示例

```
notepad_add(content="数据库连接串在 config/db.yaml，字段名是 dsn 不是 url", tag="caution")
notepad_add(content="测试跑通：42/42 passed", tag="result")

# 后续发现某条已过时
notepad_update(id="7e22da", content="数据库连接串已迁移到环境变量 DB_DSN")

# 记事本变得冗长时合并
notepad_summarize(
    replace_ids=["7e22da", "ae802c"],
    new_content="数据库用 DB_DSN 环境变量；测试全绿（42/42）",
    tag="summary",
)
```

---

## 4. System Prompt 注入机制

记事本的"常驻"能力来自**固定位置注入**，而不是把内容塞进对话历史：

```
context_builder.py::ContextBuilder.build(history)
    │
    ├─ base = build_system_prompt(...)          # agent_core / plan_mode / ... 等基础片段
    ├─ + persona 片段（如果有）
    ├─ + skill 目录
    ├─ + 记事本块  ← pm.render("system/notepad", notepad_content=...)
    ├─ + 项目结构快照
    ├─ + workdir/global 知识层
    ├─ + AgentSelfModel
    └─ + 长期记忆检索结果
```

`ContextBuilder.build()` 在**每一轮** LLM 调用前都会被调用一次，`notepad_getter` 每次都
重新读取 `NotepadStore` 的最新状态并渲染——这意味着：

1. 记事本内容永远是最新的（没有"上一轮的旧值"问题）；
2. 它完全独立于 `_history` 列表，因此 compact 无论怎么压缩对话历史，都碰不到记事本；
3. 若当前 session 尚未初始化记事本（`notepad_getter()` 返回 `None`），`build()` 会整体跳过
   这个块，不会注入一段空壳文字。

模板文件 `prompts/system/notepad.md` 包含两部分：
- 使用说明（何时必须记录、工具列表、写法建议）；
- `{{notepad_content}}` 占位符，渲染为 `NotepadStore.render()` 的输出（若为空则显示
  `(empty — nothing recorded yet)`）。

### 强制性引导

system prompt 中明确要求 agent 在遇到以下情况时**必须**调用 `notepad_add`：
- 关键结果/结论（计算值、测试结果、创建的文件路径、做出的决策及理由）
- 后续步骤要记住的约束/坑（"配置 X 必须与 Y 保持同步"、"这个 API 返回分页数据"）
- 用户明确要求记住的信息

这是软性的 prompt 引导，不是强制拦截——工具本身不会阻止 agent 不使用它。

---

## 5. 持久化与生命周期

| 存储路径 | `<project_root>/.agent/sessions/<session_id>/notepad.json` |
|---|---|
| 写入方式 | 原子写（`tempfile.mkstemp` + `os.fsync` + `os.replace`），与项目内其它持久化模块（如 `perception/goal_backlog.py`）一致 |
| 加载时机 | `NotepadStore` 首次被 `get_current_notepad()` 访问时按需构造并读盘，随后在进程内按 `session_id` 缓存，避免重复 IO |
| 配置入口 | `agent/lifecycle.py::_init_components()` 调用 `configure_notepad_store(paths_getter, session_id_getter)` 注入懒引用 |
| 生命周期 | `/session new`、`/compact` 都不会清空记事本；仅 `/notepad clear`（用户手动）或 `notepad_remove`/`notepad_summarize`（agent 主动）会改变内容 |
| 多 session 隔离 | 按 `session_id` 严格隔离，与 `SessionAgentPool` 的多会话架构天然兼容 |

### 5.1 配置开关（`notepad_enabled`）

```python
# config/models.py::AppConfig
notepad_enabled: bool = True
```

```json
// agent_config.json
{ "notepad_enabled": false }
```

默认 **开启**。关闭后：

- `ContextBuilder.build()` 不再注入记事本块（`_get_notepad_render_text()` 返回 `None`）
- `get_current_notepad()` 返回 `None`，`notepad_add`/`update`/`remove`/`list`/`summarize`
  调用会抛出 `RuntimeError`（提示"记事本已被配置关闭"），被 `ToolExecutor` 捕获后
  转为工具错误结果返回给模型，而不是让进程崩溃
- `/notepad` 命令输出"Notepad is disabled"提示
- `agent/compaction.py::_build_notepad_compact_hint()` 直接返回空字符串（`get_current_notepad()`
  为 `None`，见其内部判断），不会追加总结提示

工具本身**仍注册在全局 registry 中**（模型仍能看到 `notepad_add` 等工具定义），只是
调用会失败——这与 `workdir_knowledge_enabled` 等既有开关的取舍一致，不额外做"按 cfg
动态隐藏工具定义"的机制（那需要改造 `ToolRegistry`，成本与收益不成比例）。

**并发安全**：`configure_notepad_store()` 用 `threading.local()` 存储 paths/session_id/
enabled 三个 provider（与 `tools/evolution.py::set_project_root_provider`、
`tools/workdir_knowledge.py::set_project_root_provider` 同款写法），而不是普通模块级
全局变量——这样在多 Agent/多线程并发场景（`orchestration.py` 的 `spawn_agent` 等）下，
"Agent A 关闭了记事本"不会影响运行在另一个线程里的"Agent B 开启了记事本"。`NotepadStore`
实例缓存（按 `session_id` 索引）本身是跨线程共享的只读缓存，session_id 全局唯一，不受
此影响。

---

## 6. 与 History Compact 的联动

记事本本身**不参与** compact 的输入/输出——它不需要被"摘要进"compact 结果，因为它本来
就常驻在 system prompt 里。

但当记事本总字数超过阈值（`agent/compaction.py::CompactionMixin.NOTEPAD_COMPACT_HINT_THRESHOLD`，
默认 **20000 字符**）时，`compact_with_skills()` 的正常路径（`run_turn(compact_prompt)`）
会在发给模型的 compact 提示末尾追加一句提示，建议模型调用 `notepad_summarize` 合并
冗余/过时的条目——**这只是建议，不是自动截断**，取舍完全由模型在该轮工具调用中决定。

超限路径（`_compact_chunked`，历史本身已超出上下文窗口时的降级路径）不会追加此提示：
该路径直接调用 `_llm.chat_with_retry`，绕开 `run_turn`，模型在这次调用里无法执行任何
工具调用，加提示也没有意义。

详见 [Compact 设计文档 · 与记事本的联动](compact-design.md)。

---

## 7. CLI 命令（`/notepad`）

| 命令 | 说明 |
|------|------|
| `/notepad` 或 `/notepad show` | 显示当前记事本内容（条目数、总字数、每条的 id/tag/content） |
| `/notepad clear` | 清空当前记事本（仅供用户手动操作，agent 不会自动调用） |
| `/notepad remove <id>` | 删除指定条目 |

实现位于 `cli/commands/notepad.py`，通过 `repl.py::_handle_slash()` 分发。

`/notepad` 及其子命令（`show`/`clear`/`remove`）已注册进 `ui/terminal.py` 的命令补全表
（`_COMMANDS`），输入 `/notepad` 时会自动提示子命令；同时也已加入 `cli/parser.py` 的
`/help` 输出。

---

## 8. 相关代码位置

| 文件 | 内容 |
|---|---|
| `tools/notepad.py` | `NotepadEntry` / `NotepadStore` 数据结构、5 个内置工具、`configure_notepad_store()` |
| `prompts/system/notepad.md` | 使用说明模板 + `{{notepad_content}}` 占位符 |
| `context_builder.py::ContextBuilder.build()` | 每轮注入记事本块 |
| `agent/lifecycle.py::_init_components()` | 注入 `notepad_getter`，调用 `configure_notepad_store()` |
| `agent/lifecycle.py::_get_notepad_render_text()` | 供 `ContextBuilder` 调用的渲染文本 getter |
| `agent/compaction.py::_build_notepad_compact_hint()` | 记事本超阈值时追加的 compact 提示语 |
| `storage/paths.py::AgentPaths.session_notepad()` | `notepad.json` 的路径定义 |
| `config/models.py::AppConfig.notepad_enabled` | 功能总开关字段（默认 `True`） |
| `config/loader.py` | `notepad_enabled=_fb("notepad_enabled", None, True)` — JSON 配置加载 |
| `cli/commands/notepad.py` | `/notepad` 命令实现 |
| `cli/app.py` | `import mini_agent.tools.notepad` 触发工具注册（side-effect import） |
| `ui/terminal.py::_COMMANDS` | `/notepad` 命令行自动补全 + 子命令提示（`show`/`clear`/`remove`） |
| `cli/parser.py` | `/help` 输出中的 `/notepad` 说明文本 |

---

## 9. 设计取舍说明

- **为什么固定位置注入而不是追加到历史末尾**：system prompt 每轮都会重新组装，天然不受
  compact 影响；而如果把记事本内容作为一条历史消息追加，需要额外机制防止它被 compact
  当成普通历史处理，实现更复杂也更脆弱。
- **为什么 compact 超限路径不联动提示**：该路径本身就是"模型无法看到完整历史"的降级
  场景，加入需要工具调用的提示只会造成困惑。
- **为什么不做自动截断**：记事本内容的重要性无法用简单规则（如时间、长度）判断，交给
  模型在具备上下文的情况下主动总结，比机械截断更安全。

---

*首次发布：2026-07*
