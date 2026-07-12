# 跨子系统事件总线设计与使用指南

> 对应模块：`src/mini_agent/perception/system_events.py`
> 相关讨论见 `docs/memory-and-self-evolution-complete-reference.md` 第16节
> "已知架构不对称与后续方向"——本模块是该节里"跨系统联动靠硬编码耦合、
> 加一条新联动要改老函数"这个问题的通用基础设施解法。

## 1. 要解决的问题

记忆 / 自我进化 / 具身感知这三层之间，此前已经有多处"某模块写一个状态文件，
另一个模块按自己的节奏去读"的信号桥接，但都是**独立实现、互不复用**的：

| 信号源 | 落盘位置 | 消费方 | 触发方式 |
|---|---|---|---|
| proprioception（挫败感） | `proprioception_snapshot.json` | `ResourceArbiter._check_frustration()` | `can_run_autonomous()` 被调用时轮询 |
| Phase G 节奏治理 | `rhythm.json` | `rhythm_is_allowed()` | 每次 prune/promote 前查一次 |
| Self 维护间隔 | `self_maintenance_state.json` | `should_run_self_maintenance()` | SessionEnd / cron 定时查 |

这些都是**快照模式**（覆盖写、只存"最近一次状态"），适合"我想知道当前是什么
状态"，但不适合"我想知道刚刚发生了什么事、不想错过"——后者需要**日志语义**
（追加写、每条独立、可被多个消费者各自按自己的进度读取）。`system_events.py`
补的就是这条日志语义的通用基础设施，供任意"A 模块的状态变化需要通知 B 模块"
场景复用，不用每次都新造一个状态文件 + 手写轮询判断。

## 2. 三条设计硬约束

1. **文件优先，内存不是事实来源**。`publish()` 必须先落盘成功才算发布成功。
   原因：Windows 平台上项目此前（`goal_backlog.py`）对无 `fcntl` 环境是"不加锁、
   尽力而为"的降级策略，纯内存队列一旦丢失没有任何补救路径；`events.jsonl`
   落盘 + 跨平台锁是唯一可靠的事实来源。

2. **不同事件允许不同的响应粒度，由事件自己声明**。`tier` 字段显式标注该
   事件期望被多快处理，直接挂到代码里已经存在的三种调度节奏上，不新增
   第四种机制：

   | tier | 对应的既有调度节奏 | 检查周期 | 适合的事件类型 |
   |---|---|---|---|
   | `instant` | `AgentRunner._main_loop()` 的 `dequeue(timeout=0.5)` 循环 | ~0.5s | frustration 突增、连续工具失败 |
   | `tick` | `AutonomousLoop.tick()` | 默认 60s | 记忆稀疏区检测、探索预算调整 |
   | `cron` | `rhythm_is_allowed()`/`should_run_self_maintenance()` | 24h 或按配置 | outcome 汇总、健康报告 |

3. **不新增线程、不做真正的进程间推送**。整个代码库的调度风格是"轮询 + 状态
   文件"，本模块延续这个风格：所谓"即时"，实际是"下一次已经存在的 0.5s
   循环体顺带查一下"，不是异步中断。诚实的定位是"高频轮询 + 落盘游标"，
   不是真正的事件推送——这样接入成本低、行为可预测、不引入新的并发复杂度。

## 3. 文件布局

```
.agent/
├── events.jsonl                  # 主日志，追加写，跨平台锁保护
├── events_archive/               # 超过 10MB 自动滚动归档
│   └── events_2026-07-12_143022.jsonl
└── event_cursors/                # 每个消费者独立的读取游标
    ├── daemon_instant_consumer.json
    ├── soft_goal_deriver.json
    └── ...
```

单条事件 schema：

```json
{
  "event_id": "a1b2c3d4",
  "ts": 1752300000.123,
  "source": "session:xxxx",
  "event_type": "proprioception.frustration_spike",
  "tier": "instant",
  "payload": {"frustration": 0.62, "consecutive_failures": 3}
}
```

游标 schema：

```json
{"last_event_id": "a1b2c3d4", "last_ts": 1752300000.123}
```

## 4. API

```python
from mini_agent.perception import system_events as se

# 发布（调用方应在"状态边沿"而非"每次采样"时调用，避免刷屏）
se.publish(
    paths,
    source="session:xxxx",
    event_type="proprioception.frustration_spike",
    tier="instant",              # "instant" | "tick" | "cron"
    payload={"frustration": 0.62},
)

# 消费（按自己的调度节拍调用；同一 consumer_name 的游标自动持久化推进）
events = se.poll_since(
    paths,
    consumer_name="soft_goal_deriver",
    tiers=["tick"],                              # 可选过滤
    event_types=["memory.sparse_region_detected"],  # 可选过滤
    advance_cursor=True,          # False = 只读 peek，不推进游标（/diagnostics 用）
)
```

`publish()` 内部异常静默吞掉（事件发布是旁路增强，不能拖垮调用方主流程）；
`poll_since()` 读取失败返回空列表。两者都不会向上抛异常。

## 5. Windows 兼容性

`events.jsonl` 是高频追加场景，比 `goal_backlog.py` 更容易撞上多 session/
多进程并发写交错，因此没有沿用"Windows 就不锁"的旧模式，补全了
`msvcrt.locking` 分支（`_LockedFile` 类，见模块源码）。已在 Linux（`fcntl`）
路径下用 8 线程并发写测试验证过无交错；**Windows 的 `msvcrt` 分支需要在实际
Windows 开发环境中额外跑一次 `tests/test_system_events.py::TestSystemEventsConcurrency`
确认**，沙箱环境是 Linux，测不到那条代码路径。

## 6. 已接入的具体案例

### 6.1 frustration_spike → 提前自维护

- 发布点：`agent.py::_write_proprioception_snapshot()` 调用处，仅在
  `frustration` 越过 `cfg.proprioception.frustration_threshold` 阈值边沿时发布
  （不是每次快照变化都发）。
- 消费点：`api/server.py::AgentRunner._drain_system_events()`，挂在
  `_main_loop()` 原有的 0.5s idle 分支里，与 `_drain_self_messages()` 同一节奏。
  收到事件后调用 `_maybe_early_self_maintenance()`，复用
  `should_run_self_maintenance()`/`run_self_maintenance()`，只是把时间门控
  从默认 24h 换成事件触发专用的 1h，避免"一次挫败感尖峰触发自维护 →
  自维护本身的工具调用又失败一次 → 又触发一次"的连锁抖动。
- 同一 workdir 下所有 `AgentRunner` 共享 `consumer_name="daemon_instant_consumer"`
  同一份游标——健康检查只需要跑一次，不需要每个 session 线程各跑一次；
  第一个读到事件的线程推进游标，其余线程自然读不到已消费的事件。

### 6.2 memory.sparse_region_detected → 探索 novelty 加权

- 发布点：`hybrid_memory_backend.py::search()`，TF-IDF 和 embedding 两路
  召回都为 0 时触发，限流最短间隔 60s（没有稳定的"边沿"概念可判断，
  用限流代替严格边沿检测）。payload 携带 query 分词后的 token（截断前 8 个）。
  `HybridMemoryBackend` 未传入 `paths` 时（老调用方/测试）完全不发布，
  行为与改动前一致。
- 消费点：`soft_goal_deriver.py::_from_unexplored_capabilities()`
  （信号4：未探索能力），通过 `_recent_sparse_region_tokens()` 读取事件、
  `_domain_token_overlap()` 做简单子串匹配，novelty 按重合度加权
  （封顶 1.6x，避免稀疏信号完全压过 `total_calls` 本身的基础判断）。

### 6.3 evolution.outcome_negative → 回写 lesson，闭合效果回填环

- 发布点：`outcome_tracker.py::tick()`，`verdict == "worsened"` 时调用
  `_write_eval_failure_lesson()`——直接把负面判定写成一条
  `source="eval_failure"` 的新 lesson（不是等一个独立消费者去解析事件
  payload 重建 lesson 内容，因为 `tick()` 本身已经握有 `memory_backend`
  和完整的 baseline/post 统计，直接写是最省事也最不容易信息失真的方式），
  同时 publish `evolution.outcome_negative` 事件（tier=`tick`）广播给
  其他潜在消费者（比如未来的 `/diagnostics` 展示、`SoftGoalDeriver`
  避免重复推导同一条已知会变差的候选）。
- 此前这个环是断开的：`outcome_tracker` 判定失败后只更新自己的追踪记录
  （`get_revert_candidates()` 供 `/digest` 展示），除非用户手动
  `/evolution revert`（才会写 `revert_record` lesson），负面判定本身
  不会成为记忆系统能再次检索到的经验。修复后，同一类问题下次被自我
  进化尝试修复时，`AffordanceAnalyzer`/`SoftGoalDeriver` 能检索到"上一次
  类似的修复反而让情况变差了"这条记忆。
- `memory_aging.py` 补了 `"eval_failure": 21.0` 天的专属半衰期
  ——比 `self_reflection`（30天）衰减快，因为是针对某次具体 commit
  的观察，环境变化后可能不再适用；比 `revert_record`（14天）衰减慢，
  因为有实测数据支持（baseline/post 触发次数对比），不是单次用户操作。

### 6.4 goal.candidate_unvalidated → 轻量一致性复核

- 发布点：`soft_goal_deriver.py::commit_goals()`，`source_tag` 为
  `"workthread"`/`"lesson"` 的候选（不经过 `ExplorationSandbox` 验证）
  写入 `GoalBacklog` 时，打上 `needs_review` 标签并发布事件
  （tier=`tick`），缓解第16节提到的"验证不对称"。
- 消费点：`soft_goal_deriver.py::review_unvalidated_candidates()`，挂在
  `autonomous_loop._tick_autonomous()` 里、在本轮提交新候选之前先复核
  上一轮的 `needs_review` 候选（本轮新提交的要等下一次 tick 才被复核，
  这是"轮询+游标"模型的正常延迟）。复核逻辑不是完整的
  `ExplorationSandbox`，只是重新核对"当初触发这个候选的信号现在是否
  还成立"：workthread 类检查对应 `WorkThread` 是否还存在、是否已有新
  进展；lesson 类检查对应 `LessonGroup` 是否还存在、是否仍达到 T1 阈值。
  复核通过 → 摘掉 `needs_review` 标签，goal 保持 `active`；不通过 →
  `status` 改为 `paused`（不是删除，留痕供人工判断），标签换成
  `review_failed`，`progress_notes` 记录原因。

## 7. 顺带修复的既有 bug（与事件总线本身无关，但在接入过程中发现）

在打通四条链路的过程中，`soft_goal_deriver.py`/`goal_backlog.py`/
`lesson_review.py` 里陆续暴露出**六个**独立的既有 bug——都是同一种
失败模式：字段名/方法签名对不上导致异常，被外层宽泛的
`except Exception`/`except ImportError` 静默吞掉，不崩溃但也从未真正
work 过，常规测试很容易漏掉（这几个模块此前大多没有专门的单元测试）。

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `soft_goal_deriver._from_capability_map` | 缺少独立 `def` 头，被误拼接进 `_recently_explored_domains()` 的死代码区 | `derive_candidates()` 调用必然 `AttributeError` |
| 2 | `phase_g.load_capability_map` | 函数根本不存在 | 上面 #1 修复后仍会 `ImportError` |
| 3 | `soft_goal_deriver.commit_goals` | 调用 `goal_backlog.add_goal(description=...)`，但该关键字参数不存在（`GoalNode` 也没有 `description` 字段） | 只要有候选要提交就 `TypeError`，**软目标自动推导从未真正提交成功过一个目标节点** |
| 4 | `soft_goal_deriver._from_work_index` | `thread.thread_id` 不存在（真实字段是 `id`）；`thread.last_activity_at` 也不存在，`getattr` 静默回退成 0.0，使"是否最近有活动"判断永远为 False | 构造 description 时 `AttributeError`；即使修好这处，staleness 判断此前也是失效的 |
| 5 | `soft_goal_deriver._from_lesson_review` | `LessonGroup.meets_t1_threshold`/`meets_t2_t3_threshold` 是 `@property`，被当方法调用（多了一对括号） | 对 bool 值再调用 `()` 必然 `TypeError` |
| 6 | `lesson_review.scan_lesson_groups` | 函数根本不存在（`group_lessons`/`scan_for_proposals` 都要求传入已加载的 entries 列表，不是 `paths`） | 上面 #5 修复后仍会 `ImportError` |

修复方式：

- `phase_g.py` 新增 `load_capability_map(paths)`，复用
  `affordance_analyzer.py`/`self_model.py` 已经在用的
  `build_capability_map(paths, None)` 只读惯用法，不引入第二套统计口径；
  `CapabilityMapEntry` 补 `capability_name`/`total_calls` 两个 property 别名，
  桥接与 `domain`/`success_count`/`failure_count` 命名不一致的问题。
- `goal_backlog.py`：`GoalNode` 新增 `description` 字段（含 `to_dict`/
  `from_dict`，向后兼容——老的 `goal_backlog.json` 没有这个字段时
  静默回退成空字符串）；`add_goal()` 补 `description` 形参。顺带发现
  `api/routes.py` 的 `/goals` 接口也一直在传这个不存在的关键字参数，
  同样受益于这次修复。
- `soft_goal_deriver.py`：拆出独立的 `_from_capability_map()` 方法；
  `_from_work_index()` 改用 `thread.id`，`last_activity_at` 改用
  `thread.started_at` 做近似替代（`WorkThread` 目前没有真正的"最后
  活跃时间"字段，这是一个已知近似，更彻底的修法是给 `WorkThread` 加
  一个真正的 last_activity_at 字段并在推进时更新，超出本次改动范围）；
  三处 `meets_t1_threshold()`/`meets_t2_t3_threshold()` 去掉多余括号。
- `lesson_review.py` 新增 `scan_lesson_groups(paths)`，内部独立构造
  只读 `MemoryStore` 读取全部条目后委托给 `group_lessons()`。
- `tests/test_phase_g.py` 新增 `TestSoftGoalDeriverWorkIndexSignal`、
  `TestSoftGoalDeriverLessonReviewSignal`、
  `TestGoalCandidateUnvalidatedEventFlow` 三个测试类，`tests/test_goal_backlog.py`
  （新文件）专门覆盖 `description` 字段的回归防护。

## 8. 尚未接入、后续可以做的

- **`/diagnostics` 可视化**：目前 `events.jsonl` 只有代码在读，人还看不到
  "最近发生了哪些跨系统事件"。接入点很小——`poll_since(..., advance_cursor=False)`
  读最近 N 条，不影响真实消费者游标。
- **WorkThread 缺少真正的 last_activity_at 字段**：第7节修复 #4 用
  `started_at` 做了近似替代，语义上不完全准确（持续被推进的 thread，
  `started_at` 也不会变）。彻底修法是给 `WorkThread` 加一个真正的
  最后活跃时间字段，在每次推进时更新。

## 9. 测试

`tests/test_system_events.py`（10 用例）：基本往返、游标隔离、tier/event_type
过滤、只读 peek、非法输入拒绝、写入失败降级、滚动归档、并发安全（8 线程
共 160 条事件逐行 JSON 解析验证无交错/无丢失）。

`tests/test_phase_g.py` 新增部分（53 用例总计，其中新增约 25 个）：
`load_capability_map` 与 `build_capability_map` 结果一致性、字段别名桥接、
`_from_capability_map`/`_from_work_index`/`_from_lesson_review` 三个信号
端到端不再抛异常、`goal.candidate_unvalidated` 完整闭环（发布/复核通过/
复核不通过/幂等）。

`tests/test_goal_backlog.py`（新文件，5 用例）：`GoalNode.description`
字段的写入、默认值、序列化往返、向后兼容（老数据没有该字段时不报错）、
落盘重载。

`tests/test_outcome_tracker.py`（5 用例，新文件——此前这个模块完全没有
专门的单元测试）：`record_commit_baseline`/`tick()`/`mark_reverted` 基本
行为回归，重点覆盖 `worsened` 判定触发 `eval_failure` lesson 写入 +
`evolution.outcome_negative` 事件发布、`improved` 判定不误写 lesson。
`tests/test_memory_aging.py` 未改动，但 `TestMemoryAgingEvalFailureSource`
（在 `test_outcome_tracker.py` 里）额外验证了新半衰期条目生效。

顺带修复了 `tests/test_affordance_calibration.py` 两处 `mock.patch.dict
("sys.modules", ...)` 的测试隔离缺陷（对 `from package import submodule`
形式的导入不是导入顺序无关的，一旦 `outcome_tracker` 之前已被真实导入过
就会绕过补丁）——新增 `test_outcome_tracker.py` 在 pytest 收集阶段真实
导入了该模块，暴露出这个预置问题，改为直接 `mock.patch(
"mini_agent.evolution.outcome_tracker.get_all", ...)`，不再依赖导入时序。
