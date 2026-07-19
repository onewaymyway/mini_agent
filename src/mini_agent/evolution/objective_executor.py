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


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class ExecutionStep:
    step_id: str
    step_index: int                 # 0-based
    description: str                # 提交给 agent 的任务文本
    status: str = "pending"         # pending | running | done | failed
    turn_id: Optional[str] = None   # 对应的 InputQueue turn_id
    result_summary: str = ""        # agent 完成后写回的摘要
    started_at: float = 0.0
    finished_at: float = 0.0
    retry_count: int = 0
    error_msg: str = ""

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
        )


@dataclass
class ObjectiveExecution:
    execution_id: str
    objective_id: str
    objective_title: str
    steps: list[ExecutionStep] = field(default_factory=list)
    current_step_idx: int = 0
    status: str = "pending"         # pending | running | paused | completed | failed
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
    ) -> None:
        """
        submit_fn         — 提交 Task：(message, initiator, meta) -> turn_id | None
        llm_decompose_fn  — 拆解 Objective：(objective) -> list[str] 步骤描述列表
        on_progress_fn    — 进度回调：(execution) -> None，用于 SSE 推流
        """
        self._paths = paths
        self._submit_fn = submit_fn
        self._llm_decompose_fn = llm_decompose_fn
        self._on_progress_fn = on_progress_fn
        self._executions: dict[str, ObjectiveExecution] = {}  # execution_id → ex
        self._turn_to_exec: dict[str, tuple[str, int]] = {}   # turn_id → (execution_id, step_idx)
        self._exec_path = paths.workdir_dir / "objective_executions.json"

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
            if ex.status not in ("completed", "failed")
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
        if not submitted:
            ex.status = "failed"
            ex.progress_notes = "第一步提交失败"

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
            if not submitted:
                # 提交失败，重试或放弃
                next_step = ex.steps[next_idx]
                if next_step.retry_count < MAX_STEP_RETRIES:
                    next_step.retry_count += 1
                    submitted = self._submit_step(ex, next_idx)
                if not submitted:
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
            if step.retry_count < MAX_STEP_RETRIES:
                # 重试
                step.retry_count += 1
                step.status = "pending"
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

    def get_status_summary(self) -> list[dict]:
        """返回所有活跃 Objective 的状态摘要（用于 /digest 和 SSE）。"""
        result = []
        for ex in self._executions.values():
            if ex.status in ("completed", "failed") and (time.time() - ex.finished_at) > 3600:
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

    def _submit_step(self, ex: ObjectiveExecution, step_idx: int) -> bool:
        """提交指定 step，记录 turn_id。"""
        if self._submit_fn is None:
            return False
        step = ex.steps[step_idx]
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

            message = (
                f"[自主任务 - {ex.objective_title}]\n"
                f"步骤 {step_idx+1}/{len(ex.steps)}: {step.description}"
                f"{progress_ctx}"
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
                self._turn_to_exec[str(turn_id)] = (ex.execution_id, step_idx)
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

    def _on_objective_completed(self, ex: ObjectiveExecution) -> None:
        """Objective 全部步骤完成后的收尾动作。"""
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


__all__ = [
    "ExecutionStep",
    "ObjectiveExecution",
    "ObjectiveExecutor",
    "MAX_CONCURRENT_OBJECTIVES",
    "MAX_STEP_RETRIES",
    "_default_llm_decompose",
]
