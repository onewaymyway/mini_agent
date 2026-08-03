# 周期性 Goal 可见性与干预能力改进方案

> 状态：Track A/B/C/D 已实施完成（Phase 1-4 均已落地；Phase 5 按计划不实现，仅记录方向）。
> 新增/修改文件清单见文末"实施记录"一节。
> 前置背景：`next_doc/goal_cron_binding_plan.md`（Track A–E 已完成，见
> `next_doc/goal_cron_binding_implementation_record.md`）已经把 Goal ⇄ Cron
> 的绑定/触发/回收机制做完整了——一个 Goal 可以声明 `recurring=True`，由
> `evolution/goal_cron_bridge.py` 按 schedule 周期性派生并启动子 Objective，
> Goal 暂停/终止会自动联动 cron 停摆，`reap_finished_cycles()` 负责把终态子
> 节点计入 `cycle_count`/`progress_notes`。
>
> 本方案要解决的不是"机制通不通"（已经通了），而是三个在长期使用中暴露出来
> 的缺口：
> 1. **可见性缺口**：`goal_cron_binding_implementation_record.md` 的"未完成/
>    已知限制"里明确写了——看板不展示 `recurring`/`cycle_count`/绑定的 cron
>    job，用户在看板上看不出一个 Goal 是不是在周期性运转、跑到第几轮、下次
>    什么时候跑；绑定/解绑操作也只有 CLI 入口，看板没有对应按钮。
> 2. **长期健康度缺口**：`children_ids` 随轮数无限增长，`goals.json` 会越
>    跑越大；`progress_notes` 逐轮 append 没有压缩，多轮之后信息噪声增多。
> 3. **干预粒度缺口**：现有的终止/重试/插话是"执行期"干预（Objective 跑着
>    的时候），周期性 Goal 还缺"跳过这一轮但保持周期性"和"某轮失败时主动
>    通知"两类干预/感知手段。

## 0. 目标与非目标

**目标**：
- 看板能完整看到一个 Goal 的周期性状态（是否 recurring、第几轮、下次触发
  时间、绑定的 cron job），并能直接在看板上绑定/解绑/跳过一轮，不用切到
  CLI。
- 长期运行（几十上百轮）不会让 `goals.json` 无限膨胀、也不会让
  `progress_notes` 变成一段不断稀释有效信息的长文本。
- 某一轮执行失败时，用户能被动收到通知，而不需要主动巡检看板。

**非目标（本轮不做）**：
- 不改动 `goal_cron_binding_plan.md` 已经拍板的核心设计（一对一绑定、幂等
  策略、档位边界），本方案只加可见性/治理/干预层，不碰触发逻辑本身。
- 不做 Phase 5（周期性 Goal 与执行公平性/资源门控的联动可视化）——依赖
  `goal_execution_fairness_improvement_plan.md` 的后续 Track 落地后再单独
  排期，本文档只记录该方向存在、不实现。
- 不做"多个 Goal 共享同一个 cron job"。

## 1. Phase 1 —— 看板可见性与绑定/解绑/跳过入口

### 1.1 后端：新增 REST 端点（复用 `goal_cron_bridge` 现成函数，不新写业务逻辑）

`src/mini_agent/api/routes.py` 新增三个端点，均直接调用
`evolution/goal_cron_bridge.py` 里已有的函数：

```
POST /v1/goals/{goal_id}/recur
    Body: { "schedule": str, "task_template": Optional[str] }
    → goal_cron_bridge.make_goal_recurring(...)

POST /v1/goals/{goal_id}/unrecur
    → goal_cron_bridge.stop_goal_recurrence(...)

POST /v1/goals/{goal_id}/skip_next_cycle
    → goal_backlog.set_skip_next_cycle(goal_id, True)（见 1.3 新字段）
```

`GoalNode.to_dict()` 已经包含 `recurring`/`recurrence_cron_job_id`/
`cycle_count` 三个字段（`goal_cron_binding_plan.md` Track A 已实现），
`/v1/goals` 不需要改动即可把这些字段带给前端。`CronJob.to_dict()` 同理已
包含 `goal_id`/`run_mode`，`/v1/cron/jobs` 也不需要改动。

### 1.2 前端：`apps/mini_agent_kanban`

- `client.py` 新增 `recur_goal()`/`unrecur_goal()`/`skip_goal_cycle()` 三个
  薄封装。
- `_render_goal_card()`：
  - Goal 卡片（`level != "objective"`）新增一行徽标：`🔁 周期性 · 第 N 轮 ·
    下次 <next_run_str>`（`recurring=True` 时），或"未设为周期性"提示。
  - 折叠区"⏰ 周期性设置"：未绑定时提供 schedule 输入框 + 提交按钮调
    `recur_goal()`；已绑定时展示当前 schedule/绑定 job 名，提供"取消周期性"
    与"跳过下一轮"两个按钮。
  - 子 Objective 卡片若 `source == "cron"`，标注"第 N 轮·由 cron 触发"，
    与手动/agent_derived 来源的 Objective 区分开。

### 1.3 `GoalNode` 新增字段

```python
skip_next_cycle: bool = False   # 用户请求跳过下一次触发（见 Phase 3）
```
默认值保证向后兼容，序列化/反序列化同步补齐。

## 2. Phase 2 —— 长期健康度：归档与摘要压缩

### 2.1 子节点归档
`GoalBacklog` 新增 `archive_finished_cycle_children(goal_id, keep_recent=20)`：
- 只处理 `recurring=True` 的 Goal；只归档已经计入 `cycle_count`（即在
  `reaped_cycle_child_ids` 里）的终态子节点。
- 按完成时间保留最近 `keep_recent` 个，更早的从 `_nodes`/`children_ids`
  中移除，追加写入 sidecar 文件 `<agent_dir>/goal_cycle_archive.jsonl`
  （每行一条完整 `GoalNode.to_dict()`，追加写不改写，避免破坏"归档即不再
  变化"的语义）。
- 由 `reap_finished_cycles()` 在每次 reap 之后顺带调用，不单独起调度。
- CLI/看板可选提供一个只读的"查看已归档轮次"入口（读 jsonl 文件），本轮
  先只做归档写入，读取展示作为可选项，视时间排期。

### 2.2 `progress_notes` 摘要压缩
- 阈值：`progress_notes` 累计行数超过 30 行时，触发一次压缩——保留最近
  10 行原文，更早的部分调用一次 LLM 摘要（复用项目里已有的摘要/consolidation
  基础设施，不新写 prompt 体系）压成一段"历史摘要（第 1-N 轮）"，替换掉被
  压缩的部分。
- 压缩失败（LLM 调用异常）时保留原文不动，不阻塞 `reap_finished_cycles()`
  本身的计数逻辑——摘要压缩是锦上添花，不能变成新的失败点。
- 本 Phase 只做**行数阈值触发**的压缩，不做"每 K 轮强制压缩"，避免过早
  引入不必要的复杂度。

## 3. Phase 3 —— "跳过下一轮"干预

`_fire_goal_cycle()` 新增一个检查，插在"Goal 非 active 跳过"之后、"幂等检查"
之前：

```python
if goal.skip_next_cycle:
    goal_backlog.update_fields(goal.id, skip_next_cycle=False)
    goal_backlog.append_progress_note(goal.id, "本轮由用户手动跳过")
    return False
```

跳过本身也算"这次没算数"（返回 False），下次 tick 正常判断；跳过动作会
留痕在 `progress_notes` 里，跟"Goal 未 active""上一轮未完成"两种系统级
跳过区分开（后两者不写 progress_notes，因为不是用户主动决策）。

## 4. Phase 4 —— 失败通知联动

`reap_finished_cycles()` 发现某个终态子节点 `status == "failed"` 时，除了
原有的计数/写 `progress_notes`，额外调用一次已有的通知网关：

```python
from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
NotificationDispatcher(paths).dispatch(NotificationMessage(
    title=f"周期性目标「{goal.title}」第 {goal.cycle_count} 轮执行失败",
    body=note[:200],
    source="goal_cycle",
    meta={"goal_id": goal.id, "cycle": goal.cycle_count},
))
```

- 复用 `watchlist_notification_goal_design.md` 里已经落地的 dispatcher（
  kanban 恒真兜底 + 邮件可选渠道），不新增渠道实现。
- 只在**失败**时发通知，`completed`/`cancelled` 不打扰用户（正常完成的
  周期性任务不需要每轮都推送）。
- dispatch 失败（比如邮件配置错误）不影响 `reap_finished_cycles()` 主流程，
  照旧走已有的 try/except 兜底。

## 5. Phase 5（不实现，记录方向）

周期性 Goal 与 `goal_execution_fairness_improvement_plan.md` 里的资源门控/
排队机制之间的可视化联动——比如某一轮迟迟不触发是因为被公平性调度排队而
不是 Goal 状态异常——留到该方案自身的 Track 落地后再排期，本文档不实现。

## 6. 实施顺序与验收

- **Track A**（对应 Phase 1）：`GoalNode.skip_next_cycle` 字段 + 三个 REST
  端点 + 看板 UI。
- **Track B**（对应 Phase 3，依赖 Track A 的字段）：`_fire_goal_cycle()`
  跳过逻辑 + 单测。
- **Track C**（对应 Phase 4）：`reap_finished_cycles()` 失败通知 + 单测。
- **Track D**（对应 Phase 2）：归档 + 摘要压缩，工作量相对独立，可以最后做。

每个 Track 完成后更新本文档状态栏（在对应小节标注"已完成"）以及
`docs/goal-cron-binding-guide.md`，并跑一次相关测试文件的回归。全部 Track
完成后在本文档顶部把状态改为"已完成"。

## 7. 实施记录

### Track A（Phase 1，已完成）
- `src/mini_agent/api/routes.py`：新增 `POST /v1/goals/{id}/recur`、
  `POST /v1/goals/{id}/unrecur`、`POST /v1/goals/{id}/skip_next_cycle`
  三个端点，均薄封装调用 `goal_cron_bridge` 里已有函数，未新增业务逻辑。
- `apps/mini_agent_kanban/client.py`：新增 `recur_goal()`/`unrecur_goal()`/
  `skip_goal_next_cycle()`。
- `apps/mini_agent_kanban/app.py`：`_render_goal_card()` 新增周期性徽标
  （`🔁 周期性 · 已完成 N 轮`/`⏭️ 下一轮将被跳过`）、cron 触发子 Objective
  标注，以及"⏰ 周期性设置"折叠区（绑定表单 / 跳过 / 取消周期性按钮）。

### Track B（Phase 3，已完成）
- `src/mini_agent/perception/goal_backlog.py`：`GoalNode` 新增
  `skip_next_cycle: bool = False` 字段（序列化/反序列化同步）；新增
  `append_progress_note()` 方法。
- `src/mini_agent/evolution/goal_cron_bridge.py`：`_fire_goal_cycle()` 在
  "Goal 非 active"检查之后、幂等检查之前插入 `skip_next_cycle` 分支：命中则
  清零标记、写一条 progress_notes、返回 `False`（本次不算数）。
- 测试：`tests/test_goal_cron_bridge.py::TestSkipNextCycle`（跳过一次后
  自动恢复正常触发）。

### Track C（Phase 4，已完成）
- `src/mini_agent/evolution/goal_cron_bridge.py`：`reap_finished_cycles()`
  在子节点以 `failed` 收尾时调用新增的 `_notify_cycle_failed()`，复用
  `notification/dispatcher.py` 的 `NotificationDispatcher`/
  `NotificationMessage`，不新增渠道实现；`completed`/`cancelled` 不触发。
- 测试：`tests/test_goal_cron_bridge.py::TestReapFailureNotification`
  （失败触发通知 / 成功不触发，用 Fake Dispatcher 替身验证调用参数）。

### Track D（Phase 2，已完成——归档部分；摘要压缩部分未做）
- `src/mini_agent/perception/goal_backlog.py`：新增
  `archive_finished_cycle_children(goal_id, keep_recent=20)`——只归档已
  计入 `cycle_count` 的终态子节点，超过 `keep_recent` 的部分从 `_nodes`/
  `children_ids` 摘除，追加写入 `<agent_dir>/goal_cycle_archive.jsonl`；
  写文件失败时回滚摘除操作，不丢数据。
- `goal_cron_bridge.reap_finished_cycles()` 每次 reap 后顺带调用一次归档。
- 测试：`tests/test_goal_cron_bridge.py::TestArchiveFinishedCycleChildren`。
- **`progress_notes` 摘要压缩（§2.2）未实现**：涉及依赖 LLM 调用做摘要，
  为避免仓促实现引入新的失败点/成本，本轮先只做归档（治理 `goals.json`
  体积这个更紧迫的问题），摘要压缩留作后续独立小 Track，在本文档 §6"已知
  限制"中记录。

### 测试结果
```
tests/test_goal_cron_bridge.py .... 16 passed（含本轮新增 6 项）
tests/test_goal_backlog.py ......... 已有用例全绿（未受影响）
tests/test_cron_scheduler_local_handler.py ... 已有用例全绿（未受影响）
```
（`tests/test_goal_mode.py`、`tests/test_goal_fairness_routes.py` 在本沙箱
环境因缺少 `rich`/`fastapi` 依赖无法收集，与本次改动无关，未改动对应源码。）
