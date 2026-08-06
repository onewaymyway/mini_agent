# 看板调度模式可见性补充（基于 scheduling_unification_and_kanban_visibility_improvement_plan.md 之后的缺口）

**日期**：2026-08-07

## 背景 / 读码确认的缺口

`GET /v1/self/scheduling_overview`（P4 已实现）和看板「🕹️ 统一调度总览」
区块已经展示了 gating 状态、Goal 通道并发槽位（running/max/static_cap）、
cron 通道 running/queued、goal_cycle 通道待触发数。但读码确认还缺两块用户
明确要看的信息：

1. **cron 通道的最大并发限制**：`CronJobRunner.effective_max_concurrent()`
   和构造时的静态上限 `_max_concurrent` 后端早就实现了（P0），但从未透出到
   `scheduling_overview`，也从未在看板渲染——只显示了 running/queued 两个
   计数，看不出"上限是多少"。
2. **当前处于什么调度模式**：`scheduler.unified_arbitration_enabled`（degraded
   槽位是否由 UnifiedTaskScheduler 按 channel_weights 统一裁决）、
   `autonomy.adaptive_concurrency_enabled`（Goal 通道并发是否按失败率/耗时
   自适应收紧）、`autonomy.resource_gating_degraded_enabled`（degraded 是否
   真的收紧并发，还是只是提示）——这几个开关只能翻配置文件才知道有没有开，
   看板完全没有对应的展示位。已有的 `GET /v1/self/unified_scheduler_preview`
   端点虽然算出了 `slot_allocation`，但从未被 `client.py`/`app.py` 调用过。

## 改动

- `src/mini_agent/api/routes.py::get_self_scheduling_overview()`：
  - 新增顶层 `scheduling_mode` 字段：`unified_arbitration_enabled` /
    `adaptive_concurrency_enabled` / `resource_gating_degraded_enabled` /
    `channel_weights`（仅统一仲裁开启时非 None）/ `degraded_allocation`
    （仅统一仲裁开启且当前 gating 状态为 degraded 时才计算，复用
    `unified_scheduler_preview` 里已有的 `allocate_weighted_slots()` 调用
    方式，未新增计算逻辑本身）。
  - `cron_channel` 新增 `max_concurrent`（`CronJobRunner.
    effective_max_concurrent()`）和 `static_max_concurrent`
    （`CronJobRunner._max_concurrent`），job_runner 未注入时都是 None，
    不影响其余字段正常返回。
- `apps/mini_agent_kanban/app.py::_render_scheduling_overview()`：
  - gating 状态下方新增"当前调度模式"一行，三个开关各自用 🟢开/⚪关 展示；
    统一仲裁开启时额外展示 `channel_weights` 和（degraded 时）实际的槽位
    分配。
  - cron 通道区块的 running/queued 指标下新增"当前并发上限"caption，
    上限与静态上限不同时（即当前处于 degraded 收紧状态）额外标注静态上限
    作为对照。
  - Goal 通道的"并发槽位"指标标签改为"并发槽位（运行中/当前上限）"，
    消除"这个数字到底是运行数还是上限"的歧义。

## 未做

- 未新增 `client.py::unified_scheduler_preview()` 方法或对应的看板 tab——
  该端点展示的是"建议执行顺序"这类更细粒度的调度预览，和本轮"最大并发
  限制 + 调度模式"这个具体诉求不是同一层次的信息，留作后续独立任务。
- 未改动任何后端裁决逻辑本身（`effective_max_concurrent()` /
  `allocate_weighted_slots()`），纯粹是把已有计算结果透出到看板，
  遵循"观测和决策分离"的既有风格。

## 测试

`tests/test_scheduling_overview_route.py` 新增 3 个用例（原 5 个全部保留，
无回归）：
- `test_scheduling_mode_defaults_when_unified_arbitration_absent`：cfg 无
  `scheduler` 字段时安全降级，`unified_arbitration_enabled=False`，
  `channel_weights`/`degraded_allocation` 均为 None。
- `test_scheduling_mode_reports_degraded_allocation_when_unified_and_degraded`：
  开启统一仲裁且 gating 落入 degraded 时，`degraded_allocation` 正确算出
  goal/cron 两个槽位数。
- `test_cron_channel_reports_max_concurrent_from_job_runner`：注入真实
  `CronJobRunner` 后 `max_concurrent`/`static_max_concurrent` 正确透出。

全量运行：`test_scheduling_overview_route.py`（8 用例）+
`test_cron_job_runner.py` + `test_cron_job_runner_resource_arbiter.py` +
`test_execution_model_status_routes.py` + `test_goal_cron_bridge.py` +
`test_unified_task_scheduler*.py` 合计 73 个测试全部通过，无回归。
`py_compile` 确认 `routes.py`/`app.py` 语法正确。
