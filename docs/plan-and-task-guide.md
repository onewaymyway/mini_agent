# Plan 与 Task 机制说明

mini-agent 的执行计划系统让 Agent 在运行过程中能够明确地规划、跟踪和展示自己的工作流程。

**补充阅读**：
- [Task 日志实时查看与切换](task-focus-viewing.md) — 方向键实时查看任务日志机制

---

## 1. 核心概念

### 执行计划（ExecutionPlan）

执行计划是 Agent 的"工作记忆"——一棵由 PlanTask 节点组成的有向无环图（DAG）。它不启动任何子进程，纯粹是结构化的状态记录，有两个核心作用：

1. **注入 System Prompt**：每次 LLM 调用时，当前计划树（包含进度、依赖、结果摘要）自动注入到 system prompt，让模型始终知道"我在哪、做了什么、下一步是什么"。
2. **CLI 实时展示**：计划树以树形结构显示在终端底部状态栏，用户可以实时看到执行进度。

### 任务节点（PlanTask）

每个任务节点包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 短 ID，用于 `depends_on` 和 `parent_id` 引用（如 `"t1"`、`"read"`） |
| `title` | str | 简短标题，显示在 CLI 中 |
| `description` | str | 详细说明，running 时注入 prompt |
| `parent_id` | str? | 父任务 ID，形成层级（树形缩进） |
| `depends_on` | list[str] | 前置依赖 ID 列表（必须全部 DONE 才能 start） |
| `source` | TaskSource | 创建来源：`plan` / `task` / `user` |
| `created_by` | str? | 若 source=task，记录是哪个任务 ID 动态创建了本任务 |
| `status` | PlanTaskStatus | 当前状态 |
| `result` | str | 完成后的结果摘要（持久注入后续 prompt） |
| `error` | str | 失败原因 |

---

## 2. 两种任务关系

### 父子关系（parent_id）

**组织归属**，体现在 CLI 树形缩进中，不强制执行顺序。

```
◉ [write]  编写测试文件
  └─ ○ [fixture]  创建测试夹具数据    ← parent_id="write"
  └─ ○ [mock]    创建 Mock 对象       ← parent_id="write"
```

子任务在视觉上属于父任务，但执行顺序由 `depends_on` 控制。

### 依赖关系（depends_on）

**执行顺序约束**，某任务的 `depends_on` 列表里所有任务都 DONE 后，该任务才能 start。可以跨越父子层级。

```
○ [test]  运行测试    depends_on=["write", "fixture", "mock"]
```

两种关系可以组合使用：

```python
create_plan(
    goal="为 utils.py 编写并运行单元测试",
    tasks=[
        {"id": "read",    "title": "读取 utils.py"},
        {"id": "write",   "title": "编写测试文件",   "depends_on": ["read"]},
        {"id": "fixture", "title": "创建测试夹具",   "depends_on": ["read"],
                           "parent_id": "write"},
        {"id": "mock",    "title": "创建 Mock 对象", "depends_on": ["read"],
                           "parent_id": "write"},
        {"id": "run",     "title": "运行测试",        "depends_on": ["write", "fixture", "mock"]},
    ]
)
```

---

## 3. 任务状态机

```
PENDING ──start_task()──→ RUNNING ──complete_task()──→ DONE
                                  └──fail_task()────→ FAILED
                                                          │
PENDING（依赖 FAILED/SKIPPED）────────────────────→ SKIPPED
```

`fail_task()` 会自动传播：所有直接或间接依赖了失败任务的 PENDING 任务都被标记为 SKIPPED（级联传播）。

---

## 4. 创建来源（TaskSource）

每个任务都有明确的来源标记，在 CLI 中以不同颜色显示：

| source | 含义 | CLI 标注 |
|--------|------|----------|
| `plan` | 在 `create_plan` 时定义 | 无标注（默认） |
| `task` | 某个运行中的任务动态追加 | 橙色 `← from:task_id` |
| `user` | 用户通过 CLI 手动追加 | 紫色 `[user]` |

当某任务在执行过程中发现需要额外步骤时，用 `add_task()` 追加并设置 `created_by`，让计划树清楚反映"这个任务是谁派生出来的"。

---

## 5. Agent 工具接口

Agent 通过以下 7 个工具管理执行计划：

### `create_plan(goal, tasks)`

创建完整计划。**有 2 个及以上步骤时就应该创建计划**，不需要等到"很复杂"。

```json
{
  "goal": "为 utils.py 添加单元测试",
  "tasks": [
    {"id": "read",  "title": "读取 utils.py"},
    {"id": "write", "title": "编写测试", "depends_on": ["read"],
     "description": "覆盖所有公共函数"}
  ]
}
```

返回值包含 `next_task`，指示第一个可以开始的任务。

### `start_task(task_id)`

标记任务开始（`pending → running`）。**在开始实际工作前调用**。如果依赖未满足，返回错误。

### `complete_task(task_id, result)`

标记任务完成（`running → done`）。`result` 字段会持久出现在后续所有 LLM 调用的 prompt 中，供后续任务参考。**写有意义的结果摘要**。

### `fail_task(task_id, error)`

标记任务失败（`running → failed`）。依赖此任务的所有任务自动跳过。

### `add_task(id, title, ..., created_by)`

在执行过程中动态追加任务。关键参数：
- `created_by`：当前运行任务的 ID（会显示 `← from:id` 标注）
- `parent_id`：视觉分组（树形缩进）
- `depends_on`：执行顺序约束

### `get_plan_status()`

返回完整计划状态 JSON，包含所有任务的状态、依赖、来源、结果。

### `clear_plan()`

清除当前计划。开始无关联的新任务时调用。

---

## 6. 标准工作流

```
create_plan(goal, tasks)              ← 先规划
      │
      ▼
start_task("t1")                      ← 开始第一步
  [实际工具调用：bash / read_file / ...]
  [如果发现新步骤：add_task(..., created_by="t1")]
complete_task("t1", result="...")     ← 完成，记录结果
      │
      ▼
start_task("t2")                      ← 依赖满足后开始下一步
  [实际工具调用...]
complete_task("t2", result="...")
      │
      ▼
  [所有任务完成或失败，计划结束]
```

---

## 7. CLI 展示

### 状态栏（底部实时显示）

```
  📋 Plan  [████░░░░░░]  为 utils.py 添加单元测试  2/4 done  1 running
     ✓ [read]    读取 utils.py  1.2s
     ◉ [write]   编写测试文件  → after read
      └─ ○ [fixture]  创建测试夹具  ← from:write
     ○ [run]     运行测试  → after write
```

图例：
- `○` pending（等待执行）
- `◉` running（当前执行，青色）
- `✓` done（绿色）
- `✗` failed（红色）
- `—` skipped（黄色）
- `→ after xxx` 依赖关系（仅 pending 时显示）
- `← from:id` 橙色，由某任务动态创建
- `[user]` 紫色，用户手动追加
- 树形缩进 = 父子关系

### 完整树形视图（/plan 命令）

```
╭──────────────── Execution Plan ────────────────╮
│ 为 utils.py 添加单元测试 [████░░░░] 2/4  1 running │
│ ✓ [read]   读取 utils.py  1.2s                  │
│   ↳ 找到 5 个公共函数                            │
│ ◉ [write]  编写测试文件  → after read            │
│   ├── ○ [fixture]  创建测试夹具  ← from:write   │
│   └── ○ [mock]     创建 Mock    ← from:write   │
│ ○ [run]    运行测试  → after write              │
╰─────────────────────────────────────────────────╯
```

---

## 8. /plan 命令

| 命令 | 说明 |
|------|------|
| `/plan` | 显示当前执行计划（Rich 树形） |
| `/plan clear` | 清除当前计划 |
| `/plan summary` | 打印完成摘要表格（含用时、结果、来源） |

---

## 9. System Prompt 注入示例

每次 LLM 调用时，当前计划自动注入如下文本：

```
## Current execution plan
Goal: 为 utils.py 添加单元测试
Progress: 2/4 done, 1 running

✓ [read] 读取 utils.py
◉ [write] 编写测试文件 (after: read)
  ○ [fixture] 创建测试夹具 (after: read) [from:write]
○ [run] 运行测试 (after: write)

**Currently executing**: [write] 编写测试文件
  覆盖所有公共函数

**Completed results** (available for subsequent tasks):
  [read] 读取 utils.py: 找到 5 个公共函数: parse(), validate(), format(), load(), save()
```

这让 LLM 在长流程中不会"迷失"——即使对话历史被压缩，执行进度也始终清晰可见。

---

## 10. 实现文件

| 文件 | 职责 |
|------|------|
| `src/mini_agent/orchestrator/plan.py` | 数据模型：`ExecutionPlan`、`PlanTask`、`PlanTaskStatus`、`TaskSource` |
| `src/mini_agent/tools/plan.py` | Agent 工具：`create_plan`、`start_task`、`complete_task` 等 7 个工具 |
| `src/mini_agent/orchestrator/plan_display.py` | 渲染：状态栏紧凑行、Rich 树形视图、完成摘要表格 |
| `src/mini_agent/orchestrator/sub_agent.py` | Sub-Agent 执行单元（线程包装、重试机制、输出捕获） |
| `src/mini_agent/orchestrator/task_manager.py` | 并发任务调度器（依赖解析、调度循环、SubAgent 管理） |
| `src/mini_agent/prompts/system/plan_mode.md` | System prompt 片段：告知 LLM 如何使用计划工具 |

---

> 最后更新：2026-06（反映 src/mini_agent 包布局重构，/plan 命令实现已从 main.py 迁移至 src/mini_agent/cli/commands/plans.py）
