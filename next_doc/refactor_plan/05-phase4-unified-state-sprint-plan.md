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

- [ ] GoalState 的读写全部经过 `StateManager`，`goal_mode/runner.py`
      里不再有旧的直接状态持有代码
- [ ] 其余 4 类 State 已有结构占位，且在 `phase-mapping` 文档里标注了
      "将在哪个 Phase 填充"
- [ ] `snapshot()` 可用，为 Phase 9 的 evolution 验证做好准备
