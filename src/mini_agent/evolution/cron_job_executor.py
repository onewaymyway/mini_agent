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
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.role_agents.stuck_detector import StuckDetector, StuckSignal
from mini_agent.evolution.circuit_breaker_core import classify_error_type
from mini_agent.evolution.cron_context import set_current_cron_job_id, clear_current_cron_job_id
from mini_agent.evolution.cron_job_workspace import (
    CronJobWorkspace, CronJobState, CronJobConfig,
    STATUS_IDLE, STATUS_RUNNING, STATUS_NEEDS_REVIEW, STATUS_TIMED_OUT, STATUS_WAITING_FEEDBACK,
)

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.evolution.cron_scheduler import CronJob
    from mini_agent.evolution.circuit_breaker_core import CircuitBreakerCore


# [cron_run_debug_detail_improvement_plan.md ①②] 单步事件里"完整输出"/
# "工具调用 input/output"各自的字符数上限。写死常量，不做配置项——跟
# 代码库里其它同类阈值一样，先用固定值观察默认量级是否够用，避免过早
# 引入一个可能从来不需要调的配置项。
STEP_FULL_TEXT_MAX_CHARS = 8000
TOOL_INPUT_MAX_CHARS = 2000
TOOL_OUTPUT_MAX_CHARS = 3000


@dataclass
class StepResult:
    """submit_step_fn 每次调用的返回值。"""
    text: str                     # 本步 assistant 输出（用于卡死检测 + progress 提炼）
    done: bool = False            # 任务本身已经自然完成（不需要再续下一次触发）
    error: Optional[str] = None   # 本步执行异常信息（非 None 视为一次失败）
    # [cron_run_debug_detail_improvement_plan.md ①] 本步的工具调用轨迹，
    # 由 cron_agent_bridge.make_submit_step_fn() 从 agent._hist 里提取，
    # 每条形如 {"name": str, "input": Any, "output": str}。不产出工具调用
    # 的 submit_step_fn 实现（或提取失败时）保持默认空列表，向后兼容
    # ——run_job() 只是原样透传，不假设一定非空。
    tool_calls: list = field(default_factory=list)


def _truncate_tool_calls(tool_calls: Optional[list]) -> list:
    """[cron_run_debug_detail_improvement_plan.md ②] 写事件前对每条
    tool_calls 的 input/output 各自截断，避免个别工具调用（整段文件
    内容、超长搜索结果）把单条 run 事件记录撑得过大。非字符串 input
    先序列化成字符串再截断，保证 JSONL 写入不会因为截断切断了某个
    复杂对象的中间而破坏结构（截断只发生在最终字符串上）。防御性
    处理：单条记录解析/序列化失败不影响其它记录，也不影响主流程。
    """
    if not tool_calls:
        return []
    out = []
    for call in tool_calls:
        try:
            name = call.get("name")
            raw_input = call.get("input")
            if isinstance(raw_input, str):
                input_str = raw_input
            else:
                import json
                input_str = json.dumps(raw_input, ensure_ascii=False)
            output_str = call.get("output")
            if not isinstance(output_str, str):
                output_str = str(output_str)
            out.append({
                "name": name,
                "input": input_str[:TOOL_INPUT_MAX_CHARS],
                "output": output_str[:TOOL_OUTPUT_MAX_CHARS],
            })
        except Exception:  # noqa: BLE001 — 单条工具调用记录解析失败不影响其它
            continue
    return out


@dataclass
class RunOutcome:
    run_id: str
    status: str                   # idle(正常完成) / timed_out / needs_human_review / error
    steps_executed: int
    duration_seconds: float


class CronJobExecutor:
    """cron 任务的专用执行封装，一次 run_job() 对应 cron 的一次触发。"""

    def __init__(self, paths: "AgentPaths", circuit_breaker: Optional["CircuitBreakerCore"] = None):
        self._paths = paths
        # [daemon_stability_and_ux_improvement_plan.md 第 1 项 / P2-1]
        # 可选的共享熔断内核，通常由 CronJobRunner 持有并在构造后通过
        # `executor.circuit_breaker = ...` 属性赋值传入（跨多次 run_job()
        # 调用维持累计状态，同时保持这个构造签名与既有测试替身/直接
        # 实例化写法兼容）；构造参数同样接受，供需要一次性传入的调用方
        # 使用。未设置时（None）不启用广度熔断，行为与改造前一致。
        self.circuit_breaker = circuit_breaker
        # [growth_advisor_improvement_plan_v4.md 方向一 M3] cron 任务收尾
        # 时是否顺带产出一条长期记忆，跟 circuit_breaker 走同样的"属性
        # 赋值"接入方式——三个都是可选的、由 CronJobRunner 在构造后设置
        # 的旁路能力，不进构造签名，保持所有现有直接实例化写法（包括
        # 测试里的 `CronJobExecutor(paths)`）不受影响。`memory_backfill_cfg`
        # 为 None（默认值，未升级的调用方）时 `_maybe_backfill_memory()`
        # 直接跳过，不需要强制所有调用方都升级。
        self.memory_backfill_cfg = None
        self.memory_backend = None
        self.llm_client = None

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

        default_config — 通常由调用方根据全局 AppConfig.cron 构造，有两处
        作用：1) 透传给 CronJobWorkspace.ensure()，仅在该 job 的
        config.json 首次创建时决定写入的初始内容；2) 同时也作为
        ws.read_config() 的合并回退来源——job 自己 config.json 里没写的
        字段，每次读取都会跟随这里传入的全局配置值，不需要为"改一次全局
        配置、让所有已存在的 job 立即生效"额外写迁移脚本。
        """
        ws = CronJobWorkspace(self._paths, job.id)
        ws.ensure(default_task_template=job.task_template, default_config=default_config)
        cfg = ws.read_config(default=default_config)
        state = ws.read_state()

        # [cron_async_user_feedback_mechanism_plan] 整次 job 执行期间（含内部
        # 多次 submit_step_fn 调用，都跑在同一条 cron 执行线程上），把 job_id
        # 写进 thread-local，供本步执行到的 ask_user_async 工具据此归属问题；
        # finally 里清空，避免残留到这条线程之后的非 cron 调用。
        set_current_cron_job_id(job.id)

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

        prompt = ws.render_prompt(job.task_template, run_id=run_id)
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
                # [cron_run_debug_detail_improvement_plan.md ②] text_preview
                # 保留（500 字，向后兼容旧看板 UI），新增 full_text（8000 字
                # 上限）供调试时查看完整输出，避免无差别截断到 500 字。
                ws.append_run_event(run_id, {
                    "type": "step", "step_index": step_index,
                    "text_preview": last_text[:500],
                    "full_text": last_text[:STEP_FULL_TEXT_MAX_CHARS],
                    "tool_calls": _truncate_tool_calls(result.tool_calls),
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
            # [cron_async_user_feedback_mechanism_plan] 本次触发过程中如果通过
            # ask_user_async 提了问、其它部分已经跑完（final_status 走到了
            # "正常完成"），但那个问题此刻仍未被用户回答，不应该被记成"完全
            # 结束"（否则 progress_summary 会被下面的逻辑清空，下次触发时
            # {{unanswered_questions}} 虽然还能查到，但已经丢失了这一步的
            # 执行上下文）——改记成 STATUS_WAITING_FEEDBACK，不计入失败。
            # 只在 final_status 已经是"正常完成"时才覆盖：timed_out/
            # needs_human_review 语义上更紧急，维持原状，不被这里抢占。
            if final_status == STATUS_IDLE:
                try:
                    from mini_agent.notification import questions_store
                    still_pending = questions_store.list_pending_question_texts_for_job(self._paths, job.id)
                except Exception as exc:  # noqa: BLE001 — 感知增强不能影响主流程
                    from mini_agent.errors import log_exception
                    log_exception(exc, where="mini_agent.evolution.cron_job_executor.run_job._check_pending_questions")
                    still_pending = []
                if still_pending:
                    final_status = STATUS_WAITING_FEEDBACK

            duration = time.time() - state.last_run_started_at
            state.status = final_status
            state.last_run_finished_at = time.time()
            state.last_step_index = step_index
            state.last_error = error_text
            if final_status in (STATUS_NEEDS_REVIEW,):
                state.consecutive_failures += 1
                if self.circuit_breaker is not None and error_text:
                    self.circuit_breaker.report_breadth_failure(
                        job.id, classify_error_type(error_text),
                    )
            else:
                state.consecutive_failures = 0
            # timed_out / needs_human_review / waiting_feedback 时保留最后一步
            # 输出作为下次续接的 progress 摘要；正常完成（idle）则清空，避免
            # 下次触发时读到一段已经过时的"进度"。
            if final_status == STATUS_IDLE:
                state.progress_summary = ""
            else:
                state.progress_summary = last_text[:2000]
            ws.write_state(state)
            ws.append_run_event(run_id, {
                "type": "run_finished", "status": final_status,
                "steps_executed": step_index, "duration_seconds": duration,
            })
            self._write_output_manifest(
                job=job, run_id=run_id, status=final_status,
                started_at=state.last_run_started_at,
                finished_at=state.last_run_finished_at,
                progress_note=state.progress_summary,
            )
            # [growth_advisor_improvement_plan_v4.md 方向一 M3] 跟
            # `_write_output_manifest()` 并列的"收尾时顺带做的感知增强，
            # 不能反过来影响主流程"——严格限定只有正常收尾
            # （`final_status == STATUS_IDLE`，不含 timed_out/
            # needs_human_review/waiting_feedback）且 `last_text` 非空才会
            # 生成记忆。
            if final_status == STATUS_IDLE and last_text.strip():
                self._maybe_backfill_memory(
                    job=job, run_id=run_id, last_text=last_text,
                )
            clear_current_cron_job_id()

        return RunOutcome(
            run_id=run_id, status=final_status,
            steps_executed=step_index, duration_seconds=duration,
        )

    def _write_output_manifest(
        self,
        job: "CronJob",
        run_id: str,
        status: str,
        started_at: float,
        finished_at: float,
        progress_note: str,
    ) -> None:
        """[goal_cron_output_directory_convention_plan.md §2.2] run_job()
        收尾（无论 idle/timed_out/needs_human_review）时落一份 manifest，
        供下次触发时 CronJobWorkspace.render_prompt() 通过
        {{previous_output}} 读取。异常整体吞掉——manifest 是感知增强，
        不能反过来影响 run_job() 本身的主流程/返回值。"""
        try:
            from mini_agent.evolution import output_workspace
            base_dir = output_workspace.cron_output_base_dir(self._paths, job.id)
            run_dir = output_workspace.allocate_run_dir(self._paths, job.id, run_id)
            output_workspace.write_manifest(
                base_dir, run_dir,
                task_summary=(job.task_template or "")[:200],
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                artifacts=[],
                progress_note=progress_note,
            )
        except Exception as e:  # noqa: BLE001
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.cron_job_executor._write_output_manifest")

    def _maybe_backfill_memory(self, job: "CronJob", run_id: str, last_text: str) -> None:
        """[next_doc/growth_advisor_improvement_plan_v4.md 方向一 M3]
        cron 任务本身仍然是记忆覆盖率的结构性盲区——`cron_agent_bridge.py`
        的设计前提是"每次触发都重新构建 Agent，不跨触发保留 session
        历史"，因此 cron 任务运行完全不会经过 `Session`/`summary` 这条链
        路，M1 的存量回填天然扫不到它们。

        由调用方（`CronJobRunner`）在构造 `CronJobExecutor` 后通过属性
        赋值提供 `memory_backfill_cfg`/`memory_backend`/`llm_client` 三样
        依赖——任何一样缺失都直接跳过（`memory_backfill_cfg` 为 None 表示
        调用方还没升级到支持这个特性；`memory_backend`/`llm_client` 缺失
        通常意味着 cron 任务本身的记忆/LLM 功能就没配置好，静默跳过，
        不在这里制造一个新的报错点）。整个方法异常兜底，绝不能让"感知
        增强"这个旁路反过来影响 `run_job()` 已经产出的主流程结果。
        """
        cfg = self.memory_backfill_cfg
        if cfg is None or not getattr(cfg, "enabled", True):
            return
        if not getattr(cfg, "cron_run_backfill_enabled", True):
            return
        if self.memory_backend is None or self.llm_client is None:
            return
        try:
            from mini_agent.evolution.memory_backfill import backfill_cron_run

            backfill_cron_run(
                job.id, run_id, last_text,
                memory_backend=self.memory_backend,
                llm_client=self.llm_client,
                task_template=job.task_template or "",
            )
        except Exception as e:  # noqa: BLE001
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.cron_job_executor._maybe_backfill_memory")


__all__ = ["CronJobExecutor", "StepResult", "RunOutcome"]
