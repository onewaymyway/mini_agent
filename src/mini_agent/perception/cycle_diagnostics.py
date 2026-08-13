"""
perception/cycle_diagnostics.py — 周期性 Goal/Cron 任务的跨轮次诊断报告
（Stage 1，见 next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md §2）

只读聚合，回答"这个 Goal 整体跑得怎么样"：把散落在 goals.json / 归档
jsonl / execution_phase 状态文件 / cron_jobs.json / 产出目录 manifest
这几处的数据拼成一份 `CycleDiagnosticsReport`。

设计原则（§2.1）：
  - 不做任何新的判定逻辑——健康信号直接复用 `check_phase_health()` 的既有
    输出，阶段历史直接读 `ExecutionPhaseState.mode_history`。这是"聚合
    展示"，不是"新的决策层"。
  - `mechanism_notes` 是静态模板文本 + 变量替换，不调用 LLM（零 LLM 成本
    原则，与 docs/unified-scheduler-guide.md §3 一致）。
  - `recent_cycle_summaries` 覆盖"热数据（reaped_cycle_child_ids 对应的
    节点）+ 冷数据（goal_cycle_archive.jsonl 归档）"两部分，对用户呈现
    为一份连续时间线，不暴露内部实现细节。
  - 性能边界：归档 jsonl 只从文件尾部往前读够 N 条所需的部分，不做全
    文件扫描（见 `_tail_jsonl_records()`）。
  - 任一子数据源缺失/异常时，报告仍然要能生成，对应字段返回空/占位，
    不因为某个 Goal 从未绑定 cron 或从未触发过阶段判定就整体报错，与
    `GET /v1/self/scheduling_overview` 的降级风格一致。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog


DEFAULT_RECENT_CYCLES = 10


@dataclass
class CycleDiagnosticsReport:
    goal_id: str
    goal_title: str
    found: bool = True
    error: Optional[str] = None

    # ── 概览 ──
    cycle_count: int = 0
    recurring: bool = False
    schedule: Optional[str] = None
    status: str = ""
    created_at: float = 0.0
    last_scheduled_at: float = 0.0

    # ── 健康信号（复用 check_phase_health 的判定逻辑，不重新发明）──
    execution_phase_mode: str = "auto"
    execution_phase_locked: bool = False
    phase_history_summary: list = field(default_factory=list)
    recent_health_alerts: list = field(default_factory=list)
    cron_health: Optional[dict] = None

    # ── 产出与进展 ──
    recent_cycle_summaries: list = field(default_factory=list)
    output_dir: str = ""
    progress_notes_tail: str = ""

    # ── 机制说明 ──
    mechanism_notes: list = field(default_factory=list)

    generated_at: float = field(default_factory=time.time)

    # ── Stage 3（可选，默认不生成）：LLM 自然语言摘要 ──
    # 见 next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md
    # §2.3。只在调用方显式请求（CLI --summarize / REST ?summarize=true）且
    # `cycle_tuning.diagnostics_llm_summary_enabled=True` 时才会被填充，
    # 默认是 None，不代表报告本身不完整。
    llm_summary: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _tail_jsonl_records(path: Path, want: int, *, chunk_size: int = 65536, max_chunks: int = 200) -> list[dict]:
    """从 jsonl 文件尾部往前读，读够 `want` 条完整记录即停止，不做全文件
    扫描——`goal_cycle_archive.jsonl` 理论上可以无限增长（见方案 §2.1/§6.1），
    长期运行的 Goal 不应该因为诊断报告触发一次全文件读取。

    实现：从文件末尾按 `chunk_size` 字节倒着读，累积到换行数量足够切出
    `want` 条完整行为止；`max_chunks` 是安全上限（万一文件里长期没有
    换行符，避免无限读到文件头之外仍不停止——理论上到文件头自然停止，
    这里只是双重保险）。解析失败的单行跳过，不影响其它行。
    """
    if want <= 0 or not path.is_file():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []

    with open(path, "rb") as f:
        data = b""
        pos = size
        chunks_read = 0
        while pos > 0 and chunks_read < max_chunks:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + data
            chunks_read += 1
            # 完整行数（按换行符切分后去掉可能不完整的首行）至少要够 want 条，
            # 多留一点余量再停，避免边界正好切在一行中间导致刚好差一条。
            if data.count(b"\n") >= want + 1 or pos == 0:
                break

    lines = data.split(b"\n")
    # 第一行可能是被截断的半行（除非已经读到文件开头），丢弃它。
    if pos > 0 and lines:
        lines = lines[1:]
    # 文件通常以换行符结尾，split 会在末尾产生一个空字符串元素，不是一条
    # 真实记录，丢弃它，否则会挤掉一条本该被取到的真实记录。
    if lines and not lines[-1].strip():
        lines = lines[:-1]
    out: list[dict] = []
    for raw in lines[-want:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


def _cron_job_for_goal(paths: "AgentPaths", cron_job_id: Optional[str]) -> Optional[dict]:
    if not cron_job_id:
        return None
    try:
        from mini_agent.evolution.cron_scheduler import load_cron_scheduler
        # 只读用途：不传 submit_fn/job_runner，load() 本身不会触发 save()，
        # 不会因为诊断报告的读取而改动 cron_jobs.json（哪怕首次加载会在
        # 内存里补齐内置 job，也只在显式调用 save() 时才落盘）。
        scheduler = load_cron_scheduler(paths)
        job = scheduler.get(cron_job_id)
        if job is None:
            return None
        return {
            "job_id": job.id,
            "schedule": job.schedule,
            "enabled": job.enabled,
            "run_count": job.run_count,
            "consecutive_skip_count": job.consecutive_skip_count,
            "last_run_at": job.last_run_at,
            "next_run_at": job.next_run_at,
        }
    except Exception:
        return None


def _summarize_manifest(d: dict) -> dict:
    artifacts = d.get("artifacts") or []
    return {
        "dir": d.get("_dir", ""),
        "cycle": d.get("cycle") if "cycle" in d else d.get("run"),
        "task_summary": (d.get("task_summary") or "")[:200],
        "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
        "completed_at": d.get("completed_at") or d.get("created_at"),
        "status": d.get("status", ""),
    }


def _build_mechanism_notes(node, spec, phase_state) -> list[str]:
    notes: list[str] = []
    if node.recurring:
        notes.append(
            "产出目录规则：这是一个 recurring Goal，每轮产出写入 "
            "`.agent/daemon_run_outputs/goals/<goal_id>/cycle_%04d/`，"
            "`latest.json` 指向最新一轮目录（见 output_workspace.py）。"
        )
    else:
        notes.append(
            "产出目录规则：这是一个一次性 Goal，各子 Objective 产出写入 "
            "`.agent/daemon_run_outputs/goals/<goal_id>/run_%04d/`。"
        )
    if phase_state is not None:
        if phase_state.mode == "auto":
            notes.append(
                "阶段判定：当前为 auto 模式（未手动锁定），阶段在 "
                "explore/converge/stable/tidy 间按规则自动判定，规则见 "
                "docs/goal-execution-phase-guide.md。"
            )
        else:
            lock_txt = "已锁定" if phase_state.locked else "未锁定"
            notes.append(
                f"阶段判定：当前为用户手动指定的 '{phase_state.mode}' 阶段（{lock_txt}），"
                "不会被自动规则覆盖，除非解除锁定（`phase unlock`）。"
            )
    if spec is not None:
        notes.append(
            "执行规范：" + ("已确认（confirmed），下次触发按此规范生效。" if spec.confirmed
                          else "存在草稿但尚未确认（confirmed=False），仍在使用默认行为。")
        )
    else:
        notes.append("执行规范：尚未生成，当前按默认行为执行，未做每轮产出/交接字段的显式约束。")
    return notes


def build_cycle_diagnostics(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    goal_id: str,
    *,
    recent_n: int = DEFAULT_RECENT_CYCLES,
) -> CycleDiagnosticsReport:
    """聚合出一个 Goal 的跨轮次诊断报告。纯读取，不修改任何状态。

    goal_id 不存在 / 不是 Goal 节点时，返回 `found=False` 的报告（不抛
    异常），调用方（CLI/REST）据此决定如何展示，与项目里其它面向用户的
    只读聚合端点风格一致。
    """
    node = goal_backlog.get(goal_id)
    if node is None or not getattr(node, "is_goal", False):
        return CycleDiagnosticsReport(
            goal_id=goal_id, goal_title="", found=False,
            error=f"Goal '{goal_id}' not found",
        )

    report = CycleDiagnosticsReport(
        goal_id=node.id,
        goal_title=node.title,
        cycle_count=node.cycle_count,
        recurring=node.recurring,
        status=node.status,
        created_at=node.created_at,
        last_scheduled_at=node.last_scheduled_at,
        progress_notes_tail="\n".join((node.progress_notes or "").splitlines()[-10:]),
    )

    # ── cron 健康 ──
    try:
        report.cron_health = _cron_job_for_goal(paths, node.recurrence_cron_job_id)
        if report.cron_health:
            report.schedule = report.cron_health.get("schedule")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.cycle_diagnostics.build_cycle_diagnostics.cron')

    # ── 执行阶段 ──
    phase_state = None
    try:
        from mini_agent.perception import execution_phase as ep
        phase_state = ep.load_phase(paths, goal_id)
        report.execution_phase_mode = phase_state.mode
        report.execution_phase_locked = phase_state.locked
        report.phase_history_summary = [
            {"at": m.at, "from": m.from_mode, "to": m.to_mode, "reason": m.reason}
            for m in phase_state.mode_history[-10:]
        ]
        effective_mode = ep.last_known_effective_mode(phase_state)
        alert = ep.check_phase_health(phase_state, effective_mode)
        # check_phase_health 只在"确实要发送通知"时由调用方落盘冷却状态；
        # 诊断报告是纯读取，不代表"已发送"，因此这里不落盘 last_health_alert_*，
        # 只是把当前状态如果触发了阈值也一并展示出来，供用户回看，不影响
        # 真正的通知冷却计时。
        if alert:
            report.recent_health_alerts.append({"kind": "current", "message": alert})
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.cycle_diagnostics.build_cycle_diagnostics.phase')

    # ── 产出目录 + 最近轮次摘要（热数据 + 冷数据拼接）──
    spec = None
    try:
        from mini_agent.evolution import output_workspace as ow
        base_dir = ow.goal_output_base_dir(paths, goal_id)
        report.output_dir = str(base_dir.as_posix())
        manifests = ow.read_all_manifests(base_dir)
        hot_summaries = [_summarize_manifest(d) for d in manifests[-recent_n:]]
        if len(hot_summaries) < recent_n:
            need = recent_n - len(hot_summaries)
            archive_path = paths.workdir_dir / "goal_cycle_archive.jsonl"
            cold_records = _tail_jsonl_records(archive_path, need)
            cold_summaries = []
            for rec in cold_records:
                if rec.get("id") not in (getattr(node, "children_ids", []) or []):
                    # 归档节点本身没有独立的 manifest 摘要字段，用
                    # progress_notes/status 拼一条粗粒度摘要，跟 manifest
                    # 摘要字段名对齐，方便前端统一渲染。
                    cold_summaries.append({
                        "dir": None,
                        "cycle": None,
                        "task_summary": (rec.get("progress_notes") or "")[-200:],
                        "artifact_count": None,
                        "completed_at": rec.get("last_touched_at"),
                        "status": rec.get("status", ""),
                        "archived": True,
                    })
            report.recent_cycle_summaries = cold_summaries + hot_summaries
        else:
            report.recent_cycle_summaries = hot_summaries
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.cycle_diagnostics.build_cycle_diagnostics.outputs')

    try:
        from mini_agent.perception.goal_execution_spec import load_spec
        spec = load_spec(paths, goal_id)
    except Exception:
        spec = None

    report.mechanism_notes = _build_mechanism_notes(node, spec, phase_state)
    report.generated_at = time.time()
    return report


# ── Stage 3（可选，默认关闭）：LLM 自然语言摘要层 ──────────────────────────
# next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md §2.3

def summarize_report_with_llm(report: CycleDiagnosticsReport, llm_ask) -> Optional[str]:
    """把已经聚合好的结构化报告喂给 LLM，生成一段自然语言总结。

    `llm_ask`：`Callable[[str], str]`，与 `evolution/growth_advisor.py` /
    `evolution/next_action_advisor.py._llm_rank` 同一个约定——调用方（CLI/
    REST）负责把 `agent.llm_helper.ask` 包成这个签名，本函数不直接依赖
    `LLMHelper` 类，方便测试时传入桩函数。`llm_ask` 为 None 或调用抛异常/
    返回空文本时，静默返回 None（不生成摘要，不影响报告本身的可用性，
    §2.3"失败自动回退"）。

    输入只有已经聚合好的结构化字段（不额外读取原始产出内容/manifest 全文），
    控制 token 成本，也避免 LLM 摘要"引入报告里没有的新事实"。
    """
    if llm_ask is None or not report.found:
        return None
    payload = {
        "goal_title": report.goal_title,
        "recurring": report.recurring,
        "schedule": report.schedule,
        "cycle_count": report.cycle_count,
        "execution_phase_mode": report.execution_phase_mode,
        "recent_health_alerts": [a.get("message", "") for a in report.recent_health_alerts],
        "cron_health": report.cron_health,
        "recent_cycle_summaries": report.recent_cycle_summaries[-5:],
    }
    prompt = (
        "以下是某个周期性任务（Goal）的跨轮次诊断报告（结构化数据，已经过\n"
        "规则聚合，不需要你重新判断健康与否，只需要把它总结成 2-4 句自然语言，\n"
        "面向用户直接可读：说清楚整体进展是否平稳、有没有需要关注的信号\n"
        "（不要编造报告里没有出现的字段或数值）。用中文回答，不要用 markdown\n"
        "标题/列表，只输出一段连续文字：\n" + json.dumps(payload, ensure_ascii=False)
    )
    try:
        text = llm_ask(prompt)
        text = (text or "").strip()
        return text or None
    except Exception:
        return None
