# Stage 9 自主运行时指南（Phase H）

> 对应 `next_doc/self_evolution_stage9_plan.md`，在 Stage 0-8 的全部基础设施之上，为 agent 引入"常驻守护进程 + 跨会话目标层级 + 三档位自主调度"能力。

---

## 1. 架构概述

Stage 9 的核心变化是**进程模型升级**：

```
旧模型（Stage 0-8）：
  每次 CLI 启动 → 进程内创建 Agent → 交互完成 → 进程退出

新模型（Stage 9）：
  mini-agent daemon start     ← 一次性操作，Agent 常驻
       ↓
  daemon 进程（持续运行）
    ├─ AgentRunner 线程（消费 InputQueue）
    ├─ AutonomousLoop（tick 调度）
    └─ HTTP API（FastAPI/uvicorn）
       ↑
  CLI 连接模式（随时进入/退出，daemon 不受影响）
  Web 客户端（现有 Streamlit Demo）
```

关键设计原则：
- **daemon 与 workdir 绑定**，不是全局唯一 daemon，每个项目有自己的 daemon
- **IPC 直接复用现有 HTTP API**（POST `/v1/chat` + GET `/v1/stream`），不新增协议
- **CLI 连接模式**与现有 Web 端接入方式完全对称
- **`--no-daemon` 回退**：CI/脚本场景可完全跳过 daemon 机制

---

## 2. 守护进程管理（`cli/daemon.py`）

### 2.1 三条子命令

```bash
# 前台启动（开发调试）
mini-agent daemon start

# 后台启动（生产使用）
mini-agent daemon start --detach

# 指定端口
mini-agent daemon start --detach --http-port 9000

# 停止
mini-agent daemon stop

# 查看状态（PID、端口、autonomy_level、上次 tick 时间）
mini-agent daemon status
```

### 2.2 PID 文件管理

| 文件 | 路径 | 内容 |
|------|------|------|
| PID 文件 | `<project_root>/.agent/daemon.pid` | 进程 PID（整数） |
| info 文件 | `<project_root>/.agent/daemon_info.json` | `{"pid": N, "http_port": N, "started_at": T}` |

进程退出时自动清理两个文件。进程异常死亡后残留文件在下次 `daemon start/status` 时自动清理。

### 2.3 CLI 连接模式

当 daemon 已运行时，`mini-agent` 启动后自动进入"连接模式"：

```
[daemon] Connected to running daemon (PID=12345, port=8765)
[daemon] Type your message, or 'exit' to disconnect (daemon keeps running)

orzooo (connected) ❯ 帮我重构这个函数
...（流式输出）
orzooo (connected) ❯ exit
[daemon] Disconnected (daemon continues running)
```

输入 `exit` 只是断开 CLI 连接，daemon 继续运行。

### 2.4 `--daemon-mode` 标志

`daemon start --detach` 内部通过 `--daemon-mode` 标志调用主入口：

```bash
python -m mini_agent --http --http-port 8765 --daemon-mode
```

`--daemon-mode` 时：
1. 启动 HTTP 服务（`--http` 已含）
2. 写入 PID 文件
3. 阻塞等待 SIGTERM/SIGINT，不启动交互 REPL
4. 收到信号时优雅关闭 HTTP 服务并清理 PID 文件

---

## 3. Goal Backlog（`perception/goal_backlog.py`）

跨会话目标层级，存储在 `<project_root>/.agent/goals.json`。

### 3.1 数据结构

```
Goal（长期目标）
  └─ Objective（子目标，可关联 WorkThread）
       └─ Task（单次执行，通过 InputQueue 提交）
```

`GoalNode` 统一表示两层节点，通过 `level` 字段区分：

```python
GoalNode:
  id              str     # "goal_abc12345" | "obj_def67890"
  level           str     # "goal" | "objective"
  title           str     # 节点标题
  source          str     # "user" | "agent_derived"
  status          str     # "active" | "paused" | "completed" | "abandoned"
  created_at      float
  last_touched_at float
  progress_notes  str     # 进展备注
  parent_id       str?    # Objective 指向其父 Goal 的 id
  children_ids    list    # Goal 的子 Objective id 列表
  work_thread_ref str?    # Objective 关联的 WorkThread id（复用 work_index.json）
  priority        int     # 数字越大越优先，默认 0
  tags            list
```

### 3.2 与 WorkThread 的关系

Objective 通过 `work_thread_ref` 字段引用已有 `WorkThread`（Stage 4 的 `work_index.json`），复用其 `cumulative_progress`/`next_suggested`，不重复维护进展文本。

### 3.3 CLI 命令

```bash
# 查看 Goal Backlog
/agent goals
/goals          # 快捷方式

# 添加 Goal
/agent goals add "重构认证模块" --priority 10 --tag backend,security

# 添加 Objective（关联到 Goal 和 WorkThread）
/agent goals obj add "完成接口层重构" --goal goal_abc12345 --thread wt_xyz

# 标记完成
/agent goals done obj_def67890

# 更新进展
/agent goals progress obj_def67890 "接口层已完成，单测覆盖率 85%"

# 查看 AutonomousLoop 状态
/agent goals status

# 查看活动摘要
/digest
```

### 3.4 持久化

原子写入（tmp + `os.replace()`），格式：

```json
{
  "version": 1,
  "goals": [
    {"id": "goal_abc12345", "level": "goal", "title": "...", "status": "active", ...},
    {"id": "obj_def67890", "level": "objective", "parent_id": "goal_abc12345", ...}
  ]
}
```

---

## 4. AutonomousLoop（`evolution/autonomous_loop.py`）

运行在 daemon 进程的 `AgentRunner` 线程内，与"检查用户消息"分支并列。

### 4.1 接入点

`AgentRunner.run()` 中，当 `InputQueue.dequeue(timeout=0.5)` 超时返回 `None`（没有新用户消息）时：

```python
if autonomous_loop.should_tick():
    autonomous_loop.tick()
```

`should_tick()` 检查距上次 tick 是否已过 `tick_interval_seconds`（默认 60 秒）。

### 4.2 三档位边界

| 档位 | `autonomy_level` 值 | AutonomousLoop 行为 |
|------|--------------------|--------------------|
| 被动 | `"passive"` | 只做 Phase G 时间门控检查，**不读 GoalBacklog** |
| 维护 | `"maintenance"` | passive + 从 GoalBacklog 拆解并提交 Task |
| 自主 | `"autonomous"` | maintenance + 软目标 derive（第十二节，暂未实装） |

**边界的物理体现**（不靠注释承诺）：`_tick_passive()` 方法体内不引用 `self._goal_backlog` 任何方法；只有 `_tick_maintenance()` 及以上才调用 `goal_backlog.has_actionable_work()`。

### 4.3 autonomy_level 修改

通过修改 `~/.agent/self_profile.json` 中的 `operating_state.autonomy_level` 字段：

```json
{
  "operating_state": {
    "autonomy_level": "maintenance"
  }
}
```

默认值为 `"passive"`（最保守）。

### 4.4 tick 流程（maintenance 档位）

```
tick()
 ├─ _tick_passive()
 │   ├─ should_run_phase_g() → run_phase_g()（Stage 8 已有）
 │   └─ _run_workdir_consolidation()
 └─ [if maintenance or autonomous]
     ├─ ResourceArbiter.can_run_autonomous()  ← 预算门控
     ├─ goal_backlog.has_actionable_work()
     ├─ goal_backlog.next_task_description()  ← 拆解下一个 Task
     └─ input_queue.enqueue(..., initiator="autonomous")  ← 提交
```

---

## 5. 资源仲裁（`evolution/resource_arbiter.py`）

`AutonomousLoop._tick_maintenance()` 在提交 Task 前，必须通过 `ResourceArbiter.can_run_autonomous()` 才能继续。

### 5.1 三条仲裁规则

**规则 1：用户优先**（由 AgentRunner 循环天然保证）

用户消息优先于 autonomous tick 执行——两者都通过 `InputQueue`，用户消息会在下一个 dequeue 循环被立即取走并执行；autonomous 任务提交到队列后也遵循相同的 FIFO 顺序。

**规则 2：路径冲突检测**

```python
arbiter.check_path_conflict(task_paths)
```

从 Stage 6 的 `traces.jsonl` 提取最近 10 分钟内用户触碰的文件路径，与自主任务计划操作的路径做集合交集检查。tracing 未开启时降级为"保守地一律认为有冲突"（宁可错误暂停，不可错误覆盖）。

**规则 3：预算硬限制**

```python
used_today < daily_token_budget  →  允许
```

读取 `self_profile.json` 的 `ResourceBudget.used_today` 和 `daily_token_budget`，`daily_token_budget <= 0` 时不限制。

### 5.2 探索预算子配额

独立于目标执行预算的"实验性预算"：

```
exploration_budget = daily_token_budget × exploration_budget_ratio（默认 10%）
```

`ResourceArbiter.can_run_exploration()` 检查 `used_today_exploration < exploration_budget`。

### 5.3 activity_digest.jsonl

自主行为的粗粒度日志（对比 `activity_log.jsonl` 的 session 粒度）：

```jsonl
{"at": 1720000000.0, "type": "task_submitted", "objective_id": "obj_xxx", "task_desc": "..."}
{"at": 1720003600.0, "type": "phase_g_completed", "prune_count": 2, "capability_count": 15}
{"at": 1720007200.0, "type": "exploration_result", "success": true, "tokens_used": 800}
```

REPL 中通过 `/digest` 查看（最近 24h，按类型分组展示）。

---

## 6. initiator 字段贯穿（第九节）

本 Stage 在以下位置统一加入 `initiator` 字段，使"谁发起的"可追溯：

| 位置 | 改动 |
|------|------|
| `_TurnCommand` | 新增 `initiator` / `meta` 字段 |
| `InputQueue.enqueue()` | 新增 `initiator="user"` 参数（默认值，向后兼容） |
| `TurnInfo` | 新增 `initiator` 字段 |
| `TaskStatus` | 新增 `PAUSED`（被用户活动抢占暂停，可恢复） |
| `StateRepo.resolve_tier()` | 新增 `initiator` 参数，T0→T1 自动上浮规则 |
| `StateRepo.apply()` | 新增 `initiator` 参数，写入 commit meta |

**T0→T1 上浮规则（第九节 §9.2）**：

> 当 `initiator` 为 `"autonomous"` 或 `"scheduled"` 且 `effective_tier == "T0"` 时，自动上浮为 T1——"用户主动要求的 T0 改动可以直接 apply；同等改动若由自主 tick 发起，至少走 evolve 分支留痕"。

---

## 7. `/v1/status` 响应扩展

daemon 状态字段已加入 `/v1/status` 响应：

```json
{
  "state": "idle",
  "turn_id": null,
  "stats": {...},
  "queue_depth": 0,
  "subscribers": 1,
  "autonomy_level": "maintenance",
  "last_autonomous_tick_at": 1720000000.0,
  "tick_count": 42
}
```

---

## 8. 探索实验沙盒（`perception/exploration_sandbox.py`）

为第十二节（autonomous 档位软目标 derive）预留的接口。通过包装 Stage 2 的 `EvolutionWorkspace` 加入预算门控：

```python
sandbox = ExplorationSandbox(paths, cfg, arbiter)
with sandbox.create(capability_id="skill_xyz", goal="验证 X 方案") as ctx:
    ctx.report.finding = "X 方案在 Y 条件下可行"
    ctx.report.success = True
    ctx.record_tokens(500)
# 退出时：worktree 自动清理，report 写入 activity_digest.jsonl
```

探索预算耗尽时抛出 `ExplorationBudgetExhausted`。

---

## 9. 文件清单

### 新增文件

| 文件 | 职责 |
|------|------|
| `src/mini_agent/cli/daemon.py` | daemon 管理（start/stop/status）、DaemonClient、PID 文件 |
| `src/mini_agent/perception/goal_backlog.py` | GoalNode、GoalBacklog、goals.json 持久化 |
| `src/mini_agent/evolution/autonomous_loop.py` | AutonomousLoop、三档位 tick |
| `src/mini_agent/evolution/resource_arbiter.py` | ResourceArbiter、activity_digest.jsonl |
| `src/mini_agent/cli/commands/goals.py` | `/agent goals` 全部子命令实现 |
| `src/mini_agent/perception/exploration_sandbox.py` | 探索沙盒（第十二节接口预留） |

### 修改文件

| 文件 | 改动摘要 |
|------|----------|
| `src/mini_agent/api/bridge.py` | `_TurnCommand`/`enqueue()` 加 `initiator`/`meta` |
| `src/mini_agent/api/models.py` | `TurnInfo` 加 `initiator`；`StatusResponse` 加 daemon 状态字段 |
| `src/mini_agent/orchestrator/task.py` | `TaskStatus.PAUSED` 新值 |
| `src/mini_agent/evolution/state_repo.py` | `resolve_tier()`/`apply()` 加 `initiator`；T0→T1 上浮 |
| `src/mini_agent/api/server.py` | `AgentRunner` 接入 `AutonomousLoop`；`HttpServer._build_autonomous_loop()` |
| `src/mini_agent/api/routes.py` | `/v1/status` 填充 autonomy_level 等 daemon 字段 |
| `src/mini_agent/cli/app.py` | `daemon` 子命令短路；`--daemon-mode` 处理 |
| `src/mini_agent/cli/parser.py` | `--daemon-mode`/`--no-daemon` 标志；帮助文本 |
| `src/mini_agent/cli/repl.py` | `/agent`、`/goals`、`/digest` 路由；内联 handler |
| `src/mini_agent/cli/commands/__init__.py` | 导出 `handle_goals_cmd` |

---

## 10. 档位升级路径

```
passive（默认）
  │  self_profile.json 中修改 autonomy_level
  ▼
maintenance
  │  Phase G 数据积累后，由 check_scope_promotion() 推荐后手动升级
  ▼
autonomous（第十二节实装）
```

建议在新项目中至少运行 2-3 周积累 `traces.jsonl` + `capability_map` 数据后再切换到 `maintenance` 档位；`autonomous` 档位等第十二节完成后评估是否启用。

---

## 11. 下一步（第十二节，暂未实装）

`_tick_autonomous()` 目前只调用 `_tick_maintenance()`。第十二节将实装：
- 从 `capability_map` 低置信度条目 derive 新 Goal（`source="agent_derived"`）
- 通过 `ExplorationSandbox` 在隔离 worktree 内做轻量实验
- 实验成功后通过 `skill_propose` 提案（复用 Stage 3.1 闭环）

---

*参见：[Phase G 后台循环指南](self-evolution-phase-g-guide.md)、[Workdir 知识层指南](self-evolution-stage4-5-guide.md)、[自我演化安全网指南](self-evolution-stage2-guide.md)*
