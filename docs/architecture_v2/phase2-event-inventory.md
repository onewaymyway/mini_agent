# Phase 2 现状盘点：事件雏形 → 新 Event type 映射表

> 对应 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`
> "现状盘点（先做，再设计）" 要求的产出。目的：扫描现有代码里已经有
> 多少种"事件"雏形，避免重新发明或凭空定义 15 种 event type。
>
> 本文档只做**盘点 + 映射建议**，不代表这些旧模块已经接入
> `core/event_bus.py`——是否接入、何时接入由后续 Sprint（Sprint 2-2
> 之后）单独决定，接入完成后需在 `MIGRATION_STATUS.md` 里补一行。

## 一、扫描范围与方法

按 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md` 现状
盘点小节列出的四个模块，逐一阅读源码定位其"事件/状态变化通知"的
现有实现方式（不使用 `scripts/dep_graph.py`，因为这里要盘点的是
**数据结构语义**，不是 import 耦合关系）。

## 二、盘点结果

### 1. `history_manager.py`：历史条目结构

- 现状：`HistoryManager` 没有独立的"事件"数据类，而是一组
  `append_user` / `append_assistant` / `append_tool_results` /
  `append_skill_context` / `append_session_resume` / `append_reminder` /
  `append_format_correction` / `append_role_agent` / `append_raw_dict`
  方法，直接把内容以 `dict` 形式追加进内部消息列表，方法名本身承担了
  "事件类型"的角色，没有统一结构体，也没有 timestamp/id 之类的元数据
  字段。
- 对应新 Event type 建议：
  - `append_user` / `append_assistant` / `append_role_agent` →
    `MessageAppended`（新增，payload 里带 `role`）
  - `append_tool_results` → 可复用 Phase 1 已定义的
    `ActionCompleted`（payload 里带工具调用结果），不需要新类型
  - `append_reminder` / `append_format_correction` / `append_session_resume`
    → `HistoryAnnotationAdded`（新增，一种"非对话正文的旁注"类型，
    三者共用同一 type，用 payload 里的 `annotation_kind` 字段区分）
  - `append_skill_context` / `append_raw_dict` → 暂不建新 type，属于
    底层拼装细节，不是领域事件，保持现状即可
- 接入建议：**暂不接入**。`history_manager.py` 已经通过 Phase 1/1.5 的
  `HistoryAdapter`（`core/history_adapter.py`）做了单向快照转换，
  Event 化应该等这条 Adapter 链路进一步发展（比如需要 `to_old` 或者
  下游需要订阅历史变化）时再做，现在接入只是"为了接入而接入"。

### 2. `perception/behavior/events.py`：行为感知事件

- 现状：**这是四类雏形里唯一已经"自称"是统一事件模型的模块**——
  `ActivityEvent`（`@dataclass`）已经有 `timestamp` / `source` /
  `event_type` / `app_name` / `window_title` / `domain` / `url_path` /
  `duration_sec` / `meta` 字段，`BehaviorEventStore` 负责按天分文件的
  JSONL 落盘存储；文档注释里明确写了"所有采集器...只需要认识这一种
  结构"，设计目标与 Phase 2 的 `Event` 高度相似，但字段命名和落地位置
  是独立的（不依赖 `core/events.py`）。
- 对应新 Event type 建议：`ActivityEvent.event_type` 取值（如
  `app_focus` / `idle_start` / `idle_end` / `page_visit` / `tab_switch` /
  `clipboard_copy`）对应到 Phase 2 尚未定义的一大类
  `PerceptionActivityObserved`（新增，`kind` 用
  `perception.activity.<event_type>` 命名空间区分，而不是每种采集器
  行为都单独开一个顶层 Event type，避免把 15+ 种采集器细分类型
  一次性搬进 `EVENT_KINDS`）。
- 接入建议：**暂不接入，优先级低于 history_manager**。`ActivityEvent`
  已经有一套独立可用的存储（`BehaviorEventStore`），行为高频（用户
  操作粒度），如果直接接入 `core/event_bus.py` 的进程内总线，需要先
  评估订阅者数量级和总线性能，不适合在 Sprint 2-2 顺手做掉，留给
  Phase 2 完成后单独评估。

### 3. `evolution/`：状态变更通知（以 `candidate_queue_triage.py` 为代表）

- 现状：`evolution/` 下没有统一的 `EvolutionProposed` /
  `EvolutionApplied` 类（原计划文档里的举例命名在当前代码库里找不到
  同名概念），实际是分散在各文件里的 `status` 字符串字段，例如
  `candidate_queue_triage.py` 里 `d["status"]` 在 `"pending"` /
  `"expired"` 之间流转（本次盘点用 `grep` 确认，未发现
  `"approved"`/`"applied"`/`"rejected"` 等取值在当前代码库出现）。
- 对应新 Event type 建议：`status` 字段的每一次赋值点对应一个
  `EvolutionCandidateStatusChanged`（新增，payload 带 `from_status` /
  `to_status` / `candidate_id`），不建议按原计划文档里假设的
  `EvolutionProposed`/`EvolutionApplied` 两个固定名字来定义，因为
  实际状态机（`pending → expired`，且只在这一个文件里发现明确流转）
  比原方案设想的更简单，先按"状态变更"通用建模，等发现更多
  `evolution/` 子模块也有类似模式后再考虑拆细。
- 接入建议：**暂不接入**。`evolution/` 有 67 个模块（见
  `MIGRATION_STATUS.md` 占位行），本次盘点只覆盖了
  `candidate_queue_triage.py` 一个文件作为代表样本，不足以支撑现在就
  设计接入点；真正接入前需要先按 `12-execution-and-doc-sync-norms.md`
  第六节的方法论对 `evolution/` 做一次子模块级别的耦合扫描（类似
  Sprint 1.5 对 `perception/` 做的事情），属于 Phase 9（Self
  Evolution 接入统一 Experience）的前置工作，不在 Phase 2 范围内。

### 4. `orchestrator/`：SubAgent 调用的开始/结束通知

- 现状：`orchestrator/plan.py::PlanTask` 是一个 `@dataclass`，带
  `status`（`PlanTaskStatus` 枚举：`PENDING/RUNNING/DONE/FAILED/SKIPPED`）
  和 `started_at` / `finished_at` 两个时间戳字段；`ExecutionPlan` 类的
  `start_task()` / `complete_task()` / `fail_task()` 等方法负责状态
  流转并回填时间戳，是四类雏形里字段语义与 Phase 1 `ActionStarted` /
  `ActionCompleted` / `ActionFailed` **最接近**的一个（同样是
  "开始 → 完成/失败" 两段式，且已有明确的时间戳字段可以直接映射到
  `Event.timestamp`）。`orchestrator/sub_agent.py` 里另有一个
  `self.record` 对象（`started_at`/`finished_at` 字段同名但定义在
  别处），暂未定位到其类定义文件，视为同一模式的另一份实例，不单独
  另开映射行。
- 对应新 Event type 建议：**直接复用 Phase 1 已定义的
  `ActionStarted` / `ActionCompleted` / `ActionFailed`**，不需要新增
  `SubAgentStarted`/`SubAgentFinished`。`PlanTaskStatus.SKIPPED` 暂无
  对应 type，建议新增 `ActionSkipped`（比 `ActionFailed` 语义更准确，
  跳过不等于失败）。
- 接入建议：**四类里优先级最高，建议作为 Sprint 2-2 之后的下一条
  接入候选**，原因：(a) 字段语义已经和 Phase 1 的 Event type 高度
  对齐，改造成本低；(b) `ExecutionPlan` 的状态流转方法数量少
  （`start_task`/`complete_task`/`fail_task` 等，集中在
  `orchestrator/plan.py` 一个文件），接入点容易收敛到 1-2 处，
  符合 Phase 1/1.5 一路验证下来的"唯一接入点"模式；(c) 与 Goal 链路
  同属"任务执行"领域，Event 打通后能验证跨模块 `correlation_id`
  串联（Goal 触发 SubAgent 执行的场景）比只在 `goal_mode/runner.py`
  内部验证更有说服力。

## 三、结论与后续行动

| 现有事件雏形 | 建议新 Event type | 是否本次接入 | 优先级（作为后续接入候选） |
|---|---|---|---|
| `history_manager.py` 的 `append_*` 方法 | `MessageAppended` / 复用 `ActionCompleted` / `HistoryAnnotationAdded` | 否 | 低（已有 Adapter 快照链路，Event 化非当务之急） |
| `perception/behavior/events.py::ActivityEvent` | `PerceptionActivityObserved`（`kind` 用命名空间区分子类型） | 否 | 低（已有独立存储，需先评估总线承载高频事件的性能） |
| `evolution/*` 的 `status` 字段流转 | `EvolutionCandidateStatusChanged` | 否 | 待定（需先做子模块级耦合扫描，属于 Phase 9 前置工作） |
| `orchestrator/plan.py::PlanTask` 状态流转 | 复用 `ActionStarted`/`ActionCompleted`/`ActionFailed`，新增 `ActionSkipped` | 否 | **高**（建议作为 Sprint 2-2 完成后的下一个接入候选） |

- 本文档完成了 `03-phase2-event-model-sprint-plan.md` 完成标志清单里
  "事件雏形盘点表"一项，**不代表以上四个模块已经接入
  `core/event_bus.py`**——是否接入、何时接入是独立的后续任务，接入
  时需要在对应任务里重新走一遍
  `12-execution-and-doc-sync-norms.md` 的文档同步规范（更新本文档的
  "是否本次接入"列 + `MIGRATION_STATUS.md` 新增行）。
- 未按原计划文档字面假设的 `EvolutionProposed`/`EvolutionApplied` 固定
  两个类型名去定义，因为实际代码库里没有找到对应概念；本次改为
  按扫描到的真实状态机（`status` 字段流转）重新命名，
  是对原计划"现状盘点"环节的正常执行结果，不算计划变更，无需走
  `12-execution-and-doc-sync-norms.md` 第四节的变更记录流程（该流程
  针对的是"验收标准/任务表"层面的调整，本次只是盘点产出的自然结论）。
