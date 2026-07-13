# Agent 设计与实现

> 本文详细说明 `mini_agent.agent`（对话循环核心）的架构、组件职责、执行流程以及关键机制。Stage 12 起，实现已由单文件 `agent.py` 拆分为 `src/mini_agent/agent/` 包（见 1.2），但本文描述的类结构、职责划分与执行流程本身未变——只是"代码物理上放在哪个文件"发生了变化，`from mini_agent.agent import Agent` 的导入方式与所有方法签名保持不变。

---

## 1. 概述

`Agent` 类是 mini-agent 的核心，负责：

- 维护对话历史
- 构建 System Prompt（注入 skill/memory/project 等上下文）
- 调用 LLM（支持流式/非流式、重试策略）
- 执行工具调用（权限检查、缓存、截断）
- 管理 Session 持久化
- 支持历史压缩、回退/重试机制

### 1.1 架构演进

**早期版本**：`agent.py` 承担所有职责，代码臃肿、难以测试。

**中期版本**：拆分为三个独立组件，Agent 本身退化为纯编排层：

| 组件 | 职责 | 依赖 |
|------|------|------|
| `ContextBuilder` | System prompt 构建（skill/memory/project 注入） | AppConfig, SkillLoader, MemoryStore |
| `ToolExecutor` | 工具执行（权限检查 + 调用 + 截断 + 缓存） | ToolRegistry, PermissionGuard, ToolResultCache |
| `HistoryManager` | 历史管理（追加、压缩、快照恢复） | AppConfig, SkillLoader |

即便拆出这三个组件，`agent.py` 本身仍然是一个近 4000 行、近 100 个方法的单体文件——`Agent` 类要负责的"编排"职责本身已经膨胀出了会话生命周期、反思、LLM 切换、角色 Agent 联动、提醒注入、历史压缩、快照回滚等一整套子职责。

### 1.2 Stage 12：agent.py 拆分为 agent/ 包

为了让"编排层"内部也保持可维护，`agent.py` 按职责拆分为 `src/mini_agent/agent/` 包，采用 **Mixin 组合**方式：`core.py` 只保留 `Agent` 类骨架与 `__init__`，其余方法按下表分散到各文件，最终通过多重继承组装回同一个类。这是纯粹的代码搬迁（不改变任何方法签名、调用方式或运行时行为），本文后续章节中出现的 `agent.py::方法名` 均可按此表换算为实际文件位置。

| 文件 | Mixin 类 | 覆盖的职责（对应下文章节） |
|------|----------|---------------------------|
| `core.py` | — | `__init__`，见 2.1/2.2 |
| `lifecycle.py` | `SessionLifecycleMixin` | 会话生命周期、项目扫描/文件监听启动、认知锚点，见 5.4/6.1/6.2 |
| `reflection.py` | `ReflectionMixin` | SessionEnd 反思流水线（lesson/timeline/workdir 知识/巩固/可观测性） |
| `profile.py` | `ProfileMixin` | 用户画像读取/刷新、会话摘要生成 |
| `llm_control.py` | `LLMControlMixin` | LLM 客户端与 Provider/模型切换、`_call_llm`，见 7 |
| `turn_loop.py` | `TurnLoopMixin` | 对话主循环 `run_turn`/`_agentic_loop`，见 3 |
| `role_judge.py` | `RoleJudgeMixin` | 角色 Agent 联动与轮次质量判定 |
| `reminders_correction.py` | `RemindersCorrectionMixin` | 提醒注入与人类反馈纠正检测 |
| `compaction.py` | `CompactionMixin` | 历史压缩，见 5.3 |
| `snapshot.py` | `SnapshotMixin` | 轮次快照/重试/回滚，见 5.2 |
| `_helpers.py` | — | 模块级共享辅助函数（非 Agent 方法） |

---

## 2. 类结构

### 2.1 构造函数签名

```python
Agent(
    cfg: AppConfig,
    registry: Optional[ToolRegistry] = None,
    skill_loader: Optional[SkillLoader] = None,
    guard: Optional[PermissionGuard] = None,
    llm_client: Optional[LLMClient] = None,
)
```

所有依赖均可从外部注入，便于单元测试。

### 2.2 核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `_history` | `list[dict]` | 对话历史（user/assistant/tool 消息） |
| `_llm` | `LLMClient` | LLM 客户端（支持运行时切换） |
| `_session_mgr` | `SessionManager` | Session 持久化管理器 |
| `_retry_policy` | `RetryPolicy` | LLM 重试策略 |
| `_turn_snapshot` | `Optional[dict]` | 当前轮的快照（用于 retry/rollback） |
| `_ctx_builder` | `ContextBuilder` | System prompt 构建器 |
| `_tool_executor` | `ToolExecutor` | 工具执行器 |
| `_hist` | `HistoryManager` | 历史管理器 |

### 2.3 感知组件

| 组件 | 字段 | 开关 |
|------|------|------|
| 项目扫描 | `_project_snapshot: Optional[str]` | `cfg.project_scan_enabled` |
| 文件监听 | `_file_watcher: FileWatcher` | `cfg.file_watch_enabled` |
| 工具缓存 | `_tool_cache: ToolResultCache` | `cfg.tool_cache_enabled` |
| 长期记忆 | `_memory: MemoryStore` | `cfg.memory_enabled` |

---

## 3. 核心流程

### 3.1 一轮对话 (`run_turn`)

```
run_turn(user_message)
  │
  ├─ [SYS-WATCH] 检测文件变化 → 追加到 user_message
  │
  ├─ [SYS-SKILL] 自动激活匹配的技能
  │
  ├─ _save_turn_snapshot()  ← 保存快照（用于 retry/rollback）
  │
  ├─ _history.append({"role": "user", "content": user_message})
  ├─ stats.turns += 1
  │
  ├─ _agentic_loop()  ──→ 核心循环（见下方）
  │   └─ 返回最终 assistant 文本
  │
  └─ save_session()  ← 自动保存会话
```

### 3.2 代理循环 (`_agentic_loop`)

```
_agentic_loop()
  │
  while loop_count < max_turns:
    │
    ├─ [SYS-TOKEN] token 预估 + 自动压缩检查
    │   └─ 超过阈值 → _auto_compress_history()
    │
    ├─ _call_llm()  ← 调用 LLM（流式/非流式）
    │   ├─ _build_system()  → 构建 system prompt
    │   ├─ _build_tool_schemas() → 工具定义
    │   └─ 重试策略（空输出自动重试）
    │
    ├─ _append_assistant_response(response)  ← 写入历史
    │
    ├─ [SYS-SKILL-DETECT] 记录实际使用的技能
    │
    └─ if not response.has_tool_calls:
         break  ← 无工具调用，返回最终文本
       else:
         _execute_tools(response)  ← 执行工具调用
         结果回注到历史
```

---

## 4. 三个核心组件

### 4.1 ContextBuilder

**职责**：构建 System Prompt，按顺序注入：

1. Agent 核心身份与行为规则（`prompts/`）
2. 当前时间
3. CLAUDE.md 项目上下文
4. 已激活 Skill（目录 + 内容）
5. [SYS-PROJ] 项目结构快照（如启用）
6. [SYS-MEMORY] 相关历史记忆（如启用）
7. [SYS-SKILL-TOOL] 技能目录（让模型知道有哪些 skill 可调用）

**懒加载机制**：

- 项目扫描使用 `getter` 懒加载，扫描未完成时跳过注入
- 技能上下文支持按查询词裁剪（`skill_chunking_enabled`）

### 4.2 ToolExecutor

**职责**：工具执行的全流程管理：

```python
execute_tools(response):
  for tc in response.tool_calls:
    ├─ 权限检查 (guard.check())
    ├─ [SYS-TOOLCACHE] 检查缓存
    ├─ 调用工具 (registry.call())
    ├─ [SYS-TRIM] 结果截断
    ├─ [SYS-TOOLCACHE] 写入缓存
    └─ 返回结果字符串列表
```

**结果截断策略**（`_maybe_trim_result`）：

| 工具类型 | 截断策略 |
|----------|----------|
| `bash` | 头部 20% + 尾部 60%（`bash_tail_ratio`，默认 0.6）+ 中间额外插入匹配到的错误/失败关键行（正则匹配 `FAILED`/`ERROR`/`Traceback`/`AssertionError` 等，最多 30 行），三段合并后用 `[N lines omitted]` 标注省略 |
| `read_file` | 头尾各取窗口的一半（窗口大小 = `read_window_lines`，0 时自动按阈值推算），省略中间段，提示用 `start_line`/`end_line` 读取指定范围 |
| `grep/glob` | 只保留前 `grep_max_lines`（默认 50）行 |
| 其他 | 通用头尾截断（头 15 行 + 尾 5 行） |

### 4.3 HistoryManager

**职责**：历史列表的全生命周期管理：

- `append_assistant_response()` — 追加模型回复
- `auto_compress_history()` — 自动压缩（token 超阈值时）
- `build_compact_context()` — 构建压缩后的 Skill 重附上下文

**压缩策略**：

1. 保留最近一半历史
2. 最老一半压缩为 summary（用户请求 + 工具调用统计）
3. [SYS-FORGET] 可选剔除纯工具结果消息（`<tool_result>`）
4. [SYS-SKILL-COMPACT] 重附 Skill 上下文（LRU + budget 约束）

---

## 5. 关键机制

### 5.1 重试策略 (`RetryPolicy`)

**触发条件**：默认 `EmptyOutputCondition`（模型返回空输出时重试）

```python
retry_policy.call_with_retry(
    call_fn=_do_single_call,
    on_retry=lambda a, r: print(f"Retry {a}: {r}")
)
```

**可配置参数**（通过 `AppConfig`）：

- `llm_retry_max` — 最大重试次数（默认 2）
- `llm_retry_delay` — 重试延迟（默认 0）
- `llm_retry_verbose` — 是否显示重试提示（默认 true）

### 5.2 手动重试/回退 (`[SYS-UNDO]`)

**`retry_last_turn()`**：

- 恢复到快照状态
- 提取相同用户消息
- 重新调用 `run_turn()`

**`rollback_turn()`**：

- 完全回退到上一轮结束时的状态
- 用户消息也一并撤销

**实现机制**：

每次 `run_turn()` 开始时保存快照：

```python
{
  "history":      deepcopy(_history),
  "stats_turns":  stats.turns,
  "stats_input":  stats.input_tokens,
  "stats_output": stats.output_tokens,
  "stats_tool":   stats.tool_calls,
}
```

### 5.3 Skill 压缩上下文 (`[SYS-SKILL-COMPACT]`)

当历史被压缩后，Skill 内容会被重新附加到历史末尾：

```python
compact_with_skills():
  ├─ 调用 LLM 生成摘要
  ├─ 构建 compact_context（LRU + budget）
  ├─ 新历史 = [摘要对] + [保留段]
  └─ 如果有 dropped skills，附加警告块
```

**预算约束**：

- `skill_compact_budget` — 总预算（默认 25,000 tokens）
- `skill_compact_per_skill` — 每个技能预算（默认 5,000 tokens）

### 5.4 Session 持久化

**每轮对话后自动保存**：

```python
save_session():
  ├─ 序列化历史
  ├─ 统计信息（turns, tokens, tool_calls）
  ├─ [SYS-SUMMARY] 达到门槛后生成摘要
  └─ 原子写入（文件锁保护）
```

**摘要生成**：

- 提取用户消息（过滤工具结果）
- 调用 LLM 生成 2-3 句话摘要
- 写回 session 文件 + 长期记忆

---

## 6. 感知子系统集成

### 6.1 项目扫描 (`[SYS-PROJ]`)

```python
_start_project_scan_async(project_root):
  └─ 后台线程 → ProjectScanner.scan() → _project_snapshot
```

**懒加载**：扫描完成前为 `None`，`_build_system()` 调用时若完成则注入。

### 6.2 文件监听 (`[SYS-WATCH]`)

```python
_start_file_watch_thread():
  └─ 后台线程每 2s → check_changes() → _pending_file_changes

run_turn():
  └─ 消费 _pending_file_changes → build_change_notice() → 追加到用户消息
```

**变化处理**：

1. 让缓存失效（`_tool_cache.invalidate_file()`）
2. 下一轮 `build_context()` 时重新注入

### 6.3 长期记忆 (`[SYS-MEMORY]`)

```python
_build_system():
  if _memory and _history:
    └─ search(last_user_message) → 相关记忆 → 注入 System Prompt
```

**记忆条目**：

- Session 摘要（来自其他 session）
- 关键词标签（自动提取）
- 关键结果（用户请求前 3 条）

---

## 7. 流式输出与推理

### 7.1 流式支持

```python
if cfg.stream:
  writer = StreamWriter()
  stream(
    messages=messages,
    system=system,
    tools=tools,
    on_token=writer.write,
    on_reasoning=_on_reasoning  # 支持推理流
  )
```

### 7.2 推理处理

**显示时机**：

- 流式：`on_reasoning` 回调实时显示
- 非流式：`response.reasoning` 完成后统一显示

**区块包裹**：

```python
R.print_reasoning_header()
R.console.print(reasoning_text, style="dim")
R.print_reasoning_footer()
```

---

## 8. 工具调用协议

### 8.1 原生工具调用（`--system-tool-call` 未启用）

```python
[_history]
  {"role": "assistant", "content": [
    {"type": "text", "text": "Let me check the file..."},
    {"type": "tool_use", "id": "123", "name": "read_file", "input": {"path": "x.py"}}
  ]}
  {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "123", "content": "..."}
  ]}
```

### 8.2 系统工具调用（`--system-tool-call` 启用）

```python
[_history]
  {"role": "assistant", "content": [
    {"type": "text", "text": "Let me check the file...\n<tool_use id='123'>read_file(path='x.py')</tool_use>"}
  ]}
  {"role": "user", "content": [
    "<tool_result tool_use_id='123'>...</tool_result>"
  ]}
```

由 `system_tool_call.convert_tool_use_to_text()` 统一转换。

---

## 9. 配置项

以下配置项影响 Agent 行为（通过 `AppConfig`）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_turns` | 50 | 单轮对话最大 LLM 调用次数（CLI: `--max-turns`） |
| `stream` | true | 是否流式输出 |
| `token_estimate_enabled` | false | 是否显示 token 预估 |
| `auto_compress_enabled` | false | 是否自动压缩历史 |
| `auto_compress_threshold` | 0.7 | 压缩触发阈值（token 占用率） |
| `tool_result_trim_enabled` | true | 是否截断长结果 |
| `tool_result_trim_threshold` | 4000 | 截断阈值（字符，约 1000 tokens） |
| `forget_policy_enabled` | false | 是否移除纯工具结果消息 |
| `skill_compact_budget` | 25000 | Skill 压缩总预算 |
| `skill_compact_per_skill` | 5000 | 每个 Skill 预算 |
| `session_summary_enabled` | false | 是否生成 session 摘要（需配合 `--memory` 才有意义） |
| `session_summary_min_turns` | 4 | 生成摘要的最小轮数 |

---

## 10. 测试要点

### 10.1 单元测试覆盖

`tests/agent_tests/` 应覆盖：

1. **历史管理**：
   - 追加/压缩/恢复基本功能
   - 空历史压缩边界
   - Skill 重附预算约束

2. **工具执行**：
   - 权限检查（允许/拒绝）
   - 缓存命中/未命中
   - 结果截断（不同工具类型）

3. **重试机制**：
   - 空输出自动重试
   - 手动重试/回退

4. **Session**：
   - 保存/加载
   - 摘要生成

### 10.2 集成测试

`test_cases/` 应覆盖：

1. 复杂任务拆解（多工具调用循环）
2. 长时间对话（历史压缩触发）
3. 文件变化感知（外部编辑触发通知）
4. Session 恢复（跨进程连续对话）

---

## 11. 后续改进

### P1（高优先级）

- **并发工具调用**：目前工具调用是串行的，可并行化不依赖的工具
- **更细粒度缓存**：支持 hash-based 文件内容缓存
- **更好的错误恢复**：LLM 调用失败时的降级策略

### P2（中等优先级）

- **增量历史压缩**：按 token 而非按轮数压缩
- **记忆增强**：引入向量检索替代关键词匹配
- **可观测性**：OpenTelemetry 追踪各组件耗时

---

*最后更新：2026-06（修正配置默认值表与 bash 结果截断策略描述，使其与当前 `_maybe_trim_result` 实现及 `config/models.py` 默认值一致；此前的截断策略描述对应的是早期"简单头尾截断"版本，未跟上后续"错误关键行优先"重构）*
