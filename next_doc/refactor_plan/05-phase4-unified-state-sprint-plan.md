# Phase 4：统一 State —— 可执行计划

> 对应原方案 §37。前置条件：`core/` 中已有 `GoalState`、`Experience`，
> Phase 2 的 Event 总线已跑通。

## 目标与边界

原文强调"State 不等于数据库，是当前系统对现实的结构化认知"。这一 Phase
容易做过度设计（一次性把 SelfState/WorldState/GoalState/CapabilityState/
RuntimeState 全部实现），需要明确边界：

**本 Phase 只做 `StateManager` 的骨架 + `GoalState` 的真实托管**（因为
GoalState 已经在 Phase 1 落地）。`SelfState`、`WorldState` 只建空
dataclass 占位，不实现内部逻辑——留给 Phase 5 之后按需填充，避免在
没有真实使用场景前猜测字段。

## Sprint 4-1（1.5 周）：StateManager 骨架

| 任务 | 产出 |
|---|---|
| `core/state_manager.py` | 提供 `get_state(kind) -> State`、`update_state(kind, event) -> State` 两个核心方法 |
| GoalState 接入 | 把 Phase 1 里 `GoalAdapter` 产生的 `GoalState` 改由 `StateManager` 统一持有和更新，而不是 `goal_mode/runner.py` 自己维护 |
| 事件驱动更新 | `StateManager` 订阅 Phase 2 的 Event 总线，收到 `GoalUpdated` 类事件后自动更新内部 `GoalState`，而不是被动等外部调用 `update_state` |

**验收标准**：`goal_mode/runner.py` 不再自己持有 GoalState 的可变引用，
而是每次通过 `state_manager.get_state("goal")` 读取——这是验证
"State 真正被统一管理"的关键标志，不是看有没有写 `StateManager` 这个类。

## Sprint 4-2（1 周）：占位 State + 一致性快照

| 任务 | 产出 |
|---|---|
| `SelfState` / `WorldState` / `CapabilityState` / `RuntimeState` 占位 | 只定义字段结构（参照原文 §5.1、§6、§9、§12），不实现内部更新逻辑，标注 `# TODO: Phase 5/6/8 填充` |
| 一致性快照 | `state_manager.snapshot() -> dict`，能一次性导出当前所有 State 的快照，用于调试和 Phase 9 的 evolution 沙盒对比 |

**验收标准**：`snapshot()` 输出的结构和 Sprint 4-1 里 GoalState 的真实数据
一致，且占位 State 不会导致调用报错（哪怕内容是空的）。

**止损条件**：如果发现 SelfState/WorldState 的字段设计需要等 Phase 5/6
的真实场景才能确定，**不要在本 Phase 强行填充猜测字段**——占位就是占位，
宁可留 TODO 也不要编造不会被用到的字段。

## 完成标志

- [x] GoalState 的读写全部经过 `StateManager`，`goal_mode/runner.py`
      里不再有旧的直接状态持有代码（Sprint 4-1 已完成，见下方执行记录）
- [ ] 其余 4 类 State 已有结构占位，且在 `phase-mapping` 文档里标注了
      "将在哪个 Phase 填充"（Sprint 4-2，留待下一次推进）
- [x] `snapshot()` 可用，为 Phase 9 的 evolution 验证做好准备
      （Sprint 4-1 已提供骨架实现，Sprint 4-2 加入占位 State 后无需
      改动 `snapshot()` 本身，见下方执行记录）

## Sprint 4-1 执行记录

新增 `core/state_manager.py::StateManager`：`get_state(kind)` /
`update_state(kind, state)` 两个核心方法 + `snapshot()`（遍历内部
`_states` dict 导出所有已托管 State 的字典快照）。Sprint 4-1 范围内
只有 `"goal"` 这一个 kind 有真实托管逻辑（`SelfState`/`WorldState`/
`CapabilityState`/`RuntimeState` 占位留给 Sprint 4-2）。

`goal_mode/runner.py` 唯一接入点改造（与 Sprint 1/2/3 一贯的"只在
`run()`/`_finish()` 一处接入，不改动主循环内部逻辑"模式一致）：

1. `run()` 开始时，`GoalAdapter.to_new(spec)` 转出的 `GoalState` 不再
   只是一个用完即弃、只用来打一条 trace 日志的局部变量——立即通过
   `state_manager.update_state("goal", ...)` 交给 `StateManager` 托管
   （本次 `run()` 里唯一一次手写 `update_state` 调用），随后的 trace
   日志也改成从 `state_manager.get_state("goal")` 读回来打印，证明
   "读"这一侧也走的是 `StateManager`，不是本地变量。
2. 任务表里"事件驱动更新"一项：`EVENT_KINDS`（`core/events.py`）里
   `Sprint 2-1` 就已经预留了 `"GoalUpdated"` 这个取值，但此前一直没有
   任何 publish 调用真正用到它。Sprint 4-1 补上这个空缺——每轮
   CONTINUE 真正推进（`self._round += 1`）时 publish 一次
   `GoalUpdated(round=当前轮次, status="running")`；`_finish()` 终止时
   再 publish 一次 `GoalUpdated(round=最终轮次, status=最终状态)`。
   `StateManager.subscribe_to_bus()` 订阅这个 kind，收到后自动把内部
   持有的 `GoalState.round`/`status`/`updated_at` 更新掉——`runner.py`
   自己不再直接改一份 `GoalState` 的字段。
3. 挂载订阅（`ensure_state_manager_subscribed()`）与 Sprint 2-2/3-1 的
   `ensure_event_log_subscribed()`/`ensure_experience_recorder_subscribed()`
   同款：按 `(bus 实例, manager 实例)` 去重，幂等，放在本次 `run()`
   第一次 `publish(GoalUpdated)` 之前。

**验收标准**核对：`goal_mode/runner.py` 里搜索不到任何"自己持有一份
可变 `GoalState` 引用并直接改字段"的代码——`_core_goal_state` 现在
只是 `state_manager.get_state("goal")` 的只读返回值，且只用于打印
trace 日志，不参与任何判断分支。已用新增测试
`tests/test_phase4_state_manager.py`（7 用例，覆盖：空状态读取、
`update_state`/`get_state` 往返、`GoalUpdated` 事件驱动更新、未 seed
时收到事件的容错、`snapshot()`、端到端跑一次 `GoalRunner.run()` 后
`StateManager` 全局单例里的 `GoalState.status` 变为 `"done"`、幂等
订阅）验证，全部通过。

**回归测试**：新增的两处 `GoalUpdated` publish 会被 `EventLogStore`
（订阅的是 `EVENT_KINDS` 全集，`"GoalUpdated"` 本就在这个常量元组里）
落盘，因此因果链路上多了一环——`tests/test_goal_mode_phase2_events.py`
与 `tests/test_phase2_event_log_and_cli.py` 两处对事件序列做了精确
断言的测试同步更新为
`[..., ActionCompleted, GoalUpdated, ExperienceCreated]`（原先是
`[..., ActionCompleted, ExperienceCreated]`），这是有意的行为变化，
不是意外破坏。相关回归测试（`test_goal_mode.py` / 两个 Phase 2 事件
测试文件 / `test_phase3_experience_recorder.py` /
`test_phase3_experience_retrieval_and_patterns.py` /
`test_phase3_experience_retrieval_injection.py` / `test_core_events.py` /
`test_core_event_bus.py` / `test_goal_tree_phase1~4.py` /
`test_core_experience_store.py`）共 238 用例，233 通过，5 个既有失败
（`test_build_from_history_*`，与本次改动无关的既有 fixture 问题，
`README.md`/`MIGRATION_STATUS.md` 此前已多次确认）；依赖图核对
（`scripts/dep_graph.py --module core.state_manager` inbound=1、
outbound=0，未触发止损阈值）+ 未发现新增回归。

Sprint 4-2（`SelfState`/`WorldState`/`CapabilityState`/`RuntimeState`
占位 + 一致性快照的占位联调）留待下一次推进。
