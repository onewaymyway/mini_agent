# 并发编排系统指南

> 本文档介绍 `orchestrator/` 模块的架构、组件和使用方式。

---

## 1. 模块概述

`orchestrator/` 是 mini_agent 的**并发任务调度与 Sub-Agent 管理**核心模块，负责：

- **ExecutionPlan / PlanTask** — 结构化执行计划的定义、状态跟踪和 system prompt 注入
- **TaskManager** — 真正的并发执行引擎（纯线程模型）
- **SubAgent** — 子 Agent 的生命周期管理、历史隔离和结果回调
- **并发控制** — 信号量限制并发任务数和并发 LLM 调用数
- **Task 日志实时查看** — 支持方向键切换查看各任务实时日志

---

## 2. 包结构

```
orche
strator/
├── __init__.py          ← 统一导出公共 API
├── plan.py              ← ExecutionPlan / PlanTask — 计划定义与状态
├── plan_display.py      ← 计划进度可视化
├── task.py              ← Task / TaskRecord / TaskStatus — 任务数据模型
├── task_display.py      ← 任务表格/日志显示
├── task_manager.py      ← TaskManager — 并发调度器
├── sub_agent.py         ← SubAgent — 子 Agent 生命周期
├── concurrency.py       ← CountingSemaphore — 并发控制
├── status_bar.py        ← StatusBar — 状态栏
└── agent_profiles.py    ← AgentProfile — 自定义子 Agent 配置
```

---

## 3. 两层架构

### 3.1 计划层（ExecutionPlan / PlanTask）

`ExecutionPlan` 是**纯数据模型**，用于定义任务的结构化执行计划：

```python
from mini_agent.orchestrator.plan import ExecutionPlan, PlanTask

plan = ExecutionPlan(
    goal="Add unit tests for utils.py",
    tasks=[
        {"id": "read", "title": "Read utils.py"},
        {"id": "write", "title": "Write tests", "depends_on": ["read"]},
    ]
)
```

**特点**：
- 注入 system prompt 供模型理解当前计划
- 不启动任何线程，纯声明式
- 支持 `depends_on` 定义执行顺序
- 支持 `parent_id` 定义父子关系（视觉分组）

### 3.2 执行层（TaskManager / SubAgent）

`TaskManager` 是**真正的并发执行引擎**：

```python
mgr = TaskManager(base_cfg, max_workers=4)
mgr.start()

t1 = mgr.submit(Task(prompt="Write unit tests for parser.py"))
t2 = mgr.submit(Task(prompt="Fix the bug in utils.py", depends_on=[t1]))

mgr.wait_all()
mgr.stop()
```

**特点**：
- 纯线程模型（`threading`），不依赖 asyncio
- 单后台调度线程持续轮询，将满足条件的 PENDING 任务投入执行
- 外部线程安全：所有状态访问通过 `_lock` 保护
- 任务完成后记录保留（可查询历史）

---

## 4. SubAgent 机制

SubAgent 是 TaskManager 中每个任务的**实际执行者**，具有以下特性：

| 特性 | 说明 |
|------|------|
| 独立历史 | 每个 SubAgent 拥有独立的对话历史 |
| 独立统计 | 独立的 token 用量、工具调用次数等统计 |
| 配置继承 | 继承主 Agent 的 LLMConfig（可覆盖 provider/model） |
| 输出回调 | 输出通过回调写入 TaskRecord.log_lines（不直接打印 stdout） |
| 自动重试 | 对可重试错误（HTTP 5xx、超时）自动重试最多 3 次 |
| 线程安全 | 通过 TaskRecord 的 lock 保护并发访问 |
| Debug 日志 | 写入 `test_result/subagent_debug.jsonl` |

### 4.1 信息继承（Stage 3.3）

SubAgent 启动时会继承主 Agent 的以下状态：

- **激活的 Skill 列表** — SubAgent 可使用与主 Agent 相同的技能
- **工具结果缓存** — 共享 `ToolResultCache`，避免重复读取相同文件
- **Lesson 回流** — SubAgent 结束时将自己产生的 lesson 回传给主 Agent

### 4.2 降级重试链（Stage 7 / 13.2+15.3）

任务失败时的降级策略：

1. 按 `Task.fallback_profiles` 切换 profile
2. 按 `Task.demotion_scope` 缩小目标
3. 而非立即宣告失败

---

## 5. 并发控制

`orchestrator/concurrency.py` 提供两个独立的 `CountingSemaphore`：

| 信号量 | 限制内容 | 默认值 |
|--------|---------|--------|
| 任务信号量 | 最大并发 SubAgent 数 | 4（可通过 `--workers` 调整） |
| LLM 信号量 | 最大并发 LLM 调用数 | 8（可通过 `--max-llm-calls` 调整） |

### 5.1 运行时调整

```bash
/concurrency tasks 8      # 设置最大并发任务数为 8
/concurrency llm 16       # 设置最大并发 LLM 调用数为 16
/concurrency              # 查看当前并发状态
```

---

## 6. Task 日志实时查看

详见 [Task 日志实时查看指南](task-focus-viewing.md)。

**核心功能**：
- 支持方向键实时切换查看不同任务的日志输出
- 状态栏显示任务状态概要
- 专用渲染线程确保输出不混乱

---

## 7. Agent Profile（自定义子 Agent）

详见 [自定义子 Agent 指南](custom-sub-agents.md)。

**核心概念**：
- 通过 `.agent/agents/*.md` 文件定义自定义子 Agent
- 每个 profile 包含 `name`、`description`、`inputs`（required/optional）
- 通过 `spawn_named_agent` 工具调用
- 支持预定义 profile（如 `coach`、`code-reviewer`、`evolution-agent` 等）

---

## 8. 与系统其他部分的交互

### 8.1 与 Skill 系统

- SubAgent 继承主 Agent 激活的 Skill 列表
- Skill 内容注入 SubAgent 的 system prompt

### 8.2 与记忆系统

- SubAgent 结束时将 lesson 回传给主 Agent
- 主 Agent 的 MemoryBackend 对所有 SubAgent 可见

### 8.3 与工具系统

- 共享 `ToolResultCache`（如果启用）
- SubAgent 使用与主 Agent 相同的工具注册表

### 8.4 与 CLI 命令

- `/tasks` — 查看任务列表和状态
- `/plans` — 查看/创建执行计划
- `/concurrency` — 调整并发限制

---

## 9. 相关文档

- [Plan 与 Task 指南](plan-and-task-guide.md) — 执行计划的使用方式
- [SubAgent 机制](subagent-mechanism.md) — SubAgent 的详细设计
- [Task 日志实时查看](task-focus-viewing.md) — 方向键切换查看任务日志
- [自定义子 Agent 指南](custom-sub-agents.md) — AgentProfile 机制
- [并发编排系统指南](orchestrator-guide.md) — 本文档

---

*最后更新：2026-07*
