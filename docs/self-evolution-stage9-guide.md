# Stage 9 自主运行时指南（Phase H）

> Stage 9 在 Stage 0–8 全部基础设施之上，为 agent 引入「常驻守护进程 + 跨会话目标层级 + 三档位自主调度 + 定时任务 + Objective 持续执行 + 软目标 derive」能力。

---

## 1. 架构概述

### 进程模型升级

```
旧模型（Stage 0–8）：
  每次 CLI 启动 → 进程内创建 Agent → 交互完成 → 进程退出

新模型（Stage 9）：
  mini-agent daemon start --detach    ← 一次性操作，Agent 常驻
       ↓
  daemon 进程（持续运行）
    ├─ AgentRunner 线程（消费 InputQueue）
    │    ├─ 用户消息（initiator="user"）
    │    ├─ cron job（initiator="cron"）
    │    └─ 自主步骤（initiator="autonomous"）
    ├─ AutonomousLoop（tick 调度，60s 间隔）
    │    ├─ CronScheduler.tick()       ← passive 档位
    │    ├─ ObjectiveExecutor.resume() ← maintenance 档位
    │    └─ SoftGoalDeriver.derive()   ← autonomous 档位
    └─ HTTP API（FastAPI/uvicorn）
         ├─ /v1/autonomous/status
         ├─ /v1/goals
         └─ /v1/cron/jobs
```

关键设计原则：
- **daemon 与 workdir 绑定**，不是全局唯一，每个项目有自己的 daemon
- **IPC 直接复用 HTTP API**（POST `/v1/chat` + GET `/v1/stream`），不新增协议
- **initiator 字段贯穿**：`"user"` / `"cron"` / `"autonomous"` 区分消息来源
- **`--no-daemon` 回退**：CI/脚本场景可完全跳过 daemon 机制

---

## 2. 守护进程管理（`cli/daemon.py`）

### 2.1 三条子命令

```bash
# 前台启动（开发调试）
mini-agent daemon start

# 后台启动（生产使用）
mini-agent daemon start --detach

# 指定端口（默认 8765）
mini-agent daemon start --detach --http-port 9000

# 停止
mini-agent daemon stop

# 查看状态（PID、端口、autonomy_level、cron 摘要、上次 tick 时间）
mini-agent daemon status
```

### 2.2 PID 文件管理

| 文件 | 路径 | 内容 |
|------|------|------|
| PID 文件 | `<project_root>/.agent/daemon.pid` | 进程 PID（整数） |
| info 文件 | `<project_root>/.agent/daemon_info.json` | `{"pid": N, "http_port": N, "started_at": T}` |

进程退出时自动清理。进程异常死亡后残留文件在下次 `daemon start/status` 时自动清理。

### 2.3 CLI 连接模式

当 daemon 已运行时，`mini-agent` 启动后自动进入「连接模式」：

```
[daemon] Connected to running daemon (PID=12345, port=8765)
[daemon] Type your message, or 'exit' to disconnect (daemon keeps running)

orzooo (connected) ❯ 帮我重构这个函数
...（流式输出）
orzooo (connected) ❯ exit
[daemon] Disconnected (daemon continues running)
```

输入 `exit` 只断开 CLI 连接，daemon 继续运行。

---

## 3. Goal Backlog（`perception/goal_backlog.py`）

跨会话目标层级，持久化到 `<project_root>/.agent/goals.json`。

### 3.1 数据结构

```
Goal（长期目标）
  └─ Objective（子目标，可关联 WorkThread）
       └─ Task（单次执行，由 ObjectiveExecutor 通过 InputQueue 提交）
```

`GoalNode` 统一表示两层节点，通过 `level` 字段区分：

```python
GoalNode:
  id              str     # "goal_abc12345" | "obj_def67890"
  level           str     # "goal" | "objective"
  title           str
  source          str     # "user" | "agent_derived"
  status          str     # "active" | "paused" | "completed" | "abandoned"
  created_at      float
  last_touched_at float
  progress_notes  str
  parent_id       str?    # Objective 指向其父 Goal
  children_ids    list
  work_thread_ref str?    # 关联的 WorkThread id（复用 work_index.json）
  priority        int     # 数字越大越优先，默认 0；agent_derived 默认 20-30
  tags            list
```

### 3.2 CLI 命令

```bash
/goals                                   # 列出所有 active Goals 和 Objectives
/goals add "完善测试覆盖" --priority 70  # 添加 Goal
/goals obj add "为 agent.py 加单测" --goal goal_abc12345
/goals done obj_def67890                 # 标记完成
/goals abandon <id>                      # 放弃（agent_derived 的会记录 30 天去重）
/goals accept <id>                       # 接受 agent_derived Goal，激活并提升 priority
/goals reject <id>                       # 拒绝 agent_derived Goal（30 天内不再建议相同主题）
/goals pause <id>                        # 暂停
/goals progress <id> "覆盖率已达 80%"   # 更新进展备注
/goals status                            # 显示 AutonomousLoop tick 状态
/digest                                  # 查看自主活动摘要（最近 24h，分组展示）
```

---

## 4. AutonomousLoop（`evolution/autonomous_loop.py`）

运行在 `AgentRunner` 线程内，当 `InputQueue.dequeue(timeout=0.5)` 超时（无新消息）时触发。

### 4.1 三档位完整行为

| 档位 | `autonomy_level` | 行为 |
|------|-----------------|------|
| **passive** | `"passive"` | 只运行 CronScheduler.tick()，**不读 GoalBacklog** |
| **maintenance** | `"maintenance"` | passive + 给缺 Objective 的 Goal 自动补 Objective（3.3 节）+ ObjectiveExecutor 推进活跃 Objective，可启动新 Objective |
| **autonomous** | `"autonomous"` | maintenance + SoftGoalDeriver.derive() 主动 derive 新 Goal |

边界的物理体现：`_tick_passive()` 方法体内不引用 `self._goal_backlog` 任何方法；`_tick_maintenance()` 才调用 `goal_backlog.active_objectives()`。

> 注意区分两件事：`maintenance` **不会**凭空产生新 Goal（新意图），但**会**把已有的、已被批准的 Goal 拆成 Objective——这不是"派生新目标"，只是把一句话意图操作化为可执行单元，所以没有越过 maintenance/autonomous 的边界。真正"要不要做这件事"的决策权仍然只在 `autonomous` 档位的 SoftGoalDeriver 手里。

### 3.3 Goal → Objective 自动拆解

`has_actionable_work()` / `active_objectives()` 只认 `level="objective"` 的节点——单纯建一个 Goal（不管是看板"➕ 新建目标"表单、CLI `/goals add`，还是 `SoftGoalDeriver` 派生出来的 agent_derived Goal），本身**不会**被执行，agent 也不会主动去做，必须先有一个挂在它下面的 active Objective。

`maintenance` 档位每次 tick，`_tick_maintenance()` 开头会先调用 `_ensure_goal_objectives()`：

```
_ensure_goal_objectives()
 ├─ goal_backlog.goals_missing_objective()      ← 只读，找出没有 active Objective 子节点的 active Goal
 ├─ 对每个 Goal：
 │    ├─ goal_decompose_fn(goal) → LLM 拆成 1~N 个 Objective 标题（锁外调用，可能较慢）
 │    └─ 拆解失败 / 未注入 / 返回空 → 降级为 1 个与 Goal 同名的 Objective（保底可执行）
 └─ goal_backlog.add_objectives_for_goal(goal_id, titles)   ← 锁内写入，纯数据操作、毫秒级
```

`goals_missing_objective()` 特意设计成**不加锁**的只读查询：真正耗时的 LLM 拆解在锁外做，只有最后落盘那一步（`add_objectives_for_goal`）才短暂持有 `GoalBacklog` 的跨进程文件锁，避免因为一次 LLM 请求把其它进程（别的 CLI session、API 请求）对 `goals.json` 的读写卡住。

每创建一个 Objective 会写一条 `activity_digest`（`type: objective_auto_created`），`/digest` 和看板"🧠 自我状态"里可见，不是静默行为。

**相关配置**（`self_config.json` 的 `autonomy` 段）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `auto_objective_from_goal_enabled` | `true` | 总开关，关闭后 Goal 只能靠手动 `/goals obj add` 或看板手动拆解按钮补 Objective |
| `auto_objective_max_per_goal` | `3` | 单个 Goal 一次最多自动拆出几个 Objective（LLM 拆解结果会被截断到这个数；降级镜像不受此限，恒为 1 个） |

关掉总开关后，`goals_missing_objective()` 的判断逻辑不变，只是 `_ensure_goal_objectives()` 直接 return，Goal 会一直停留在"待拆解"状态，需要人工介入。

### 4.2 tick 流程（maintenance 档位）

```
tick()
 ├─ _tick_passive()
 │   └─ CronScheduler.tick()         ← 检查所有 job 是否到期并触发
 └─ _tick_maintenance()
     ├─ _ensure_goal_objectives()               ← 给缺 Objective 的 Goal 自动补 Objective（3.3 节）
     ├─ ResourceArbiter.can_run_autonomous()   ← 预算 + 路径冲突 + 本体感知门控
     ├─ ObjectiveExecutor.resume()             ← 恢复因资源仲裁暂停的 Objective
     └─ 若有空闲槽位：
         └─ ObjectiveExecutor.start(objective) ← 启动新 Objective
```

### 4.3 autonomy_level 修改

修改 `~/.agent/self_profile.json`：

```json
{
  "operating_state": {
    "autonomy_level": "maintenance"
  }
}
```

默认值为 `"passive"`（最保守）。

---

## 5. 定时任务（`evolution/cron_scheduler.py`）

### 5.1 设计动机

原 `_tick_passive()` 直接调用 `should_run_consolidation() / run_consolidation()`，每个周期性任务都硬编码在 tick 里。`CronScheduler` 统一所有周期性任务：

- 巩固循环 等系统维护任务注册为 `sys:` 前缀 job，逻辑本身不变
- 用户可以 disable 系统 job、调整触发频率，也可以添加自定义 job
- 触发记录写入 `activity_digest.jsonl`，`/digest` 可见

### 5.2 Schedule 格式

```
interval:<秒>          固定间隔，如 interval:3600（每小时）
cron:<分 时 日 月 周>   5 字段 cron，如 cron:0 */6 * * *（每 6 小时整点）
```

内置轻量 cron 解析器，支持 `*`、`*/n`、`n,m`、`n-m` 语法，不依赖外部库。

### 5.3 内置系统 Job

持久化到 `<project_root>/.agent/cron_jobs.json`，首次 daemon 启动时自动创建：

| id | 默认间隔 | 用途 |
|----|---------|------|
| `sys:consolidation` | 6h | 巩固循环 扫描（技能剪枝 + 能力地图） |
| `sys:workdir_sync` | 1h | WorkdirKnowledge 整合（文件变化同步） |
| `sys:self_eval` | 24h | 能力自评（capability_map 置信度更新） |
| `sys:goal_review` | 12h | Goal 清理（标记已完成/无进展的目标） |
| `sys:digest_trim` | 7d | 日志修剪（删除 30 天前的 digest 记录） |

系统 job 可 `disable`、可 `set-schedule`，**不可 `remove`**。

### 5.4 Job 提交机制

触发的 job 通过 `submit_fn(message, initiator="cron", meta)` 提交到 `InputQueue`，
和用户消息走同一条 AgentRunner 线程，保证执行的串行性（cron job 不会抢占正在响应的用户消息）。

### 5.5 CLI 命令

详见 [命令与工具参考](commands-and-tools-reference.md#定时任务)，完整子命令：
`list [--all]` / `status` / `enable` / `disable` / `run` / `add` / `remove` / `set-schedule`

---

## 6. Objective 持续执行（`evolution/objective_executor.py`）

### 6.1 设计动机

原 `_tick_maintenance()` 对每个 Objective 只做一次 LLM 调用拆解 + 一次 Task 提交，执行完就结束。`ObjectiveExecutor` 实现真正的多步持续推进：

```
start(objective)
  └─ LLM 拆解 Objective → steps[0..N]（3-8 步）
       └─ step[0] → InputQueue（initiator="autonomous"）
            └─ AgentRunner 执行完成 → on_turn_done(turn_id, summary)
                 └─ step[0].status = done
                      └─ step[1] → InputQueue
                           └─ ...（循环直到所有 step done）
                                └─ objective.status = completed
                                     └─ activity_digest 记录
```

### 6.2 并发控制

- 同时最多运行 `MAX_CONCURRENT_OBJECTIVES = 2` 个 Objective
- 每个 Objective 的步骤**串行**执行（保证因果性，步骤 N+1 可以看到步骤 N 的结果）
- 每步提交前经过 `ResourceArbiter.can_run_autonomous()` 检查

### 6.3 失败处理

- 单步最多重试 `MAX_STEP_RETRIES = 2` 次
- 超过重试次数 → Objective 状态改为 `"failed"`，写入 `activity_digest`
- 用户可通过 `/goals progress <id> <notes>` 更新状态后重新激活

### 6.4 步骤上下文注入

每个步骤提交的 Task 消息包含前序步骤的结果摘要：

```
[自主任务 - 完善测试覆盖]
步骤 3/4: 生成测试用例并写入文件

[前序步骤结果]
步骤1: 扫描到 23 个未覆盖函数（agent.py x 15, llm/*.py x 8）
步骤2: 确定优先覆盖 agent.run_turn(), _call_llm(), _execute_tools()
```

### 6.5 持久化

状态持久化到 `<project_root>/.agent/objective_executions.json`，daemon 重启后恢复进行中的 Objective。

### 6.6 turn 完成回调接入点

`AgentRunner.run()` 中，`bridge.agent.run_turn()` 完成后：

```python
# server.py AgentRunner.run() 内
result = bridge.agent.run_turn(cmd.message)
iq.mark_done(turn_id)
bridge.emit_turn_done(turn_id, text=result or "")

# ObjectiveExecutor 回调（仅 initiator 为 autonomous/cron 时）
if cmd.initiator in ("autonomous", "cron"):
    obj_exec.on_turn_done(turn_id, result_summary)
```

---

## 7. 软目标 Derive（`evolution/soft_goal_deriver.py`）

### 7.1 触发条件

`autonomous` 档位下，每次 `tick()` 时检查：
- 距上次 derive 超过 `DERIVE_INTERVAL_SECONDS = 21600`（6 小时）
- GoalBacklog 中 `agent_derived` + `active` 的 Goal 数量 < `MAX_PENDING_DERIVED = 5`

### 7.2 三路信号

**信号 1：capability_map 低置信度**

`confidence < 0.35` 且 `total_calls >= 3` 的能力条目，说明 agent 在该能力上经常失败，主动生成「改善 X 执行可靠性」类型的 Goal。

**信号 2：WorkThread next_suggested 积压**

`next_suggested` 非空但 30 天无活动的 WorkThread，说明 agent 自己建议的后续工作一直没有跟进，生成对应的 Goal。

**信号 3：高频 Lesson（T1+）**

`total_occurrence >= 3` 且来自不止一个 session 的 LessonGroup，说明某类错误模式反复出现，生成「系统性解决：xxx」类型的 Goal。

**[方案一] 高风险域降权**：信号 1 产出的候选，若能力名与
`AffordanceAnalyzer` 最近落盘的 `high_risk_zones`（见
[具身智能改进指南 8 节](embodied-agent-guide.md#8-b4-余裕感知层affordancemap)）
子串重合，`urgency` 乘以 `cfg.affordance.risk_downweight_factor`
（默认 0.4）——不拒绝，只降权，因为具身层的风险判断本身也可能过时。

**[方案三] 未探索能力 + uncertainty 域重合加权**：`_from_unexplored_capabilities()`
产出的候选，若命中最近的 `memory.sparse_region_detected` 或
`proprioception.uncertainty_sustained` 事件所附带的 domain，novelty 获得
最多 1.6x 加权（两路证据都命中时取较大值，不相乘）。详见
[system-events-bus-guide.md](system-events-bus-guide.md#已接入的具体案例)。

**[方案四] 负面回填域强降权**：`derive_candidates()` 排序前，读取
`AgentSelfModel.recent_negative_outcome_domains()`（桥接
`outcome_tracker.get_revert_candidates()`），落在这些域里的所有候选
（不分来源）`urgency *= 0.15`——比方案一的 0.4 更激进，因为这是有实测
baseline/post 数据支持的负面结论。详见
[具身智能改进指南 5.1 节](embodied-agent-guide.md#51-方案四-agentselfmodel-接入softgoalderiver-候选打分单场景验证)。

### 7.3 优先级与去重

- Lesson 来源：`priority = 30`（最高，有实证失败）
- capability_map 来源：`priority = 25`
- WorkThread 来源：`priority = 20`
- 每次最多 derive `MAX_NEW_GOALS = 2` 个
- 已有相同主题的 Goal 或用户已 `reject` 的（30 天内）不再 derive

### 7.4 用户处理

Derive 的 Goal 在 `/digest` 中以「💡 Agent 建议」分组展示：

```
【新软目标（Agent 建议）】
  💡 "改善 _call_llm 的执行可靠性" — 来自 capability_map（成功率 28%）
     /goals accept <id>  接受 | /goals reject <id>  拒绝

  💡 "系统性解决：连续工具调用失败" — 来自 lesson（触发 7 次，3 个 session）
     /goals accept <id>  接受 | /goals reject <id>  拒绝
```

`reject` 后 `SoftGoalDeriver.record_rejected()` 记录到 `soft_goal_rejected.json`，30 天内不会再 derive 相同主题。

---

## 7.5 探索实验（`perception/exploration_sandbox.py`）

`autonomous` 档位下，`capability` 类软目标候选在写入 GoalBacklog 之前，会先经过 `ExplorationSandbox` 做一次轻量验证实验。

### 触发条件

- `SoftGoalDeriver.derive_candidates()` 返回 `source_tag="capability"` 的候选
- `ResourceArbiter.can_run_exploration()` 返回 True（未超探索预算）
- 每次 tick 最多处理 **1 个** capability 候选

### 实验流程

```
_run_capability_exploration(candidate)
  │
  ├─ ExplorationSandbox.create(capability_id, goal_text)
  │    ├─ ResourceArbiter.can_run_exploration()  ← 预算门控
  │    └─ EvolutionWorkspace.create_worktree()   ← 隔离 git worktree
  │
  ├─ _submit_exploration_task()
  │    └─ InputQueue.enqueue("[探索实验] ...", initiator="autonomous")
  │         └─ AgentRunner 执行（在 worktree 内，不影响主分支）
  │              └─ 同步等待结果（最多 5 分钟）
  │
  ├─ 成功（result 非空）：
  │    ├─ commit_goals([candidate])          → 写 GoalBacklog
  │    └─ _maybe_propose_skill()             → skill_propose()（含关键词时触发）
  │         └─ activity_digest: exploration_result（含 proposed_skill_id）
  │
  └─ 失败（result 空 / 超时 / 预算耗尽）：
       └─ 静默丢弃，不写 Goal，不骚扰用户
```

### `_maybe_propose_skill` 触发条件

探索结果文本中包含关键词：`skill` / `技能` / `封装` / `通用` / `可复用` / `pattern`

触发时调用 `skill_propose(name, content, source_lessons=[])` 生成 `explore/capability/<name>` 分支，
用户可通过 `/evolve review` 查看和审核。

### 降级策略

- `ExplorationSandbox` 模块不可用（ImportError）→ 直接写 Goal，不做实验
- `ExplorationBudgetExhausted` → 跳过本 tick 的 capability 候选
- EvolutionWorkspace 不可用 → fallback 到 tempdir（功能可用，不隔离 git 历史）

### [方案一] 高风险域 token 上限收紧

`ExplorationSandbox.create()` 内部会调用 `_risk_adjusted_token_limit()`
判断 `capability_id` 是否落在 `AffordanceAnalyzer` 最近落盘的
`high_risk_zones` 里；命中时把本次探索的 token 上限收紧到探索预算总额
（`daily_token_budget * exploration_budget_ratio`）的一半，而非默认的
不设上限——高风险域的探索仍然放行（探索的价值就是验证风险判断是否还
成立），只是更早止损。`_ExplorationContext.record_tokens()` 累计超出
该上限时抛 `ExplorationTokenLimitExceeded`，与其余探索期间异常
（如工具调用失败）走同一条收尾路径：`report.success=False`，
`report.error` 记录原因，不会导致 sandbox 泄漏或 worktree 残留。

总开关：`cfg.affordance.risk_gating_enabled`（默认 `True`）。

---

## 8. 资源仲裁（`evolution/resource_arbiter.py`）

`_tick_maintenance()` 在推进 Objective 前必须通过 `ResourceArbiter.can_run_autonomous()`：

### 8.1 五条仲裁规则

**规则 1：用户优先**（由 `InputQueue` FIFO 天然保证）

用户消息和自主消息都通过同一个 `InputQueue`，用户消息会在下一个 `dequeue` 循环立即取走执行；`ObjectiveExecutor.pause_all()` 在资源不足时也可主动暂停。

**规则 2：路径冲突检测**

从 `traces.jsonl` 提取最近 10 分钟用户触碰的文件路径，与自主任务计划操作路径做集合交集检查。

**规则 3：预算硬限**

`used_today < daily_token_budget`，`daily_token_budget <= 0` 时不限制。

**规则 4：本体感知信号（B1 → Stage 9 信号桥接）**

读取 `agent.py` 每轮 sense() 后落盘的 `proprioception_snapshot.json`（`AgentPaths.
proprioception_snapshot`）：`frustration` 达到 `cfg.proprioception.
frustration_threshold`（默认 0.5）时，本次 tick 跳过自主任务提交——一个正在
反复受挫的 Agent 不应该同时还在后台跑高置信度要求的自主探索。快照不存在
（没有本体感知开启的活跃 session）或超过 10 分钟未更新（近期没有活跃 session
在跑，信号已过期）时不阻塞，与规则 3 "读取失败不阻塞"是同一保守降级风格。
详见 [具身智能改进指南](embodied-agent-guide.md#5-b1-本体感知模块proprioceptionmodule)。

**规则 5：用户在场信号（方案二：BehaviorContext → Stage 9 信号桥接）**

`_check_user_presence()` 在 `cfg.autonomy.behavior_gating_enabled=True`
（默认 `False`）时生效：调用 `affordance_analyzer.load_behavior_context()`
读取最近 5 分钟的应用切换活动，`context_switch_count` 达到
`cfg.autonomy.behavior_gating_switch_threshold`（默认 3）且判定为
"活跃在场"时，本次 tick 跳过自主任务提交——避免和用户抢资源/写冲突。
信号缺失（未开启 behavior collector）或读取异常时保守放行，不阻塞。
详见 [具身智能改进指南 8.1 节](embodied-agent-guide.md#81-方案二-behaviorcontext-接入自主任务调度门控)。

### 8.2 activity_digest.jsonl

记录所有自主行为：

```jsonl
{"at": 1720000000.0, "type": "cron_run", "job_id": "sys:consolidation", "summary": "..."}
{"at": 1720003600.0, "type": "objective_auto_created", "goal_id": "goal_abc", "objective_id": "obj_xxx", "title": "...", "summary": "自动为目标「...」创建执行子目标：..."}
{"at": 1720003700.0, "type": "objective_started", "objective_id": "obj_xxx", "title": "..."}
{"at": 1720007200.0, "type": "objective_completed", "execution_id": "exec_yyy", "steps": 4}
{"at": 1720010800.0, "type": "soft_goal_created", "goal_id": "goal_zzz", "title": "..."}
```

`/digest` 按类型分组展示最近 24h 的记录。

---

## 9. HTTP API 新增端点

详见 [HTTP API 指南](http-api-guide.md#stage-9-daemon-模式说明)，摘要：

| 端点 | 说明 |
|------|------|
| `GET /v1/autonomous/status` | daemon 自主执行实时状态（档位 + cron_jobs + objective_executions） |
| `GET /v1/goals` | GoalBacklog 视图（所有 active Goals 和 Objectives） |
| `POST /v1/goals` | 添加 Goal |
| `PATCH /v1/goals/{goal_id}` | 更新 Goal 状态/进展/优先级 |
| `GET /v1/cron/jobs` | 列出所有 cron job |
| `POST /v1/cron/jobs` | 添加用户 cron job |
| `PUT /v1/cron/jobs/{job_id}` | 启用/禁用/修改 schedule |
| `POST /v1/cron/jobs/{job_id}/run` | 立即触发一次 |

### SSE 新增事件

`objective_progress`：Objective 步骤推进时推送，包含 `execution_id`、`progress`（"3/4"）、`current_step` 等字段，客户端可实时渲染进度条。

---

## 10. initiator 字段贯穿

| 值 | 来源 | 说明 |
|----|------|------|
| `"user"` | CLI REPL / HTTP `/v1/chat` | 用户主动发送的消息 |
| `"cron"` | CronScheduler.tick() | 定时任务触发的消息 |
| `"autonomous"` | ObjectiveExecutor._submit_step() | Objective 自主步骤 |

`StateRepo.resolve_tier()` 会对 `initiator` 为 `"autonomous"/"cron"` 且 `effective_tier == "T0"` 的改动自动上浮为 T1（自主发起的改动至少留痕）。

---

## 11. 文件清单

### Stage 9 Phase 1（基础架构）

| 文件 | 职责 |
|------|------|
| `cli/daemon.py` | daemon 管理（start/stop/status）、DaemonClient、PID 文件 |
| `perception/goal_backlog.py` | GoalNode、GoalBacklog、goals.json 持久化 |
| `evolution/autonomous_loop.py` | AutonomousLoop、三档位 tick（完整实现） |
| `evolution/resource_arbiter.py` | ResourceArbiter、activity_digest.jsonl |
| `evolution/cron_scheduler.py` | CronJob、CronScheduler、5 个内置系统 job、轻量 cron 解析器 |
| `evolution/objective_executor.py` | ExecutionStep、ObjectiveExecution、ObjectiveExecutor |
| `evolution/soft_goal_deriver.py` | SoftGoalDeriver（三路信号：capability/workthread/lesson） |
| `cli/commands/goals.py` | `/agent goals` 全部子命令 |
| `cli/commands/cron.py` | `/cron` 全部子命令 |
| `perception/exploration_sandbox.py` | 探索沙盒（ExplorationSandbox + ExplorationReport，由 `_tick_autonomous` 驱动） |

### Stage 9 Phase 2（接入与 API）

| 文件 | 改动摘要 |
|------|----------|
| `api/bridge.py` | `_TurnCommand` 加 `initiator`/`meta`；新增 `emit_objective_progress()` |
| `api/models.py` | `TurnInfo` 加 `initiator`；新增 `OBJECTIVE_PROGRESS` EventType |
| `api/server.py` | `_build_autonomous_loop()` 注入 CronScheduler + ObjectiveExecutor；turn 完成/失败回调 |
| `api/routes.py` | 新增 `/v1/autonomous/status`、`/v1/goals` CRUD、`/v1/cron/jobs` CRUD |
| `cli/repl.py` | 路由 `/cron` 命令 |
| `ui/terminal.py` | Tab 补全新增 `/cron` 及所有子命令 |

---

## 12. 档位升级路径

```
passive（默认，只跑 cron job）
  │  self_profile.json: autonomy_level = "maintenance"
  ▼
maintenance（cron + Objective 持续执行）
  │  确认 capability_map 数据充足 + ResourceArbiter 配置合理后
  ▼
autonomous（maintenance + 软目标 derive）
```

**建议**：新项目至少运行 2 周积累 `traces.jsonl` + `capability_map` 数据后再切到 `maintenance`；`autonomous` 档位在 `MAX_PENDING_DERIVED` 和 `DERIVE_INTERVAL_SECONDS` 调整合适后再启用，避免 GoalBacklog 被 derive 出的 Goal 淹没。

---

*参见：[HTTP API 指南](http-api-guide.md) · [命令与工具参考](commands-and-tools-reference.md) · [巩固循环 后台循环指南](self-evolution-consolidation-guide.md) · [Workdir 知识层指南](self-evolution-stage4-5-guide.md)*
