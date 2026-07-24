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
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

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
    # [Track G 预留] 本步骤实际产出/修改的文件路径（agent 在回复里以
    # `[ARTIFACTS] path1, path2` 声明，供后续步骤引用具体路径）。
    artifacts: list[str] = field(default_factory=list)
    # [Track D] 用户通过 inject_guidance() 追加的补充上下文，将在下一次
    # 提交该 step 时拼进 prompt；提交后清空，避免重复注入。
    pending_guidance: str = ""

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
        """
        self._paths = paths
        self._submit_fn = submit_fn
        self._llm_decompose_fn = llm_decompose_fn
        self._on_progress_fn = on_progress_fn
        self._declare_paths_fn = declare_paths_fn
        self._goal_backlog = goal_backlog
        self._executions: dict[str, ObjectiveExecution] = {}  # execution_id → ex
        self._turn_to_exec: dict[str, tuple[str, int]] = {}   # turn_id → (execution_id, step_idx)
        self._exec_path = paths.workdir_dir / "objective_executions.json"
        # [Track C] execution_id → 该 execution 当前 running step 声明的路径集合。
        # 只在内存里维护（不持久化）——重启后 reap_stale_steps()/正常推进会
        # 重新提交并重新声明，不需要跨进程重启保持这份状态。
        self._active_step_paths: dict[str, set] = {}

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

    def can_start_new(self) -> bool:
        return self.running_count() < MAX_CONCURRENT_OBJECTIVES

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

    def on_turn_done(self, turn_id: str, result_summary: str = "") -> Optional[str]:
        """
        AgentRunner 完成一个 turn 后回调。
        找到对应的 step，标记为 done，然后推进到下一步。
        返回推进到的 execution_id（若有）。
        """
        mapping = self._turn_to_exec.get(turn_id)
        if not mapping:
            return None

        exec_id, step_idx = mapping
        ex = self._executions.get(exec_id)
        if not ex:
            return None

        # 标记当前 step 完成
        step = ex.steps[step_idx] if step_idx < len(ex.steps) else None
        if step:
            step.status = "done"
            step.finished_at = time.time()
            step.result_summary = result_summary[:500] if result_summary else ""

        del self._turn_to_exec[turn_id]
        self._release_step_paths(exec_id)

        # 检查是否全部完成
        next_idx = step_idx + 1
        if next_idx >= len(ex.steps):
            ex.status = "completed"
            ex.finished_at = time.time()
            ex.current_step_idx = len(ex.steps)
            self._on_objective_completed(ex)
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
                # 超过重试次数
                step.status = "failed"
                step.error_msg = error[:200]
                step.finished_at = time.time()
                del self._turn_to_exec[turn_id]
                ex.status = "failed"
                ex.finished_at = time.time()
                ex.progress_notes = f"步骤 {step_idx+1} 执行失败：{error[:100]}"
                self._on_objective_failed(ex)

        self._notify_progress(ex)
        self.save()

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

    def cancel(self, execution_id: str) -> bool:
        """用户主动终止一个 execution：释放并发槽位和路径占用，不再重试/推进。
        与 "failed" 区分——这是决策，不是执行判定失败。"""
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
        self._on_objective_cancelled(ex)
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
                if prev_summaries:
                    progress_ctx = "\n\n[前序步骤结果]\n" + "\n".join(prev_summaries)

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

    def _on_objective_completed(self, ex: ObjectiveExecution) -> None:
        """Objective 全部步骤完成后的收尾动作。"""
        self._sync_goal_status(ex.objective_id, "completed")
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

    def _on_objective_cancelled(self, ex: ObjectiveExecution) -> None:
        """[Track D] Objective 被用户主动终止后的收尾动作。"""
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
    """
    prompt = f"""将以下目标拆解为 3-6 个具体的执行步骤，每步可在单次 Task 中完成。

目标：{objective.title}
当前进展：{objective.progress_notes or '无'}

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


__all__ = [
    "ExecutionStep",
    "ObjectiveExecution",
    "ObjectiveExecutor",
    "MAX_CONCURRENT_OBJECTIVES",
    "MAX_STEP_RETRIES",
    "_default_llm_decompose",
    "_default_declare_paths",
]
