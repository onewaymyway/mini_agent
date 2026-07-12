"""
perception/exploration_sandbox.py — Stage 9 探索实验沙盒（第十节）

在已有 EvolutionWorkspace（Stage 2）基础上增加探索预算管理层：
  - ExplorationBudget：今日探索 token 上限（ResourceBudget 的 exploration_budget_ratio 子集）
  - ExplorationSandbox：将 EvolutionWorkspace.create_worktree() 限制在
    exploration_budget_ratio 内，超出时自动拒绝创建

与 Stage 2 EvolutionWorkspace 的关系：
  - ExplorationSandbox 不替换 EvolutionWorkspace，而是包装它
  - EvolutionWorkspace 负责 git worktree 生命周期（create/cleanup）
  - ExplorationSandbox 负责预算门控和上下文管理

"探索实验"的定义（stage9_plan.md 第十节）：
  不依赖外部用户输入的、由 AutonomousLoop._tick_autonomous() 发起的
  小型试验性 git worktree，用于探索 capability_map 低置信度条目。
  探索结果以 ExplorationReport 形式收集，成功的提升为正式 skill 提案。

档位边界：
  只有 autonomous 档位（第十二节）才会调用 ExplorationSandbox。
  本 Stage 9 里 _tick_autonomous() 还未实装，预留给第十二节使用。
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig
    from mini_agent.evolution.resource_arbiter import ResourceArbiter


# ── ExplorationReport ──────────────────────────────────────────────────────────

@dataclass
class ExplorationReport:
    """
    一次探索实验的结果记录。
    对应 activity_digest.jsonl 中 type="exploration_result" 的条目。
    """
    sandbox_id: str
    capability_id: str           # 对应 CapabilityMapEntry.skill_id 或 discovery_id
    goal: str                    # 本次探索的假设
    started_at: float = 0.0
    ended_at: float = 0.0
    tokens_used: int = 0
    success: bool = False
    finding: str = ""            # 实验结论（自然语言描述）
    proposed_skill_id: Optional[str] = None  # 成功时：拟提案的 skill id
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    def to_digest_entry(self) -> dict:
        return {
            "type": "exploration_result",
            "sandbox_id": self.sandbox_id,
            "capability_id": self.capability_id,
            "goal": self.goal,
            "success": self.success,
            "tokens_used": self.tokens_used,
            "finding": self.finding[:200],
            "proposed_skill_id": self.proposed_skill_id,
            "error": self.error,
        }


# ── ExplorationSandbox ─────────────────────────────────────────────────────────

class ExplorationSandbox:
    """
    探索实验沙盒管理器。

    使用方式（第十二节调用者的视角）：
        sandbox = ExplorationSandbox(paths, cfg, arbiter)
        with sandbox.create(capability_id="skill_xyz", goal="验证 X 方案可行性") as ctx:
            # ctx.worktree_path: Path  — 隔离的 git worktree 路径
            # ctx.report: ExplorationReport  — 待填写的结果对象
            ctx.report.finding = "X 方案在 Y 条件下可行"
            ctx.report.success = True
        # 退出 with 块后：
        #   1. worktree 自动清理
        #   2. report 写入 activity_digest.jsonl
        #   3. tokens_used 计入探索预算

    若探索预算已耗尽，create() 抛出 ExplorationBudgetExhausted。
    """

    def __init__(
        self,
        paths: "AgentPaths",
        cfg: "AppConfig",
        arbiter: "ResourceArbiter",
        memory_backend=None,   # [方案三] Optional[MemoryBackend]，用于探索结果回写记忆
    ) -> None:
        self._paths = paths
        self._cfg = cfg
        self._arbiter = arbiter
        self._memory_backend = memory_backend
        self._active_sandboxes: int = 0
        self._max_concurrent: int = 1  # 同时只允许一个探索实验（保守策略）

    @contextlib.contextmanager
    def create(
        self,
        capability_id: str,
        goal: str,
        branch_prefix: str = "explore",
    ) -> Generator["_ExplorationContext", None, None]:
        """
        创建一次探索实验环境（git worktree）。
        用 with 语句管理生命周期：退出时自动清理。
        """
        # 预算门控
        if not self._arbiter.can_run_exploration():
            raise ExplorationBudgetExhausted(
                f"探索预算已耗尽（今日 exploration_budget_ratio 限额已达上限）"
            )

        # 并发限制
        if self._active_sandboxes >= self._max_concurrent:
            raise ExplorationBudgetExhausted(
                f"已有 {self._active_sandboxes} 个探索实验进行中，最多允许 {self._max_concurrent} 个"
            )

        import uuid
        sandbox_id = f"explore_{uuid.uuid4().hex[:8]}"
        report = ExplorationReport(
            sandbox_id=sandbox_id,
            capability_id=capability_id,
            goal=goal,
            started_at=time.time(),
        )

        # 创建 git worktree
        worktree_path = self._create_worktree(sandbox_id, branch_prefix)

        self._active_sandboxes += 1
        ctx = _ExplorationContext(
            sandbox_id=sandbox_id,
            worktree_path=worktree_path,
            report=report,
        )

        try:
            yield ctx
        except Exception as e:
            report.error = str(e)
            report.success = False
        finally:
            report.ended_at = time.time()
            self._active_sandboxes -= 1

            # 清理 worktree
            self._cleanup_worktree(worktree_path)

            # 记录探索预算用量（从 report.tokens_used 读取）
            if report.tokens_used > 0:
                self._arbiter.record_autonomous_token_usage(
                    report.tokens_used, usage_type="exploration"
                )

            # 写入 activity_digest.jsonl
            self._write_report(report)

            # [方案三] 探索结果回写记忆（无论成功失败），防止同样的探索性
            # 错误被重复"发现"，也让"最近已探索过的领域"能被
            # SoftGoalDeriver._recently_explored_domains() 用于降权。
            self._record_exploration_outcome(report)

    def _record_exploration_outcome(self, report: ExplorationReport) -> None:
        """
        探索无论成功失败都应该沉淀为经验，否则同样的探索性错误会被重复
        "发现"，浪费探索预算。

        - 成功：outcome="验证有效，已提升为 skill 提案候选"，confidence 较高
        - 失败：outcome="尝试该方案不可行"，confidence 中等（这类"此路不通"的
          负面经验同样有价值——防止未来的 SoftGoalDeriver 或 skill_propose
          再次把同一条路径列为候选）

        entry_type="lesson", source="exploration"，半衰期基准与
        self_reflection 相同（30天）——探索结论不如人类反馈可靠，但也不应该
        衰减过快导致刚探索过的"此路不通"很快被遗忘又重新尝试。

        失败静默降级：memory_backend 不可用或写入异常都不影响探索流程本身。
        """
        if self._memory_backend is None:
            return
        try:
            from mini_agent.perception.memory_store import MemoryEntry

            if report.success:
                outcome = report.finding or "验证有效，已提升为 skill 提案候选"
                confidence = 0.7
            else:
                outcome = report.finding or (report.error or "尝试该方案不可行，具体原因未知")
                confidence = 0.5

            entry = MemoryEntry(
                session_id=report.sandbox_id,
                summary=f"探索实验 [{report.capability_id}]：{report.goal}",
                key_outcomes=[outcome],
                tags=["exploration", report.capability_id],
                model="",
                entry_type="lesson",
                trigger=report.goal,
                outcome=outcome,
                root_cause=report.error or "",
                suggested_action=(
                    "该方案已验证可行，可考虑正式提案。" if report.success
                    else "该方案已验证不可行，避免重复尝试同一路径。"
                ),
                confidence=confidence,
                occurrence_count=1,
                source="exploration",
            )
            self._memory_backend.add(entry)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.exploration_sandbox.record_outcome')

    def _create_worktree(self, sandbox_id: str, branch_prefix: str) -> Path:
        """
        尝试复用 Stage 2 EvolutionWorkspace 的 worktree 创建逻辑。
        若 EvolutionWorkspace 不可用，fallback 到 tempdir。
        """
        try:
            from mini_agent.evolution.workspace import EvolutionWorkspace
            ws = EvolutionWorkspace(self._paths)
            branch_name = f"{branch_prefix}/{sandbox_id}"
            return ws.create_worktree(branch_name)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.exploration_sandbox')
            pass

        # fallback: 直接用 tempdir（不隔离 git 历史，但功能上可用）
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix=f"minagent_{sandbox_id}_")
        return Path(tmpdir)

    def _cleanup_worktree(self, worktree_path: Path) -> None:
        """清理 worktree（不抛出异常）。"""
        try:
            from mini_agent.evolution.workspace import EvolutionWorkspace
            ws = EvolutionWorkspace(self._paths)
            ws.cleanup_worktree(worktree_path)
        except Exception:
            # fallback: 直接删除目录
            try:
                import shutil
                shutil.rmtree(str(worktree_path), ignore_errors=True)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.exploration_sandbox')
                pass

    def _write_report(self, report: ExplorationReport) -> None:
        """写入 activity_digest.jsonl。"""
        try:
            from mini_agent.evolution.resource_arbiter import append_activity_digest
            append_activity_digest(
                self._paths,
                {**report.to_digest_entry(), "at": report.ended_at},
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.exploration_sandbox')
            pass


# ── _ExplorationContext ─────────────────────────────────────────────────────────

@dataclass
class _ExplorationContext:
    """在 `with sandbox.create(...) as ctx:` 块内使用的上下文对象。"""
    sandbox_id: str
    worktree_path: Path
    report: ExplorationReport

    def record_tokens(self, count: int) -> None:
        """记录本次探索消耗的 token 数（在 with 块内调用）。"""
        self.report.tokens_used += max(0, count)


# ── 异常 ───────────────────────────────────────────────────────────────────────

class ExplorationBudgetExhausted(Exception):
    """探索预算耗尽或并发限制达到时抛出。"""
    pass


# ── 工厂函数 ────────────────────────────────────────────────────────────────────

def make_exploration_sandbox(
    paths: "AgentPaths",
    cfg: "AppConfig",
    memory_backend=None,
) -> ExplorationSandbox:
    """
    工厂函数：创建 ExplorationSandbox，内部自动构建 ResourceArbiter。
    第十二节的 _tick_autonomous() 调用此函数。
    memory_backend: [方案三] 可选，传入后探索结果会回写记忆（见
    ExplorationSandbox._record_exploration_outcome()）。
    """
    from mini_agent.evolution.resource_arbiter import ResourceArbiter
    arbiter = ResourceArbiter(paths, cfg)
    return ExplorationSandbox(paths, cfg, arbiter, memory_backend=memory_backend)


__all__ = [
    "ExplorationReport",
    "ExplorationSandbox",
    "ExplorationBudgetExhausted",
    "_ExplorationContext",
    "make_exploration_sandbox",
]
