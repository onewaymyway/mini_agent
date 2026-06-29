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
        cron_scheduler=None,
        objective_executor=None,
    ) -> None:
        self._goal_backlog = goal_backlog
        self._input_queue = input_queue
        self._paths = paths
        self._cfg = cfg
        self._tick_interval = tick_interval_seconds
        self._last_tick_at: float = 0.0
        self._tick_count: int = 0
        self._digest_records: list[dict] = []  # 待写入 activity_digest.jsonl 的记录
        # Phase 1 新增：CronScheduler 和 ObjectiveExecutor（可选注入，降级安全）
        self._cron_scheduler = cron_scheduler
        self._objective_executor = objective_executor

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
        [passive 档位] 运行所有到期的 cron job。
        Phase G、workdir_sync、self_eval、goal_review、digest_trim
        都作为 cron job 注册，不再在此直接调用。

        边界的物理体现：本方法体内不引用 self._goal_backlog 任何方法。
        """
        # CronScheduler.tick()：检查所有 enabled job 是否到期并触发
        if self._cron_scheduler is not None:
            try:
                triggered = self._cron_scheduler.tick()
                for job_id in triggered:
                    self._record_digest({
                        "type": "cron_run",
                        "job_id": job_id,
                        "summary": f"Cron job 触发：{job_id}",
                    })
            except Exception:
                pass
        else:
            # 降级：CronScheduler 未注入时直接调用 Phase G（保持向后兼容）
            try:
                from mini_agent.evolution.phase_g import should_run_phase_g, run_phase_g
                if should_run_phase_g(self._paths):
                    report = run_phase_g(self._paths)
                    self._record_phase_g_for_digest(report)
            except Exception:
                pass

        # Workdir knowledge 定期整合（CronScheduler 未注入时的降级路径）
        if self._cron_scheduler is None:
            try:
                self._run_workdir_consolidation()
            except Exception:
                pass

    def _tick_maintenance(self) -> None:
        """
        [maintenance 档位] passive 的全部任务 + Objective 持续执行推进。
        不 derive 新 Goal/Objective（这是与 autonomous 档位的边界）。
        """
        self._tick_passive()

        # 检查资源仲裁
        try:
            from mini_agent.evolution.resource_arbiter import ResourceArbiter
            arbiter = ResourceArbiter(self._paths, self._cfg)
            if not arbiter.can_run_autonomous():
                # 资源不足：暂停所有 Objective 执行
                if self._objective_executor is not None:
                    self._objective_executor.pause_all()
                return
        except Exception:
            return

        # ObjectiveExecutor：推进已有活跃 Objective
        if self._objective_executor is not None:
            try:
                self._objective_executor.resume()  # 恢复因资源仲裁暂停的 Objective
            except Exception:
                pass

        # 若有 ObjectiveExecutor 且还有并发槽位，从 GoalBacklog 启动新 Objective
        if (
            self._objective_executor is not None
            and self._objective_executor.can_start_new()
            and self._goal_backlog.has_actionable_work()
        ):
            objectives = self._goal_backlog.active_objectives()
            for obj in objectives:
                if self._objective_executor.is_running(obj.id):
                    continue
                if not self._objective_executor.can_start_new():
                    break
                exec_id = self._objective_executor.start(obj)
                if exec_id:
                    self._record_digest({
                        "type": "objective_started",
                        "objective_id": obj.id,
                        "title": obj.title,
                        "execution_id": exec_id,
                        "summary": f"开始执行 Objective：{obj.title}",
                    })
            return

        # ObjectiveExecutor 未注入时的降级路径：沿用旧的单次 Task 提交
        if self._objective_executor is None:
            if not self._goal_backlog.has_actionable_work():
                return
            result = self._goal_backlog.next_task_description()
            if not result:
                return
            objective_id, task_desc = result
            if self._submit_autonomous_task(task_desc, objective_id):
                self._record_digest({
                    "type": "task_submitted",
                    "objective_id": objective_id,
                    "task_desc": task_desc[:200],
                })

    def _tick_autonomous(self) -> None:
        """
        [autonomous 档位] maintenance 的全部任务 + 软目标 derive。

        软目标 derive 逻辑：
          1. 读 capability_map：confidence < 0.3 的条目（agent 不确定自己能做的）
          2. 读 workdir_knowledge：next_suggested 非空但 30 天无 Objective 的 WorkThread
          3. 读 recent_lessons：高频触发的 LessonRule（说明某类问题反复出现）
          4. 每次最多 derive 2 个新 Goal，source="agent_derived"
        """
        self._tick_maintenance()

        # 软目标 derive（每 tick 至多执行一次，由节奏治理控制频率）
        try:
            from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver
            deriver = SoftGoalDeriver(self._paths, self._cfg)
            if deriver.should_derive():
                new_goals = deriver.derive(self._goal_backlog)
                for goal in new_goals:
                    self._record_digest({
                        "type": "soft_goal_created",
                        "goal_id": goal.id,
                        "title": goal.title,
                        "summary": f"Agent 建议新目标：{goal.title}",
                    })
                if new_goals:
                    self._goal_backlog.save()
        except ImportError:
            pass  # soft_goal_deriver 尚未实现时静默跳过
        except Exception:
            pass

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
