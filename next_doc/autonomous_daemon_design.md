# Autonomous Daemon Design
# daemon 模式下的真正自主 Agent 能力设计

## 现状摘要

当前 daemon 已实现：
- 常驻进程（PID 文件 + HTTP API + SSE 推流）
- 三档位 AutonomousLoop（passive / maintenance / autonomous）
- GoalBacklog（Goal → Objective 两层，持久化到 goals.json）
- ResourceArbiter（预算硬限 + 路径冲突检查）
- Phase G 后台扫描（技能剪枝 / 能力地图 / 节奏治理）
- activity_digest.jsonl（自主行为日志）

**缺口：**
1. Objective → Task 的拆解只是轻量 LLM 一次调用，没有持续执行 / 重试 / 进度反馈
2. AutonomousLoop.tick() 是单线程串行，无法并行跑多个 Objective
3. 没有定时任务（cron）：所有触发都靠 GoalBacklog 有没有 active objective
4. _tick_autonomous() 中软目标 derive 是 TODO stub，从未实现
5. 没有 "agent 自我评估当前能做什么" 的能力边界感知
6. 用户设定目标后，agent 完全不主动沟通进度（除非用户问）

---

## 设计目标

在不破坏现有 CLI/HTTP API 兼容性的前提下，让 daemon 能够：

1. **定时任务**：支持 cron 表达式或 interval 触发的周期性 Task
2. **目标持续执行**：Objective 拆解为多步 Task，每步完成后自动推进
3. **软目标 derive**：agent 根据已有能力和工作区状态，主动提出新 Goal
4. **进度主动推送**：执行中的 Objective 有进度更新时通知到 SSE
5. **能力自评**：agent 定期评估"我能做什么 / 不能做什么"并更新 capability_map
6. **优雅降级**：任何自主行为失败不影响 CLI 交互响应

---

## 核心新增模块

### 1. `evolution/cron_scheduler.py` — 定时任务调度器

```
CronJob:
  id: str
  name: str
  schedule: str          # "*/30 * * * *" (cron) 或 "interval:3600" (秒)
  task_template: str     # 提交给 InputQueue 的 Task 描述模板
  enabled: bool
  last_run_at: float
  next_run_at: float
  run_count: int
  tags: list[str]        # ["maintenance", "evolution", "user"]
  initiator: str         # "cron" (区别于 "autonomous" / "user")

CronScheduler:
  - load() / save()      → .agent/cron_jobs.json
  - tick()               → 检查 next_run_at，触发到期 job
  - add_job() / remove_job() / enable() / disable()
  - list_jobs() → 含 next_run_at, last_run_at, run_count
```

**内置 Job（首次启动时自动创建，用户可修改）：**

| id | name | schedule | 描述 |
|----|------|----------|------|
| `sys:phase_g` | Phase G 扫描 | `interval:21600` (6h) | 技能剪枝/能力地图 |
| `sys:workdir_sync` | 工作区知识整合 | `interval:3600` (1h) | WorkdirKnowledge 更新 |
| `sys:self_eval` | 能力自评 | `interval:86400` (24h) | capability_map 更新 |
| `sys:goal_review` | 目标清理 | `interval:43200` (12h) | 清理已完成/过期 Goal |
| `sys:digest_trim` | 日志修剪 | `interval:604800` (7d) | activity_digest 保留最近 30 天 |

---

### 2. `evolution/objective_executor.py` — Objective 持续执行引擎

当前 GoalBacklog.next_task_description() 只拆解**一次**，执行完就完。

新引擎实现**多步持续推进**：

```
ObjectiveExecution:
  objective_id: str
  steps: list[ExecutionStep]
  current_step_idx: int
  status: "running" | "paused" | "completed" | "failed"
  started_at: float
  last_step_at: float
  retry_count: int

ExecutionStep:
  step_id: str
  description: str        # 提交给 agent 的 Task 文本
  status: "pending" | "running" | "done" | "failed"
  result_summary: str     # agent 完成后写回的摘要
  turn_id: str            # 对应的 InputQueue turn_id

ObjectiveExecutor:
  - start(objective_id)   → 调用 LLM 拆解 Objective 为 steps[]
  - on_turn_done(turn_id, result) → 推进当前 step，触发下一步
  - status(objective_id)
  - pause() / resume()    → 用户优先仲裁调用
```

**与现有架构的集成点：**
- `start()` 调用轻量 LLM（与 `GoalBacklog._llm_decompose` 同一客户端）把 Objective 拆解为 3-7 个 Step
- 每个 Step 通过 `InputQueue.enqueue(initiator="autonomous")` 提交
- AgentRunner 完成 turn 时，在 `activity_digest` 中记录 `turn_id + result_summary`
- ObjectiveExecutor 订阅 `activity_digest` 或直接在 AgentRunner 回调中更新 step 状态

---

### 3. `evolution/soft_goal_deriver.py` — 软目标 derive（_tick_autonomous 补全）

补全当前的 TODO stub：

```
SoftGoalDeriver:
  - derive(capability_map, workdir_knowledge, recent_lessons) → list[GoalNode]

derive 逻辑：
  1. 读 capability_map：找 confidence < 0.3 的能力条目（"不确定自己能做"）
  2. 读 workdir_knowledge：找 next_suggested 非空但 30 天没有 active Objective 的 WorkThread
  3. 读 recent_lessons：找 LessonRule 触发频率高的（说明某类问题反复出现）
  4. 合并去重，每次最多 derive 2 个新 Goal（避免爆炸）
  5. source="agent_derived"，priority 比 user Goal 低一级
```

---

### 4. `cli/commands/cron.py` — `/cron` 命令

```
/cron list                    — 列出所有 job（id / name / schedule / next_run / enabled）
/cron add <name> <schedule> <task_template>  — 添加 user job
/cron remove <id>             — 删除 user job（sys: 前缀的不可删，只可 disable）
/cron enable <id>
/cron disable <id>
/cron run <id>                — 立即触发一次（不改变 next_run_at）
/cron status                  — 下次触发时间总览
```

---

### 5. `api/routes.py` 新增端点

```
GET  /v1/autonomous/status    — 返回当前 autonomy_level + 活跃 Objective 执行状态 + 下次 tick
GET  /v1/cron/jobs            — CronScheduler.list_jobs()
POST /v1/cron/jobs            — 添加 cron job
PUT  /v1/cron/jobs/{id}       — 修改（enable/disable/schedule）
POST /v1/cron/jobs/{id}/run   — 立即运行一次
GET  /v1/goals                — GoalBacklog 完整视图
POST /v1/goals                — add_goal / add_objective
PATCH /v1/goals/{id}          — set_status / update_progress
```

---

## AutonomousLoop 三档位重新定义

| 档位 | tick 行为 | 额外触发 |
|------|-----------|---------|
| **passive** | CronScheduler.tick() only | 无 GoalBacklog |
| **maintenance** | passive + ObjectiveExecutor 推进活跃 Objective | 不 derive 新 Goal |
| **autonomous** | maintenance + SoftGoalDeriver.derive() | 可以创建新 Goal/Objective |

`passive` 档位的语义从"只跑 Phase G"变为"跑所有 cron job"，
Phase G 本身成为一个 cron job（`sys:phase_g`），逻辑更清晰。

---

## 数据流

```
用户 /goals add "完善测试覆盖" priority=80
  → GoalBacklog.add_goal()
  → 用户 /goals obj "给 agent.py 加单元测试" --parent <goal_id>
  → GoalBacklog.add_objective()

daemon tick (maintenance):
  → ObjectiveExecutor.start("obj_xxx")
  → LLM 拆解 → steps: ["扫描现有测试", "识别未覆盖路径", "生成测试用例", "运行并修复"]
  → step[0] 提交 InputQueue（initiator="autonomous"）
  → AgentRunner 执行，turn_done 回调 → step[0].status=done, result_summary=...
  → ObjectiveExecutor.on_turn_done() → 提交 step[1]
  → ... 循环直到所有 step done
  → GoalBacklog.set_status(obj_id, "completed")
  → activity_digest 记录 objective_completed

SSE 推流 (daemon → CLI):
  每个 step 完成时推 {"event": "objective_progress", "objective_id": ..., "step": ...}
  CLI 连接模式的 observer 线程显示进度

用户回来时：
  /digest → 看到"[3/4 步完成] 完善测试覆盖 — 昨晚执行"
```

---

## 用户体验设计

### daemon start 初次体验

```
$ mini-agent daemon start --detach
[daemon] Starting in background on port 8765...
[daemon] Started: PID=12345, port=8765
[daemon] Autonomy level: passive (change with /self autonomy)
[daemon] Cron jobs active: 5 (phase_g, workdir_sync, self_eval, goal_review, digest_trim)
[daemon] Set goals with: /goals add <title>
```

### 用户设定目标后的状态栏变化

```
  🤖 [autonomous] obj: 完善测试覆盖 [2/4] ● running  queue=1
```

### /digest 输出

```
自上次交互以来的自主活动（15h，16 条）：

【Objective 进展】
  ✓ 完善测试覆盖：步骤 1/4 完成（扫描到 23 个未覆盖函数）
  ● 步骤 2/4 运行中（识别高优先路径）

【Cron 执行记录】
  ✓ Phase G 扫描（6h前）：剪枝 2 个过时技能，能力地图 +3 条目
  ✓ 工作区知识整合（1h前）：新增 WorkThread 关联

【新软目标（agent 建议）】
  💡 "为 ProviderMixin 补充错误处理测试" — 基于 lesson 高频触发
     /goals accept <id> 接受 | /goals reject <id> 拒绝
```

---

## 实现优先级

### Phase 1（核心骨架，本次实现）

1. `evolution/cron_scheduler.py` — CronJob + CronScheduler + 5 个内置 job
2. `AutonomousLoop._tick_passive()` 改为调用 `CronScheduler.tick()`
3. `cli/commands/cron.py` — `/cron list` / `enable` / `disable` / `run`
4. `_COMMANDS` 新增 `/cron` 条目

### Phase 2（持续执行引擎）

5. `evolution/objective_executor.py` — ObjectiveExecution + on_turn_done 回调
6. `AutonomousLoop._tick_maintenance()` 调用 ObjectiveExecutor
7. SSE 新增 `objective_progress` 事件类型
8. `/v1/autonomous/status` API 端点

### Phase 3（软目标 + 能力自评）

9. `evolution/soft_goal_deriver.py` — SoftGoalDeriver
10. `AutonomousLoop._tick_autonomous()` 调用 SoftGoalDeriver
11. `sys:self_eval` cron job 实现（调用 Phase G 的 capability_map builder）
12. `/digest` 输出增加"agent 建议"分组 + accept/reject 命令

---

## 与现有代码的兼容性

| 现有模块 | 变化 |
|---------|------|
| `GoalBacklog` | 不变（ObjectiveExecutor 直接使用） |
| `AutonomousLoop` | `_tick_passive()` 改调 CronScheduler，`_tick_autonomous()` 补全 |
| `ResourceArbiter` | 不变（ObjectiveExecutor 在提交每个 step 前调用） |
| `phase_g.py` | 从 `_tick_passive()` 直接调用 → 改为 cron job 触发，逻辑本身不变 |
| `InputQueue` | 新增 `initiator="cron"` 值，路由逻辑不变 |
| `activity_digest` | 新增 `objective_progress` / `cron_run` 记录类型 |
| HTTP API | 新增端点，现有端点不变 |
| CLI REPL | 新增 `/cron` 命令，现有命令不变 |
