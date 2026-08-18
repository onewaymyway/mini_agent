# Goal 与 Cron 绑定指南

> 对应设计与实施记录：`next_doc/goal_cron_binding_plan.md` /
> `next_doc/goal_cron_binding_implementation_record.md`
> 前置阅读：[Stage 9 自主运行时指南 · 3. Goal Backlog](self-evolution-stage9-guide.md#3-goal-backlogperceptiongoal_backlogpy)、
> [Stage 9 自主运行时指南 · 5. 定时任务](self-evolution-stage9-guide.md#5-定时任务evolutioncron_schedulerpy)
> 延伸阅读：[Goal 执行规范指南](goal-execution-spec-guide.md)——在本文
> 第 10 节的通用产出目录管道之上，为具体 Goal 生成"该怎么执行"的结构化
> 规范（自动生成草稿 → 反馈迭代 → 确认后生效）。

## 1. 要解决的问题

`GoalBacklog`（Goal/Objective 层级）和 `CronScheduler`（定时任务）此前是完全独立的两套体系：

- 一个 Goal 一旦被标记完成，就真的结束了——没有"每天/每周自动重跑同一个 Goal"这种机制。
- Cron job 触发时只会把 `task_template` 当一条裸消息塞进 `InputQueue`，不知道、也不关心
  这条消息是否对应某个具体的 Goal。
- 用户暂停/放弃一个背后挂着定时任务的 Goal 后，如果没人记得手动 `/cron disable`，
  cron 会继续无脑触发，变成僵尸任务。

本方案让一个 Goal 可以声明"我需要被周期性推进"：绑定后，Cron 到期时会自动为该 Goal
派生并启动新一轮子 Objective，而不是发一条孤立的消息。

## 2. 使用方式

### 2.1 把已有 Goal 声明为周期性

```bash
/agent goals add "持续关注 Agent 和 AI 领域最新技术，维护 research/agent_and_ai 下的技术 wiki"
# 假设新建的 Goal id 是 goal_1a2b3c4d

/agent goals recur goal_1a2b3c4d interval:86400 "搜索最新的 Agent/AI 技术进展，将结构化知识更新到 research/agent_and_ai/ 下的 wiki，接续上一轮 progress_notes 里记录的进度"
```

也可以反过来，从 cron 一侧发起绑定（等价效果）：

```bash
/cron add-goal-cycle goal_1a2b3c4d interval:86400 "搜索最新的 Agent/AI 技术进展..."
```

`task_template` 参数省略时，会复用 Goal 自身的 `description`/`title` 作为每轮任务描述。

### 2.2 停止周期性

```bash
/agent goals unrecur goal_1a2b3c4d
```

只是 disable 绑定的 cron job、把 `Goal.recurring` 置回 `False`，**不会删除 Goal，也不会
删除 cron job**——随时可以再次 `recur` 复用同一个绑定。

### 2.3 观察进度

```bash
/agent goals            # 列表里能看到 Goal 的 cycle_count（已跑过几轮）
/agent goals progress goal_1a2b3c4d "..."   # 也可以手动追加进展备注
/cron list --all        # 能看到绑定的 goal_cycle job 及下次触发时间
```

每一轮子 Objective 进入终态（完成/失败/取消）后，`Goal.progress_notes` 会自动追加一行
形如 `[2026-08-02 10:00] 第 3 轮：<该轮结果摘要前 80 字>` 的记录，方便回看历史轮次。

### 2.4 看板操作（Kanban，见 `goal_cron_visibility_and_intervention_improvement_plan.md`）

以上绑定/解绑操作现在也可以直接在 Kanban 看板"📌 目标看板"Tab 里完成，不用切到 CLI：
每张 Goal 卡片下方有一个"⏰ 周期性设置"折叠区，未绑定时提供调度输入框 + "设为周期性"
按钮；已绑定时展示当前 schedule、绑定的 cron job id、已完成轮数，并提供"⏭️ 跳过下一轮"
和"🛑 取消周期性"两个按钮。卡片标题下也会直接展示 `🔁 周期性 · 已完成 N 轮` 徽标，
子 Objective 若是本轮由 cron 触发的，会标注"⏰ 由 cron 周期触发"。

想一次性看到所有周期性 Goal 的下次触发时间（而不是逐张卡片展开查看），可以切到
"🗓️ 全局日程"Tab（`scheduling_unification_and_kanban_visibility_improvement_plan.md`
P5）——它把所有 `recurring` Goal 的下次触发、未来 24 小时内到期的 cron job、以及
仲裁状态变化时间线合并成一条时间线展示，详见 `docs/kanban-dashboard-guide.md`。

### 2.5 跳过某一轮（不停止周期性）

```bash
curl -X POST .../v1/goals/<goal_id>/skip_next_cycle
```

或看板"⏭️ 跳过下一轮"按钮。跟 `unrecur`（彻底停止）不同——只是让**下一次**触发被跳过，
`recurring` 保持 `True`，之后照常按 schedule 继续。跳过会在 `progress_notes` 里留下
"本轮由用户手动跳过"的记录，跟系统级跳过（Goal 未 active / 上一轮未完成，这两种不写
progress_notes）区分开。

## 3. 触发规则（重要，决定了"为什么这次没自动跑"）

一个 `run_mode="goal_cycle"` 的 cron job 到期时，按以下顺序检查，**任一条件不满足就
静默跳过，不报错、不消耗触发计数（下次 tick 会再检查一次）**：

1. **Goal 必须是 `active` 状态**。用户 `pause`/`abandon`/`done` 掉 Goal 之后，
   即便绑定的 cron job 还是 enabled，也不会再触发——这是刻意设计，避免你需要额外记得
   去 `/cron disable`。
2. **autonomy_level 不能是 `passive`**。周期性 Goal 的自动续期本质上是一种自主行为，
   只有守护进程处于 `maintenance` 或 `autonomous` 档位时才会生效
   （见 [Stage 9 自主运行时指南 · 4.1 三档位](self-evolution-stage9-guide.md#41-三档位完整行为)）。
   `passive` 档位下 cron 的其余（`sys:` 前缀的）维护任务仍会正常运行，只是 goal_cycle
   不会。
3. **上一轮不能还在跑**。如果这一轮的子 Objective 还处于执行中，不会叠加开第二轮，
   要等上一轮进入终态才会开始下一轮——即便到了下一次 schedule 触发点也会先跳过。

## 4. 数据结构变化（供二次开发参考）

`GoalNode`（`perception/goal_backlog.py`）新增字段：

| 字段 | 说明 |
|---|---|
| `recurring` | 是否已绑定一个 `run_mode="goal_cycle"` 的 CronJob |
| `recurrence_cron_job_id` | 反向指针，指回绑定的 `CronJob.id` |
| `cycle_count` | 已完成（含失败）的周期数 |
| `reaped_cycle_child_ids` | 已被计过数的子 Objective id 集合，避免重复计数 |

`CronJob`（`evolution/cron_scheduler.py`）新增字段：

| 字段 | 说明 |
|---|---|
| `goal_id` | 绑定的 GoalNode.id |
| `run_mode` | `"message"`（默认，裸消息投递） \| `"goal_cycle"`（驱动 Goal 周期） |

核心逻辑在新增模块 `evolution/goal_cron_bridge.py`：

- `register_goal_cycle_handler()` — daemon 启动时接线一次（`api/server.py`）
- `_fire_goal_cycle()` — 上述三条触发规则的实现
- `make_goal_recurring()` / `stop_goal_recurrence()` — CLI 命令背后调用的绑定/解绑函数
- `reap_finished_cycles()` — 由 `AutonomousLoop._tick_maintenance()` 周期调用，
  回收终态子节点计入 `cycle_count`/`progress_notes`

## 5. 长期运行的健康度治理

`goal_cron_visibility_and_intervention_improvement_plan.md` Track D 补上了两处长期运行
会暴露的问题：

- **子节点归档**：`GoalBacklog.archive_finished_cycle_children(goal_id, keep_recent=20)`
  由 `reap_finished_cycles()` 每次 reap 后顺带调用——只保留最近 20 轮的子 Objective 节点
  在 `goals.json`/`children_ids` 里，更早的追加写入 `<agent_dir>/goal_cycle_archive.jsonl`
  （每行一条完整节点 JSON），避免长期运行（几十上百轮）后 `goals.json` 无限膨胀。归档不
  影响 `cycle_count`/`progress_notes` 的历史记录，只是把节点本体挪到冷存储。
- **失败通知**：某一轮子 Objective 以 `failed` 收尾时，`reap_finished_cycles()` 会调用
  已有的 `notification/dispatcher.py` 推一条通知（kanban 恒真兜底 + 可选邮件渠道），
  不需要用户主动巡检看板才能发现某个周期性任务连续失败。`completed`/`cancelled` 不触发
  通知。

## 6. 已知限制

- 一对一绑定：一个 Goal 只能绑定一个 goal_cycle job，暂不支持"多个 job 共享同一个 Goal"。
- `reap_finished_cycles()` 是轮询式回收，最坏情况下有一个 tick 间隔（约 60s）的计数延迟。
- `progress_notes` 的摘要压缩（超过一定行数后自动压缩早期记录，避免长期运行后信息噪声
  过多）尚未实现，见 `goal_cron_visibility_and_intervention_improvement_plan.md` §2.2，
  留作后续独立小 Track。
## 7. 用户意见反馈（持久化，区别于一次性 inject_guidance）

[`goal_cron_feedback_and_output_policy_plan.md`] 之前唯一的反馈通道
`ObjectiveExecutor.inject_guidance()` 只影响下一次提交的单个 step，提交后立即清空。
现在多了一条**持久化**通道：对一个 Goal 或 CronJob 提的意见，会永久合入该节点的
`description`（CronJob 还会同步写入 `task_template`），此后所有基于这个节点派生的
执行（新一轮子 Objective、下一次 cron 触发）都会带着这条意见，不需要每次手动重复。

### 用法

CLI：
```
/agent goals feedback <goal_id_or_objective_id> <text>
/cron feedback <job_id> <text>
```

REST：
```
POST /v1/goals/{goal_id}/feedback   Body: {"text": str}
POST /v1/cron/jobs/{job_id}/feedback   Body: {"text": str}
```

kanban：Goal 详情卡片、Cron job 卡片各有一个「💬 提意见」折叠面板，可以看历史记录、
提交新意见。

### 写入位置与联动

- Goal：追加进 `GoalNode.user_feedback`（历史记录）+ 合入 `description`。
- CronJob：追加进 `CronJob.user_feedback` + 合入 `description` 和 `task_template`
  （`task_template` 才是真正决定裸投递内容/goal_cycle 兜底内容的字段）；如果该 job
  是 dedicated-execution 模式（存在 `.agent/cron_jobs/<id>/prompt.md`），同时追加进
  `prompt.md` 末尾（不解析占位符，不破坏已有模板结构）。
- **双向同步**：如果 Goal 已绑定周期性 CronJob（`recurring=True`），对 Goal 提的意见
  会自动同步到绑定的 CronJob，反之亦然（`run_mode="goal_cycle"` 且 `goal_id` 有效时）。
  两边各自只联动一次，不会来回反弹。

### 子任务如何看到父级说明和意见

`GoalBacklog.add_objectives_for_goal()` 创建子 Objective 时会带上父 Goal 的
`description`；`goal_cron_bridge._fire_goal_cycle()` 创建周期子 Objective 时会拼接
父 Goal 说明和本轮 `task_template`（不再是"配了 task_template 就丢父级约束"的
二选一）。执行侧还有 `GoalBacklog.effective_context(node_id)` 作为双保险：向上遍历
`parent_id` 链拼出完整说明链，`ObjectiveExecutor._submit_step()` 在每个 step 的
`message` 里都会带上这段 `[目标说明]`。

本轮不做"意见"的语义理解/冲突检测——新意见和旧意见只做追加，不去重、不判断是否矛盾，
由 agent 自己在执行时综合判断。

## 8. 产出路径规范（用户可编辑）

[`goal_cron_feedback_and_output_policy_plan.md` 第5节] 新增一份**用户可编辑**的规范
文件：`.agent/policies/output_path_policy.md`，首次不存在时自动写入内置默认模板：

```markdown
# 产出路径规范

在没有特殊说明的情况下，执行任务时请遵守：

1. 禁止把产出的代码写入主项目 `src/` 目录。
2. 禁止把产出的代码写入 `tests/` 目录。
3. 和 skill 相关的产出，放到对应 skill 的目录下。
4. 任务本身已经说明了工作目录的，产出放到该工作目录下。
5. 如果任务描述里出现了"本轮产出请写入：<目录>"这一行（周期性 Goal/
   CronJob 每轮自动附加，见第 10 节），以该目录为准，优先级高于本规范其他各条。

如果任务描述中明确要求修改 `src/`、`tests/` 或指定了其他路径，以任务描述的
明确说明为准，本规范不覆盖显式指令。
```

用户后续可以直接编辑这个文件（比如加第 5 条规则），不需要改代码。已存在的文件不会被
覆盖。对应模块：`evolution/output_path_policy.py`（`ensure_policy_file()` 幂等创建，
`load_policy()` 读取当前内容）。

### 注入点（三条执行路径统一注入，行为一致）

- `ObjectiveExecutor._submit_step()`：每个 step 提交时都追加 `[产出路径规范]` 段
  （每步都加，不是只加第一步，避免长任务多步执行中间"忘记"）。
- dedicated-execution cron（`CronJobWorkspace`）：`DEFAULT_PROMPT_TEMPLATE` 新增
  `{{output_policy}}` 占位符，`render_prompt()` 渲染时替换为规范全文。已存在的、用户
  自定义的 `prompt.md` 如果没有这个占位符则不受影响（不强行插入）——想要这段规范就自己
  在 `prompt.md` 里加上 `{{output_policy}}`。
- `run_mode="message"` 裸投递路径（`CronScheduler._fire()`）：投递前统一在消息末尾
  追加同一段规范文本。

**本轮不做强制拦截**——只做 prompt 层面的规则注入，不在 hook 里硬拦截写 `src/` 的工具
调用，避免误伤"用户确实特殊说明要改 `src/` 下代码"的合法场景。任务描述里的显式指令
始终优先于这份规范。

## 10. 产出目录规范（周期性 Goal/CronJob + 一次性 Goal）

> **recurring Goal 的模型已升级**：本节描述的"每次触发一个
> `cycle_NNNN/`/`run_NNNN/` 目录"模型，目前仍适用于**独立 cron job**（非
> `goal_cycle` 模式）和**一次性 Goal 的子 Objective**；recurring Goal
> （`run_mode=goal_cycle`）已迁移到"四个跨轮共用固定目录
> `output/`/`notes/`/`spec/`/`scratch/`"的新模型，见
> [产出目录规范（新模型）](goal-output-directory-guide.md)。已存在的历史
> `cycle_NNNN/` 目录保留原样，不做自动迁移。

[`goal_cron_output_directory_convention_plan.md`] 为每个（非 goal_cycle 的）普通
CronJob 的每次触发、以及**每个一次性 Goal 的每个子 Objective**（§7 新增，见下），
分配一个稳定、可预测、按时间/创建顺序排序的产出目录，并把"上一轮（或上一个子
任务）产出了什么"结构化传给下一轮，不再只靠 `progress_summary` 自由文本回忆。

### 目录结构

```
<project_root>/.agent/daemon_run_outputs/
├── goals/<goal_id>/
│   ├── latest.json              # 指针文件：{"latest_dir": "cycle_0003", ...}
│   ├── cycle_0001/manifest.json # recurring Goal：按"第几轮"编号
│   ├── cycle_0002/manifest.json
│   ├── run_0001/manifest.json   # 一次性 Goal：按子 Objective 创建顺序编号
│   ├── run_0002/manifest.json   # （cycle_/run_ 两种前缀不会同时出现在
│   └── ...                      #  同一个 goal_id 下，见下方说明）
└── cron/<job_id>/                # job_id 里的 ':' 换成 '_'，与
    ├── latest.json                # CronJobWorkspace 目录命名一致
    ├── run_<run_id>/manifest.json
    └── ...
```

- **`goals/<goal_id>/`**：
  - `recurring=True` 的 Goal：每次 `goal_cron_bridge._fire_goal_cycle()`
    成功触发新一轮时，分配 `cycle_%04d` 目录，编号为
    `GoalNode.cycle_count + 1`（触发前的值 +1）。
  - `recurring=False` 的一次性 Goal（§7 新增）：`GoalBacklog.
    add_objectives_for_goal()` 每创建一个子 Objective 时，分配
    `run_%04d` 目录，编号为该子 Objective 在父 `GoalNode.children_ids`
    里的 1-based 位置。
  - 一个 Goal 要么 `recurring=True` 走 `cycle_` 系列，要么走 `run_`
    系列，两种前缀不会混在同一个 `goal_id` 下。
- **`cron/<job_id>/`**：对应没有绑定 recurring Goal 的普通 CronJob
  （dedicated-execution 模式，`run_mode != "goal_cycle"`），用触发时的
  `run_id`（与 `.agent/cron_jobs/<job_id>/runs/<run_id>.jsonl` 同一个
  `run_id`，方便对照）分配 `run_<run_id>` 目录。
- 两者互斥：`run_mode="goal_cycle"` 的 job 只走 `goals/<goal_id>/`，不在
  `cron/<job_id>/` 下重复开一份。
- 不使用符号链接（跨平台，Windows 默认无权限创建），"最新一轮"用
  `latest.json` 这个小指针文件表达，每轮（或每个子 Objective）收尾（无论
  completed/failed/cancelled/timed_out/needs_human_review）时更新。

### `manifest.json`

```json
{
  "version": 1,
  "dir_name": "cycle_0003",
  "task_summary": "本轮任务的一句话描述",
  "started_at": 1754567890.0,
  "finished_at": 1754568900.0,
  "status": "completed",
  "artifacts": [{"path": "weekly_report.md", "description": ""}],
  "progress_note": "已完成 3/3 步骤",
  "previous_cycle_dir": ".agent/daemon_run_outputs/goals/goal_abcd1234/cycle_0002"
}
```

- `artifacts` 复用已经在跑的 Track G 产出提取结果（`ExecutionStep.
  artifacts`）——execution 收尾时把所有 step 的 `artifacts` 去重合并写入，
  不新增一套产出发现机制；dedicated-execution cron 路径（不经过
  ObjectiveExecutor）暂时写空列表。
- `progress_note` 复用 `ObjectiveExecution.progress_notes` /
  `CronJobState.progress_summary` 的既有文本。
- `previous_cycle_dir`（一次性 Goal 场景下语义是"上一个子任务的目录"，
  字段名沿用不改）让"下一轮/下一个子任务读上一份产出"不需要额外查表。

### 传递机制

- **dedicated-execution cron**：`CronJobWorkspace.render_prompt()` 新增
  `{{previous_output}}`/`{{previous_output_dir}}`/`{{output_dir}}` 三个
  占位符（配合 `{{#previous_output}}` 条件块），`DEFAULT_PROMPT_TEMPLATE`
  已更新为包含这三个占位符；已存在的自定义 `prompt.md` 如果没有这些
  占位符则不受影响。
- **recurring Goal**：`_fire_goal_cycle()` 触发子 Objective 时，在拼好的
  `description` 末尾追加"上一轮产出摘要 + 本轮产出请写入：<绝对路径>"，
  与 dedicated 模式共享同一份 `evolution/output_workspace.py` 里的
  分配/读写工具函数，避免两处实现分叉。
- **一次性 Goal**（§7 新增）：`GoalBacklog.add_objectives_for_goal()`
  创建每个子 Objective 时，同样在 `description` 末尾追加"上一个子任务
  产出摘要 + 本轮产出请写入：<绝对路径>"，逻辑与 recurring 侧对称
  （`perception/goal_backlog.py::_append_onetime_output_workspace_context()`），
  只是分配时机在子节点创建时，而不是 cron 触发时。
- `output_path_policy.py` 的默认规范新增第 5 条，说明"本轮产出请写入："
  这行优先级最高（见第 8 节）。

### 看板

Goal 卡片（周期性 + 一次性均覆盖，§7 新增）新增一个"📂 查看产出"折叠区，
通过已有的 `/fs/list`/`/fs/read` 只读接口读 `latest.json` + 最近几轮
`manifest.json`，只列文件名和备注，不做文件预览/下载——需要的话用户直接去
`.agent/daemon_run_outputs/` 目录看。折叠区内部从 `latest.json` 的
`latest_dir` 反推目录名前缀（`cycle_` 或 `run_`），不再硬编码
`cycle_`，两种命名都能正确列出历史目录。一次性 Goal 还没有任何子
Objective 收尾（没有 `latest.json`）时，折叠区静默不展示，不报错。

对应模块：`evolution/output_workspace.py`（目录分配 + manifest 读写 +
prompt 格式化，`cron_job_workspace.py`/`goal_cron_bridge.py`/
`cron_job_executor.py`/`objective_executor.py`/`perception/goal_backlog.py`
都只调用这个模块）。

## 12. Goal 执行规范（GoalExecutionSpec）

第 10 节的产出目录/`manifest.json`/跨轮传递机制是**对所有 Goal 一视同仁**
的通用管道。如果还想让某个具体 Goal 声明"我每轮该产出什么文件、要跨轮
记住哪些结构化信息、用什么标准判断这一轮做到位了"，可以在此基础上为该
Goal 生成一份 `GoalExecutionSpec`（自动生成草稿 → 反馈迭代 → 用户确认后
才生效），入口就在「⏰ 周期性设置」/「➕ 新建目标」/`/agent goals spec
generate` 命令里。详见 [Goal 执行规范指南](goal-execution-spec-guide.md)。

## 13. 已知限制（新增）

- 用户意见追加不做去重/冲突检测：同一个节点反复提相互矛盾的意见时，历史全部保留，
  agent 在执行时自己综合判断，不会自动合并或提示冲突。
- 产出路径规范只是 prompt 层面的软约束，不做运行时强制校验/拒绝执行。
- 产出目录规范不做旧数据迁移（改造上线之前的历史产出散落在哪就留在哪），也不做
  `manifest.json` 里 `artifacts` 声明路径的存在性校验，更不做自动清理/归档策略。
## 14. 机制是否需要覆盖一次性（非 recurring）Goal 的判断原则

[Track 4，见 `next_doc/goal_cron_convergence_and_governance_improvement_
plan.md` §4] 梳理已有机制时发现一个反复出现的问题：新设计一个
goal/cron 相关机制时，"要不要覆盖一次性 Goal，只覆盖周期性 Goal 够不够"
经常要重新纠结一遍。这里把已经出现过的两个判例归纳成一条可复用的判断
原则，供后续设计新机制时直接参考：

> **判断某个机制是否需要覆盖一次性 Goal，问一个问题：这个机制的价值是否
> 依赖"跨轮次"这个前提？**
>
> - 如果价值本质上来自"比较多轮之间的变化"（健康趋势判断、
>   探索期→收敛期→稳定期的阶段状态机演进），一次性 Goal 天然不存在
>   "多轮"，不需要覆盖。
> - 如果价值来自"让后续步骤能接上前面步骤的产出/进度"，这件事在
>   周期性 Goal 的"下一轮 cycle"和一次性 Goal 的"下一个子 Objective"
>   之间是同构的（都是"链表指针指向上一个节点"），应该覆盖。

两个对照案例：

| 机制 | 是否覆盖一次性 Goal | 依据 |
|---|---|---|
| 主动巡检（`cycle_patrol`，见第 5 节） | **不覆盖**，只巡检 `recurring=True` | 巡检的价值来自"这个 Goal 最近几轮的健康趋势"，一次性 Goal 没有"几轮"这个概念 |
| 产出目录规范（第 10 节） | **覆盖**，一次性 Goal 拆解出的多个子 Objective 之间同样套用 | 价值来自"下一个子任务接得上上一个子任务的产出"，与是否周期性无关，§7 的评审结论正是这个道理的具体实例 |

这条原则不要求现有机制立刻按它调整覆盖范围——已经存在的设计选择如果
本身符合这条原则就保持不变；如果后续复查发现某个机制的现状与原则推导
结果不一致，单独记录、单独评审，不在归纳原则的同时顺带改动代码行为。
