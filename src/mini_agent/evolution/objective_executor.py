"""
evolution/objective_executor.py — Objective 持续执行引擎

将 GoalBacklog 中的 Objective 拆解为多步 ExecutionStep，
依次通过 InputQueue 提交，每步完成后自动推进到下一步。

与现有架构的集成：
  - 启动时调用轻量 LLM 将 Objective.title 拆解为 3-7 个 Step
  - 每个 Step 通过 submit_fn(message, "autonomous", meta) 提交
  - AgentRunner 在 turn 结束时调用 on_turn_done(turn_id, result_summary)
  - 所有状态持久化到 .agent/objective_executions.json

并发控制：
  - 同时最多运行 MAX_CONCURRENT_OBJECTIVES 个 Objective（默认 2）
  - 每个 Objective 串行执行 Step（不并行步骤，保证因果性）
  - ResourceArbiter.can_run_autonomous() 在每个 Step 提交前检查

失败处理：
  - 单 Step 最多重试 MAX_STEP_RETRIES 次（默认 2）
  - 超过重试次数 → Objective 状态改为 failed，记录 digest
  - agent 可以通过 /goals 命令重置后重新开始

产出物传递（Track G）：
  - 每个 step 完成后优先尝试从其调用过的写文件类工具记录里提取真实产出
    路径（artifacts_from_tools_fn，见 api/routes.py 的
    _extract_tool_write_paths），拿不到时退化为解析回复文本里的
    `[ARTIFACTS]` 标记（artifacts_parse_fn）。
"""

from __future__ import annotations

import json
import os
import re as _re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.role_agents.stuck_detector import StuckSignal as _GuardianStuckSignal

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode


MAX_CONCURRENT_OBJECTIVES = 2
MAX_STEP_RETRIES = 2
MAX_STEPS_PER_OBJECTIVE = 8
DEFAULT_STEP_TIMEOUT_SECONDS = 600  # 10 分钟，超时算失败

# [看板与自主性改进方案 Track C] 声明不出具体路径时使用的哨兵路径。
# 任何两个 step 只要都落在这个哨兵上，就视为冲突——即"保守退化为串行"：
# 拆解不出可靠的路径信息时，宁可牺牲并行度也不允许两个 Objective 同时写文件。
_UNKNOWN_PATH_SENTINEL = "__unknown__"


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class ExecutionStep:
    step_id: str
    step_index: int                 # 0-based
    description: str                # 提交给 agent 的任务文本
    # pending | running | done | failed | blocked
    # [Track C] "blocked"：本步骤声明的路径与其他正在运行的 Objective 冲突，
    # 暂不提交，等占用方释放后由 retry_blocked_steps() 重新尝试——不算失败，
    # 不计入 retry_count。
    status: str = "pending"
    turn_id: Optional[str] = None   # 对应的 InputQueue turn_id
    result_summary: str = ""        # agent 完成后写回的摘要
    started_at: float = 0.0
    finished_at: float = 0.0
    retry_count: int = 0
    error_msg: str = ""
    # [Track C] 本步骤预期会涉及的文件/目录路径（LLM 声明，可能不精确，
    # 宁可保守多列）。为空列表时统一按 [_UNKNOWN_PATH_SENTINEL] 处理。
    paths: list[str] = field(default_factory=list)
    # [Track G] 本步骤实际产出/修改的文件路径，供后续步骤引用具体路径而
    # 非模糊指代。优先从这一步调用过的写文件类工具（write_file/
    # create_file/patch_file/patch_file_simple）记录里直接提取真实路径
    # 参数（Track G 深化）；拿不到时退化为解析 agent 回复里的
    # `[ARTIFACTS] path1, path2` 标记（Track G 退化版，依赖模型自觉遵守
    # 固定格式）。
    artifacts: list[str] = field(default_factory=list)
    # [Track D] 用户通过 inject_guidance() 追加的补充上下文，将在下一次
    # 提交该 step 时拼进 prompt；提交后清空，避免重复注入。
    pending_guidance: str = ""
    # [Track E] 本步骤最近一次实际提交给 agent 的完整 prompt 文本（含
    # 前序步骤上下文/重试原因/用户插话等拼装后的内容）。用途：看板"查看
    # 详情"功能需要从 agent 的会话历史里精确定位这一步对应的
    # user_input 消息、进而截取它到下一条 user_input 之间的完整
    # tool_call/tool_result 序列——单纯用 description 做匹配容易和其他
    # 步骤/重试混淆，拼装后的完整文本才是真正写进历史的那一条，能做
    # 精确匹配。每次重新提交（含重试）都会覆盖为最新一次的文本。
    submitted_message: str = ""

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "description": self.description,
            "status": self.status,
            "turn_id": self.turn_id,
            "result_summary": self.result_summary,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_count": self.retry_count,
            "error_msg": self.error_msg,
            "paths": self.paths,
            "artifacts": self.artifacts,
            "pending_guidance": self.pending_guidance,
            "submitted_message": self.submitted_message,
        }

    @staticmethod
    def from_dict(d: dict) -> "ExecutionStep":
        return ExecutionStep(
            step_id=d.get("step_id", ""),
            step_index=d.get("step_index", 0),
            description=d.get("description", ""),
            status=d.get("status", "pending"),
            turn_id=d.get("turn_id"),
            result_summary=d.get("result_summary", ""),
            started_at=d.get("started_at", 0.0),
            finished_at=d.get("finished_at", 0.0),
            retry_count=d.get("retry_count", 0),
            error_msg=d.get("error_msg", ""),
            paths=list(d.get("paths", []) or []),
            artifacts=list(d.get("artifacts", []) or []),
            pending_guidance=d.get("pending_guidance", ""),
            submitted_message=d.get("submitted_message", ""),
        )


@dataclass
class ObjectiveExecution:
    execution_id: str
    objective_id: str
    objective_title: str
    steps: list[ExecutionStep] = field(default_factory=list)
    current_step_idx: int = 0
    # pending | running | paused | completed | failed | cancelled
    # [Track D] "cancelled"：用户在看板上主动终止，区别于 "failed"（系统判定
    # 执行失败）——不再重试、不再推进，但不代表"做错了"。
    status: str = "pending"
    started_at: float = 0.0
    finished_at: float = 0.0
    progress_notes: str = ""
    # [Track F 第二部分] 是否已经为这个 execution 尝试过一次"重新分解剩余
    # 步骤"。每个 execution 只允许尝试一次，避免"分解出的新步骤仍然失败 →
    # 又重新分解"无限循环，最终变成一种更隐蔽的资源浪费方式。
    redecompose_attempted: bool = False
    # [goal_execution_fairness_improvement_plan.md P4] 当前"执行片段"
    # （slice）的起点：开始时间戳 + 起始 step 下标。start() 时初始化一次；
    # 每次从 paused_for_fairness 恢复时（resume_fairness()）重新赋值。
    # 只用来计算"这一片跑了多久/多少步"，不代表整个 Objective 的起止。
    fairness_slice_started_at: float = 0.0
    fairness_slice_start_step: int = 0

    @property
    def current_step(self) -> Optional[ExecutionStep]:
        if 0 <= self.current_step_idx < len(self.steps):
            return self.steps[self.current_step_idx]
        return None

    @property
    def progress_ratio(self) -> tuple[int, int]:
        """返回 (完成步数, 总步数)。"""
        done = sum(1 for s in self.steps if s.status == "done")
        return done, len(self.steps)

    @property
    def progress_str(self) -> str:
        done, total = self.progress_ratio
        return f"{done}/{total}"

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "objective_id": self.objective_id,
            "objective_title": self.objective_title,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_idx": self.current_step_idx,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress_notes": self.progress_notes,
            "redecompose_attempted": self.redecompose_attempted,
            "fairness_slice_started_at": self.fairness_slice_started_at,
            "fairness_slice_start_step": self.fairness_slice_start_step,
        }

    @staticmethod
    def from_dict(d: dict) -> "ObjectiveExecution":
        ex = ObjectiveExecution(
            execution_id=d.get("execution_id", ""),
            objective_id=d.get("objective_id", ""),
            objective_title=d.get("objective_title", ""),
            current_step_idx=d.get("current_step_idx", 0),
            status=d.get("status", "pending"),
            started_at=d.get("started_at", 0.0),
            finished_at=d.get("finished_at", 0.0),
            progress_notes=d.get("progress_notes", ""),
            redecompose_attempted=bool(d.get("redecompose_attempted", False)),
            fairness_slice_started_at=d.get("fairness_slice_started_at", 0.0),
            fairness_slice_start_step=d.get("fairness_slice_start_step", 0),
        )
        ex.steps = [ExecutionStep.from_dict(s) for s in d.get("steps", [])]
        return ex


# ── ObjectiveExecutor 主类 ────────────────────────────────────────────────────

class ObjectiveExecutor:
    """
    Objective 持续执行引擎。

    由 AutonomousLoop._tick_maintenance() 调用：
      - start(objective) → 拆解为 steps，提交第一步
      - on_turn_done(turn_id, summary) → 推进当前 step，提交下一步
      - pause_all() → ResourceArbiter 发现用户优先时调用
      - resume() → 用户消息处理完后恢复
    """

    VERSION = 1

    def __init__(
        self,
        paths: "AgentPaths",
        submit_fn: Optional[Callable[[str, str, dict], Optional[str]]] = None,
        llm_decompose_fn: Optional[Callable] = None,
        on_progress_fn: Optional[Callable] = None,
        declare_paths_fn: Optional[Callable[[str], list]] = None,
        goal_backlog: Optional["GoalBacklog"] = None,
        llm_redecompose_fn: Optional[Callable] = None,
        artifacts_parse_fn: Optional[Callable[[str], list]] = None,
        artifacts_from_tools_fn: Optional[Callable[[str], list]] = None,
        cfg: Optional["AppConfig"] = None,
    ) -> None:
        """
        submit_fn         — 提交 Task：(message, initiator, meta) -> turn_id | None
        llm_decompose_fn  — 拆解 Objective：(objective) -> list[str] 步骤描述列表
        on_progress_fn    — 进度回调：(execution) -> None，用于 SSE 推流
        declare_paths_fn  — [Track C] 声明单个 step 预期涉及的路径：
                             (step_description) -> list[str]。未提供或调用失败/
                             返回空列表时，退化为 [_UNKNOWN_PATH_SENTINEL]（保守
                             串行化，见模块头部说明）。
        goal_backlog      — [Track B] 提供后，Objective 完成/失败/取消时会单向
                             同步回写对应 GoalNode.status（completed/failed/
                             cancelled），不提供则只更新 execution 自身状态
                             （向后兼容旧调用方）。
        llm_redecompose_fn — [Track F 第二部分] 某个 step 耗尽重试次数后，
                             先尝试"重新分解剩余步骤"而不是直接判 Objective
                             failed：(objective_title, completed_summaries,
                             remaining_descs, failure_reason) -> list[str]
                             新的步骤描述列表（替换从当前失败点开始的剩余
                             步骤）。未提供、调用异常、或返回空列表/单一元素
                             （无法拆出比原来更细的步骤，重新分解没有意义）
                             时，退化为原有行为——直接判 Objective failed。
                             每个 execution 只允许尝试一次（见
                             ObjectiveExecution.redecompose_attempted）。
        artifacts_parse_fn — [Track G 退化版] 从 agent 的 step 回复文本里
                             正则解析 `[ARTIFACTS] path1, path2` 标记：
                             (result_summary) -> list[str]。依赖模型自觉
                             按固定格式声明，不可靠——只在
                             artifacts_from_tools_fn 未提供、或对某一步
                             解析不出任何路径时，作为兜底使用。
        artifacts_from_tools_fn — [Track G 深化，优先于 artifacts_parse_fn]
                             从这一步实际调用过的写文件类工具（write_file/
                             create_file/patch_file/patch_file_simple）的
                             调用记录里，直接提取真实路径参数：
                             (step.submitted_message) -> list[str]。不依赖
                             模型自觉声明格式，只要模型确实调用了写文件
                             工具就一定能拿到。未提供、调用异常、或返回
                             空列表（比如这一步压根没写文件，或只是查询类
                             步骤）时，退化为 artifacts_parse_fn 的正则解析
                             结果；两者都拿不到时 ExecutionStep.artifacts
                             保持空列表（向后兼容，不影响现有行为）。
        cfg                — [Track K] 提供后，`can_start_new()` 改为参考
                             `cfg.autonomy` 里的并发数自适应配置动态计算
                             生效并发上限；不提供、或 `cfg.autonomy` 不存在
                             /关闭自适应时，退化为改造前的行为——恒定使用
                             模块级常量 MAX_CONCURRENT_OBJECTIVES（=2）。
        """
        self._paths = paths
        self._submit_fn = submit_fn
        self._llm_decompose_fn = llm_decompose_fn
        self._on_progress_fn = on_progress_fn
        self._declare_paths_fn = declare_paths_fn
        self._goal_backlog = goal_backlog
        self._llm_redecompose_fn = llm_redecompose_fn
        self._artifacts_parse_fn = artifacts_parse_fn
        self._artifacts_from_tools_fn = artifacts_from_tools_fn
        self._cfg = cfg
        # [Track J] 资源门控降级标志——由 AutonomousLoop 每次 tick 根据
        # ResourceArbiter.gating_state() 的结果调用 set_gating_degraded()
        # 设置，不持久化（只反映"此刻"的资源状况，下次 tick 会重新计算），
        # 默认 False（不降级），保证未接入 Track J 的调用方行为不变。
        self._gating_degraded: bool = False
        self._executions: dict[str, ObjectiveExecution] = {}  # execution_id → ex
        self._turn_to_exec: dict[str, tuple[str, int]] = {}   # turn_id → (execution_id, step_idx)
        self._exec_path = paths.workdir_dir / "objective_executions.json"
        # [Track C] execution_id → 该 execution 当前 running step 声明的路径集合。
        # 只在内存里维护（不持久化）——重启后 reap_stale_steps()/正常推进会
        # 重新提交并重新声明，不需要跨进程重启保持这份状态。
        self._active_step_paths: dict[str, set] = {}
        # [daemon_autonomous_state_recovery_plan.md 阶段四 / P2]
        # execution_id → GuardianRunner，只在开启 cfg.autonomy.guardian_mode_enabled
        # 时才会被真正创建/使用（见 _get_guardian()）；不持久化——重启后
        # 每个仍在跑的 execution 会在下一次 on_turn_done() 时惰性重建一个
        # 全新的 GuardianRunner，代价是重启瞬间"卡住检测的连续计数"会归零，
        # 这与其它内存态计数器（如 _active_step_paths）的取舍一致，不影响
        # 正确性，只是重启后需要重新累积几轮才能再次触发。
        self._guardians: dict[str, "GuardianRunner"] = {}

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        if not self._exec_path.exists():
            return
        try:
            data = json.loads(self._exec_path.read_text(encoding="utf-8"))
            for ed in data.get("executions", []):
                ex = ObjectiveExecution.from_dict(ed)
                if ex.execution_id:
                    self._executions[ex.execution_id] = ex
                    # 重建 turn_to_exec 索引
                    for step in ex.steps:
                        if step.turn_id and step.status == "running":
                            self._turn_to_exec[step.turn_id] = (ex.execution_id, step.step_index)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
            pass

    def save(self) -> None:
        self._exec_path.parent.mkdir(parents=True, exist_ok=True)
        active = [
            ex for ex in self._executions.values()
            if ex.status not in ("completed", "failed", "cancelled")
            or (time.time() - ex.finished_at) < 86400  # 保留 24h 已完成记录
        ]
        data = {
            "version": self.VERSION,
            "executions": [ex.to_dict() for ex in active],
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self._exec_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self._exec_path)

    # ── 主接口 ────────────────────────────────────────────────────────────────

    def running_count(self) -> int:
        return sum(1 for ex in self._executions.values() if ex.status == "running")

    def is_running(self, objective_id: str) -> bool:
        return any(
            ex.objective_id == objective_id and ex.status == "running"
            for ex in self._executions.values()
        )

    def _goal_id_of_objective(self, objective_id: str) -> Optional[str]:
        """[goal_execution_fairness_improvement_plan.md P1] 反查 objective_id
        所属的 Goal id（GoalNode.parent_id）。拿不到 goal_backlog 或找不到
        节点/父节点时返回 objective_id 自身——把它当成"自己就是一个独立
        分组"，不影响 max_concurrent_objectives_per_goal 以外的任何行为。
        """
        if self._goal_backlog is None:
            return objective_id
        try:
            node = self._goal_backlog.get(objective_id)
        except Exception:
            return objective_id
        if node is None or not node.parent_id:
            return objective_id
        return node.parent_id

    def running_count_for_goal(self, goal_id: str) -> int:
        """[goal_execution_fairness_improvement_plan.md P1] 统计当前
        status == "running" 且所属 Goal 为 goal_id 的 execution 数量，供
        AutonomousLoop._tick_maintenance() 做"同一 Goal 同时最多占用 N 个
        槽位"的判断。"""
        count = 0
        for ex in self._executions.values():
            if ex.status != "running":
                continue
            if self._goal_id_of_objective(ex.objective_id) == goal_id:
                count += 1
        return count

    def _should_yield_for_fairness(self, ex: "ObjectiveExecution", next_step_idx: int) -> bool:
        """[goal_execution_fairness_improvement_plan.md P4] 判断某个刚完成
        一步、准备提交下一步的 execution 是否应该改为主动让出槽位。

        条件（全部满足才让出）：
          1. `autonomy.fairness_time_slicing_enabled` 开启（默认关闭，未
             开启时本函数恒返回 False，行为与改造前完全一致）；
          2. 当前时间片已完成的 step 数 >= `fairness_yield_after_steps`，
             或者当前时间片已运行时长 >= `fairness_yield_after_seconds`；
          3. 按 P2 的公平排序，确实存在另一个"未在运行"的 Goal 排在自己
             前面——如果没有其它 Goal 在排队（比如就这一个 active Goal），
             让出槽位没有任何意义，白白多一次暂停/恢复的开销，所以不让出。
        """
        autonomy_cfg = getattr(self._cfg, "autonomy", None) if self._cfg is not None else None
        if autonomy_cfg is None or not getattr(autonomy_cfg, "fairness_time_slicing_enabled", False):
            return False
        if self._goal_backlog is None:
            return False

        k = getattr(autonomy_cfg, "fairness_yield_after_steps", 3)
        t = getattr(autonomy_cfg, "fairness_yield_after_seconds", 900.0)
        steps_done = next_step_idx - ex.fairness_slice_start_step
        slice_start = ex.fairness_slice_started_at or ex.started_at
        elapsed = time.time() - slice_start
        if steps_done < k and elapsed < t:
            return False

        own_goal_id = self._goal_id_of_objective(ex.objective_id)
        try:
            stale_days = getattr(self._cfg, "next_action_stale_days", 7.0) if self._cfg is not None else 7.0
            boost_per_day = getattr(autonomy_cfg, "fairness_aging_boost_per_day", 1.0)
            boost_max_days = getattr(autonomy_cfg, "fairness_aging_boost_max_days", 14.0)
            ranked = self._goal_backlog.active_objectives_fair_ranked(
                stale_days=stale_days,
                aging_boost_per_day=boost_per_day,
                aging_boost_max_days=boost_max_days,
            )
        except Exception:
            return False

        for cand in ranked:
            if cand.id == ex.objective_id:
                continue
            if self.is_running(cand.id):
                continue
            cand_goal_id = self._goal_id_of_objective(cand.id)
            if cand_goal_id != own_goal_id:
                return True
        return False

    def fairness_paused_objective_ids(self) -> list[str]:
        """[P4] 当前处于 paused_for_fairness 状态的 execution 所对应的
        objective_id 列表，供 AutonomousLoop 判断某个候选是否应该走
        resume_fairness() 而不是 start()。"""
        return [
            ex.objective_id
            for ex in self._executions.values()
            if ex.status == "paused_for_fairness"
        ]

    def resume_fairness(self, objective_id: str) -> bool:
        """[P4] 恢复一个因公平性让出槽位的 execution：从
        `current_step_idx`（断点）重新提交，不重新拆解、不丢失已完成的
        step。开启新的时间片计时。找不到对应的 paused_for_fairness
        execution 时返回 False（调用方应退化为正常 start() 逻辑）。"""
        ex = next(
            (
                e
                for e in self._executions.values()
                if e.objective_id == objective_id and e.status == "paused_for_fairness"
            ),
            None,
        )
        if ex is None:
            return False

        ex.status = "running"
        ex.fairness_slice_started_at = time.time()
        ex.fairness_slice_start_step = ex.current_step_idx

        step_idx = ex.current_step_idx
        submitted = self._submit_step(ex, step_idx)
        step = ex.steps[step_idx] if step_idx < len(ex.steps) else None
        if not submitted and (step is None or step.status != "blocked"):
            ex.status = "failed"
            ex.progress_notes = "从公平性暂停恢复时提交失败"
            self._on_objective_failed(ex)

        self._notify_progress(ex)
        self.save()
        return True

    def effective_max_concurrent(self) -> int:
        """[Track K] 计算当前生效的并发上限。

        规则（只降不升，安全阀在两端）：
          - 起点是 `min(MAX_CONCURRENT_OBJECTIVES, cfg.autonomy.
            max_concurrent_objectives_cap)`——模块级常量永远是绝对天花板，
            配置项只能进一步收紧，不能突破它。
          - 未提供 `cfg`，或 `cfg.autonomy.adaptive_concurrency_enabled`
            为 False 时，直接返回上面这个天花板，等价于改造前"写死用
            MAX_CONCURRENT_OBJECTIVES"的行为（前提是也没配置更低的
            cap——这是默认配置下的实际效果）。
          - 否则读取最近 `adaptive_concurrency_window` 个已结束
            （completed/failed）execution：
              - 样本数 < `adaptive_concurrency_min_samples` → 不参与判定，
                信号不足时不瞎调。
              - 失败率 ≥ `adaptive_concurrency_failure_rate_threshold` →
                下调一档。
              - 已完成（不含失败）execution 的平均耗时 ≥
                `adaptive_concurrency_slow_duration_seconds` → 再下调
                一档（可与上面失败率信号叠加）。
          - 最终结果不低于 `adaptive_concurrency_min`。

        失败静默降级：任何异常都退化为返回天花板值（不下调），保持"没有
        这个 Track"时的原始行为，不会因为统计逻辑本身出错而让并发数被
        错误地砍掉。
        """
        autonomy_cfg = getattr(self._cfg, "autonomy", None) if self._cfg is not None else None
        cap = MAX_CONCURRENT_OBJECTIVES
        if autonomy_cfg is not None:
            configured_cap = getattr(autonomy_cfg, "max_concurrent_objectives_cap", MAX_CONCURRENT_OBJECTIVES)
            cap = min(MAX_CONCURRENT_OBJECTIVES, configured_cap)

        # [Track J] 资源门控降级：优先级高于 Track K 的自适应逻辑（两者都是
        # "只降不升"，取更严格的那一个即可）——ResourceArbiter 判定为
        # degraded 时，天花板先被收紧到 resource_gating_degraded_max_concurrent，
        # 再让 Track K 的自适应逻辑在这个更低的天花板基础上继续计算（如果
        # 自适应逻辑算出来的值更低，以更低者为准；不会因为叠加了 Track J
        # 就让并发数变得比单独任一机制更宽松）。
        if self._gating_degraded and autonomy_cfg is not None and getattr(
            autonomy_cfg, "resource_gating_degraded_enabled", True
        ):
            degraded_cap = getattr(autonomy_cfg, "resource_gating_degraded_max_concurrent", 1)
            cap = min(cap, degraded_cap)

        if autonomy_cfg is None or not getattr(autonomy_cfg, "adaptive_concurrency_enabled", False):
            return cap

        try:
            floor = getattr(autonomy_cfg, "adaptive_concurrency_min", 1)
            min_samples = getattr(autonomy_cfg, "adaptive_concurrency_min_samples", 3)
            failure_threshold = getattr(autonomy_cfg, "adaptive_concurrency_failure_rate_threshold", 0.5)
            slow_seconds = getattr(autonomy_cfg, "adaptive_concurrency_slow_duration_seconds", 1800.0)
            window = getattr(autonomy_cfg, "adaptive_concurrency_window", 10)

            finished = [
                ex for ex in self._executions.values()
                if ex.status in ("completed", "failed") and ex.finished_at
            ]
            finished.sort(key=lambda ex: ex.finished_at, reverse=True)
            recent = finished[:window]

            effective = cap
            if len(recent) >= min_samples:
                failed_count = sum(1 for ex in recent if ex.status == "failed")
                if failed_count / len(recent) >= failure_threshold:
                    effective -= 1

                completed_durations = [
                    ex.finished_at - ex.started_at
                    for ex in recent
                    if ex.status == "completed" and ex.started_at
                ]
                if completed_durations and (sum(completed_durations) / len(completed_durations)) >= slow_seconds:
                    effective -= 1

            return max(floor, min(cap, effective))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor.ObjectiveExecutor.effective_max_concurrent')
            return cap

    def set_gating_degraded(self, degraded: bool) -> None:
        """[Track J] 由 AutonomousLoop 每次 tick 调用，反映
        ResourceArbiter.gating_state() 的最新结果是否为 "degraded"。
        不做任何 I/O，纯内存标志位，下一次 effective_max_concurrent() 调用
        即生效。"""
        self._gating_degraded = bool(degraded)

    def can_start_new(self) -> bool:
        return self.running_count() < self.effective_max_concurrent()

    def get_execution(self, execution_id: str) -> Optional[ObjectiveExecution]:
        """[Track E] 只读查询：按 execution_id 返回执行记录，供看板"查看详情"
        接口定位到具体的 step 与其 submitted_message。不存在时返回 None。"""
        return self._executions.get(execution_id)

    def find_running_execution_by_objective(self, objective_id: str) -> Optional[str]:
        """[Track B 完整版] 只读查询：返回该 objective_id 当前"仍在推进中"
        （running/pending，即 GoalNode 视角下应算作"运行中"）的 execution_id；
        没有则返回 None。供反向同步使用：用户在看板上把 GoalNode.status 手动
        改成非"运行中"时，据此找到对应 execution 并调用 cancel()——不直接
        在这里 cancel，保持"谁触发查询就由谁决定下一步动作"的职责分离。"""
        for ex in self._executions.values():
            if ex.objective_id == objective_id and ex.status in ("running", "pending"):
                return ex.execution_id
        return None

    def start(self, objective: "GoalNode") -> Optional[str]:
        """
        为 Objective 创建执行计划并提交第一步。
        返回 execution_id，失败返回 None。
        """
        if self.is_running(objective.id):
            return None  # 已在运行中，不重复启动

        # 拆解 Objective → steps
        step_descs = self._decompose(objective)
        if not step_descs:
            return None

        # 构建 ObjectiveExecution
        exec_id = f"exec_{uuid.uuid4().hex[:8]}"
        steps = [
            ExecutionStep(
                step_id=f"{exec_id}_s{i}",
                step_index=i,
                description=desc,
            )
            for i, desc in enumerate(step_descs)
        ]
        ex = ObjectiveExecution(
            execution_id=exec_id,
            objective_id=objective.id,
            objective_title=objective.title,
            steps=steps,
            status="running",
            started_at=time.time(),
        )
        ex.fairness_slice_started_at = ex.started_at
        ex.fairness_slice_start_step = 0
        self._executions[exec_id] = ex

        # 提交第一步
        submitted = self._submit_step(ex, 0)
        if not submitted and steps[0].status != "blocked":
            ex.status = "failed"
            ex.progress_notes = "第一步提交失败"
        # 注：steps[0].status == "blocked" 时（Track C 路径冲突）ex.status
        # 保持 "running"——不是失败，只是排队等待，由 retry_blocked_steps()
        # 在下次 tick 时重新尝试提交。

        self._notify_progress(ex)
        self.save()
        return exec_id if submitted else None

    def on_turn_done(self, turn_id: str, result_summary: str = "", valid: bool = True) -> Optional[str]:
        """
        AgentRunner 完成一个 turn 后回调。
        找到对应的 step，标记为 done，然后推进到下一步。
        返回推进到的 execution_id（若有）。

        [daemon_autonomous_state_recovery_plan.md 阶段一] valid=False 表示
        调用方（api/server.py）已经判定这次 run_turn() 的结果是畸形/半成品
        文本（比如未解析成功的 <tool_use> 协议残留），不能被当作真实的步骤
        结果——绝不能写入 step.result_summary，否则会被后续 _build_prompt()
        当作"事实"原样拼进下一步 prompt，把错误状态一路传递下去。这种情况
        按"本步骤失败，走既有重试/重新分解逻辑"处理，而不是当作完成继续推进。
        """
        mapping = self._turn_to_exec.get(turn_id)
        if not mapping:
            return None

        exec_id, step_idx = mapping
        ex = self._executions.get(exec_id)
        if not ex:
            return None

        if not valid:
            return self._handle_invalid_step_result(exec_id, step_idx, turn_id)

        # 标记当前 step 完成
        step = ex.steps[step_idx] if step_idx < len(ex.steps) else None
        if step:
            step.status = "done"
            step.finished_at = time.time()
            step.result_summary = result_summary[:500] if result_summary else ""
            # [Track G 深化] 优先从这一步实际调用过的写文件类工具记录里
            # 提取真实路径（不依赖模型自觉声明格式）；拿不到时退化为
            # 从 agent 完整回复原文（result_summary 截断前）里正则解析
            # `[ARTIFACTS]` 标记——用截断前的参数解析，避免标记恰好落在
            # 被截断掉的尾部。两种方式都解析失败/未提供回调时静默保持
            # 空列表，不影响主流程。
            step.artifacts = self._extract_tool_artifacts(step) or self._parse_step_artifacts(result_summary)

        del self._turn_to_exec[turn_id]
        self._release_step_paths(exec_id)

        # [daemon_autonomous_state_recovery_plan.md 阶段四 / P2] 看护模式：
        # 关闭时 _get_guardian() 恒返回 None，下面整段直接跳过，行为与升级
        # 前完全一致。开启时，把这一步的结果摘要喂给 GuardianRunner——判定
        # GIVE_UP（多次恢复无效）时复用既有的"先尝试重新分解，不行再判
        # Objective failed"路径，不新增一套终止逻辑；判定 RECOVER 时，给
        # 下一步注入一条"换个思路"的 guidance（复用 pending_guidance 字段，
        # 与 reset_step() 的 guidance 注入方式一致），不终止执行。
        guardian = self._get_guardian(exec_id)
        if guardian is not None and step is not None:
            gsignal = guardian.observe_step(step_idx, step.result_summary)
            if gsignal is _GuardianStuckSignal.GIVE_UP:
                guardian.record_dead_end(step_idx, "连续多步结果高度相似，判定原地打转")
                if self._attempt_redecompose(ex, step_idx, "guardian: 连续多轮无实质进展"):
                    self._notify_progress(ex)
                    self.save()
                    return exec_id
                ex.status = "failed"
                ex.finished_at = time.time()
                ex.progress_notes = "guardian: 连续多轮无实质进展，重新分解不可用/已尝试过"
                self._on_objective_failed(ex)
                self._notify_progress(ex)
                self.save()
                return exec_id
            if gsignal is _GuardianStuckSignal.RECOVER and (step_idx + 1) < len(ex.steps):
                next_step_for_guidance = ex.steps[step_idx + 1]
                hint = "[guardian 提示] 最近几步结果高度相似，看起来没有实质进展，请换一种思路或方法尝试。"
                next_step_for_guidance.pending_guidance = (
                    (next_step_for_guidance.pending_guidance + "\n" + hint).strip()
                    if next_step_for_guidance.pending_guidance else hint
                )
            elif guardian.should_terminate_by_rounds():
                ex.status = "failed"
                ex.finished_at = time.time()
                ex.progress_notes = f"guardian: 已达最大轮次上限（{guardian.round_count}），停止执行"
                self._on_objective_failed(ex)
                self._notify_progress(ex)
                self.save()
                return exec_id

        # 检查是否全部完成
        next_idx = step_idx + 1
        if next_idx >= len(ex.steps):
            ex.status = "completed"
            ex.finished_at = time.time()
            ex.current_step_idx = len(ex.steps)
            self._on_objective_completed(ex)
        elif self._should_yield_for_fairness(ex, next_idx):
            # [goal_execution_fairness_improvement_plan.md P4] 已跑满一个
            # 时间片，且确实有其它 Goal 在排队等待——主动让出槽位，不提交
            # 下一步。current_step_idx 停在 next_idx（断点），下次
            # resume_fairness() 会从这里继续，已完成的 step 不受影响。
            ex.status = "paused_for_fairness"
            ex.current_step_idx = next_idx
        else:
            # 提交下一步
            ex.current_step_idx = next_idx
            submitted = self._submit_step(ex, next_idx)
            next_step = ex.steps[next_idx]
            if not submitted and next_step.status == "blocked":
                # [Track C] 路径冲突，不是真正的提交失败——留在 blocked
                # 状态，等 retry_blocked_steps() 下次 tick 时重新尝试。
                pass
            elif not submitted:
                # 提交失败（非路径冲突），重试或放弃
                if next_step.retry_count < MAX_STEP_RETRIES:
                    next_step.retry_count += 1
                    submitted = self._submit_step(ex, next_idx)
                if not submitted and next_step.status != "blocked":
                    ex.status = "failed"
                    ex.progress_notes = f"步骤 {next_idx+1} 提交失败（重试 {next_step.retry_count} 次）"
                    self._on_objective_failed(ex)

        self._notify_progress(ex)
        self.save()
        return exec_id

    def on_turn_failed(self, turn_id: str, error: str = "") -> None:
        """AgentRunner 某个 turn 执行失败时回调。"""
        mapping = self._turn_to_exec.get(turn_id)
        if not mapping:
            return

        exec_id, step_idx = mapping
        ex = self._executions.get(exec_id)
        if not ex:
            return

        step = ex.steps[step_idx] if step_idx < len(ex.steps) else None
        if step:
            self._release_step_paths(ex.execution_id)
            if step.retry_count < MAX_STEP_RETRIES:
                # 重试：先记下这次失败原因，_submit_step 拼 prompt 时会把它
                # 作为"重试上下文"注入，而不是原样重发同一句 description
                # （Track F：避免把系统性失败当偶发重试，浪费预算）。
                step.retry_count += 1
                step.status = "pending"
                step.error_msg = error[:200]
                del self._turn_to_exec[turn_id]
                self._submit_step(ex, step_idx)
            else:
                # 超过重试次数：先尝试一次"重新分解剩余步骤"（Track F 第二
                # 部分），不是直接判失败——系统性走不通的步骤描述，换一种
                # 拆法有时候能绕过去；仅在这一次也失败/不适用时才真正判
                # Objective failed。
                step.status = "failed"
                step.error_msg = error[:200]
                step.finished_at = time.time()
                del self._turn_to_exec[turn_id]
                if self._attempt_redecompose(ex, step_idx, error[:200]):
                    self._notify_progress(ex)
                    self.save()
                    return
                ex.status = "failed"
                ex.finished_at = time.time()
                ex.progress_notes = f"步骤 {step_idx+1} 执行失败：{error[:100]}"
                self._on_objective_failed(ex)

        self._notify_progress(ex)
        self.save()

    def _handle_invalid_step_result(self, exec_id: str, step_idx: int, turn_id: str) -> Optional[str]:
        """[daemon_autonomous_state_recovery_plan.md 阶段一] on_turn_done(valid=False)
        的分流处理。逻辑与 on_turn_failed() 基本一致（复用同一套重试/重新分解
        机制），差异只在于失败原因固定为"结果健全性校验未通过"，且不清空
        step.result_summary/artifacts 之外的信息（沿用 on_turn_failed 的字段
        更新方式）。"""
        ex = self._executions.get(exec_id)
        if not ex:
            return None

        step = ex.steps[step_idx] if step_idx < len(ex.steps) else None
        reason = "结果健全性校验未通过（本轮输出疑似未解析成功的工具调用残留/半成品文本，已作废重试）"
        if step:
            self._release_step_paths(exec_id)
            if step.retry_count < MAX_STEP_RETRIES:
                step.retry_count += 1
                step.status = "pending"
                step.error_msg = reason
                self._turn_to_exec.pop(turn_id, None)
                self._submit_step(ex, step_idx)
            else:
                step.status = "failed"
                step.error_msg = reason
                step.finished_at = time.time()
                self._turn_to_exec.pop(turn_id, None)
                if self._attempt_redecompose(ex, step_idx, reason):
                    self._notify_progress(ex)
                    self.save()
                    return exec_id
                ex.status = "failed"
                ex.finished_at = time.time()
                ex.progress_notes = f"步骤 {step_idx+1} 多次收到无效结果后判定失败：{reason}"
                self._on_objective_failed(ex)
        else:
            self._turn_to_exec.pop(turn_id, None)

        self._notify_progress(ex)
        self.save()
        return exec_id

    def reset_step(self, exec_id: str, step_idx: int, reason: str = "") -> bool:
        """[daemon_autonomous_state_recovery_plan.md 阶段二] 手动/自动重置某个
        自主任务的某一步。

        用途：当 step 已经"完成"（status=="done"）但事后发现其
        result_summary 是错误状态（比如脏内容漏过了阶段一的健全性校验、或者
        人工巡检发现任务跑偏），需要把这一步"打回重做"，并明确告诉模型
        "之前的结果已经作废，不要继续沿用"，而不是让污染的上下文继续被后续
        步骤复用。

        与 _handle_invalid_step_result 的区别：后者是"提交回调时自动判定
        无效"，只能作用于刚提交完的当前 step；reset_step 是显式操作，可以
        重置任意历史 step（包括已经不是 current_step_idx 的），并会把
        current_step_idx 拨回该 step、清掉它之后所有 step 的既有进度
        （这些进度本身可能是基于被污染的上下文产生的，不能保留）。

        返回 True 表示重置成功；exec_id 不存在或 step_idx 越界时返回 False。
        """
        ex = self._executions.get(exec_id)
        if not ex or step_idx < 0 or step_idx >= len(ex.steps):
            return False

        step = ex.steps[step_idx]
        note = reason.strip() or "人工/自动触发重置"
        step.status = "pending"
        step.result_summary = ""
        step.artifacts = []
        step.turn_id = None
        step.retry_count = 0
        step.error_msg = f"[reset] {note}"
        step.finished_at = 0.0
        # [关键] 在 prompt 里显式声明"前序结果已重置"，避免模型的对话历史里
        # 仍留着旧的（可能被污染的）步骤结果时继续沿用——重新提交时
        # _build_prompt() 会拼上这段 reset 说明，而不是让模型误以为
        # "前序步骤结果"里没提到这件事就等于没发生过。
        step.pending_guidance = (
            (step.pending_guidance + "\n" if step.pending_guidance else "")
            + f"[系统提示] 本步骤已被重置（原因：{note}）。请忽略你此前可能已经"
            "看到的、关于这一步或后续步骤的旧结果描述，基于当前实际情况重新"
            "确认并完成本步骤。"
        )

        # 之后的 step 视为"基于被污染上下文产生的进度"，一并清空，不保留。
        for later in ex.steps[step_idx + 1:]:
            later.status = "pending"
            later.result_summary = ""
            later.artifacts = []
            later.turn_id = None
            later.retry_count = 0
            later.error_msg = ""
            later.finished_at = 0.0
            later.submitted_message = ""

        # 若该 step 当前正挂着一个未完成的 turn 映射，一并清掉，避免野指针。
        stale_turn_ids = [tid for tid, m in self._turn_to_exec.items() if m == (exec_id, step_idx)]
        for tid in stale_turn_ids:
            self._turn_to_exec.pop(tid, None)

        self._release_step_paths(exec_id)
        ex.current_step_idx = step_idx
        ex.status = "running"
        ex.progress_notes = f"步骤 {step_idx+1} 已重置：{note}"

        submitted = self._submit_step(ex, step_idx)
        if not submitted and step.status != "blocked":
            ex.status = "failed"
            ex.progress_notes = f"步骤 {step_idx+1} 重置后重新提交失败"

        self._notify_progress(ex)
        self.save()
        return True

    def reap_stale_steps(self, timeout_seconds: Optional[float] = None) -> list[str]:
        """
        [并发槽位卡死修复] 扫描所有 status=="running" 的 Objective，若其
        当前 step 处于 "running" 且已超过 timeout_seconds 仍未收到
        on_turn_done()/on_turn_failed() 回调，视为该 turn 已经死掉（进程
        重启导致 turn 丢失、工具调用挂起、回调路径异常吞掉等），按跟
        on_turn_failed() 一致的重试/终止逻辑处理。

        根因：此前 DEFAULT_STEP_TIMEOUT_SECONDS 定义了但从未被使用——
        running_count()/can_start_new() 只看 status 字段，一个 turn 一旦
        没能触发回调，对应 Objective 会永久占用并发槽位，新 Objective 永远
        排不上。AutonomousLoop 应在每次 tick（推进 Objective 之前）调用
        本方法做存活性回收；这样即使是进程重启后 load() 从磁盘恢复的
        "running" 状态，只要 started_at 已经过期，下一次 tick 也会被清理，
        不需要额外做 turn 存活性探测。

        返回本次被回收（重试或终止）的 execution_id 列表。
        """
        timeout = timeout_seconds if timeout_seconds is not None else DEFAULT_STEP_TIMEOUT_SECONDS
        reaped: list[str] = []
        now = time.time()
        for ex in list(self._executions.values()):
            if ex.status != "running":
                continue
            step = ex.current_step
            if not step or step.status != "running":
                continue
            if step.started_at <= 0 or (now - step.started_at) < timeout:
                continue

            # 超时：先清掉旧索引，避免万一迟到的回调命中已经被回收的 step
            if step.turn_id:
                self._turn_to_exec.pop(step.turn_id, None)
            self._release_step_paths(ex.execution_id)

            timeout_msg = f"步骤超时（超过 {timeout:.0f}s 未收到执行结果，判定为已卡死/丢失）"

            if step.retry_count < MAX_STEP_RETRIES:
                step.retry_count += 1
                step.status = "pending"
                step.turn_id = None
                step.error_msg = timeout_msg
                submitted = self._submit_step(ex, ex.current_step_idx)
                if not submitted:
                    step.status = "failed"
                    step.finished_at = now
                    ex.status = "failed"
                    ex.finished_at = now
                    ex.progress_notes = f"步骤 {ex.current_step_idx+1} {timeout_msg}，重试提交也失败"
                    self._on_objective_failed(ex)
            else:
                step.status = "failed"
                step.finished_at = now
                step.error_msg = f"{timeout_msg}（已重试 {step.retry_count} 次）"
                if self._attempt_redecompose(ex, ex.current_step_idx, step.error_msg):
                    reaped.append(ex.execution_id)
                    self._notify_progress(ex)
                    continue
                ex.status = "failed"
                ex.finished_at = now
                ex.progress_notes = f"步骤 {ex.current_step_idx+1} {step.error_msg}"
                self._on_objective_failed(ex)

            reaped.append(ex.execution_id)
            self._notify_progress(ex)

        if reaped:
            self.save()
        return reaped

    def pause_all(self) -> None:
        """暂停所有运行中的 Objective（用户优先仲裁）。"""
        for ex in self._executions.values():
            if ex.status == "running":
                ex.status = "paused"
        self.save()

    def resume(self, execution_id: Optional[str] = None) -> None:
        """恢复暂停的 Objective（执行引擎在下次 tick 时会重新提交当前步）。"""
        targets = (
            [self._executions[execution_id]]
            if execution_id and execution_id in self._executions
            else [ex for ex in self._executions.values() if ex.status == "paused"]
        )
        for ex in targets:
            # 检查当前 step 是否需要重新提交
            cur = ex.current_step
            if cur and cur.status == "running" and cur.turn_id:
                # turn_id 仍在 _turn_to_exec 中，等待结果即可
                ex.status = "running"
            elif cur and cur.status in ("pending", "running"):
                ex.status = "running"
                # 当前 step 的 turn 可能已经丢失，重新提交
                if cur.turn_id not in self._turn_to_exec:
                    cur.turn_id = None
                    cur.status = "pending"
                    self._submit_step(ex, ex.current_step_idx)
        self.save()

    # ── 看板可操作能力（Track D） ─────────────────────────────────────────────

    def cancel(self, execution_id: str, sync_goal_status: bool = True) -> bool:
        """用户主动终止一个 execution：释放并发槽位和路径占用，不再重试/推进。
        与 "failed" 区分——这是决策，不是执行判定失败。

        sync_goal_status — 是否把对应 GoalNode.status 回写为 "cancelled"。
        默认 True（看板"🛑 终止"按钮走这条路径，此时 GoalNode.status 还
        没被显式改过，需要 cancel() 顺带同步）。[Track B 完整版] 反向
        同步路径（用户在看板上直接把 GoalNode.status 手动改成别的值，
        比如 "abandoned"）调用时应传 False——那种场景下 GoalNode.status
        已经是用户显式选择的值，cancel() 不应该再把它覆盖回
        "cancelled"，只需要真正停止对应 execution。"""
        ex = self._executions.get(execution_id)
        if ex is None or ex.status in ("completed", "failed", "cancelled"):
            return False
        cur = ex.current_step
        if cur and cur.turn_id:
            self._turn_to_exec.pop(cur.turn_id, None)
        self._release_step_paths(execution_id)
        ex.status = "cancelled"
        ex.finished_at = time.time()
        ex.progress_notes = "用户手动终止"
        self._on_objective_cancelled(ex, sync_goal_status=sync_goal_status)
        self._notify_progress(ex)
        self.save()
        return True

    def retry_current_step(self, execution_id: str) -> bool:
        """手动触发当前 step 重新提交，不检查是否超时，随时可调用。"""
        ex = self._executions.get(execution_id)
        if ex is None or ex.status not in ("running", "failed"):
            return False
        cur = ex.current_step
        if cur is None:
            return False
        if cur.turn_id:
            self._turn_to_exec.pop(cur.turn_id, None)
        self._release_step_paths(execution_id)
        cur.turn_id = None
        cur.status = "pending"
        ex.status = "running"
        submitted = self._submit_step(ex, ex.current_step_idx)
        self._notify_progress(ex)
        self.save()
        return submitted

    def inject_guidance(self, execution_id: str, message: str) -> bool:
        """把用户的一句话作为补充上下文塞进下一次提交该 step 的 prompt。
        实现成本远低于真正打断正在跑的 turn——只在"下一次"提交时生效，
        若当前 step 仍在运行，需要配合 retry_current_step() 才会立即生效。"""
        ex = self._executions.get(execution_id)
        if ex is None:
            return False
        cur = ex.current_step
        if cur is None:
            return False
        cur.pending_guidance = (message or "").strip()[:500]
        self.save()
        return bool(cur.pending_guidance)

    def get_status_summary(self) -> list[dict]:
        """返回所有活跃 Objective 的状态摘要（用于 /digest 和 SSE）。"""
        result = []
        for ex in self._executions.values():
            if ex.status in ("completed", "failed", "cancelled") and (time.time() - ex.finished_at) > 3600:
                continue  # 完成超过 1h 的不再显示
            done, total = ex.progress_ratio
            result.append({
                "execution_id": ex.execution_id,
                "objective_id": ex.objective_id,
                "title": ex.objective_title,
                "status": ex.status,
                "progress": f"{done}/{total}",
                "current_step": ex.current_step.description[:80] if ex.current_step else "",
                "started_at": ex.started_at,
                "finished_at": ex.finished_at,
                "progress_notes": ex.progress_notes,
                # [看板改进] 完整计划 + 逐步状态，供看板展示"这个 Objective
                # 具体拆成了哪几步、哪些做完了、哪些还没做"，而不只是一个
                # 笼统的 done/total 比例。之前这里没有暴露，看板只能显示
                # GoalBacklog 里手填的 progress_notes（跟真实执行进度脱节）。
                "steps": [
                    {
                        "step_index": s.step_index,
                        "description": s.description,
                        "status": s.status,
                        "result_summary": s.result_summary,
                        "error_msg": s.error_msg,
                        "retry_count": s.retry_count,
                        "paths": s.paths,
                        "pending_guidance": s.pending_guidance,
                    }
                    for s in ex.steps
                ],
            })
        return result

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _decompose(self, objective: "GoalNode") -> list[str]:
        """
        将 Objective 拆解为 3-8 个执行步骤。
        优先使用 llm_decompose_fn；降级为单步（直接用 title）。
        """
        if self._llm_decompose_fn:
            try:
                steps = self._llm_decompose_fn(objective)
                if steps and isinstance(steps, list) and len(steps) >= 2:
                    return [str(s) for s in steps[:MAX_STEPS_PER_OBJECTIVE]]
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
                pass

        # 降级：单步执行
        return [objective.title]

    def _get_guardian(self, exec_id: str):
        """[daemon_autonomous_state_recovery_plan.md 阶段四 / P2] 惰性获取（或
        创建）某个 execution 专属的 GuardianRunner。仅在
        `cfg.autonomy.guardian_mode_enabled=True` 时才会真正创建；关闭时
        （默认）恒返回 None，调用方据此完全跳过看护逻辑，行为与升级前一致。
        """
        autonomy_cfg = getattr(self._cfg, "autonomy", None) if self._cfg is not None else None
        if not getattr(autonomy_cfg, "guardian_mode_enabled", False):
            return None
        guardian = self._guardians.get(exec_id)
        if guardian is None:
            from mini_agent.evolution.guardian import GuardianRunner

            guardian = GuardianRunner(
                max_rounds=getattr(autonomy_cfg, "guardian_max_rounds", 20),
                similarity_threshold=getattr(autonomy_cfg, "guardian_stuck_similarity_threshold", 0.92),
                consecutive_limit=getattr(autonomy_cfg, "guardian_stuck_consecutive_limit", 3),
                max_recoveries=getattr(autonomy_cfg, "guardian_max_recoveries", 2),
            )
            self._guardians[exec_id] = guardian
        return guardian

    def _attempt_redecompose(self, ex: ObjectiveExecution, step_idx: int, failure_reason: str) -> bool:
        """[Track F 第二部分] 某个 step 耗尽重试次数后，先尝试"重新分解
        剩余步骤"再判定 Objective failed——系统性走不通的拆法，换一种
        分法有时能绕过去，而不是把偶发失败和方法性错误一视同仁地直接
        放弃整个 Objective。

        成功条件（同时满足才真正替换）：
          1. 本 execution 之前没有尝试过（redecompose_attempted 为 False，
             每个 execution 只允许尝试一次，避免"新步骤又失败 → 又分解"
             的隐性资源浪费循环）；
          2. 提供了 llm_redecompose_fn 且调用不抛异常；
          3. 返回的新步骤描述列表非空——只有 1 步且和原描述雷同（换汤不
             换药）没有意义，但这里不做语义判断，交给调用方（LLM）尽量
             给出确实不同的分法，本方法只做"非空"这一基本校验。

        成功时：用新步骤替换 ex.steps[step_idx:]（保留 step_idx 之前已
        完成的步骤和其 result_summary/artifacts 不变），重置 ex.status
        为 running，提交新的第一步；返回 True。

        任何一步不满足/提交失败，原样返回 False——调用方（on_turn_failed/
        reap_stale_steps）据此走回原有的"判定 Objective failed"逻辑，
        不改变现有行为。
        """
        if ex.redecompose_attempted or self._llm_redecompose_fn is None:
            return False
        ex.redecompose_attempted = True  # 无论本次是否成功，只允许尝试一次

        completed_summaries = [
            s.result_summary for s in ex.steps[:step_idx] if s.result_summary
        ]
        remaining_descs = [s.description for s in ex.steps[step_idx:]]
        # [watchlist_notification_goal_design.md §4.5，P6 新增] 只读取
        # *这一个* objective 自己的 external_context，传给 redecompose_fn；
        # 拿不到 goal_backlog 或找不到节点时退化为空列表，不影响原有行为。
        external_context: list = []
        if self._goal_backlog is not None:
            try:
                node = self._goal_backlog.get(ex.objective_id)
                if node is not None:
                    external_context = list(node.external_context or [])
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._attempt_redecompose.external_context')
        try:
            new_descs = self._llm_redecompose_fn(
                ex.objective_title, completed_summaries, remaining_descs, failure_reason,
                external_context=external_context,
            )
        except TypeError:
            # 向后兼容：调用方注入的 llm_redecompose_fn 若还是旧签名
            # （不接受 external_context 关键字参数），退化为不传这个参数，
            # 不影响未升级的自定义实现。
            try:
                new_descs = self._llm_redecompose_fn(
                    ex.objective_title, completed_summaries, remaining_descs, failure_reason,
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._attempt_redecompose')
                return False
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._attempt_redecompose')
            return False

        if not new_descs or not isinstance(new_descs, list):
            return False
        new_descs = [str(d) for d in new_descs if str(d).strip()][:MAX_STEPS_PER_OBJECTIVE]
        if not new_descs:
            return False

        kept = ex.steps[:step_idx]
        new_steps = [
            ExecutionStep(
                step_id=f"{ex.execution_id}_r{step_idx}_{i}",
                step_index=step_idx + i,
                description=desc,
            )
            for i, desc in enumerate(new_descs)
        ]
        ex.steps = kept + new_steps
        ex.current_step_idx = step_idx
        ex.status = "running"
        ex.progress_notes = f"步骤 {step_idx+1} 多次失败后已重新分解剩余步骤（原因：{failure_reason[:80]}）"
        return self._submit_step(ex, step_idx)

    def _extract_tool_artifacts(self, step: "ExecutionStep") -> list[str]:
        """[Track G 深化] 优先路径：从这一步实际调用过的写文件类工具记录
        （由 artifacts_from_tools_fn 定位并解析，见构造函数说明）里提取
        真实路径。未提供回调、这一步没有 submitted_message 可供定位、
        调用异常、或解析不出任何路径时返回空列表——调用方
        （on_turn_done）据此退化到 `_parse_step_artifacts()` 的正则解析。"""
        if self._artifacts_from_tools_fn is None or not step.submitted_message:
            return []
        try:
            found = self._artifacts_from_tools_fn(step.submitted_message) or []
            return [str(p) for p in found if str(p).strip()]
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._extract_tool_artifacts')
            return []

    def _parse_step_artifacts(self, result_summary: str) -> list[str]:
        """[Track G 退化版] 从 agent 的 step 回复原文里正则解析
        `[ARTIFACTS]` 标记，作为 `_extract_tool_artifacts()` 拿不到结果时
        的兜底。未提供 artifacts_parse_fn 或调用异常/返回空时静默退化为
        空列表，不影响 step 完成这一主流程。"""
        if not result_summary or self._artifacts_parse_fn is None:
            return []
        try:
            found = self._artifacts_parse_fn(result_summary) or []
            return [str(p) for p in found if str(p).strip()]
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._parse_step_artifacts')
            return []

    def _declare_step_paths(self, ex: ObjectiveExecution, step: ExecutionStep) -> set:
        """[Track C] 确保 step.paths 已声明（缓存到 step 上，避免重复调用 LLM）。
        返回规范化后的路径集合；拆解不出信息时退化为哨兵路径。"""
        if not step.paths:
            declared: list = []
            if self._declare_paths_fn is not None:
                try:
                    declared = self._declare_paths_fn(step.description) or []
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._declare_step_paths')
                    declared = []
            step.paths = [str(p) for p in declared if p] or [_UNKNOWN_PATH_SENTINEL]
        return set(step.paths)

    def _find_path_conflict(self, ex: ObjectiveExecution, candidate_paths: set) -> Optional[str]:
        """检查 candidate_paths 是否与其他 execution 当前占用的路径冲突。
        冲突时返回占用方的 execution_id，否则返回 None。哨兵路径与任何其他
        哨兵路径都视为冲突（保守串行化），但不与具体路径冲突（避免"一个
        Objective 拆解不出路径"就把所有其他 Objective 也一起卡住）。"""
        for other_id, other_paths in self._active_step_paths.items():
            if other_id == ex.execution_id:
                continue
            if not other_paths:
                continue
            if candidate_paths & other_paths:
                return other_id
        return None

    def _release_step_paths(self, execution_id: str) -> None:
        self._active_step_paths.pop(execution_id, None)

    def retry_blocked_steps(self) -> list[str]:
        """[Track C] 每次 tick 时调用：尝试重新提交所有处于 blocked 状态的
        当前 step——占用方可能已经在上一次 tick 完成/失败/取消，释放了路径。
        返回本次成功重新提交的 execution_id 列表。"""
        submitted_ids: list[str] = []
        for ex in list(self._executions.values()):
            if ex.status != "running":
                continue
            step = ex.current_step
            if not step or step.status != "blocked":
                continue
            if self._submit_step(ex, ex.current_step_idx):
                submitted_ids.append(ex.execution_id)
                self._notify_progress(ex)
        if submitted_ids:
            self.save()
        return submitted_ids

    def _submit_step(self, ex: ObjectiveExecution, step_idx: int) -> bool:
        """提交指定 step，记录 turn_id。

        [Track C] 提交前先做路径互斥检测：若本步骤声明的路径与其他正在
        运行的 Objective 冲突，不提交，把 step.status 置为 "blocked" 并
        返回 False——调用方（start()/on_turn_done() 等）需要区分这种
        "暂时排队"和真正的提交失败，不能直接判 Objective failed。
        """
        if self._submit_fn is None:
            return False
        step = ex.steps[step_idx]
        candidate_paths = self._declare_step_paths(ex, step)
        conflict_with = self._find_path_conflict(ex, candidate_paths)
        if conflict_with is not None:
            step.status = "blocked"
            step.error_msg = f"与 execution {conflict_with} 存在路径冲突，等待其释放后重试"
            return False
        try:
            # 构建包含上下文的 Task 消息
            progress_ctx = ""
            if step_idx > 0:
                prev_summaries = [
                    f"步骤{i+1}: {ex.steps[i].result_summary}"
                    for i in range(step_idx)
                    if ex.steps[i].result_summary
                ]
                # [Track G] 汇总前序步骤已声明的产出物路径，让后续步骤能
                # 明确引用具体路径，而不是"上一步生成的那个文件"这种模糊
                # 指代——没有任何步骤声明过 artifacts 时这段为空，不额外
                # 打印占位内容。
                prev_artifacts = [
                    p for i in range(step_idx) for p in ex.steps[i].artifacts
                ]
                if prev_summaries:
                    progress_ctx = "\n\n[前序步骤结果]\n" + "\n".join(prev_summaries)
                if prev_artifacts:
                    progress_ctx += "\n\n[前序步骤产出文件]\n" + "\n".join(
                        f"- {p}" for p in dict.fromkeys(prev_artifacts)  # 去重且保序
                    )

            guidance_ctx = f"\n\n[用户补充说明]\n{step.pending_guidance}" if step.pending_guidance else ""
            retry_ctx = ""
            if step.retry_count > 0 and step.error_msg:
                retry_ctx = (
                    f"\n\n[重试 - 第 {step.retry_count} 次] 上一次尝试失败原因：{step.error_msg}\n"
                    f"请根据失败原因调整方法后重试，不要重复同样的做法。"
                )

            message = (
                f"[自主任务 - {ex.objective_title}]\n"
                f"步骤 {step_idx+1}/{len(ex.steps)}: {step.description}"
                f"{progress_ctx}{guidance_ctx}{retry_ctx}"
            )
            step.submitted_message = message
            turn_id = self._submit_fn(
                message,
                "autonomous",
                {
                    "execution_id": ex.execution_id,
                    "objective_id": ex.objective_id,
                    "step_index": step_idx,
                    "step_id": step.step_id,
                },
            )
            if turn_id:
                step.turn_id = str(turn_id)
                step.status = "running"
                step.started_at = time.time()
                step.pending_guidance = ""
                self._turn_to_exec[str(turn_id)] = (ex.execution_id, step_idx)
                self._active_step_paths[ex.execution_id] = candidate_paths
                return True
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
            pass
        return False

    def _notify_progress(self, ex: ObjectiveExecution) -> None:
        """调用 on_progress_fn（SSE 推流或 digest 记录）。"""
        if self._on_progress_fn:
            try:
                self._on_progress_fn(ex)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
                pass

    def _sync_goal_status(self, objective_id: str, status: str) -> None:
        """[Track B] 单向同步：ObjectiveExecutor 是执行态的事实来源，
        Objective 完成/失败/取消时把结果回写到 GoalBacklog。反方向（用户在
        看板上手动改状态）由看板/REST 层调用 cancel()，不在这里处理，
        避免两个方向的写入互相覆盖。"""
        if self._goal_backlog is None:
            return
        try:
            self._goal_backlog.set_status(objective_id, status)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._sync_goal_status')

    def _record_theme_outcome(self, ex: ObjectiveExecution, outcome: str) -> None:
        """[Track H] 把这个 Objective 的完成/失败结果按"标题归一化主题"
        计入历史，供 SoftGoalDeriver.derive_candidates() 判断"这类主题是否
        反复失败"。cancelled 不算数（见 objective_outcome_tracker 模块头部
        说明），因此只有 completed/failed 两处调用点。失败静默降级，不影响
        Objective 收尾主流程。"""
        try:
            from mini_agent.evolution.objective_outcome_tracker import record_outcome
            record_outcome(self._paths, ex.objective_title, outcome)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._record_theme_outcome')

    def _on_objective_completed(self, ex: ObjectiveExecution) -> None:
        """Objective 全部步骤完成后的收尾动作。"""
        self._sync_goal_status(ex.objective_id, "completed")
        self._record_theme_outcome(ex, "completed")
        try:
            from mini_agent.evolution.resource_arbiter import append_activity_digest
            append_activity_digest(self._paths, {
                "type": "objective_completed",
                "execution_id": ex.execution_id,
                "objective_id": ex.objective_id,
                "title": ex.objective_title,
                "steps": len(ex.steps),
                "duration": ex.finished_at - ex.started_at,
            })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
            pass

    def _on_objective_failed(self, ex: ObjectiveExecution) -> None:
        """Objective 执行失败后的收尾动作。"""
        self._sync_goal_status(ex.objective_id, "failed")
        self._record_theme_outcome(ex, "failed")
        try:
            from mini_agent.evolution.resource_arbiter import append_activity_digest
            append_activity_digest(self._paths, {
                "type": "objective_failed",
                "execution_id": ex.execution_id,
                "objective_id": ex.objective_id,
                "title": ex.objective_title,
                "reason": ex.progress_notes,
            })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
            pass

    def _on_objective_cancelled(self, ex: ObjectiveExecution, sync_goal_status: bool = True) -> None:
        """[Track D] Objective 被用户主动终止后的收尾动作。

        sync_goal_status — 见 cancel() 的同名参数说明：反向同步路径
        （GoalNode.status 已经被用户显式改过）传 False，跳过回写，
        避免覆盖用户刚设置的值。digest 记录不受影响，始终执行。"""
        if sync_goal_status:
            self._sync_goal_status(ex.objective_id, "cancelled")
        try:
            from mini_agent.evolution.resource_arbiter import append_activity_digest
            append_activity_digest(self._paths, {
                "type": "objective_cancelled",
                "execution_id": ex.execution_id,
                "objective_id": ex.objective_id,
                "title": ex.objective_title,
            })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor')
            pass


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def _format_external_context_items(items: Optional[list], max_items: int = 5) -> str:
    """[watchlist_notification_goal_design.md §4.5，P6 新增] 把一份
    external_context 记录列表（每项 dict：title/snippet/occurred_at...）
    格式化成一段可以拼进 prompt 的文本块，前后各带一个换行，方便直接嵌入
    多行 f-string 中间而不破坏排版；没有记录时返回空字符串（不额外插入
    空标题）。是 `_format_external_context`（读取 GoalNode）和
    `_default_llm_redecompose`（直接传入记录列表）共享的底层实现。
    """
    items = list(items or [])
    if not items:
        return ""
    recent = items[-max_items:]
    lines = [f"相关外部信息（最近 {len(recent)} 条）："]
    for item in recent:
        occurred_at = item.get("occurred_at")
        try:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(occurred_at))) if occurred_at else ""
        except (TypeError, ValueError, OverflowError):
            ts = ""
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        prefix = f"[{ts}] " if ts else ""
        lines.append(f"- {prefix}{title}：{snippet}")
    return "\n" + "\n".join(lines) + "\n"


def _format_external_context(node: "GoalNode", max_items: int = 5) -> str:
    """[watchlist_notification_goal_design.md §4.5，P6 新增]

    把 `node.external_context`（GoalRelevanceEngine Stage② 附加的外部信息，
    见 goal_backlog.py::GoalNode.external_context）格式化成一段可以直接
    拼进 prompt 的文本，只取**这一个** node 自己的记录（objective 层级没有
    自己的 external_context 时，退化为空字符串，不会去读父 Goal 或其它
    Objective 的数据——精确注入是本次设计的核心约束，见 §4.5："只在处理
    这个 Goal 的任务里注入"）。

    没有外部上下文时返回空字符串（不额外插入空标题，保持 prompt 干净）。
    """
    return _format_external_context_items(getattr(node, "external_context", None), max_items=max_items)


def _default_llm_decompose(llm_helper, objective: "GoalNode") -> list[str]:
    """
    轻量 LLM 调用：将 Objective 拆解为步骤列表。
    返回字符串列表，每项是一个步骤的任务描述。

    llm_helper — 需实现 .ask(prompt, ...) -> str，通常传入
    Agent.llm_helper（见 llm/service.py::LLMHelper）。

    历史提示：此函数曾直接接收裸 LLMClient 并调用
    `llm_client.chat(messages=msgs, max_tokens=500)`——LLMClient.chat()
    的真实签名是 (messages, system, tools)，不接受 max_tokens，会直接
    抛 TypeError，被下面的 except 吞掉，导致这个函数一直静默返回 []。
    改用 LLMHelper.ask() 后签名统一、自带重试，且不会再犯这个错误。

    [watchlist_notification_goal_design.md §4.5，P6 新增] 若该 objective
    自己的 `external_context` 非空，会紧跟"当前进展"之后追加进 prompt——
    只取这一个 objective 自己的记录，不会把其它 Goal/Objective 的上下文
    混进来（见 `_format_external_context`）。
    """
    prompt = f"""将以下目标拆解为 3-6 个具体的执行步骤，每步可在单次 Task 中完成。

目标：{objective.title}
当前进展：{objective.progress_notes or '无'}
{_format_external_context(objective)}
要求：
1. 步骤之间有明确的先后依赖关系
2. 每步有明确的完成标准
3. 每步描述不超过 80 字
4. 只输出步骤列表，每行一步，用数字编号，不要其他内容

格式：
1. 第一步描述
2. 第二步描述
...
"""
    try:
        result = llm_helper.ask(prompt)
        if not result:
            return []
        steps = []
        for line in result.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # 去掉数字编号
            if line[0].isdigit() and len(line) > 2 and line[1] in ".、）)":
                line = line[2:].strip()
            elif line[:2].isdigit() and len(line) > 3 and line[2] in ".、":
                line = line[3:].strip()
            if line:
                steps.append(line)
        return steps[:MAX_STEPS_PER_OBJECTIVE]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._default_llm_decompose')
        return []


def _default_declare_paths(llm_helper, step_description: str) -> list[str]:
    """[看板与自主性改进方案 Track C] 轻量 LLM 调用：猜测某个 step 描述
    可能会涉及哪些文件/目录路径。宁可保守多列——目的只是给路径互斥检测
    提供输入，多列几个不相关路径的代价远小于漏列导致真正的并发写冲突。

    返回字符串列表；解析不出结构化内容时返回空列表，调用方
    （ObjectiveExecutor._declare_step_paths）会据此退化为哨兵路径。
    """
    prompt = f"""判断下面这个任务步骤大概率会读写哪些文件或目录路径（相对路径即可，
不确定的话可以给出一个大概的目录，比如某个模块目录）。

步骤描述：{step_description}

要求：
1. 只输出路径，每行一个，不要编号、不要其他说明文字
2. 如果完全无法判断会涉及哪些文件（比如纯查询/说明类步骤），输出一行：无
3. 最多列出 5 个路径
"""
    try:
        result = llm_helper.ask(prompt)
        if not result:
            return []
        paths: list[str] = []
        for line in result.strip().splitlines():
            line = line.strip().strip("-*").strip()
            if not line or line in ("无", "none", "None"):
                continue
            paths.append(line)
        return paths[:5]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._default_declare_paths')
        return []


def _default_llm_redecompose(
    llm_helper, objective_title: str, completed_summaries: list, remaining_descs: list, failure_reason: str,
    external_context: Optional[list] = None,
) -> list[str]:
    """[Track F 第二部分] 轻量 LLM 调用：某个 step 耗尽重试次数后，把"已完成
    步骤的结果 + 原计划里还没做的步骤 + 这次失败的原因"喂给模型，让它给出
    一份新的剩余步骤拆解，而不是简单原样重试同一句描述。

    返回字符串列表（新的剩余步骤描述）；解析不出结构化内容、或模型判断
    "原计划本身没问题、不需要重新拆"时返回空列表——调用方
    （ObjectiveExecutor._attempt_redecompose）据此退化为原有的"判定
    Objective failed"逻辑。

    external_context — [watchlist_notification_goal_design.md §4.5，
    P6 新增] 该 objective 自己的 external_context 记录列表（每项
    {"title","snippet","occurred_at",...}），追加进"已完成步骤的结果"
    之后（紧跟既有的注入位置，见 §4.5 第2点）。为 None/空列表时不追加
    任何内容，prompt 与升级前完全一致。
    """
    completed_ctx = (
        "\n".join(f"- {s}" for s in completed_summaries) if completed_summaries else "（尚无已完成步骤）"
    )
    remaining_ctx = "\n".join(f"- {d}" for d in remaining_descs)
    external_ctx_block = _format_external_context_items(external_context)
    prompt = f"""一个多步骤任务的其中一步反复失败，需要重新规划剩余步骤。

目标：{objective_title}

已完成步骤的结果：
{completed_ctx}
{external_ctx_block}
原计划中剩余（含反复失败的这一步）的步骤：
{remaining_ctx}

失败原因：{failure_reason}

请判断原来的拆法是否有问题（比如步骤本身不可行、依赖了不存在的前提、
粒度不合适等），给出一份新的剩余步骤拆解（3-6 步），尽量避开导致失败的
做法。

要求：
1. 只输出新的步骤列表，每行一步，用数字编号，不要其他内容
2. 如果反复思考后认为原计划没有问题（失败是偶发的，不是方法问题），
   只输出一行：无需重新分解
"""
    try:
        result = llm_helper.ask(prompt)
        if not result or "无需重新分解" in result:
            return []
        steps = []
        for line in result.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line[0].isdigit() and len(line) > 2 and line[1] in ".、）)":
                line = line[2:].strip()
            elif line[:2].isdigit() and len(line) > 3 and line[2] in ".、":
                line = line[3:].strip()
            if line:
                steps.append(line)
        return steps[:MAX_STEPS_PER_OBJECTIVE]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_executor._default_llm_redecompose')
        return []


def _default_parse_artifacts(result_summary: str) -> list[str]:
    """[Track G] 从 step 回复原文里解析 `[ARTIFACTS] path1, path2` 标记。

    这是方案原文"待确认/待细化项 2"里标注为退化方案的做法——更可靠的方式
    是直接从 tool_call 记录里自动提取 write_file/patch_file 类工具的路径
    参数，不依赖模型自觉遵守固定格式；那种做法需要能访问该 step 对应的
    完整 tool_call 序列（见 Track E 的 trace 接口），本函数作为轻量、
    不依赖额外数据源的默认实现，解析不出内容时返回空列表，不影响主流程。
    """
    if not result_summary:
        return []
    m = _re.search(r"\[ARTIFACTS\]\s*(.+)", result_summary)
    if not m:
        return []
    raw = m.group(1).splitlines()[0]
    paths = [p.strip() for p in raw.split(",")]
    return [p for p in paths if p]


__all__ = [
    "ExecutionStep",
    "ObjectiveExecution",
    "ObjectiveExecutor",
    "MAX_CONCURRENT_OBJECTIVES",
    "MAX_STEP_RETRIES",
    "_default_llm_decompose",
    "_default_declare_paths",
    "_default_llm_redecompose",
    "_default_parse_artifacts",
]
