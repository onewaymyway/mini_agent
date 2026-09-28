"""mini_agent.core — 架构收敛后的最小领域模型。

见 `next_doc/refactor_plan/02-executable-sprint-plan.md`：
Sprint 1 只放 Goal 迁移链当前真正用得上的 4 个文件
（`types.py` / `events.py` / `goal.py` / `experience.py`）+
`adapter.py`（Adapter 协议）+ `goal_adapter.py`（GoalAdapter 实现）；
Sprint 2 新增 `experience_store.py`（Experience 的最小 JSONL 持久化 +
检索），对应原方案 §52 列的 11 个文件里，本包仍只建当前迁移链真正
用得上的部分，不提前补全。

Phase 2 Sprint 2-1（见
`next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`）新增
`event_bus.py`（进程内最小发布/订阅总线），并扩展了 `events.py` 里
`Event` 的字段集（详见该文件顶部说明）。Sprint 2-2 新增
`event_log_store.py`（Event 的 JSONL 落盘 + 按 correlation_id 检索，
供 `mini-agent events trace` CLI 命令使用）。

Phase 3 Sprint 3-1（见
`next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`）
扩展了 `experience.py` 里 `Experience` 的字段集（补齐原方案 §7 yaml
结构），把 `experience_store.py` 从 JSONL 升级为 SQLite，并新增
`experience_recorder.py`（`ExperienceRecorder` 订阅 `ExperienceCreated`
事件落库，替代此前 `goal_mode/runner.py` 手写的落盘调用）。

Sprint 3-2 新增 `experience_retrieval.py`（按关键词重叠度检索"同类
历史 Experience"，`goal_mode/runner.py` 在 Goal 启动时把检索结果注入
LLM 上下文）与 `experience_patterns.py`（Analyzer 雏形：对同类失败
Experience 做聚合统计，为 Phase 9 Self Evolution 打基础）。

Phase 4 Sprint 4-1（见
`next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`）新增
`state_manager.py`（`StateManager` 骨架：`get_state`/`update_state`
两个核心方法 + 订阅事件总线上的 `GoalUpdated` 自动更新内部持有的
`GoalState`），`goal_mode/runner.py` 唯一接入点改为把 `GoalState` 的
持有权交给 `StateManager`，不再自己另外保留一份可变引用。

Sprint 4-2 新增 `world.py`/`capability.py`/`runtime.py` 三个占位
dataclass（`WorldState`/`CapabilityState`/`RuntimeState`，均无字段，
标注 `# TODO: Phase 5/6/8 填充`）；`SelfState`（Sprint 1.5 已建，不是
占位）补齐了向 `StateManager` 的托管接入——`agent/lifecycle.py`
唯一接入点新增 `update_state("self", ...)` 调用，是本 Sprint 内唯一
托管了真实数据的 State kind。

Phase 5 Sprint 5-2（见
`next_doc/refactor_plan/06-phase5-goal-convergence-sprint-plan.md`）
给 `goal.py::GoalState` 追加 `current_state`/`ideal_state`/
`problems`/`gap`/`constraints`/`resources`/`priority`/`evidence`/
`deadline` 九个字段，供新增的 `mini_agent.goals.gap.detect_gap()`
读写（`goals/` 是与 `core/` 平级的新包，不在本包内，这里只提一句
避免以为 `GoalState` 这几个新字段没有调用方）。

Action / Simulation 的最小 dataclass，待对应 Phase（Phase 6 / Phase 7）
的迁移链启动时再补，不提前占位。
"""

from .adapter import Adapter
from .capability import CapabilityState
from .capability_projector import build_capability_state, refresh_capability_state
from .event_bus import EventBus, get_event_bus, reset_event_bus
from .event_log_store import (
    EventLogStore,
    ensure_event_log_subscribed,
    reset_event_log_subscriptions,
)
from .events import EVENT_KINDS, Event
from .experience import Experience
from .experience_recorder import (
    ExperienceRecorder,
    ensure_experience_recorder_subscribed,
    reset_experience_recorder_subscriptions,
)
from .experience_store import ExperienceStore
from .experience_retrieval import retrieve_similar_experiences, render_experiences_as_context
from .experience_patterns import FailurePatternSummary, summarize_failures
from .goal import GoalState
from .goal_adapter import GoalAdapter, goal_run_result_to_experience
from .history import HistorySnapshot
from .history_adapter import HistoryAdapter
from .lesson_adapter import LessonAdapter
from .lesson_import import LessonImportResult, import_lessons
from .objective_adapter import (
    ObjectiveAdapter,
    project_objective_executions,
    reset_objective_projection_cache,
)
from .runtime import RuntimeState
from .self import SelfState
from .self_adapter import SelfAdapter
from .state_manager import (
    StateManager,
    ensure_state_manager_subscribed,
    get_state_manager,
    reset_state_manager,
)
from .world import WorldState

__all__ = [
    "Adapter",
    "CapabilityState",
    "build_capability_state",
    "refresh_capability_state",
    "RuntimeState",
    "WorldState",
    "EVENT_KINDS",
    "Event",
    "EventBus",
    "get_event_bus",
    "reset_event_bus",
    "EventLogStore",
    "ensure_event_log_subscribed",
    "reset_event_log_subscriptions",
    "Experience",
    "ExperienceRecorder",
    "ensure_experience_recorder_subscribed",
    "reset_experience_recorder_subscriptions",
    "ExperienceStore",
    "retrieve_similar_experiences",
    "render_experiences_as_context",
    "FailurePatternSummary",
    "summarize_failures",
    "GoalState",
    "GoalAdapter",
    "goal_run_result_to_experience",
    "HistorySnapshot",
    "HistoryAdapter",
    "LessonAdapter",
    "ObjectiveAdapter",
    "project_objective_executions",
    "reset_objective_projection_cache",
    "LessonImportResult",
    "import_lessons",
    "SelfState",
    "SelfAdapter",
    "StateManager",
    "ensure_state_manager_subscribed",
    "get_state_manager",
    "reset_state_manager",
]
