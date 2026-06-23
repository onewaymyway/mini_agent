"""
evolution/autonomous_loop.py — Stage 9 AutonomousLoop（第七节）

跑在 daemon 进程内（第三节确立的进程模型），是 AgentRunner 循环内部的一个
tick 分支，与"检查用户消息"分支并列，共享同一个常驻进程的生命周期。

只要 daemon 进程存活（不依赖任何客户端连接），tick() 就会按设定频率持续执行。
这是本类与"挂在某次 CLI 调用上的循环"的本质区别。

三档位边界（stage9_plan.md 7.2 节）：
  passive:     只做 Stage 8 已有周期性任务（Phase G），不读 GoalBacklog
  maintenance: passive + 探索预算分配，不 derive 新 Goal
  autonomous:  maintenance + 软目标 derive（第十二节，暂未实现内部逻辑）

档位边界在代码层面的物理体现（不是靠注释承诺）：
  _tick_passive() 方法体内不引用 GoalBacklog 任何方法
  _tick_maintenance()/_tick_autonomous() 才会调用 goal_backlog.has_actionable_work()
"""

from __future__ import annotations

import time
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.api.bridge import InputQueue


class AutonomousLoop:
    """
    daemon 进程内的自主调度循环。

    不持有自己的线程，由调用方（AgentRunner 循环）决定 tick 频率。
    AgentRunner.run() 的 dequeue(timeout=0.5) 超时返回 None（没有新用户消息）时，
    检查"距上次 tick 是否已过 tick_interval_seconds"，是则调用 tick()。
    """

    def __init__(
        self,
        *,
        goal_backlog: "GoalBacklog",
        input_queue: "InputQueue",
        paths: "AgentPaths",
        cfg: "AppConfig",
        tick_interval_seconds: float = 60.0,
    ) -> None:
        self._goal_backlog = goal_backlog
        self._input_queue = input_queue
        self._paths = paths
        self._cfg = cfg
        self._tick_interval = tick_interval_seconds
        self._last_tick_at: float = 0.0
        self._tick_count: int = 0
        self._digest_records: list[dict] = []  # 待写入 activity_digest.jsonl 的记录

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def should_tick(self) -> bool:
        """判断是否应该执行一次 tick（由 AgentRunner 在每次 dequeue 超时后调用）。"""
        return (time.time() - self._last_tick_at) >= self._tick_interval

    def tick(self) -> None:
        """
        主调度入口。AgentRunner 在没有用户消息时调用。
        根据当前 autonomy_level 选择对应档位的 tick 逻辑。
        """
        self._last_tick_at = time.time()
        self._tick_count += 1

        autonomy_level = self._get_autonomy_level()

        if autonomy_level == "passive":
            self._tick_passive()
            return

        if autonomy_level == "maintenance":
            self._tick_maintenance()
            return

        # autonomous 档位
        self._tick_autonomous()

    @property
    def last_tick_at(self) -> float:
        return self._last_tick_at

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ── 档位实现 ──────────────────────────────────────────────────────────────

    def _tick_passive(self) -> None:
        """
        [passive 档位] 只检查 Stage 8 已有周期性任务是否到期，
        不读取、不创建 Goal/Objective。

        边界的物理体现：本方法体内不引用 self._goal_backlog 任何方法。
        """
        # Phase G 时间门控检查（从 SessionEnd 时间门控迁移到 daemon tick 时间门控）
        try:
            from mini_agent.evolution.phase_g import should_run_phase_g, run_phase_g
            if should_run_phase_g(self._paths):
                report = run_phase_g(self._paths)
                self._record_phase_g_for_digest(report)
        except Exception:
            pass

        # Workdir knowledge 定期整合（Stage 4 周期性任务，如已实现）
        try:
            self._run_workdir_consolidation()
        except Exception:
            pass

    def _tick_maintenance(self) -> None:
        """
        [maintenance 档位] passive 的全部任务 + Goal Backlog 驱动的任务提交。
        不 derive 新 Goal/Objective（这是与 autonomous 档位的边界）。
        """
        self._tick_passive()

        # 检查资源仲裁（第八节 ResourceArbiter）
        try:
            from mini_agent.evolution.resource_arbiter import ResourceArbiter
            arbiter = ResourceArbiter(self._paths, self._cfg)
            if not arbiter.can_run_autonomous():
                return
        except Exception:
            # 资源仲裁模块未就绪时，保守地跳过自主任务
            return

        # 从 Goal Backlog 拆解下一个 Task
        # 注意：只有 maintenance 及以上档位才到达这里
        if not self._goal_backlog.has_actionable_work():
            return

        result = self._goal_backlog.next_task_description()
        if not result:
            return

        objective_id, task_desc = result

        # 通过 InputQueue 提交（与用户消息走同一条路，但 initiator 不同）
        if self._submit_autonomous_task(task_desc, objective_id):
            self._record_digest({
                "type": "task_submitted",
                "objective_id": objective_id,
                "task_desc": task_desc[:200],
            })

    def _tick_autonomous(self) -> None:
        """
        [autonomous 档位] maintenance 的全部任务 + 软目标 derive。
        第十二节实现，本 Stage 暂时只执行 maintenance 逻辑。
        """
        self._tick_maintenance()
        # 软目标 derive 逻辑留给第十二节实现
        # TODO Stage 9.12: derive new Goal/Objective based on capability_map low confidence

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _get_autonomy_level(self) -> str:
        """读取当前 autonomy_level（从 self_profile.json）。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if profile:
                return profile.operating_state.autonomy_level
        except Exception:
            pass
        return "passive"  # 读取失败时保守降级

    def _run_workdir_consolidation(self) -> None:
        """
        定期运行 workdir knowledge 整合（若有对应函数）。
        这是"从 SessionEnd 时间门控迁移到 daemon tick"的另一个例子。
        """
        # 目前 Stage 4 的整合是在 session end 时触发，
        # daemon 化后可以改为 tick 触发，但本节先以 Phase G 为主要验证目标。
        pass

    def _submit_autonomous_task(
        self, task_desc: str, objective_id: str
    ) -> bool:
        """
        通过 InputQueue 提交一条自主 Task（initiator="autonomous"）。
        与用户消息走同一条路，保证调度公平性和资源仲裁生效。
        """
        try:
            # initiator 字段在第七节已新增到 _TurnCommand 和 enqueue()
            turn_id = self._input_queue.enqueue(
                message=f"[自主任务] {task_desc}",
                initiator="autonomous",
                meta={"objective_id": objective_id},
            )
            return bool(turn_id)
        except Exception:
            return False

    def _record_phase_g_for_digest(self, report: Any) -> None:
        """将 Phase G 报告记录到 activity_digest.jsonl。"""
        try:
            prune_count = len(getattr(report, "prune_candidates", []))
            promote_count = len(getattr(report, "promotion_candidates", []))
            cap_count = len(getattr(report, "capability_map", []))
            summary = (
                f"Phase G 扫描完成：{prune_count} 个剪枝候选，"
                f"{promote_count} 个晋升候选，{cap_count} 个能力条目"
            )
            self._record_digest({
                "type": "phase_g_completed",
                "summary": summary,
                "prune_count": prune_count,
                "promote_count": promote_count,
                "capability_count": cap_count,
            })
        except Exception:
            pass

    def _record_digest(self, extra: dict) -> None:
        """向 activity_digest.jsonl 追加一条记录。"""
        try:
            import json
            path = self._paths.workdir_dir / "activity_digest.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "at": time.time(),
                "initiator": "autonomous",
                **extra,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False))
                f.write("\n")
        except Exception:
            pass

    def get_digest_status(self) -> dict:
        """返回 AutonomousLoop 状态摘要（供 daemon status 命令使用）。"""
        return {
            "last_tick_at": self._last_tick_at,
            "tick_count": self._tick_count,
            "tick_interval_seconds": self._tick_interval,
            "autonomy_level": self._get_autonomy_level(),
        }


__all__ = ["AutonomousLoop"]
