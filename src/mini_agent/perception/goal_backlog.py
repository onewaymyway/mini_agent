"""
perception/goal_backlog.py — Stage 9 跨会话目标层级（Phase H，第六节）

维护 <project_root>/.agent/goals.json，存储两层目标：
  - Goal（目标）：用户或 agent derive 的长期意图
  - Objective（子目标）：可在若干 Task 内完成的具体目标，引用 WorkThread

与 Stage 4.3 work_index.json 的关系：
  Objective 通过 work_thread_ref 字段引用已有 WorkThread，
  复用其 cumulative_progress/next_suggested，不重复维护进展文本。

存储设计：
  - 纯运行时状态，不经过 StateRepo（与 work_index.json 定位一致）
  - 原子写（tmp + os.replace）
  - 跨进程并发：goals.json 是全项目共享的单文件，可能被多个进程（多个
    CLI session / daemon / HTTP API）同时读写。所有会修改内容的方法
    （add_goal / add_objective / set_status / update_progress）都通过
    ``_locked()`` 临界区执行：进入时加进程间独占文件锁并从磁盘重新加载
    最新状态，退出时落盘并释放锁。这样可以避免"A、B 两个进程各自基于
    旧快照修改后写回，后写的把先写的整个覆盖掉"的丢失更新问题。

档位边界（stage9_plan.md 第七节）：
  - passive 档位：AutonomousLoop.tick() 不读取 GoalBacklog 任何方法
  - maintenance 档位：读 has_actionable_work() 和 next_task()，但不 derive 新 Goal
  - autonomous 档位：可 derive 新 Goal/Objective（第十二节，暂不实现）
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from mini_agent import time_utils
from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler

try:
    import fcntl  # POSIX only（Linux / macOS）
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows 等无 fcntl 平台
    fcntl = None  # type: ignore
    _HAS_FCNTL = False


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class GoalNode:
    """
    统一的目标节点（Goal 或 Objective，用 level 字段区分）。
    与 WorkThread 现有\"一个 dataclass + 枚举字段区分类型\"风格一致。
    """
    id: str
    level: str                      # "goal" | "objective"
    title: str
    source: str                     # "user" | "agent_derived"
    # "active" | "paused" | "completed" | "abandoned" | "failed" | "cancelled"
    # [看板与自主性改进方案 Track B] 新增 "failed"/"cancelled" 两个取值：
    # - "failed"：由 ObjectiveExecutor 在其对应 execution 判定失败后单向回写，
    #   代表"事实上执行失败了"，区别于用户主动放弃的 "abandoned"。
    # - "cancelled"：用户在看板上主动终止一个仍在运行的 Objective 时使用
    #   （见 objective_executor.cancel()），区别于"从未开始就放弃"的 "abandoned"。
    # 两个新值不影响任何既有读取方——所有既有代码都是把 status 当不透明字符串
    # 比较/展示，没有做枚举校验，看板侧的展示映射见 apps/mini_agent_kanban/app.py
    # 的 GOAL_STATUS_COLUMNS。
    status: str
    created_at: float = 0.0
    last_touched_at: float = 0.0
    progress_notes: str = ""
    parent_id: Optional[str] = None
    children_ids: list[str] = field(default_factory=list)
    # 仅 Objective 使用：关联 WorkThread id
    work_thread_ref: Optional[str] = None
    # 优先级权重（数字越大越优先）
    priority: int = 0
    # 标签（用于分类）
    tags: list[str] = field(default_factory=list)
    # [修复] 目标的静态描述（"为什么要做这件事"），与 progress_notes
    # （"做到哪一步了"的动态追踪记录）语义不同，此前没有独立字段，
    # soft_goal_deriver.commit_goals() 一直在调用 add_goal(description=...)
    # 但该关键字参数根本不存在——每次有候选写入时 TypeError，被外层
    # except Exception 静默吞掉，"软目标自动推导"从未真正提交成功过一个
    # 目标节点。见 docs/system-events-bus-guide.md 第7节。
    description: str = ""

    # [goal_user_output_dir_plan.md] 用户在创建/编辑 Goal 时明确指定的产出
    # 目录（相对 project_root 的路径，如 "research/stock_analyse"）。一旦设置，
    # output_workspace.goal_output_dir() 会把这个 Goal 的正式产出目录（四目录
    # 模型里的 output/）解析到这里，而不是默认的
    # .agent/daemon_run_outputs/goals/<goal_id>/output/——notes/spec/scratch
    # 三个内部目录不受影响，仍然放在默认位置下（那三个是"给 agent 自己用的
    # 过程数据"，不是用户想找的交付物，改产出目录的诉求只针对"产出物放
    # 哪"，跟内部记账目录无关）。None 表示未设置，沿用默认路径。
    user_output_dir: Optional[str] = None

    # [goal_user_output_dir_plan.md] 从 description 里粗略检测到的"疑似用户
    # 指定产出路径"候选值（detect_user_specified_output_hint() 的结果，取
    # 第一个匹配），只是**建议**，不会自动生效——纯规则匹配可能不准，需要
    # 用户在看板上确认或改写后才会真正写入 user_output_dir。用户已手动设置过
    # user_output_dir 后不再更新这个建议字段（避免覆盖用户已确认的选择）。
    #
    # 三种取值的语义（用于区分"没检测过"和"检测过但没结果"）：
    #   None  —— 从未跑过检测。只会出现在这个字段引入之前就已经存在的
    #            历史 Goal 上（旧版本 goals.json 没有这个键）——
    #            `GoalBacklog.load()` 加载时会自动为这类 Goal 补跑一次
    #            检测（见该方法注释），跑完之后这个字段就不会再是 None。
    #   ""    —— 检测跑过了，但 description 里没有匹配到任何路径提示。
    #   非空字符串 —— 检测到的候选路径片段。
    user_output_dir_suggested: Optional[str] = None

    # [watchlist_notification_goal_design.md §3.5，P5 新增]
    # GoalRelevanceEngine Stage② 判定 relevant=true 时追加的外部信息摘要，
    # 只保留最近 max_keep 条（见 attach_external_context()）。跟
    # progress_notes（"做到哪一步了"）语义不同，这里纯粹是"外部世界发生的
    # 跟这个 Goal 相关的事"，只在处理这个 Goal 自己的任务时被读取（§4.5），
    # 不做全局注入。每项: {"event_id","title","snippet","occurred_at","source_id"}。
    external_context: list = field(default_factory=list)

    # [watchlist_notification_goal_design.md §3.5，P5 新增]
    # 上一次因外部信号被"主动拉起"(advance_goal) 的时间戳，用于 §4.4 的
    # 冷却限流判断。跟 progress_notes 不是一回事。
    last_external_advance_at: float = 0.0

    # [goal_execution_fairness_improvement_plan.md P2 新增]
    # 上一次这个 Goal（或其 Objective）被调度器实际分配到执行槽位
    # （ObjectiveExecutor.start() 成功）的时间戳。与 last_touched_at
    # （"内容/进度有更新"）语义不同——纯粹用于 active_objectives_fair_ranked()
    # 的排序，不代表这个 Goal 有任何实质进展。只由 mark_scheduled() 写入，
    # 不走 update_fields()（避免顺带把 last_touched_at 也刷新，破坏 P3
    # 老化加成"只随实质进展归零"的语义）。
    last_scheduled_at: float = 0.0

    # [goal_cron_binding_plan.md Track A] 周期性 Goal 支持：
    # recurring — 是否已绑定一个 run_mode="goal_cycle" 的 CronJob；
    # recurrence_cron_job_id — 反向指针，指回绑定的 CronJob.id，方便 UI/CLI 跳转，
    #   也用于 stop_goal_recurrence() 定位要 disable 哪个 job；
    # cycle_count — 已完成（含失败）的周期数，由 goal_cron_bridge.reap_finished_cycles()
    #   在检测到本轮子 Objective 进入终态时递增，不代表"成功"次数，只代表"跑过几轮"。
    recurring: bool = False
    recurrence_cron_job_id: Optional[str] = None
    cycle_count: int = 0
    # 已被 reap_finished_cycles() 计过数的子 Objective id 集合，避免同一个终态子节点
    # 被重复计数（tick 间隔内多次扫描到同一个 completed 子节点是正常情况）。
    reaped_cycle_child_ids: list[str] = field(default_factory=list)

    # [goal_cron_visibility_and_intervention_improvement_plan.md Track B]
    # 用户请求"跳过下一次触发"，但保持 recurring=True 不变——区别于
    # unrecur（彻底停止周期性）。由 goal_cron_bridge._fire_goal_cycle()
    # 消费：命中后清零本字段、写一条 progress_notes、本次触发按"未算数"
    # 处理（下次 tick 正常判断）。
    skip_next_cycle: bool = False

    # [goal_cron_task_optimization_holistic_plan.md 方向 C] 用户请求"下一轮
    # 降级执行"——与 skip_next_cycle（完全不跑）不同，这一轮仍然触发，但
    # goal_cron_bridge 会在 execution phase 提示片段之外额外拼接一段"从简
    # 执行"引导（不新增探索/变更，只做最小同步），执行完当轮后自动清零，
    # 不影响 recurring 本身、也不改变 ExecutionPhaseState.mode。
    next_cycle_lightweight: bool = False

    # [personal_researcher_and_coach_capability_gap_plan.md C1] 可选的"长期
    # 方向"分组标签，指向 GoalBacklog._directions 里的一个 Direction.id。
    # 与 parent_id（"Objective 属于哪个 Goal"）语义不同——这里表达的是
    # "多个独立的 Goal 共同服务于同一个更高层、没有验收标准的长期方向"，
    # 不参与 GoalJudge 判定、不影响执行调度、不改变 level 的两值约束，
    # 纯粹用于看板展示聚合。None 表示未分组。
    direction_id: Optional[str] = None

    # [goal_output_directory_and_execution_phase_redesign_plan.md Stage 9]
    # 用户请求"下一次触发时附加一次历史数据迁移任务"——把旧模型
    # （每轮一个 cycle_NNNN/ 目录）下遗留的历史产出，搬迁进新的固定四目录
    # 模型（output/notes/spec/scratch）。与 skip_next_cycle/
    # next_cycle_lightweight 同样的"一次性标记，消费后自动清零"模式，由
    # goal_cron_bridge._fire_goal_cycle() 消费：命中后清零本字段、在本轮
    # description 里追加一段迁移指令（见
    # evolution/output_workspace.py::build_legacy_migration_directive()），
    # 不改变 ExecutionPhaseState.mode、不影响本轮阶段判定本身，纯粹是叠加
    # 的一次性任务。
    legacy_migration_requested: bool = False

    # [goal_cron_feedback_and_output_policy_plan.md Track A] 用户对本节点持续
    # 生效的意见反馈历史。每条：{"text": str, "at": float}。这里只做追加记录，
    # 供 UI/CLI 回看；真正影响后续执行的是同步追加进 description 的那部分
    # （见 GoalBacklog.add_user_feedback()），与 inject_guidance()「只影响下一次
    # 提交的单个 step」的一次性语义不同，本字段代表「以后永久都要考虑」。
    user_feedback: list[dict] = field(default_factory=list)

    # [goal-provenance-guide.md] 这个 Goal 是在哪一轮对话/触发链路里被
    # 创建的——跟 `source`（"谁负责决定要建这个 Goal"：user/agent_derived/
    # novelty_candidate）是两个正交维度，`source_initiator` 记录的是
    # "创建它的那次 add_goal() 调用，发生在哪个 InputQueue initiator
    # 触发的轮次里"：
    #   "user"        — 用户在 CLI/看板/API 手动创建，或没有任何轮次上下文
    #                    （thread-local 未设置）时的默认兜底
    #   "cron"        — 在一次由 CronScheduler 提交的轮次（比如
    #                    goal_relevance_judge 判定 advance_worthy 后提交的
    #                    那轮对话）里，Agent 自己决定创建的
    #   "external"    — 在一次由 external_input 网关 enqueue_turn 落点
    #                    直接提交的轮次里创建的
    #   "autonomous_loop" — 由 SoftGoalDeriver.commit_goals() 在
    #                    autonomous 档位 tick 内部直接调用创建（不经过
    #                    InputQueue，没有"轮次"这个概念，显式标记）
    # 见 GoalBacklog.add_goal() 的解析逻辑与 perception/turn_context.py。
    source_initiator: str = "user"

    # [next_doc/growth_advisor_cron_search_and_status_history_plan.md
    # 方向三] `status` 此前只是一个不透明字符串，只能查到"当前状态"，
    # 查不到"怎么走到这个状态的"——一个 Goal 完成后又被重新打开
    # （比如周期性 Goal 的下一轮，或用户手动改回 active）在既有数据
    # 结构里完全看不出来。这里补一条极简的状态变更历史，只有
    # `set_status()` 会追加，其它字段变更（`update_progress()`/
    # `update_fields()` 等）不触碰本字段。每项：
    # `{"status": str, "at": float}`，只追加不修改/删除，旧数据反序列化
    # 时缺该字段按 `[]` 处理（等价于"这个节点还没经历过一次显式状态
    # 变更"，不需要额外迁移）。
    status_history: list = field(default_factory=list)

    # [next_doc/goal_execution_spec_generation_plan.md §4] 轻量指针字段：
    # 这个 Goal 是否存在一份已确认（confirmed=true）的执行规范
    # （.agent/goal_execution_specs/<goal_id>.json）。真正的规范内容以独立
    # 文件为准，本字段只是缓存/索引，供 goal_cron_bridge/看板快速判断
    # 是否需要读取独立文件，不是真值来源——独立文件与本字段不一致时（比如
    # 文件被手工删除），以独立文件的实际存在状态为准。
    execution_spec_confirmed: bool = False

    # [goal_execution_spec_generation_plan.md §5 第二段 / implementation_
    # record.md §11 后续建议顺序第 1 条"整体关闭判定结果持久化展示"]
    # 最近一次 `GoalBacklog.maybe_close_goal_by_overall_criteria()` 实际
    # 执行判定（前置条件全部满足、真正调用了 LLM/受限 Agent）后的结果快照：
    # `{"outcome": "closed"|"kept_open", "reasoning": str, "used_agent":
    # bool, "at": float}`。只在真正触发判定时写入（前置条件不满足、返回
    # `None` 的情况不写），供看板/CLI 展示"上一次整体关闭判定是什么时候、
    # 判了什么、是否挂了 Agent"，不需要用户翻 progress_notes 里的文本行去
    # 找。旧数据反序列化缺省为 `None`，代表"从未触发过判定"。
    overall_completion_last_check: Optional[dict] = None

    # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 6] 成长
    # 顾问自动持续推进的 Goal 落地时（`auto_pursue_candidate()`）判定出的
    # 调研/呈现风格：`"技能实操类"` / `"知识理论类"` / `"习惯养成类"`。只
    # 影响 `growth_pursuit` 模板每一轮 prompt 里追加的一段风格提示（见
    # `growth_advisor.pursuit_style_hint()`），不影响任何排序/执行判定。
    # `None` 表示未分类（非 growth_advisor 相关 Goal 的常态，或方向 6
    # 落地前创建的旧 Goal），下游按"不追加风格提示"处理，不需要迁移。
    growth_pursuit_style: Optional[str] = None

    # [next_doc/goal_tree_system_plan.md §4.1 阶段一] `level` 从"goal"/
    # "objective" 两值开放为字符串，新增 "ultimate"/"domain"/"stage" 三个
    # 纯结构层级（不进入完成态、不接入 GoalJudge/GoalRunner/cron 调度，见
    # 该文档§三"执行语义只在树的下半段生效"）。以下三个字段仅这三层非叶子
    # 节点使用：

    # 当前应关注的直接子节点 id 列表，由 compute_current_focus()（规则计算，
    # 见 §4.3）定期刷新，用户可通过 focus_pinned_ids 覆盖部分结果。空列表
    # 表示"没有子节点，或子节点已全部进入终态"——意味着该节点该被停滞巡检
    # 捕获，触发一次新的分解建议。
    current_focus_ids: list[str] = field(default_factory=list)
    # 用户手动 pin 的子节点 id，持续生效直到用户显式 unpin（不是一次性
    # 标记）；计算 current_focus_ids 时优先并入这些 id，再从其余子节点里
    # 按规则补足到 top-N。
    focus_pinned_ids: list[str] = field(default_factory=list)
    # GoalTreeDecomposer（阶段二实现）针对本节点生成、尚未被用户处理的分解
    # 候选。每项：{"id", "title", "description", "level", "generated_at",
    # "reason"}；这里的 "id" 是候选自己的临时 id，不是真实 GoalNode.id，
    # accept 后才会用它创建真正的节点并从本列表移除。
    decompose_candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "last_touched_at": self.last_touched_at,
            "progress_notes": self.progress_notes,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "work_thread_ref": self.work_thread_ref,
            "priority": self.priority,
            "tags": self.tags,
            "description": self.description,
            "external_context": self.external_context,
            "last_external_advance_at": self.last_external_advance_at,
            "last_scheduled_at": self.last_scheduled_at,
            "recurring": self.recurring,
            "recurrence_cron_job_id": self.recurrence_cron_job_id,
            "cycle_count": self.cycle_count,
            "reaped_cycle_child_ids": self.reaped_cycle_child_ids,
            "skip_next_cycle": self.skip_next_cycle,
            "next_cycle_lightweight": self.next_cycle_lightweight,
            "legacy_migration_requested": self.legacy_migration_requested,
            "user_feedback": self.user_feedback,
            "source_initiator": self.source_initiator,
            "status_history": self.status_history,
            "execution_spec_confirmed": self.execution_spec_confirmed,
            "overall_completion_last_check": self.overall_completion_last_check,
            "growth_pursuit_style": self.growth_pursuit_style,
            "user_output_dir": self.user_output_dir,
            "user_output_dir_suggested": self.user_output_dir_suggested,
            "direction_id": self.direction_id,
            "current_focus_ids": self.current_focus_ids,
            "focus_pinned_ids": self.focus_pinned_ids,
            "decompose_candidates": self.decompose_candidates,
        }

    @staticmethod
    def from_dict(d: dict) -> "GoalNode":
        return GoalNode(
            id=d.get("id", ""),
            level=d.get("level", "goal"),
            title=d.get("title", ""),
            source=d.get("source", "user"),
            status=d.get("status", "active"),
            created_at=d.get("created_at", 0.0),
            last_touched_at=d.get("last_touched_at", 0.0),
            progress_notes=d.get("progress_notes", ""),
            parent_id=d.get("parent_id"),
            children_ids=d.get("children_ids", []),
            work_thread_ref=d.get("work_thread_ref"),
            priority=d.get("priority", 0),
            tags=d.get("tags", []),
            description=d.get("description", ""),
            external_context=d.get("external_context", []),
            last_external_advance_at=d.get("last_external_advance_at", 0.0),
            last_scheduled_at=d.get("last_scheduled_at", 0.0),
            recurring=d.get("recurring", False),
            recurrence_cron_job_id=d.get("recurrence_cron_job_id"),
            cycle_count=d.get("cycle_count", 0),
            reaped_cycle_child_ids=d.get("reaped_cycle_child_ids", []),
            skip_next_cycle=d.get("skip_next_cycle", False),
            next_cycle_lightweight=d.get("next_cycle_lightweight", False),
            legacy_migration_requested=d.get("legacy_migration_requested", False),
            user_feedback=d.get("user_feedback", []),
            # 历史数据（本字段新增前写入的 goals.json）没有这个键，兜底
            # "user"——对旧数据保守估计为用户创建，不会把历史 Goal 误标成
            # 某种自动触发来源。
            source_initiator=d.get("source_initiator", "user"),
            status_history=d.get("status_history", []),
            execution_spec_confirmed=d.get("execution_spec_confirmed", False),
            overall_completion_last_check=d.get("overall_completion_last_check"),
            growth_pursuit_style=d.get("growth_pursuit_style"),
            user_output_dir=d.get("user_output_dir"),
            user_output_dir_suggested=d.get("user_output_dir_suggested"),
            direction_id=d.get("direction_id"),
            current_focus_ids=d.get("current_focus_ids", []),
            focus_pinned_ids=d.get("focus_pinned_ids", []),
            decompose_candidates=d.get("decompose_candidates", []),
        )

    @property
    def is_goal(self) -> bool:
        return self.level == "goal"

    @property
    def is_objective(self) -> bool:
        return self.level == "objective"

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_structural(self) -> bool:
        """[goal_tree_system_plan.md §三] "ultimate"/"domain"/"stage" 三层是
        纯结构+说明+聚合展示，不接入 GoalJudge、不会进入 completed 终态
        判定、不会被 GoalRunner/cron 调度执行。"""
        return self.level in _STRUCTURAL_LEVELS


# [next_doc/goal_tree_system_plan.md §4.1] 允许的父子层级顺序（可跳级，
# 校验规则见 validate_node_hierarchy()）。"goal"/"objective" 是原有的执行
# 层，"ultimate"/"domain"/"stage" 是新增的三层纯结构节点。
LEVEL_ORDER: tuple = ("ultimate", "domain", "stage", "goal", "objective")

# 不接入 GoalJudge/GoalRunner/cron 调度、不进入 completed 终态判定的层级
# （见方案文档§三"执行语义只在树的下半段生效"）。
_STRUCTURAL_LEVELS = frozenset({"ultimate", "domain", "stage"})

# 每个 level 允许挂靠的父节点 level 集合。None 代表"只能是根"。
# - ultimate：全局唯一根节点，parent 必须为 None。
# - domain：父节点只能是 ultimate（顺序表里紧邻的上一层，是"排在自己
#   前面的层级"里唯一的一个）。
# - stage：父节点可以是 ultimate 或 domain（允许跳过 domain 直接挂根）。
# - goal：父节点可以是 domain/stage/goal（goal 挂 goal 下，即"大目标拆
#   小目标，子目标本身仍要走 GoalJudge 判定"，现状本就没有禁止，保留）。
# - objective：父节点只能是 goal（与现状一致，不变）。
_ALLOWED_PARENT_LEVELS: dict = {
    "ultimate": frozenset(),  # 仅允许 parent_id is None，见 validate_node_hierarchy
    "domain": frozenset({"ultimate"}),
    "stage": frozenset({"ultimate", "domain"}),
    "goal": frozenset({"domain", "stage", "goal"}),
    "objective": frozenset({"goal"}),
}


def validate_node_hierarchy(level: str, parent_level: Optional[str]) -> Optional[str]:
    """[goal_tree_system_plan.md §4.1.1] 校验"把一个 level=level 的节点挂在
    level=parent_level 的节点下"是否合法。

    不强制"每层必须存在"（允许跳级，比如 domain 直接挂 goal），但父子之间
    的 level 顺序不能倒挂（比如 goal 不能挂在 objective 下面）。

    返回 None 表示合法；返回非空字符串表示不合法，内容可直接展示给调用方。
    这里只做纯规则判断，不接触任何 GoalBacklog 状态（全局唯一性等需要看
    "是否已存在其它节点"的校验由调用方 GoalBacklog.add_node() 补充）。
    """
    if level not in _ALLOWED_PARENT_LEVELS:
        return f"未知的 level={level!r}，合法取值为 {LEVEL_ORDER}"
    if level == "ultimate":
        if parent_level is not None:
            return "ultimate 是全局唯一根节点，parent_id 必须为空"
        return None
    allowed = _ALLOWED_PARENT_LEVELS[level]
    if parent_level not in allowed:
        allowed_desc = "/".join(sorted(allowed)) if allowed else "(无，只能是根节点)"
        return (
            f"level={level!r} 的节点不能挂在 level={parent_level!r} 的节点下，"
            f"允许的父节点 level 为：{allowed_desc}"
        )
    return None


def _next_default_level(parent_level: str) -> str:
    """[goal_tree_system_plan.md §4.2] 默认子节点 level = 父节点在
    `LEVEL_ORDER` 里的下一层；`parent_level` 未知或已经是最后一层
    （`objective`）时兜底为 `"objective"`（`goal` 挂 `goal` 属于允许的
    显式选择，不是"下一层"的默认推断结果，见 §4.1.1）。供
    `GoalBacklog.accept_candidate()`/`GoalTreeDecomposer` 在候选没有给出
    合法 level 建议时兜底使用。
    """
    if parent_level not in LEVEL_ORDER:
        return "objective"
    idx = LEVEL_ORDER.index(parent_level)
    if idx + 1 < len(LEVEL_ORDER):
        return LEVEL_ORDER[idx + 1]
    return "objective"


@dataclass
class Direction:
    """[personal_researcher_and_coach_capability_gap_plan.md C1] "长期方向"
    分组：多个独立的 Goal 可能共同服务于同一个更高层、没有验收标准的
    长期方向（如"工作项目""投资学习""内容创作"）。

    与 GoalNode 的本质区别：Goal 是"要做完的事"（有验收标准、参与
    GoalJudge 判定、会进入完成态），Direction 是"一个不会真正'完成'
    的方向"——只是标题 + 创建时间的纯展示聚合结构，不参与任何执行/判定
    逻辑。刻意不复用 growth_advisor 的置信度/衰减机制——那套是为"候选
    要不要生成"这个决策服务的，Direction 分组不需要决策逻辑。
    """
    id: str
    title: str
    created_at: float = 0.0
    # 可选备注，供用户补充"这个方向具体指什么"
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> "Direction":
        return Direction(
            id=d.get("id", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", 0.0),
            description=d.get("description", ""),
        )


# [goal_cron_status_integrity_and_self_healing_plan.md] 周期性 Goal 通过
# "通用状态写入口"（CLI `/agent goals done|pause`、REST `PATCH /v1/goals/
# {goal_id}`）允许直接写入的状态集合。这三个之外的状态（completed/failed/
# cancelled 等）不是不能达成，而是不该由这些"不知道 recurring 是什么"的
# 通用入口随手写入——它们要么应该走 `stop_goal_recurrence()`（先解绑周期性
# 再谈"结束"），要么是只该由 `_sync_goal_status()`/`goal_cron_bridge` 这类
# 明确知道自己在处理周期性语义的内部路径来写。
_RECURRING_GOAL_ALLOWED_GENERIC_STATUSES = {"active", "paused", "abandoned"}


def validate_status_write_for_recurring_goal(
    node: Optional["GoalNode"], status: str,
) -> Optional[str]:
    """周期性 Goal 通过通用状态写入口写状态时的合法性校验。

    背景（详见 next_doc/goal_cron_status_integrity_and_self_healing_plan.md）：
    此前 `/agent goals done <id>` 和 `PATCH /v1/goals/{goal_id}` 都能无条件
    把任意节点的 status 改成任意值，包括把一个 `recurring=True` 的 Goal 改
    成 `completed`——而 `goal_cron_bridge._fire_goal_cycle()` 一旦发现
    `goal.status != "active"` 就静默跳过触发，导致"周期性 Goal 永远不会
    结束"这个设计不变量被悄悄打破，且没有任何报错或日志。

    返回 `None` 表示允许写入；返回非空字符串表示拒绝，内容是可以直接展示
    给调用方（CLI/REST）的错误说明。

    只约束 `level == "goal"` 且 `recurring=True` 的节点——Objective 节点、
    非周期性 Goal 都不受影响，沿用原有的"想改成什么就改成什么"行为。
    """
    if node is None or node.level != "goal" or not node.recurring:
        return None
    if status in _RECURRING_GOAL_ALLOWED_GENERIC_STATUSES:
        return None
    return (
        f"{node.id} 是周期性 Goal（recurring=True），不允许通过通用状态"
        f"写入口直接改成 {status!r}。如果确实要彻底结束这个周期性 Goal，"
        f"请先执行 `/agent goals unrecur {node.id}` 停止周期性，再进行本次"
        f"操作；如果只是想暂停这一轮/这段时间，请用 "
        f"`/agent goals pause {node.id}`。"
    )


def compose_context(parent_desc: str, own_desc: str) -> str:
    """[goal_cron_feedback_and_output_policy_plan.md 4.2] 拼接父级说明与自身
    说明，父级在前、自身在后，都保留（不做二选一）。供 goal_cron_bridge 等
    多处创建子任务时复用。
    """
    parts = [p.strip() for p in (parent_desc, own_desc) if p and p.strip()]
    return "\n\n".join(parts)


def _append_onetime_output_workspace_context(
    paths: Optional[AgentPaths], goal_id: str, ordinal: int, description: str,
) -> str:
    """[goal_cron_output_directory_convention_plan.md §5 开放问题 3] 一次性
    Goal 版本的 `goal_cron_bridge._append_output_workspace_context()`：分配
    本个子 Objective 的产出目录、读上一个子 Objective 的 manifest（若有），
    拼进 description 末尾。逻辑与 recurring 版本刻意保持对称，只是这里的
    "上一轮"指"同一个一次性 Goal 下按创建顺序更早的子 Objective"，不是
    "周期性 Goal 的上一轮 cycle"。

    paths 为 None 或任何环节异常时静默跳过，退化为改造前的行为（agent 自己
    判断产出放哪），不影响 Goal 子节点创建主流程。
    """
    if paths is None:
        return description
    try:
        from mini_agent.evolution import output_workspace
        base_dir = output_workspace.goal_output_base_dir(paths, goal_id)
        run_dir = output_workspace.allocate_objective_dir(paths, goal_id, ordinal)

        parts = [description] if description and description.strip() else []

        prev_manifest = output_workspace.read_latest_manifest(base_dir)
        if prev_manifest:
            prev_text = output_workspace.format_manifest_for_prompt(prev_manifest)
            if prev_text:
                parts.append(f"--- 上一个子任务产出（{prev_manifest.get('_dir', '')}） ---\n{prev_text}")

        parts.append(f"本轮产出请写入：{run_dir}")
        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog._append_onetime_output_workspace_context')
        return description


def _append_execution_spec_prompt_block(
    paths: Optional[AgentPaths], goal: "GoalNode", description: str,
) -> str:
    """[goal_execution_spec_generation_plan.md §5] `add_objectives_for_goal()`
    一侧的对称处理：如果 Goal 已确认执行规范，把 deliverables/
    per_cycle_criteria/special_constraints/handoff_fields 格式化后拼进子
    Objective description 末尾。

    与 recurring 版本（goal_cron_bridge._append_execution_spec_context）的
    区别：这里不做 §5.1 的轻量核对（一次性 Goal 的子 Objective 之间不是
    "轮次"关系，"连续 N 轮未达标"的语义不适用；单个子 Objective 完成后是否
    满足 per_cycle_criteria，留给 GoalJudge/用户在该子任务收尾时判断）。

    未确认时完全不读规范文件，任何环节异常都静默跳过，不影响子节点创建
    主流程。
    """
    if paths is None or not getattr(goal, "execution_spec_confirmed", False):
        return description
    try:
        from mini_agent.perception import goal_execution_spec as ges
        spec = ges.load_spec(paths, goal.id)
        if spec is None or not spec.confirmed or spec.is_empty():
            return description
        block = spec.render_prompt_block()
        if not block:
            return description
        parts = [description] if description and description.strip() else []
        parts.append(block)
        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog._append_execution_spec_prompt_block')
        return description


def compute_aging_boost(
    node: "GoalNode",
    now: float,
    *,
    stale_days: float = 7.0,
    boost_per_day: float = 1.0,
    max_boost_days: float = 14.0,
) -> float:
    """[goal_execution_fairness_improvement_plan.md P3] 计算某个节点在调度侧
    应该额外获得的"老化加成"，只影响排序用的 effective_priority，从不写回
    node.priority 本身。

    判定标准与 next_action_advisor.py::_find_stale_active_goals() 复用同一套
    "days_since_touched >= stale_days 视为停滞"的口径（该模块保留自己独立的
    STALE_DAYS/STALE_PRIORITY_FLOOR 常量用于晨报展示，两边不合并成同一段
    代码路径，但含义一致，便于对照）。

    未停滞（或没有任何 last_touched_at/created_at 时间戳）返回 0；停滞后每
    多停滞一天加成 +boost_per_day，累计不超过 max_boost_days 天对应的量。
    """
    last_touched = node.last_touched_at or node.created_at
    if not last_touched:
        return 0.0
    days_since = (now - last_touched) / 86400
    if days_since < stale_days:
        return 0.0
    over_days = min(days_since - stale_days, max_boost_days)
    return over_days * boost_per_day


# [goal_tree_system_plan.md §4.3 阶段三] current_focus_ids 的默认 top-N，
# 方案原文"N 默认 1~3，可配置"，跟 compute_aging_boost 的常量一样只是
# 量级参考，不是拍死的数值——`recompute_current_focus_tree()`/
# `set_focus_pin()` 都允许调用方覆盖。
DEFAULT_FOCUS_TOP_N = 3


def compute_current_focus(
    node: "GoalNode",
    children: list["GoalNode"],
    now: float,
    *,
    top_n: int = DEFAULT_FOCUS_TOP_N,
) -> list[str]:
    """[goal_tree_system_plan.md §4.3] 纯规则、同步、不调用 LLM，计算某个
    非叶子节点当前应该关注的直接子节点 id 列表。

    规则（方案原文）：先并入 `node.focus_pinned_ids`（用户手动 pin 的，
    优先保留——只要求 id 仍然是 `children` 里的一个，不要求其状态是
    active，因为"持续生效直到用户取消"是 pin 语义本身的承诺，不该被
    状态变化悄悄撤销，用户需要看到"我 pin 的这个已经完成/放弃了"这个
    事实本身，而不是被静默替换成别的子节点），再从剩余 **active** 直接
    子节点里按 `priority + compute_aging_boost()` 老化加成降序排序，取
    `top_n - len(pinned)` 个补足，最终按"pinned 在前、补足在后"合并去重
    返回。

    没有子节点，或全部 `completed`/`abandoned`（且没有 pin，或 pin 的也
    已进入终态）时返回空列表——意味着该节点该被 §4.2 的停滞巡检捕获，
    去生成新的分解候选，这正是 `current_focus_ids` 字段注释里
    "空列表表示子节点已全部进入终态"这句话的计算侧落地。

    只读、无副作用，不加锁——调用方（`recompute_current_focus_tree()`/
    `set_focus_pin()`）负责在自己的锁临界区内调用并回写结果。
    """
    child_by_id = {c.id: c for c in children}
    pinned = [cid for cid in node.focus_pinned_ids if cid in child_by_id]

    pinned_set = set(pinned)
    pool = [c for c in children if c.id not in pinned_set and c.status == "active"]
    ranked = sorted(
        pool, key=lambda c: c.priority + compute_aging_boost(c, now), reverse=True,
    )
    remaining_slots = max(top_n - len(pinned), 0)
    picked = [c.id for c in ranked[:remaining_slots]]

    return list(dict.fromkeys(pinned + picked))


@dataclass
class AdvanceDecision:
    """`try_advance_goal()` 的返回值（P5，§3.5/§4.4）。

    action 取值：
      - "not_found"     — goal_id 不存在，未做任何修改。
      - "cooldown_skip" — 仍在冷却期内，未执行拉起动作（remaining_seconds
                          给出还剩多少秒）。
      - "reactivated"   — Goal 原本非 active，已被 set_status(active)。
      - "enqueue_turn"  — Goal 本来就是 active，调用方需要自己去
                          enqueue_turn（本方法不直接依赖 InputQueue）。
    """
    action: str
    goal_id: str
    remaining_seconds: float = 0.0


# ── GoalBacklog 主类 ──────────────────────────────────────────────────────────

class GoalBacklog:
    """
    跨会话目标层级管理器。

    存储路径：<project_root>/.agent/goals.json
    """

    VERSION = 1

    def __init__(self, paths: AgentPaths) -> None:
        self._paths = paths
        self._goals_path = paths.workdir_dir / "goals.json"
        self._nodes: dict[str, GoalNode] = {}  # id -> GoalNode
        # [personal_researcher_and_coach_capability_gap_plan.md C1]
        # id -> Direction，与 _nodes 同文件持久化（goals.json 顶层新增
        # "directions" 键），跟着 _locked() 走同一把并发锁。
        self._directions: dict[str, Direction] = {}

    # ── 跨进程并发控制 ────────────────────────────────────────────────────────

    @property
    def _lock_path(self) -> Path:
        return self._goals_path.with_suffix(".json.lock")

    @contextlib.contextmanager
    def _locked(self):
        """独占临界区：加进程间文件锁 → 重新加载磁盘最新状态 → yield 给调用方
        做单个修改 → 落盘 → 释放锁。

        重新 load 这一步是关键：不这样做的话，即使加了锁，调用方内存里
        仍然是"进入临界区之前"的旧快照，一样会在 save() 时把锁等待期间
        其他进程写入的改动覆盖掉。加锁只保证互斥，"以最新数据为基础改"
        才能真正避免丢失更新。

        无 fcntl 的平台（如 Windows）退化为不加锁（仅刷新数据），尽力而为。
        """
        self._goals_path.parent.mkdir(parents=True, exist_ok=True)
        lock_f = None
        if _HAS_FCNTL:
            lock_f = open(self._lock_path, "w")
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            self.load()  # 丢弃旧内存状态，换成磁盘上最新的
            yield
            self.save()
        finally:
            if lock_f is not None:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog._locked')
                    pass
                lock_f.close()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """从磁盘加载（不存在时静默忽略）。"""
        if not self._goals_path.exists():
            return
        try:
            data = json.loads(self._goals_path.read_text(encoding="utf-8"))
            goals_list = data.get("goals", [])
            self._nodes = {
                g["id"]: GoalNode.from_dict(g)
                for g in goals_list
                if isinstance(g, dict) and "id" in g
            }
            directions_list = data.get("directions", [])
            self._directions = {
                dr["id"]: Direction.from_dict(dr)
                for dr in directions_list
                if isinstance(dr, dict) and "id" in dr
            }
            self._backfill_user_output_dir_suggestions()
        except Exception as _mini_agent_exc:
            # 读取失败不阻塞 agent 主流程
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog.load')
            self._nodes = {}
            self._directions = {}

    def _backfill_user_output_dir_suggestions(self) -> None:
        """[goal_user_output_dir_plan.md] 对"存量" Goal 自动补跑一次产出
        路径检测——`user_output_dir_suggested` 字段是在这个功能上线之后
        才引入的，`add_goal()` 只在**创建时**跑检测；已经存在的 Goal（不
        管是这个功能上线之前创建的，还是从旧版本 `goals.json` 加载进来
        的）从来没有机会跑过这次检测，字段值停留在默认的 `None`。

        这里在**每次加载**时检查：`level == "goal"` 且
        `user_output_dir_suggested is None`（这个精确的 `None` 就是"从未
        检测过"的标记，见字段注释的三态说明）的节点，对其 `description`
        跑一遍 `detect_user_specified_output_hint()`，结果写回内存（命中
        写路径片段，没命中写空字符串）。

        [重要] 这里**只改内存，不落盘**——`load()` 有多处调用方明确要求
        "只读查询，不加锁"（如 `goals_missing_objective()`），在这些路径
        里如果顺手落盘，等于绕开了 `_locked()` 的加锁写入保护，可能跟其他
        进程在锁保护下的并发写产生"最后写入覆盖前面更新"的问题。不落盘
        没有正确性代价：真正暴露给用户看的入口（`GET /v1/goals`）每次
        请求都会重新 `load_goal_backlog()`，检测本身是纯函数、开销可忽略
        不计，等于"每次读都顺手算一遍"，结果对用户来说和"已经落盘"没有
        区别；而真正需要落盘的场景（比如这个 Goal 之后触发了周期性执行）
        会自然地经过 `_locked()`（`self.load()` → 调用方修改 → `self.
        save()`），届时这次内存里算出来的补齐结果会随着那次正常的锁保护
        写入一起落盘，不需要这里额外做任何事。

        任何环节异常都不应该阻塞正常的加载流程，静默跳过。
        """
        try:
            from mini_agent.evolution import output_workspace as ow
        except Exception:
            return
        for node in self._nodes.values():
            if node.level != "goal" or node.user_output_dir_suggested is not None:
                continue
            try:
                hints = ow.detect_user_specified_output_hint(node.description or "")
                node.user_output_dir_suggested = hints[0] if hints else ""
            except Exception:
                continue

    def save(self) -> None:
        """原子写入磁盘（使用通用工具，含指数退避重试）。"""
        data = {
            "version": self.VERSION,
            "goals": [n.to_dict() for n in self._nodes.values()],
            "directions": [dr.to_dict() for dr in self._directions.values()],
        }
        atomic_write_json(self._goals_path, data)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def has_actionable_work(self) -> bool:
        """
        是否存在 status=active 且 level=objective 的节点。
        这是 AutonomousLoop._tick_maintenance() 调用的核心查询。
        passive 档位的 tick() 不调用此方法（边界由 AutonomousLoop 在调用方保证）。
        """
        return any(
            n.is_active and n.is_objective
            for n in self._nodes.values()
        )

    def active_objectives(self) -> list[GoalNode]:
        """返回所有 active objective，按优先级降序。"""
        objs = [
            n for n in self._nodes.values()
            if n.is_active and n.is_objective
        ]
        return sorted(objs, key=lambda n: n.priority, reverse=True)

    def active_objectives_fair_ranked(
        self,
        *,
        stale_days: float = 7.0,
        aging_boost_per_day: float = 1.0,
        aging_boost_max_days: float = 14.0,
        now: Optional[float] = None,
    ) -> list[GoalNode]:
        """[goal_execution_fairness_improvement_plan.md P2/P3]
        返回所有 active objective，"公平轮询"排序，而不是纯 priority 排序。

        与 active_objectives() 并存、互不影响：旧方法/priority 策略下的
        调用方行为完全不变。

        排序规则：
          1. 按 parent_id（所属 Goal）分组，组内按
             effective_priority = priority + aging_boost 降序，取组内
             第一名作为该 Goal 本轮的"代表候选"；
          2. 结果列表按 Goal 分组，组间以该 Goal 代表候选所属 GoalNode 的
             last_scheduled_at 升序排列（从未被调度过记为 0，排最前）；
             同一时间桶内按代表候选的 effective_priority 降序；
          3. 每组的非代表候选（同一 Goal 下排名靠后的其它 Objective）依次
             追加在所有代表候选之后，组间顺序、组内顺序规则同上——保证
             调用方"挑不到代表候选时继续往下挑"依然能拿到本 Goal 的其它
             Objective，不会把它们直接丢弃。

        排序本身是自我修正的：一个 Goal 本轮被选中执行后，
        last_scheduled_at 更新，下一轮自然排到后面；未被选中的 Goal
        last_scheduled_at 不变，下一轮自然排到更前面。不需要额外的
        补偿计数器。
        """
        objs = [
            n for n in self._nodes.values()
            if n.is_active and n.is_objective
        ]
        if not objs:
            return []
        now = time.time() if now is None else now

        # 所属 Goal：优先用 objective 自己的 parent_id 对应的 GoalNode；
        # 找不到（数据异常/孤儿 objective）时退化为把它自己当成独立的一组，
        # 避免整体报错。
        def _owner(node: GoalNode) -> GoalNode:
            parent = self._nodes.get(node.parent_id) if node.parent_id else None
            return parent if parent is not None else node

        def _effective_priority(node: GoalNode) -> float:
            return node.priority + compute_aging_boost(
                node, now,
                stale_days=stale_days,
                boost_per_day=aging_boost_per_day,
                max_boost_days=aging_boost_max_days,
            )

        groups: dict[str, list[GoalNode]] = {}
        owners: dict[str, GoalNode] = {}
        for n in objs:
            owner = _owner(n)
            groups.setdefault(owner.id, []).append(n)
            owners[owner.id] = owner

        ranked_groups: list[tuple[GoalNode, list[GoalNode]]] = []
        for owner_id, members in groups.items():
            members_sorted = sorted(members, key=_effective_priority, reverse=True)
            ranked_groups.append((owners[owner_id], members_sorted))

        ranked_groups.sort(
            key=lambda pair: (pair[0].last_scheduled_at or 0.0, -_effective_priority(pair[0]))
        )

        result: list[GoalNode] = []
        for _owner_node, members_sorted in ranked_groups:
            result.append(members_sorted[0])
        for _owner_node, members_sorted in ranked_groups:
            result.extend(members_sorted[1:])
        return result

    def mark_scheduled(self, node_id: str, when: Optional[float] = None) -> bool:
        """[goal_execution_fairness_improvement_plan.md P2] 记录 node_id
        所属 Goal 刚被分配到一次执行槽位（ObjectiveExecutor.start() 成功
        后调用）。只写 last_scheduled_at，不touch last_touched_at/其它
        字段——语义上"被调度"不等于"有实质进展"，P3 的老化加成只应该在
        真正有进展（last_touched_at 更新）时才归零。

        node_id 可以是 Objective 自己的 id，也可以是它的父 Goal id；两种
        情况都直接更新传入 id 对应节点的 last_scheduled_at（调用方通常传
        Objective id，排序时读的是该 Objective 通过 parent_id 找到的
        GoalNode，若上层还想单独标记 Goal 自身可以再调用一次）。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.last_scheduled_at = time.time() if when is None else when
            parent = self._nodes.get(node.parent_id) if node.parent_id else None
            if parent is not None:
                parent.last_scheduled_at = node.last_scheduled_at
        return True

    def active_goals(self) -> list[GoalNode]:
        """返回所有 active goal，按优先级降序。"""
        goals = [
            n for n in self._nodes.values()
            if n.is_active and n.is_goal
        ]
        return sorted(goals, key=lambda n: n.priority, reverse=True)

    def focus_research_nodes(self) -> list[GoalNode]:
        """[next_doc/goal_tree_research_and_action_recommendation_plan.md
        §4.1] 供 `external_input/goal_relevance.py` 等"该往哪个方向找
        外部信息"的调研入口使用的节点集合——在 `active_goals()`（叶子层，
        有验收标准/执行内容）基础上，并入当前"现阶段焦点"里的结构节点
        （`domain`/`stage`，即 §三 定义的纯结构层）。

        动机：`current_focus_ids` 是树上"现在该关注哪"的权威信号，但
        `active_goals()` 只看 `level=goal`，会漏掉"现阶段焦点恰好停在
        domain/stage 这一层、下面还没细化出具体 goal"的情况——这种时候
        恰恰更需要调研信息来帮用户想清楚下一步该往哪个方向细化，而不是
        被现有扫描范围忽略。

        只读、无副作用；结果按 `active_goals()` 相同的优先级降序排列，
        结构节点排在其后（结构节点没有细粒度优先级语义时默认排在叶子
        Goal 之后，不抢占既有 Goal 相关性判断的相对顺序）。
        """
        leaf_goals = self.active_goals()
        seen_ids = {n.id for n in leaf_goals}
        focus_structural_ids: set[str] = set()
        for node in self._nodes.values():
            if not node.is_active or not node.is_structural:
                continue
            for child_id in node.current_focus_ids:
                child = self._nodes.get(child_id)
                if child is not None and child.is_active and child.is_structural:
                    focus_structural_ids.add(child_id)
        structural_nodes = [
            self._nodes[cid] for cid in focus_structural_ids
            if cid not in seen_ids and cid in self._nodes
        ]
        structural_nodes.sort(key=lambda n: n.priority, reverse=True)
        return leaf_goals + structural_nodes

    def all_nodes(self) -> list[GoalNode]:
        """返回全部节点（不按 status 过滤），按优先级降序。

        供看板等"需要看到完整 goals.json 内容"的场景使用——
        active_goals()/active_objectives() 是给 AutonomousLoop 用的，
        只关心 active 状态；这里是给外部展示用的全量视图，paused /
        completed / abandoned 节点也要能看到，否则看板会显示不出
        goals.json 里实际存在的数据（这几种状态的节点永远不出现）。
        """
        return sorted(self._nodes.values(), key=lambda n: n.priority, reverse=True)

    def get(self, node_id: str) -> Optional[GoalNode]:
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[GoalNode]:
        return list(self._nodes.values())

    # ── 写入 ──────────────────────────────────────────────────────────────────

    def add_goal(
        self,
        title: str,
        description: str = "",
        source: str = "user",
        priority: int = 0,
        tags: Optional[list[str]] = None,
        source_initiator: Optional[str] = None,
        status: str = "active",
    ) -> GoalNode:
        """
        添加 Goal 节点（通常由用户 /agent goals add 触发）。
        source="user" 时对应用户手动添加；
        source="agent_derived" 时由第十二节 autonomous 档位 tick 内部调用。

        status —— [goal_draft_flow_plan.md] 默认 "active"，与历史行为一致
        （现有调用方——CLI、SoftGoalDeriver 等——不传这个参数时不受影响）。
        看板"新建目标"表单允许用户显式传 "draft"：draft 状态的 Goal
        `is_active` 为 False，会被 `active_goals()`/`active_objectives()`
        和 `_fire_goal_cycle()`（周期性触发点，见该函数注释"Goal 非 active
        时挂起等待"）自动跳过，不会被调度执行——但不影响用户在这期间继续
        通过 `recur_goal()`/`update_fields()` 等接口完善周期性、执行规范等
        信息，这些接口本身不检查 status。用户确认无误后通过
        `update_fields(status="active")` 手动"激活"，即可正常进入调度。

        source_initiator —— [goal-provenance-guide.md] 记录"创建它的这次
        调用发生在哪个 InputQueue 轮次里"，跟 `source` 是正交维度。调用方
        显式知道自己的触发上下文时（比如 SoftGoalDeriver、NoveltyJudge 的
        用户确认动作）应该显式传入；不传（None）时读取
        `perception.turn_context.get_current_turn_initiator()` 的
        thread-local 兜底值——这个值由 AgentRunner._main_loop() 在处理
        每一轮 InputQueue 消息之前设置为该轮的 initiator（"user"/"cron"/
        "external"/...）。这样即使 Agent 是在处理一轮由 cron/external
        触发的对话时，通过工具/命令间接调用到这里创建了 Goal，即使调用方
        （比如 cli/commands/goals.py::_cmd_add_goal）自己不知道这轮对话
        是谁触发的，也能被正确标记，而不是一律退化成看起来像用户手动
        创建。没有任何轮次上下文时（CLI 独立进程、测试）兜底为 "user"。

        内部会先重新加载磁盘最新状态再追加，并立即落盘，
        避免与其他进程并发写入互相覆盖。
        """
        if source_initiator is None:
            try:
                from mini_agent.perception.turn_context import get_current_turn_initiator
                source_initiator = get_current_turn_initiator()
            except Exception:
                source_initiator = "user"
        with self._locked():
            node = GoalNode(
                id=f"goal_{uuid.uuid4().hex[:8]}",
                level="goal",
                title=title,
                source=source,
                status=status,
                created_at=time.time(),
                last_touched_at=time.time(),
                priority=priority,
                tags=tags or [],
                description=description,
                source_initiator=source_initiator,
            )
            # [goal_user_output_dir_plan.md] 创建时顺手跑一遍规则检测，把
            # description 里疑似的产出路径提示存成"建议"（不自动生效，见
            # user_output_dir_suggested 字段注释），供看板展示给用户确认。
            # 规则检测本身不精确，任何异常都不应该影响 Goal 创建主流程。
            # 无论有没有检测到，都要写一个非 None 的值（命中写路径片段，
            # 没命中写空字符串）——`None` 专门保留给"从未跑过检测"这个
            # 状态，用于区分\"新建时已检测过、确实没有提示\"和\"这是加载自
            # 旧版本 goals.json 的历史 Goal，还没来得及检测过\"，后者会在
            # `GoalBacklog.load()` 里被自动补跑一次检测（见该方法注释）。
            try:
                from mini_agent.evolution import output_workspace as ow
                hints = ow.detect_user_specified_output_hint(description or "")
                node.user_output_dir_suggested = hints[0] if hints else ""
            except Exception:
                pass
            self._nodes[node.id] = node
        return node

    def add_objective(
        self,
        title: str,
        parent_id: Optional[str] = None,
        work_thread_ref: Optional[str] = None,
        source: str = "user",
        priority: int = 0,
        description: str = "",
    ) -> GoalNode:
        """添加 Objective 节点，可关联 WorkThread。

        description — [goal_cron_binding_plan.md Track A 新增] 主要供
        goal_cron_bridge._fire_goal_cycle() 使用：周期性 Goal 每轮创建的子 Objective
        需要带上 cron job 的 task_template 作为该轮任务的具体说明，title
        （"XXX（第 N 轮）"）只是给人看的短标签。其余既有调用方不传时保持空
        字符串，行为不变。

        内部会先重新加载磁盘最新状态再追加，并立即落盘，
        避免与其他进程并发写入互相覆盖。
        """
        with self._locked():
            node = GoalNode(
                id=f"obj_{uuid.uuid4().hex[:8]}",
                level="objective",
                title=title,
                source=source,
                status="active",
                created_at=time.time(),
                last_touched_at=time.time(),
                parent_id=parent_id,
                work_thread_ref=work_thread_ref,
                priority=priority,
                description=description,
            )
            self._nodes[node.id] = node
            # 更新 parent 的 children_ids（用重新加载后的最新 parent 节点）
            if parent_id and parent_id in self._nodes:
                self._nodes[parent_id].children_ids.append(node.id)
        return node

    def add_node(
        self,
        level: str,
        title: str,
        parent_id: Optional[str] = None,
        description: str = "",
        source: str = "user",
        priority: int = 0,
        tags: Optional[list[str]] = None,
        status: str = "active",
        source_initiator: Optional[str] = None,
    ) -> GoalNode:
        """[goal_tree_system_plan.md §4.1.1 / §4.5] 通用节点创建入口，支持
        `LEVEL_ORDER` 里的任意一层（"ultimate"/"domain"/"stage"/"goal"/
        "objective"）。`add_goal()`/`add_objective()` 是对本方法的薄封装
        （保留是因为两者各自还有一些该 level 专属的副作用，如 Goal 的产出
        目录提示检测），新的三层结构节点应统一通过本方法创建。

        校验规则见 `validate_node_hierarchy()`：父子 level 顺序不能倒挂；
        `level="ultimate"` 额外要求全局唯一（`parent_id` 必须为空，且当前
        不存在其它 ultimate 节点）。不合法时抛出 `ValueError`，不会创建
        任何节点、不会落盘。

        内部会先重新加载磁盘最新状态再追加，并立即落盘，避免与其他进程
        并发写入互相覆盖（与 add_goal()/add_objective() 同样的并发语义）。
        """
        if source_initiator is None:
            try:
                from mini_agent.perception.turn_context import get_current_turn_initiator
                source_initiator = get_current_turn_initiator()
            except Exception:
                source_initiator = "user"
        with self._locked():
            parent = self._nodes.get(parent_id) if parent_id else None
            if parent_id and parent is None:
                raise ValueError(f"parent_id={parent_id!r} 不存在")
            parent_level = parent.level if parent is not None else None
            err = validate_node_hierarchy(level, parent_level)
            if err:
                raise ValueError(err)
            if level == "ultimate" and any(n.level == "ultimate" for n in self._nodes.values()):
                raise ValueError("已存在全局根节点（level=ultimate），不允许创建第二个")
            id_prefix = {"goal": "goal", "objective": "obj"}.get(level, level)
            node = GoalNode(
                id=f"{id_prefix}_{uuid.uuid4().hex[:8]}",
                level=level,
                title=title,
                source=source,
                status=status,
                created_at=time.time(),
                last_touched_at=time.time(),
                parent_id=parent_id,
                priority=priority,
                tags=tags or [],
                description=description,
                source_initiator=source_initiator,
            )
            self._nodes[node.id] = node
            if parent is not None:
                parent.children_ids.append(node.id)
        return node

    def get_root_node(self) -> GoalNode:
        """返回全局唯一的 `level="ultimate"` 根节点，不存在时自动创建一个
        占位根节点（标题留空，等用户在看板里编辑）。保证任何时候调用本方法
        都恰好返回同一个根节点（幂等）。见 §4.1"根节点"一节。"""
        with self._locked():
            for node in self._nodes.values():
                if node.level == "ultimate":
                    return node
            node = GoalNode(
                id=f"ultimate_{uuid.uuid4().hex[:8]}",
                level="ultimate",
                title="我的人生目标",
                source="user",
                status="active",
                created_at=time.time(),
                last_touched_at=time.time(),
            )
            self._nodes[node.id] = node
        return node

    def migrate_directions_to_domain_nodes(self, dry_run: bool = True) -> dict:
        """[goal_tree_system_plan.md §4.1] 一次性迁移：把每条 `Direction`
        转成 `level="domain"` 的 `GoalNode`（`id` 复用，挂在全局根节点下），
        原来通过 `direction_id` 关联的 Goal 节点，改成直接 `parent_id`
        指向对应的 domain 节点。

        `dry_run=True`（默认）时只返回预览报告，不修改任何状态；
        `dry_run=False` 时真正执行迁移并落盘。迁移前后旧版本代码路径
        （`direction_id` 字段）保留读取兼容，本方法不会清空 `direction_id`
        ——过渡期结束后再单独清理，见方案文档§4.1 相关段落。

        返回 `{"directions_migrated": [...], "goals_reparented": [...]}`，
        每项分别是 `{"direction_id", "domain_node_id", "title"}` 和
        `{"goal_id", "old_parent_id", "new_parent_id"}`，方便调用方（CLI）
        打印预览。已经迁移过的 Direction（即已存在一个
        `id == direction_id` 的 domain 节点）会被跳过，保证多次调用幂等。
        """
        with self._locked():
            root = None
            for node in self._nodes.values():
                if node.level == "ultimate":
                    root = node
                    break
            if root is None and not dry_run:
                root = GoalNode(
                    id=f"ultimate_{uuid.uuid4().hex[:8]}",
                    level="ultimate",
                    title="我的人生目标",
                    source="user",
                    status="active",
                    created_at=time.time(),
                    last_touched_at=time.time(),
                )
                self._nodes[root.id] = root
            report: dict = {"directions_migrated": [], "goals_reparented": []}
            for direction in list(self._directions.values()):
                if direction.id in self._nodes:
                    continue  # 已经迁移过，幂等跳过
                report["directions_migrated"].append({
                    "direction_id": direction.id,
                    "domain_node_id": direction.id,
                    "title": direction.title,
                })
                if not dry_run:
                    domain_node = GoalNode(
                        id=direction.id,
                        level="domain",
                        title=direction.title,
                        source="user",
                        status="active",
                        created_at=direction.created_at or time.time(),
                        last_touched_at=time.time(),
                        parent_id=root.id if root else None,
                        description=direction.description,
                    )
                    self._nodes[domain_node.id] = domain_node
                    if root is not None:
                        root.children_ids.append(domain_node.id)
                for goal in self._nodes.values():
                    if goal.direction_id != direction.id or not goal.is_goal:
                        continue
                    if goal.parent_id == direction.id:
                        continue  # 已经指向迁移后的 domain 节点，幂等跳过
                    report["goals_reparented"].append({
                        "goal_id": goal.id,
                        "old_parent_id": goal.parent_id,
                        "new_parent_id": direction.id,
                    })
                    if not dry_run:
                        goal.parent_id = direction.id
        return report

    def append_decompose_candidates(self, node_id: str, candidates: list[dict]) -> bool:
        """[goal_tree_system_plan.md §4.2 落盘] 把 `GoalTreeDecomposer` 生成的
        候选追加进节点的 `decompose_candidates`。不去重、不校验候选内容
        （由 `GoalTreeDecomposer` 在生成阶段负责去重/合法性），本方法只
        负责在锁保护下原子追加，避免与其他进程并发写入互相覆盖。

        `node_id` 不存在时返回 `False`，不做任何修改。
        """
        if not candidates:
            return True
        with self._locked():
            node = self._nodes.get(node_id)
            if node is None:
                return False
            node.decompose_candidates.extend(candidates)
        return True

    def accept_candidate(
        self, node_id: str, candidate_id: str, overrides: Optional[dict] = None,
    ) -> Optional[GoalNode]:
        """[goal_tree_system_plan.md §4.5] 接受一个待确认的分解候选：从
        `node.decompose_candidates` 里取出该候选、创建真正的 `GoalNode`
        （挂在 `node_id` 下）、并把候选从待确认列表移除。

        `overrides` 可选，用于"编辑后采纳"（§4.4 的"✏️ 编辑后采纳"）：
        允许覆盖候选的 `title`/`description`/`level`，键名与这三者同名，
        其它键会被忽略。

        与 `add_node()` 做的事本质相同，但不能直接调用 `add_node()`——
        两者都需要持有 `_locked()` 临界区，而 `_locked()` 底层的进程间
        文件锁不可重入，嵌套调用会死锁，所以这里在同一个 `_locked()` 块
        内内联完成校验 + 创建 + 候选移除。

        候选不存在、`node_id` 不存在、或候选内容校验不通过（层级不合法）
        时返回 `None`，不做任何修改。
        """
        overrides = overrides or {}
        with self._locked():
            node = self._nodes.get(node_id)
            if node is None:
                return None
            idx = next(
                (i for i, c in enumerate(node.decompose_candidates)
                 if c.get("id") == candidate_id),
                None,
            )
            if idx is None:
                return None
            candidate = node.decompose_candidates[idx]
            title = overrides.get("title", candidate.get("title", ""))
            description = overrides.get("description", candidate.get("description", ""))
            level = overrides.get("level", candidate.get("level") or _next_default_level(node.level))
            err = validate_node_hierarchy(level, node.level)
            if err:
                return None
            id_prefix = {"goal": "goal", "objective": "obj"}.get(level, level)
            new_node = GoalNode(
                id=f"{id_prefix}_{uuid.uuid4().hex[:8]}",
                level=level,
                title=title,
                source="agent_derived",
                status="active",
                created_at=time.time(),
                last_touched_at=time.time(),
                parent_id=node_id,
                description=description,
            )
            self._nodes[new_node.id] = new_node
            node.children_ids.append(new_node.id)
            del node.decompose_candidates[idx]
        return new_node

    def reject_candidate(self, node_id: str, candidate_id: str) -> Optional[dict]:
        """从 `node.decompose_candidates` 移除一个候选（用户点"忽略"）。

        只负责移除，不写"30 天内不再对同一节点重复生成同主题候选"的去重
        记录——去重记录跟 `GoalTreeDecomposer` 的巡检节奏治理是同一套状态
        （见该类 `record_rejected()`），调用方应优先用
        `GoalTreeDecomposer.reject_candidate()`（同时做移除 + 记去重），
        这里保留基础版本供不需要去重语义的场景（如测试、脚本清理）单独
        调用。

        返回被移除的候选 dict（供调用方拿到 title 去记去重），不存在时
        返回 `None`，不做任何修改。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if node is None:
                return None
            idx = next(
                (i for i, c in enumerate(node.decompose_candidates)
                 if c.get("id") == candidate_id),
                None,
            )
            if idx is None:
                return None
            candidate = node.decompose_candidates[idx]
            del node.decompose_candidates[idx]
        return candidate

    def get_tree(self, root_id: Optional[str] = None) -> Optional[dict]:
        """[goal_tree_system_plan.md §4.5] 返回以 `root_id`（默认全局根
        节点）为起点的完整子树，供看板/CLI 渲染。

        返回结构：`{"node": GoalNode, "children": [同结构, ...]}`；
        `root_id` 不存在时返回 `None`。存在环（数据异常）时对同一节点只
        展开一次，避免死循环——正常数据不会出现环，这里只是防御。

        只读查询，不加锁，与 `all_nodes()`/`goals_missing_objective()` 一致。
        """
        if root_id is None:
            root = next((n for n in self._nodes.values() if n.level == "ultimate"), None)
            if root is None:
                return None
            root_id = root.id

        def _build(node_id: str, seen: set) -> Optional[dict]:
            if node_id in seen:
                return None
            node = self._nodes.get(node_id)
            if node is None:
                return None
            seen = seen | {node_id}
            return {
                "node": node,
                "children": [
                    child for cid in node.children_ids
                    if (child := _build(cid, seen)) is not None
                ],
            }

        return _build(root_id, set())

    # ── §4.3 现阶段焦点：pin / 重算 ──────────────────────────────────────────

    def set_focus_pin(self, node_id: str, child_id: str, pinned: bool) -> bool:
        """[goal_tree_system_plan.md §4.3/§4.5] 手动 pin/unpin 某个直接
        子节点为"现阶段焦点"。`pinned=True` 时把 `child_id` 加入
        `node.focus_pinned_ids`（已存在则不重复加）；`pinned=False` 时移除。

        改动后立即用 `compute_current_focus()` 重算**该节点自身**的
        `current_focus_ids`，不用等下一次 `sys:goal_tree_focus_recompute`
        巡检才生效；不递归影响祖先——pin 只改变"这个节点该关注哪个子
        节点"，不改变子节点自身在祖父节点排序里的 priority/aging，祖先的
        `current_focus_ids` 由下一次巡检自然覆盖到。

        `node_id` 不存在、或 `child_id` 不是它的直接子节点（
        `node.children_ids`）时返回 `False`，不做任何修改。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if node is None or child_id not in node.children_ids:
                return False
            pins = list(node.focus_pinned_ids)
            if pinned:
                if child_id not in pins:
                    pins.append(child_id)
            else:
                pins = [cid for cid in pins if cid != child_id]
            node.focus_pinned_ids = pins
            children = [
                self._nodes[cid] for cid in node.children_ids if cid in self._nodes
            ]
            node.current_focus_ids = compute_current_focus(node, children, time.time())
        return True

    def recompute_current_focus_tree(
        self, root_id: Optional[str] = None, *, top_n: int = DEFAULT_FOCUS_TOP_N,
    ) -> int:
        """[goal_tree_system_plan.md §4.3] `sys:goal_tree_focus_recompute`
        cron job 的核心逻辑：自底向上（后序遍历，子节点先算好，结果再
        影响父节点排序）重算 `root_id`（默认全局根节点）为起点的整棵子树
        的 `current_focus_ids`。

        只对 `level` 在 `("ultimate", "domain", "stage")` 三层的非叶子
        节点写回结果——跟 `GoalNode.current_focus_ids` 字段注释"仅这三层
        非叶子节点使用"保持一致；`goal`/`objective` 两层继续只走既有的
        `GoalRunner`/fairness 排序，不参与本次计算，即使某个 `goal` 节点
        下面挂了别的 `goal`（"goal 挂 goal"场景）。

        返回实际发生变化（新旧 `current_focus_ids` 不同）的节点数，供
        cron handler 打日志；`root_id` 不存在（包括压根还没有全局根节点）
        时返回 0，不做任何修改。
        """
        with self._locked():
            if root_id is None:
                root = next(
                    (n for n in self._nodes.values() if n.level == "ultimate"), None,
                )
                if root is None:
                    return 0
                root_id = root.id
            elif root_id not in self._nodes:
                return 0

            order: list[str] = []
            seen: set[str] = set()

            def _post_order(nid: str) -> None:
                if nid in seen or nid not in self._nodes:
                    return
                seen.add(nid)
                for cid in self._nodes[nid].children_ids:
                    _post_order(cid)
                order.append(nid)

            _post_order(root_id)

            now = time.time()
            updated = 0
            for nid in order:
                node = self._nodes[nid]
                if node.level not in ("ultimate", "domain", "stage"):
                    continue
                children = [
                    self._nodes[cid] for cid in node.children_ids if cid in self._nodes
                ]
                new_focus = compute_current_focus(node, children, now, top_n=top_n)
                if new_focus != node.current_focus_ids:
                    node.current_focus_ids = new_focus
                    updated += 1
        return updated

    def goals_missing_objective(self) -> list[GoalNode]:
        """返回没有任何 active Objective 子节点的 active Goal（按优先级降序）。

        只读查询，不加锁——调用方（AutonomousLoop）通常紧接着要做一次可能
        较慢的 LLM 拆解，若在锁内做会让持有跨进程文件锁的时间从"毫秒级"
        变成"LLM 请求耗时"，阻塞同一时间其他进程（别的 CLI session / API
        请求）对 goals.json 的读写。所以这里只负责"读出需要处理什么"，
        真正写入用 add_objectives_for_goal()，两者分开、各自最小化持锁/
        无锁窗口。
        """
        self.load()
        result = [
            n for n in self._nodes.values()
            if n.is_active and n.is_goal and not any(
                (child := self._nodes.get(cid)) is not None and child.is_active and child.is_objective
                for cid in n.children_ids
            )
        ]
        return sorted(result, key=lambda n: n.priority, reverse=True)

    def add_objectives_for_goal(self, goal_id: str, titles: list[str]) -> list[GoalNode]:
        """在锁保护下，为指定 Goal 批量创建 Objective 子节点。

        titles 应该是调用方已经在锁外算好的具体标题（无论是 LLM 拆解结果
        还是降级后的镜像标题）——这个方法本身只做纯粹的数据写入，不做
        任何耗时操作，保证锁的持有时间可控。

        [goal_cron_output_directory_convention_plan.md §5 开放问题 3] **一次性**
        （非 recurring）Goal 的子 Objective 也套用产出目录规范：每创建一个子
        Objective，按其在 `children_ids` 里的 1-based 位置分配一个
        `goals/<goal_id>/run_%04d/` 目录，并把"本轮产出请写入：<目录>"
        （连同上一个子 Objective 的产出摘要，若有）拼进 description 末尾——
        与 recurring Goal 侧 `goal_cron_bridge._append_output_workspace_context()`
        是同一套逻辑，只是分配时机在这里（子节点创建时）而不是 cron 触发时。
        recurring Goal 不在这里处理（走 `add_objective()` 单数版本 + cron 触发
        时机分配 `cycle_%04d` 目录），两条路径不会重复分配。
        """
        with self._locked():
            goal = self._nodes.get(goal_id)
            if not goal or not goal.is_goal:
                return []
            created: list[GoalNode] = []
            for title in titles:
                description = goal.description
                if not goal.recurring:
                    description = _append_onetime_output_workspace_context(
                        self._paths, goal_id, len(goal.children_ids) + 1, description,
                    )
                description = _append_execution_spec_prompt_block(
                    self._paths, goal, description,
                )
                node = GoalNode(
                    id=f"obj_{uuid.uuid4().hex[:8]}",
                    level="objective",
                    title=title,
                    source="agent_derived",
                    status="active",
                    created_at=time.time(),
                    last_touched_at=time.time(),
                    parent_id=goal_id,
                    priority=goal.priority,
                    # [goal_cron_feedback_and_output_policy_plan.md P2] 父 Goal 的
                    # description 里常写着约束条件，子 Objective 之前完全没继承，
                    # 这里补上；执行侧还有 effective_context() 做双保险。
                    description=description,
                )
                self._nodes[node.id] = node
                goal.children_ids.append(node.id)
                created.append(node)
        return created

    def maybe_close_goal_by_overall_criteria(
        self, goal_id: str, cfg: Optional[object] = None, use_agent: Optional[bool] = None,
    ) -> Optional[str]:
        """[goal_execution_spec_generation_plan.md §5 第二段 /
        implementation_record.md 未实施清单第 5 项] 一次性（非 recurring）
        Goal 名下全部子 Objective 都已进入终态后，若该 Goal 存在已确认的执行
        规范且 `overall_completion_criteria` 非空，调用一次 LLM 判定是否可以
        把整个 Goal 标记为 `completed`。

        use_agent：[implementation_record.md §11 后续建议顺序第 2 条] 单次
        覆盖是否走受限 Agent 路径判定，`None`（默认）时回退配置文件
        `goal_execution_spec.overall_completion_use_agent`，与 Stage 8 给
        `build_draft`/`revise` 加的单次 `mode` 覆盖是同一风格，不修改配置
        文件；透传给 `evaluate_overall_completion(use_agent_override=...)`。

        由 `ObjectiveExecutor._on_objective_completed()` 在每次子 Objective
        收尾后调用一次（见该方法调用点注释）；本方法自己重新判断"是否真的到
        了该判断的时候"（是否还有子节点未终态等），调用方不需要预先过滤，
        多传/传早了也只会提前 return None，不会误判。

        前置只读判断在锁外完成（`load()` 之后直接读快照），真正的 LLM 判定
        调用同样在锁外进行——与 `goals_missing_objective()` 的"读写分离"原则
        一致，避免长时间持有跨进程文件锁阻塞其它进程；只有最终落盘
        （`set_status()`/`append_progress_note()`）才各自短暂加锁。

        返回值：
          - "closed"    — 已判定关闭，goal.status 已置为 "completed"。
          - "kept_open" — 已判定不关闭（标准证据不足），未做任何状态修改
                          （可能已追加一条说明性 progress_notes）。
          - None        — 未触发判定：不是一次性 Goal / Goal 已非 active /
                          还有子节点未进入终态 / 没有已确认的执行规范 /
                          `overall_completion_criteria` 为空。这种情况下不会
                          消耗任何 LLM 调用，代表"这个 Goal 根本不适用本机制"
                          （多数周期性 Goal、没生成过规范的 Goal 都会落在这里，
                          等价于本方案引入前的行为）。

        任何环节异常都静默降级为 None，不影响调用方的 Objective 收尾主流程
        ——关闭判断是可选增强，不是必经关卡。
        """
        self.load()
        goal = self._nodes.get(goal_id)
        if goal is None or not goal.is_goal:
            return None
        if goal.recurring or goal.status != "active":
            return None
        if not goal.children_ids:
            return None
        children: list[tuple[str, str]] = []
        for cid in goal.children_ids:
            child = self._nodes.get(cid)
            if child is None:
                continue
            if child.status in ("active", "paused"):
                # 还有子节点未进入终态，还不到判断"整体是否完成"的时机。
                return None
            children.append((child.title, child.status))
        if not children:
            return None
        if not getattr(goal, "execution_spec_confirmed", False):
            return None

        try:
            from mini_agent.perception import goal_execution_spec as ges
            spec = ges.load_spec(self._paths, goal_id)
            if spec is None or not spec.confirmed or not spec.overall_completion_criteria:
                return None

            from mini_agent.evolution import output_workspace
            base_dir = output_workspace.goal_output_base_dir(self._paths, goal_id)
            manifests = output_workspace.read_all_manifests(base_dir)

            if cfg is None:
                from mini_agent.config import load_config
                cfg = load_config(self._paths.project_root)

            builder = ges.GoalExecutionSpecBuilder(cfg)
            result = builder.evaluate_overall_completion(
                goal.title, goal.description, spec, children, manifests,
                output_base_dir=str(base_dir),
                use_agent_override=use_agent,
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(
                _mini_agent_exc,
                where="mini_agent.perception.goal_backlog.GoalBacklog.maybe_close_goal_by_overall_criteria",
            )
            return None

        reasoning = (result.get("reasoning") or "").strip()
        decision = result.get("decision")
        outcome = "closed" if decision == "close" else "kept_open"
        # [implementation_record.md §11 后续建议顺序第 1 条"整体关闭判定
        # 结果持久化展示"] 前置条件全部满足、真正调用了判定之后，无论
        # close/kept_open 都把本次结果写进 GoalNode，供看板/CLI 展示"上一
        # 次判定是什么时候、判了什么、是否挂了 Agent"，不必翻 progress_
        # notes 里的文本行去找。
        self.update_fields(
            goal_id,
            overall_completion_last_check={
                "outcome": outcome,
                "reasoning": reasoning,
                "used_agent": bool(getattr(builder, "last_used_agent", False)),
                "at": time.time(),
            },
        )
        if outcome == "closed":
            self.set_status(goal_id, "completed")
            if reasoning:
                self.append_progress_note(goal_id, f"✅ 整体完成判定：{reasoning[:200]}")
            return "closed"
        if reasoning:
            self.append_progress_note(goal_id, f"ℹ️ 整体完成判定（暂不关闭）：{reasoning[:200]}")
        return "kept_open"

    def update_progress(self, node_id: str, notes: str) -> bool:
        """更新节点进展记录。

        内部会先重新加载磁盘最新状态，在最新数据基础上改这一个字段再落盘，
        避免与其他进程并发写入互相覆盖。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.progress_notes = notes
            node.last_touched_at = time.time()
        return True

    def set_status(self, node_id: str, status: str) -> bool:
        """更新节点状态。
        [goal_cron_status_integrity_and_self_healing_plan.md] 本方法本身不
        做周期性 Goal 的合法性校验——它是最底层的写入原语，`goal_cron_bridge`
        的自愈逻辑也要通过它把状态拉回 active。真正面向"通用调用方"的校验
        在更上层的 CLI（`_cmd_set_status`）和 REST（`update_goal`）入口，
        调用前先过 `validate_status_write_for_recurring_goal()`。

        内部会先重新加载磁盘最新状态，在最新数据基础上改这一个字段再落盘，
        避免与其他进程并发写入互相覆盖。

        [next_doc/growth_advisor_cron_search_and_status_history_plan.md
        方向三] 状态真正发生变化时（新状态与当前状态不同）追加一条
        `status_history` 记录——同一状态被重复 set（比如外部调用方不
        判断当前状态就无脑调一次 `set_status(node_id, "active")`）不
        产生冗余历史条目，避免历史里全是无意义的重复记录。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            if node.status != status:
                node.status_history = list(node.status_history) + [
                    {"status": status, "at": time.time()}
                ]
            node.status = status
            node.last_touched_at = time.time()
        return True

    def delete_goal(self, goal_id: str) -> list[str]:
        """[看板目标删除功能] 硬删除一个 Goal 及其全部子孙节点（Objective）。

        与 `set_status(..., "abandoned")` 不同——那只是状态迁移，节点本身
        （以及 goals.json 里的记录）仍然保留；这里是真正从 `goals.json`
        里物理移除，配合调用方（API 路由层）清理 cron job 绑定、
        `.agent/daemon_run_outputs/goals/<goal_id>/`、执行规范/执行阶段/
        调优草案等外部文件，做到"删除即彻底清干净，不留孤儿数据"。

        只接受 `level == "goal"` 的节点——不允许单独删除某个 Objective
        （那属于"取消这个子任务"的语义，已有 `ObjectiveExecutor.cancel()`/
        `set_status()` 覆盖，不需要额外的硬删除入口）。

        级联规则：递归收集 `children_ids`（虽然当前数据模型里 Objective
        一般不再有自己的子节点，这里仍按树形结构递归，对未来可能出现的
        多级结构保持健壮）。

        返回被删除的全部节点 id 列表（goal 本身排在最前面），调用方据此
        去清理各自 keyed by node id 的外部文件/cron job；节点不存在或不是
        Goal 时返回空列表，不做任何修改。
        """
        with self._locked():
            goal = self._nodes.get(goal_id)
            if goal is None or not goal.is_goal:
                return []

            to_delete: list[str] = []
            frontier = [goal_id]
            seen: set[str] = set()
            while frontier:
                nid = frontier.pop()
                if nid in seen or nid not in self._nodes:
                    continue
                seen.add(nid)
                to_delete.append(nid)
                frontier.extend(self._nodes[nid].children_ids)

            for nid in to_delete:
                self._nodes.pop(nid, None)

        return to_delete

    def add_user_feedback(self, node_id: str, text: str, *, _sync: bool = True) -> bool:
        """[goal_cron_feedback_and_output_policy_plan.md Track B] 用户对某个
        Goal/Objective「提意见」，持久化合入该节点的 description，此后所有
        基于这个节点派生的执行都会带上——区别于 ObjectiveExecutor.inject_guidance()
        只对下一次提交的单个 step 生效一次就清空的临时语义。

        _sync=True 时，如果该节点是绑定了 recurring CronJob 的 Goal，会额外把
        同一条意见同步到 CronScheduler 一侧（见 CronScheduler.add_user_feedback()）；
        联动调用时对方内部传 _sync=False，跳过继续往外联动这一步，防止两边
        互相调用形成死循环。
        """
        if not text or not text.strip():
            return False
        cron_job_id = None
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            text = text.strip()
            node.user_feedback.append({"text": text, "at": time.time()})
            stamp = f"[用户意见 {time_utils.ts_to_str(time.time())}] {text}"
            node.description = (
                f"{node.description}\n\n{stamp}" if node.description else stamp
            )
            node.last_touched_at = time.time()
            if _sync and node.is_goal and node.recurring and node.recurrence_cron_job_id:
                cron_job_id = node.recurrence_cron_job_id
        if cron_job_id:
            try:
                from mini_agent.evolution.cron_scheduler import CronScheduler
                scheduler = CronScheduler(self._paths)
                scheduler.load()
                scheduler.add_user_feedback(cron_job_id, text, _sync=False)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(
                    _mini_agent_exc,
                    where="mini_agent.perception.goal_backlog.GoalBacklog.add_user_feedback",
                )
        return True

    def effective_context(self, node_id: str) -> str:
        """[goal_cron_feedback_and_output_policy_plan.md 4.3] 执行侧兜底：向上
        遍历 parent_id 链，拼出从根 Goal 到当前节点的完整说明链（含用户意见，
        因为意见已经合入 description）。即使某次创建 Objective 时忘了传
        description，执行侧仍能通过这个方法补上父级说明。
        """
        chain: list[str] = []
        node = self._nodes.get(node_id)
        seen: set[str] = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            if node.description:
                chain.append(node.description)
            node = self._nodes.get(node.parent_id) if node.parent_id else None
        return "\n\n".join(reversed(chain))

    # ── [goal_cron_binding_plan.md Track A/C] 周期性 Goal 支持 ─────────────────

    def set_recurrence(
        self, goal_id: str, recurring: bool, cron_job_id: Optional[str] = None
    ) -> bool:
        """写回 Goal 的周期性绑定状态。由 goal_cron_bridge.make_goal_recurring()/
        stop_goal_recurrence() 调用，不单独暴露给其它调用方——绑定/解绑必须同时
        改 CronJob 那一侧，业务逻辑收敛在 goal_cron_bridge，这里只做纯字段写入。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node or not node.is_goal:
                return False
            node.recurring = recurring
            node.recurrence_cron_job_id = cron_job_id if recurring else None
            node.last_touched_at = time.time()
        return True

    def record_cycle_completed(self, goal_id: str, child_id: str, note: str = "") -> bool:
        """[Track C] 周期性 Goal 的某一轮子 Objective 进入终态后调用一次：
        cycle_count += 1，并把摘要追加进 progress_notes（不覆盖，保留历史轮次的
        简短记录，便于用户回看"这个 recurring Goal 每轮都干了什么"）。

        child_id 必须已经记录在 reaped_cycle_child_ids 里才算"计过数"——
        reap_finished_cycles() 在调用本方法前会先检查这一点，这里再存一次纯粹是
        防御性的（避免未来有其它调用方跳过检查直接调用本方法导致重复计数）。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node or not node.is_goal:
                return False
            if child_id in node.reaped_cycle_child_ids:
                return False
            node.cycle_count += 1
            node.reaped_cycle_child_ids.append(child_id)
            if note:
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
                line = f"[{stamp}] 第 {node.cycle_count} 轮：{note[:80]}"
                node.progress_notes = (
                    f"{node.progress_notes}\n{line}" if node.progress_notes else line
                )
            node.last_touched_at = time.time()
        return True

    def append_progress_note(self, node_id: str, line: str) -> bool:
        """[goal_cron_visibility_and_intervention_improvement_plan.md Track B]
        追加一行带时间戳的 progress_notes，不覆盖已有内容。跟
        record_cycle_completed() 里内联的追加逻辑是同一种格式，抽出来是因为
        "跳过本轮"这类用户主动干预动作也需要留痕，但不属于"完成一轮"，
        不应该复用 record_cycle_completed()（那个会推进 cycle_count）。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
            entry = f"[{stamp}] {line}"
            node.progress_notes = (
                f"{node.progress_notes}\n{entry}" if node.progress_notes else entry
            )
            node.last_touched_at = time.time()
        return True

    def archive_finished_cycle_children(self, goal_id: str, keep_recent: int = 20) -> int:
        """[goal_cron_visibility_and_intervention_improvement_plan.md Track D]
        周期性 Goal 跑了很多轮之后，`children_ids`/`goals.json` 会无限增长。
        本方法只处理"已经被 record_cycle_completed() 计过数"的终态子节点
        （reaped_cycle_child_ids 里的），按 id 出现顺序（即完成顺序，因为
        reaped_cycle_child_ids 是按 reap 到的先后 append 的）保留最近
        keep_recent 个，更早的从 _nodes/children_ids 中移除，追加写入
        sidecar 文件 `<workdir>/goal_cycle_archive.jsonl`（每行一条完整
        GoalNode.to_dict()，只追加不改写，归档后的节点视为不再变化）。

        返回本次归档的节点数量。不影响 cycle_count/reaped_cycle_child_ids
        本身的计数语义——只是把节点从"热数据"搬到"冷数据"，历史轮次仍可
        通过 jsonl 文件回看。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node or not node.is_goal or not node.recurring:
                return 0
            reaped = node.reaped_cycle_child_ids
            if len(reaped) <= keep_recent:
                return 0
            to_archive_ids = reaped[: len(reaped) - keep_recent]
            archived_nodes = []
            for child_id in to_archive_ids:
                child = self._nodes.get(child_id)
                if child is None:
                    continue
                archived_nodes.append(child.to_dict())
                del self._nodes[child_id]
                if child_id in node.children_ids:
                    node.children_ids.remove(child_id)
            if not archived_nodes:
                return 0
            archive_path = self._paths.workdir_dir / "goal_cycle_archive.jsonl"
            try:
                with open(archive_path, "a", encoding="utf-8") as f:
                    for d in archived_nodes:
                        f.write(json.dumps(d, ensure_ascii=False) + "\n")
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog.archive_finished_cycle_children')
                # 写归档文件失败时不删节点——宁可 goals.json 继续膨胀，
                # 也不能丢数据（archived_nodes 已经从 _nodes 摘除的操作
                # 发生在写文件之前，这里失败要把节点加回去）。
                for d in archived_nodes:
                    self._nodes[d["id"]] = GoalNode.from_dict(d)
                    if d["id"] not in node.children_ids:
                        node.children_ids.append(d["id"])
                return 0
            node.reaped_cycle_child_ids = reaped[len(reaped) - keep_recent:]
        return len(archived_nodes)

    def update_fields(self, node_id: str, **fields) -> Optional[GoalNode]:
        """在锁保护下批量更新节点的任意字段（如 status/priority/progress_notes）。

        用于"一次性改好几个字段再存"的场景（例如 accept/PATCH 接口），
        避免每个字段单独调用一次 set_status/update_progress 时中间态被
        其他进程读到，也避免调用方自己直接改 node 属性再手动 save()
        （那样不会重新加载磁盘最新状态，等于绕开了并发保护）。

        内部会先重新加载磁盘最新状态，在最新数据基础上应用这些字段修改再落盘。
        返回更新后的节点；节点不存在时返回 None（不做任何修改）。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return None
            for key, value in fields.items():
                setattr(node, key, value)
            node.last_touched_at = time.time()
        return self._nodes.get(node_id)

    def reparent_node(self, node_id: str, new_parent_id: Optional[str]) -> Optional[GoalNode]:
        """[goal_tree_system_plan.md §4.4/阶段四遗留项] 把一个节点重新挂载到
        另一个父节点下（看板"🌳 目标树"的"改父节点"下拉框对应的后端能力，
        方案原文把"拖拽"排除在外、明确改成下拉选择新父节点）。

        校验规则跟 `add_node()` 完全一致，走同一个 `validate_node_hierarchy()`：
        新父节点的 level 顺序不能跟 `node_id` 自身 level 倒挂；
        `level="ultimate"` 的根节点不允许被 reparent（`new_parent_id`
        非空时直接拒绝——根节点定义上 `parent_id` 必须为空、全局唯一，
        改父节点这个操作对根节点没有意义）。

        额外校验"不能挂到自己的子孙节点下"（否则会在树里形成环，
        `get_tree()` 虽然对环有防御性处理不会死循环，但那只是兜底，
        正常数据不应该允许产生环）——沿 `new_parent_id` 往上遍历
        `parent_id` 链，如果途中遇到 `node_id` 本身，说明 `new_parent_id`
        是 `node_id` 的子孙，拒绝。

        `new_parent_id=None` 表示"提升为根节点"，只有非 `ultimate`
        节点可以这样做，且会跳过父子层级校验（没有父节点就无所谓层级
        顺序），但结果节点会脱离原有树、不再能通过全局根节点遍历到——
        `get_tree()` 只从 `ultimate` 根出发，一个 `parent_id=None` 但
        `level != "ultimate"` 的节点会变成孤儿，UI 侧不建议提供"提升为
        根节点"这个选项（看板下拉框留空态直接禁用改父节点操作），这里
        仅在后端保留该语义完整性、不强行拒绝，供脚本/测试场景使用。

        `node_id`/`new_parent_id`（非空时）不存在、`node_id` 是
        `level="ultimate"`、层级校验不通过、或会形成环时都返回 `None`，
        不做任何修改。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if node is None:
                return None
            if node.level == "ultimate":
                return None
            if new_parent_id is not None:
                new_parent = self._nodes.get(new_parent_id)
                if new_parent is None:
                    return None
                if new_parent_id == node_id:
                    return None
                # 环检测：沿 new_parent_id 往上走 parent_id 链，如果碰到
                # node_id 本身，说明 new_parent 是 node 的子孙，不能把
                # node 挂到自己子孙下面（`visited` 只是防御已有环导致的
                # 死循环，正常数据不会触发）。
                cursor_id = new_parent_id
                visited: set = set()
                while cursor_id is not None:
                    if cursor_id == node_id:
                        return None
                    if cursor_id in visited:
                        break
                    visited.add(cursor_id)
                    cursor = self._nodes.get(cursor_id)
                    cursor_id = cursor.parent_id if cursor else None
                err = validate_node_hierarchy(node.level, new_parent.level)
                if err:
                    return None
            old_parent_id = node.parent_id
            if old_parent_id and old_parent_id in self._nodes:
                old_parent = self._nodes[old_parent_id]
                old_parent.children_ids = [
                    cid for cid in old_parent.children_ids if cid != node_id
                ]
            node.parent_id = new_parent_id
            if new_parent_id is not None:
                new_parent = self._nodes[new_parent_id]
                if node_id not in new_parent.children_ids:
                    new_parent.children_ids.append(node_id)
            node.last_touched_at = time.time()
        return self._nodes.get(node_id)

    # ── C1：长期方向分组（personal_researcher_and_coach_capability_gap_plan.md）──
    # Direction 是纯展示聚合，不参与 GoalJudge 判定、不影响执行调度，见
    # Direction 类注释。这里只提供最基础的增删查改 + 分组关联，不引入
    # 任何置信度/衰减一类的决策逻辑。

    def add_direction(self, title: str, description: str = "") -> Direction:
        """新建一个长期方向分组。走 `_locked()`，与 Goal 写入同一把锁。"""
        with self._locked():
            direction = Direction(
                id=f"dir_{uuid.uuid4().hex[:8]}",
                title=title,
                created_at=time.time(),
                description=description,
            )
            self._directions[direction.id] = direction
        return direction

    def list_directions(self) -> list[Direction]:
        """返回全部长期方向，按创建时间升序（早创建的排前面，跟目标
        看板"先看到老方向"的直觉一致）。"""
        return sorted(self._directions.values(), key=lambda d: d.created_at)

    def get_direction(self, direction_id: str) -> Optional[Direction]:
        return self._directions.get(direction_id)

    def rename_direction(self, direction_id: str, title: str, description: Optional[str] = None) -> bool:
        with self._locked():
            direction = self._directions.get(direction_id)
            if not direction:
                return False
            direction.title = title
            if description is not None:
                direction.description = description
        return True

    def delete_direction(self, direction_id: str) -> bool:
        """删除一个长期方向分组。已挂在这个方向下的 Goal 不会被删除或
        阻塞任何执行——只是把它们的 `direction_id` 清空为 None（未分组），
        与"删除父节点不应该级联破坏子节点的执行状态"这一贯取舍一致。"""
        with self._locked():
            if direction_id not in self._directions:
                return False
            del self._directions[direction_id]
            for node in self._nodes.values():
                if node.direction_id == direction_id:
                    node.direction_id = None
        return True

    def assign_direction(self, goal_id: str, direction_id: Optional[str]) -> bool:
        """把一个 Goal（通常是 level="goal" 的节点，但不强制校验——纯展示
        聚合，Objective 挂上去也不影响任何执行逻辑）关联到某个长期方向。
        direction_id=None 表示取消分组。"""
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node:
                return False
            if direction_id is not None and direction_id not in self._directions:
                return False
            node.direction_id = direction_id
            node.last_touched_at = time.time()
        return True

    def goals_by_direction(self) -> dict[Optional[str], list[GoalNode]]:
        """按 direction_id 对全部 Goal（level="goal"）分组，供看板"按长期
        方向聚合视图"直接消费。键为 direction_id（未分组的 Goal 归到
        None 键下），值按 priority 降序排列。不过滤 status——聚合视图
        需要看到完整状态分布，跟 `all_nodes()` 的取舍一致。"""
        groups: dict[Optional[str], list[GoalNode]] = {}
        for node in self._nodes.values():
            if not node.is_goal:
                continue
            groups.setdefault(node.direction_id, []).append(node)
        for key in groups:
            groups[key].sort(key=lambda n: n.priority, reverse=True)
        return groups

    # ── P5：外部信号驱动 Goal 执行 ────────────────────────────────────────────
    # 设计背景见 next_doc/watchlist_notification_goal_design.md §3.5/§4.4。

    def attach_external_context(self, goal_id: str, item: dict, max_keep: int = 20) -> bool:
        """把一条外部事件摘要 append 进 GoalNode.external_context，只保留
        最近 max_keep 条（超出部分从队首丢弃）。不改变 Goal 的
        status/priority，纯粹是"信息至少要能被看到"这一步（§4.4），
        由 GoalRelevanceEngine Stage② 在 relevant=true 时无条件调用。

        走 `_locked()` 临界区（与 set_status/update_progress 同一把锁），
        避免跟看板手动编辑 Goal 的写入路径产生丢失更新（§9.1 #3）。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node:
                return False
            node.external_context.append(item)
            if len(node.external_context) > max_keep:
                node.external_context = node.external_context[-max_keep:]
            node.last_touched_at = time.time()
        return True

    def try_advance_goal(self, goal_id: str, cooldown_seconds: float) -> "AdvanceDecision":
        """在冷却期检查通过后"拉起"一个 Goal（§4.4）：
        - status != active（如 paused）→ set_status(active) + progress_notes
          追加一笔"因外部信号被自动重新激活"的记录；
        - status == active → 不在这里调用 enqueue_turn（那是
          GoalRelevanceEngine 的职责，需要 InputQueue 依赖，本方法只负责
          纯 GoalBacklog 内部状态判断/变更），只返回 action="enqueue_turn"
          让调用方自己去 enqueue。

        无论是否真的执行了"拉起"，只要不在冷却期内，都会更新
        `last_external_advance_at = now`（见 §4.4：\"执行了拉起动作之后
        更新时间戳\"——`enqueue_turn` 分支视为\"提交动作\"本身已经算一次
        拉起，不等 agent 执行完才算数）。

        冷却期内直接返回 action="cooldown_skip"，不做任何写入。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node:
                return AdvanceDecision(action="not_found", goal_id=goal_id)

            now = time.time()
            if now - node.last_external_advance_at < cooldown_seconds:
                return AdvanceDecision(
                    action="cooldown_skip",
                    goal_id=goal_id,
                    remaining_seconds=cooldown_seconds - (now - node.last_external_advance_at),
                )

            node.last_external_advance_at = now
            if node.status != "active":
                node.status = "active"
                note = f"[{time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}] 因外部信号被自动重新激活"
                node.progress_notes = (
                    f"{node.progress_notes}\n{note}" if node.progress_notes else note
                )
                node.last_touched_at = now
                return AdvanceDecision(action="reactivated", goal_id=goal_id)

            node.last_touched_at = now
            return AdvanceDecision(action="enqueue_turn", goal_id=goal_id)

    # ── 从 WorkThread 拆解下一个 Task ─────────────────────────────────────────

    def next_task_description(
        self,
        llm_helper=None,
        *,
        workdir_knowledge=None,
    ) -> Optional[tuple[str, str]]:
        """
        从最高优先级 active Objective 拆解出下一个可执行 Task 描述。
        返回 (objective_id, task_description) 或 None。

        拆解逻辑：
        1. 取最高优先级 active Objective
        2. 若有 work_thread_ref，从 WorkThread 的 next_suggested 获取提示
        3. 用轻量 LLM 调用生成具体 Task 描述（若有 llm_helper）
           否则直接用 Objective.title 作为 Task 描述

        llm_helper — 需实现 .ask(prompt, ...) -> str，通常传入
        Agent.llm_helper（见 llm/service.py::LLMHelper），
        天然复用主 agent 当前的 provider/model 与统一重试策略。
        """
        objectives = self.active_objectives()
        if not objectives:
            return None

        obj = objectives[0]  # 最高优先级

        # 获取 WorkThread 进展提示
        next_suggested = ""
        if obj.work_thread_ref and workdir_knowledge:
            try:
                wt = workdir_knowledge.get_work_thread(obj.work_thread_ref)
                if wt:
                    next_suggested = wt.next_suggested or ""
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog')
                pass

        # 构建 Task 描述
        if next_suggested:
            base_desc = f"{obj.title}\n\n[来自工作线索的提示] {next_suggested}"
        else:
            base_desc = obj.title

        # 有 LLM 时做一次轻量拆解
        if llm_helper:
            try:
                task_desc = self._llm_decompose(llm_helper, obj, next_suggested)
                if task_desc:
                    return obj.id, task_desc
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog.next_task_description')
                pass  # 降级为直接使用 title

        return obj.id, base_desc

    def _llm_decompose(self, llm_helper, obj: GoalNode, next_suggested: str) -> Optional[str]:
        """
        轻量 LLM 调用：将 Objective 拆解为具体可执行的 Task 描述。
        参照 Stage 4.2 timeline.jsonl 反思调用的独立轻量调用模式。

        历史提示：此函数曾直接接收裸 LLMClient 并调用
        `llm_client.chat(messages=msgs, max_tokens=200)`——签名不匹配，
        实际每次都抛 TypeError 被吞掉，此方法一直静默返回 None。
        改用 LLMHelper.ask() 后签名统一、自带重试。
        """
        prompt = f"""将以下目标拆解为一个具体可在单次 Task 中完成、有明确验收标准的任务描述。

目标：{obj.title}
当前进展：{obj.progress_notes or '暂无'}
工作建议：{next_suggested or '暂无'}

要求：
1. 输出一句话的具体任务描述（不超过 100 字）
2. 任务必须可在单次执行中完成
3. 有明确的完成标准

只输出任务描述，不要其他内容。"""

        try:
            text = llm_helper.ask(prompt).strip()
            if text:
                return text
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog')
            pass
        return None


# ── 模块级便捷函数 ────────────────────────────────────────────────────────────

def default_goal_to_objectives(
    llm_helper,
    title: str,
    description: str = "",
    max_n: int = 3,
) -> list[str]:
    """LLM 拆解：把一个 Goal 拆成多个可独立执行的 Objective 标题。

    与 GoalBacklog._llm_decompose（Objective → 单个 Task）是同一种"轻量
    LLM 调用做结构化拆解"模式，但拆解方向不同：这里是 Goal → 多个
    Objective，每个 Objective 之后还会再被 ObjectiveExecutor 各自拆成
    3-7 个 Step。

    调用方（AutonomousLoop）负责在拿到返回值之后再决定是否要做 1:1 镜像
    降级——这个函数本身失败/无输出时只返回空列表，不做任何降级决定。

    llm_helper — 需实现 .ask(prompt, ...) -> str（同 LLMHelper.ask）。
    """
    prompt = f"""将以下目标拆解为 1~{max_n} 个可以独立执行、彼此边界清晰的子目标。

目标标题：{title}
目标描述：{description or '（无）'}

要求：
1. 每个子目标一行，不要编号、不要多余符号
2. 子目标要具体到"可以单独作为一项工作去推进"，不要重复目标标题本身
3. 如果目标本身已经足够具体、拆不出多个子目标，只输出 1 行也可以
4. 不要输出子目标数量之外的任何说明文字

只输出子目标标题，每行一个。"""

    try:
        text = llm_helper.ask(prompt)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.default_goal_to_objectives')
        return []

    if not text:
        return []
    lines = [ln.strip(" -•\t") for ln in text.splitlines()]
    titles = [ln for ln in lines if ln]
    return titles[:max_n]


def load_goal_backlog(paths: AgentPaths) -> GoalBacklog:
    """加载并返回 GoalBacklog（便捷函数）。"""
    gb = GoalBacklog(paths)
    gb.load()
    return gb


# [goal_tree_system_plan.md §4.3/§五 阶段三] sys:goal_tree_focus_recompute
# 的 job id，daemon 启动时补注册，见 ensure_goal_tree_focus_recompute_job()。
JOB_ID_FOCUS_RECOMPUTE = "sys:goal_tree_focus_recompute"


def ensure_goal_tree_focus_recompute_job(
    backlog: GoalBacklog, cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:goal_tree_focus_recompute`
    （§4.3，纯规则计算、零 LLM 成本、本地回调，与
    `failure_pattern_store.ensure_failure_pattern_aggregation_job()` 同构）。

    每小时（`interval:3600`，比停滞巡检的 24 小时短很多——方案原文"这个
    只是纯规则计算、成本很低，可以比 LLM 驱动的巡检跑得更勤"）自底向上
    重算全树的 `current_focus_ids`，直接调用
    `GoalBacklog.recompute_current_focus_tree()`（默认从全局根节点开始）。

    返回值同其它 `ensure_*_job()`：`True` 表示这次调用新建了该 job（此前
    不存在），`False` 表示 job 已存在（不会覆盖用户可能已经手动改过的
    schedule/enabled）。
    """
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID_FOCUS_RECOMPUTE not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID_FOCUS_RECOMPUTE,
        name="目标树现阶段焦点重算",
        schedule="interval:3600",
        description=(
            "自底向上重算目标树上 ultimate/domain/stage 三层非叶子节点的 "
            "current_focus_ids（优先级 + 停滞老化加成，叠加用户手动 pin），"
            "零 LLM 成本、纯规则计算，每小时一次。"
        ),
        tags=["maintenance", "goal_tree"],
    )

    def _handler(job: "CronJob") -> bool:
        backlog.recompute_current_focus_tree()
        return True

    cron_scheduler.register_local_handler(JOB_ID_FOCUS_RECOMPUTE, _handler)
    return newly_added


__all__ = [
    "GoalNode",
    "GoalBacklog",
    "AdvanceDecision",
    "load_goal_backlog",
    "default_goal_to_objectives",
    "LEVEL_ORDER",
    "validate_node_hierarchy",
    "compute_aging_boost",
    "compute_current_focus",
    "DEFAULT_FOCUS_TOP_N",
    "JOB_ID_FOCUS_RECOMPUTE",
    "ensure_goal_tree_focus_recompute_job",
]
