"""
evolution/cron_job_executor.py — cron 任务专用执行通道

与"普通用户消息走 InputQueue → 主 Agent.run_turn()"不是同一条路：
  - run_turn() 没有单任务超时概念（max_turns 只限步数，不限墙钟时间），
    一个 cron job 卡住会导致 sys:* 例行维护任务和用户的其它 cron job
    长期得不到执行（同一个 daemon 进程共享同一条 InputQueue）。
  - cron 任务需要"上次做到哪了"跨次恢复，普通对话 turn 没有这个语义。
  - cron 任务需要独立的卡死检测（连续输出雷同 → 判定不再前进），
    不该沿用主对话里给人类交互设计的判断逻辑。

设计：
  CronJobExecutor.run_job(job, submit_step_fn) 是同步阻塞调用，由
  AutonomousLoop._tick_passive() 在触发到期 job 时调用（不再走
  InputQueue.enqueue 那条排队给主 Agent 的路）。

  submit_step_fn: Callable[[str], StepResult] —— 由调用方注入"跑一步"的
  具体实现（通常是拿一个独立/轻量的 sub-agent 实例跑一次 run_turn 或
  单次 LLM+工具调用），本模块完全不关心底层是用哪个 Agent 实现，只负责：
    1. 每步之间检查墙钟超时/步数上限，触达则收尾（写 progress_summary，
       state.status=timed_out），不强杀线程（Python 做不到），而是让
       submit_step_fn 在下一次被调用前就不再被调用。
    2. 用 StuckDetector 观察每步输出，判定 GIVE_UP 则标记
       needs_human_review 并停止，不再消耗调度资源，直到人工介入后
       手动 reset。
    3. 把每步事件写进 runs/<run_id>.jsonl，把最终进度摘要写回
       state.json，供下次触发时通过 CronJobWorkspace.render_prompt()
       续接。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.role_agents.stuck_detector import StuckDetector, StuckSignal
from mini_agent.evolution.cron_job_workspace import (
    CronJobWorkspace, CronJobState, CronJobConfig,
    STATUS_IDLE, STATUS_RUNNING, STATUS_NEEDS_REVIEW, STATUS_TIMED_OUT,
)

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.evolution.cron_scheduler import CronJob


@dataclass
class StepResult:
    """submit_step_fn 每次调用的返回值。"""
    text: str                     # 本步 assistant 输出（用于卡死检测 + progress 提炼）
    done: bool = False            # 任务本身已经自然完成（不需要再续下一次触发）
    error: Optional[str] = None   # 本步执行异常信息（非 None 视为一次失败）


@dataclass
class RunOutcome:
    run_id: str
    status: str                   # idle(正常完成) / timed_out / needs_human_review / error
    steps_executed: int
    duration_seconds: float


class CronJobExecutor:
    """cron 任务的专用执行封装，一次 run_job() 对应 cron 的一次触发。"""

    def __init__(self, paths: "AgentPaths"):
        self._paths = paths

    def run_job(
        self,
        job: "CronJob",
        submit_step_fn: Callable[[str], StepResult],
        default_config: Optional[CronJobConfig] = None,
    ) -> RunOutcome:
        """
        执行一次 job。submit_step_fn(prompt_text) -> StepResult：
        第一次调用传入渲染好的完整 prompt（含上次进度），之后每次调用
        传入 "继续" 这类简短续步指令（具体由调用方与其 sub-agent 实现
        约定，本函数只负责调度节奏，不关心 prompt 怎么拼）。

        default_config — 透传给 CronJobWorkspace.ensure()，仅在该 job
        的 config.json 首次创建时生效（见 ensure() 的说明），通常由调用方
        根据全局 AppConfig.cron 构造。
        """
        ws = CronJobWorkspace(self._paths, job.id)
        ws.ensure(default_task_template=job.task_template, default_config=default_config)
        cfg = ws.read_config()
        state = ws.read_state()

        # 上次异常退出、state 还停在 running 的僵尸状态：记一次失败但不
        # 阻止本次继续执行——避免"一次异常退出就永久卡在 running 无法
        # 再被调度"（tick() 只看 CronJob.enabled/next_run_at，不看这里
        # 的 state，所以这里只做记录，不做门控）。
        if state.status == STATUS_RUNNING:
            state.consecutive_failures += 1

        run_id = ws.new_run_id()
        state.status = STATUS_RUNNING
        state.last_run_started_at = time.time()
        state.last_run_id = run_id
        ws.write_state(state)

        detector = StuckDetector(
            similarity_threshold=cfg.stuck_similarity_threshold,
            consecutive_limit=cfg.stuck_consecutive_limit,
            max_recoveries=cfg.stuck_max_recoveries,
        )

        prompt = ws.render_prompt(job.task_template)
        deadline = time.time() + cfg.timeout_seconds
        step_index = 0
        final_status = STATUS_IDLE
        last_text = ""
        error_text = ""

        ws.append_run_event(run_id, {
            "type": "run_started", "job_id": job.id, "job_name": job.name,
            "timeout_seconds": cfg.timeout_seconds, "max_steps": cfg.max_steps,
        })

        try:
            while True:
                if time.time() >= deadline:
                    final_status = STATUS_TIMED_OUT
                    ws.append_run_event(run_id, {
                        "type": "timed_out", "step_index": step_index,
                    })
                    break
                if step_index >= cfg.max_steps:
                    final_status = STATUS_TIMED_OUT
                    ws.append_run_event(run_id, {
                        "type": "max_steps_reached", "step_index": step_index,
                    })
                    break

                step_input = prompt if step_index == 0 else "继续"
                try:
                    result = submit_step_fn(step_input)
                except Exception as e:  # noqa: BLE001 — 单步异常不能让整个 job 崩溃
                    from mini_agent.errors import log_exception
                    log_exception(e, where="mini_agent.evolution.cron_job_executor.run_job")
                    final_status = STATUS_NEEDS_REVIEW
                    error_text = str(e)
                    ws.append_run_event(run_id, {
                        "type": "step_error", "step_index": step_index, "error": error_text,
                    })
                    break

                step_index += 1
                last_text = result.text or ""
                ws.append_run_event(run_id, {
                    "type": "step", "step_index": step_index,
                    "text_preview": last_text[:500],
                    "error": result.error,
                })

                if result.error:
                    final_status = STATUS_NEEDS_REVIEW
                    error_text = result.error
                    break

                if result.done:
                    final_status = STATUS_IDLE
                    break

                signal = detector.observe(last_text)
                if signal is StuckSignal.RECOVER:
                    ws.append_run_event(run_id, {
                        "type": "stuck_recover", "step_index": step_index,
                    })
                    continue
                if signal is StuckSignal.GIVE_UP:
                    final_status = STATUS_NEEDS_REVIEW
                    error_text = "连续多步输出无实质进展（StuckDetector 判定 GIVE_UP）"
                    ws.append_run_event(run_id, {
                        "type": "stuck_give_up", "step_index": step_index,
                    })
                    break
        finally:
            duration = time.time() - state.last_run_started_at
            state.status = final_status
            state.last_run_finished_at = time.time()
            state.last_step_index = step_index
            state.last_error = error_text
            if final_status in (STATUS_NEEDS_REVIEW,):
                state.consecutive_failures += 1
            else:
                state.consecutive_failures = 0
            # timed_out / needs_human_review 时保留最后一步输出作为下次续接的
            # progress 摘要；正常完成（idle）则清空，避免下次触发时读到一段
            # 已经过时的"进度"。
            if final_status == STATUS_IDLE:
                state.progress_summary = ""
            else:
                state.progress_summary = last_text[:2000]
            ws.write_state(state)
            ws.append_run_event(run_id, {
                "type": "run_finished", "status": final_status,
                "steps_executed": step_index, "duration_seconds": duration,
            })

        return RunOutcome(
            run_id=run_id, status=final_status,
            steps_executed=step_index, duration_seconds=duration,
        )


__all__ = ["CronJobExecutor", "StepResult", "RunOutcome"]
