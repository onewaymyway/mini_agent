"""
perception/cycle_tuning.py — 周期性 Goal/Cron 任务的交互式调优（Stage 2）
（next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md §3）

草案（draft）→ 确认（confirm）→ 应用（apply）的两阶段流程，复用
`GoalExecutionSpecBuilder` 的既有"草稿 → 确认"范式，不发明新的状态机。

设计边界（§3.1，最高优先级）：调优机制**只能**修改一组预先定义好的白名单
参数，每个参数都有已有独立修改入口且已被测试覆盖，不允许通过这个机制让
Agent 去改任意代码/配置文件/执行任意工具调用。白名单：

    schedule          → goal_cron_bridge.make_goal_recurring()
    priority          → GoalBacklog.update_fields()
    execution_phase   → execution_phase.set_mode()
    task_template     → CronScheduler.update_task_template()
    regenerate_spec   → GoalExecutionSpecBuilder.build_draft()（只生成新
                         草稿，不自动 confirm——确认动作仍走既有的
                         `/agent goals spec confirm`，调优机制本身不代替
                         用户做"确认执行规范"这个决定）

Stage 2 不含 LLM：草案的生成来源只有两条路径——
  1. `source="user_request"`：调用方（CLI/REST）已经把用户意见解析成
     结构化的 `{param, to}` 列表（比如命令行直接传参数名+新值），本模块
     只负责补全 `from`/`reason`/校验白名单、不做自然语言理解。
  2. `source="rule_suggested"`：`suggest_tuning_from_diagnostics()` 基于
     `CycleDiagnosticsReport` 里已经算出的规则信号（cron 连续跳过、长期
     卡在 explore）直接生成候选草案，同样不调用 LLM。
自然语言解析层是 Stage 3 的范围，本模块不涉及。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.evolution.cron_scheduler import CronScheduler
    from mini_agent.perception.cycle_diagnostics import CycleDiagnosticsReport


# 白名单参数——扩大范围需要单独评审，不能通过"用户要求"绕过（§5 第 1 条）。
WHITELIST_PARAMS = ("schedule", "priority", "execution_phase", "task_template", "regenerate_spec")

VALID_STATUSES = ("draft", "confirmed", "applied", "rejected")

# [方向 B 规则触发建议] 与 execution_phase.DEFAULT_STUCK_EXPLORE_CYCLES /
# cron.skip_alert_threshold 是同一类阈值，这里先各自给一份默认值（见方案
# §6 开放问题 2：是否复用同一套阈值配置留待实施前确认，Stage 2 先独立
# 给默认值，不阻塞规则建议本身落地）。
DEFAULT_SKIP_SUGGEST_THRESHOLD = 5


@dataclass
class TuningChange:
    param: str
    from_value: object
    to_value: object
    reason: str = ""

    def to_dict(self) -> dict:
        return {"param": self.param, "from": self.from_value, "to": self.to_value, "reason": self.reason}

    @staticmethod
    def from_dict(d: dict) -> "TuningChange":
        return TuningChange(
            param=d.get("param", ""),
            from_value=d.get("from"),
            to_value=d.get("to"),
            reason=d.get("reason", ""),
        )


@dataclass
class CycleTuningProposal:
    id: str
    goal_id: str
    proposed_changes: list = field(default_factory=list)  # list[TuningChange]
    source: str = "user_request"   # "user_request" | "rule_suggested"
    created_at: float = field(default_factory=time.time)
    status: str = "draft"          # draft -> confirmed -> applied | rejected
    confirmed_at: Optional[float] = None
    applied_at: Optional[float] = None
    apply_results: list = field(default_factory=list)  # 每项 apply 的结果，见 apply_tuning_proposal
    reject_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["proposed_changes"] = [
            c.to_dict() if isinstance(c, TuningChange) else c for c in self.proposed_changes
        ]
        return d

    @staticmethod
    def from_dict(d: dict) -> "CycleTuningProposal":
        return CycleTuningProposal(
            id=d.get("id", ""),
            goal_id=d.get("goal_id", ""),
            proposed_changes=[TuningChange.from_dict(c) for c in d.get("proposed_changes", [])],
            source=d.get("source", "user_request"),
            created_at=float(d.get("created_at", time.time())),
            status=d.get("status", "draft"),
            confirmed_at=d.get("confirmed_at"),
            applied_at=d.get("applied_at"),
            apply_results=d.get("apply_results", []),
            reject_reason=d.get("reject_reason", ""),
        )


class WhitelistViolation(ValueError):
    """草案里出现了不在白名单内的参数——安全边界，不允许绕过（§5 第 1 条）。"""


# ── 存储：`.agent/cycle_tuning_proposals/<goal_id>/<proposal_id>.json` ──
# 与 GoalExecutionSpec 存放在 `.agent/execution_specs/` 的风格一致，不塞进
# goals.json 主文件，避免主文件因为草稿历史膨胀（见方案 §3.2 末尾）。

def _proposal_dir(paths: "AgentPaths", goal_id: str) -> Path:
    safe_id = goal_id.replace("/", "_")
    return Path(paths.project_root) / ".agent" / "cycle_tuning_proposals" / safe_id


def _proposal_path(paths: "AgentPaths", goal_id: str, proposal_id: str) -> Path:
    return _proposal_dir(paths, goal_id) / f"{proposal_id}.json"


def save_proposal(paths: "AgentPaths", proposal: CycleTuningProposal) -> Path:
    d = _proposal_dir(paths, proposal.goal_id)
    d.mkdir(parents=True, exist_ok=True)
    p = _proposal_path(paths, proposal.goal_id, proposal.id)
    atomic_write_json(p, proposal.to_dict())
    return p


def load_proposal(paths: "AgentPaths", goal_id: str, proposal_id: str) -> Optional[CycleTuningProposal]:
    p = _proposal_path(paths, goal_id, proposal_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return CycleTuningProposal.from_dict(data)


def list_proposals(paths: "AgentPaths", goal_id: str) -> list[CycleTuningProposal]:
    """按 created_at 升序返回某个 Goal 的全部历史草案（含各状态）。"""
    d = _proposal_dir(paths, goal_id)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(CycleTuningProposal.from_dict(data))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda p: p.created_at)
    return out


# ── 生成草案 ──────────────────────────────────────────────────────────────

def build_tuning_proposal(
    goal_id: str,
    changes: list[dict],
    *,
    source: str = "user_request",
) -> CycleTuningProposal:
    """把一组结构化的改动请求打包成草案。`changes` 每项形如
    `{"param": "schedule", "to": "interval:3600", "reason": "..."}`（`from`
    可以不传，未传时留空，展示层可自行去当前值补全，不强制要求这里知道
    "改前"是什么——生成草案时不强制读取当前状态，保持这个函数是纯函数，
    真正的"改前值"由 apply 阶段读取最新状态时校验/记录）。

    `param` 不在 `WHITELIST_PARAMS` 里 → 抛 `WhitelistViolation`，这是硬边界，
    不允许通过任何参数组合绕过（§5 第 1 条），调用方（CLI/REST）应把这个
    异常转成用户可读的错误提示，而不是吞掉后生成一份"部分生效"的草案。
    """
    if not changes:
        raise ValueError("changes 不能为空——没有改动内容的草案没有意义")
    parsed: list[TuningChange] = []
    for c in changes:
        param = c.get("param")
        if param not in WHITELIST_PARAMS:
            raise WhitelistViolation(
                f"参数 '{param}' 不在白名单内（允许：{', '.join(WHITELIST_PARAMS)}），"
                "调优机制不允许修改白名单之外的任何东西。"
            )
        parsed.append(TuningChange(
            param=param, from_value=c.get("from"), to_value=c.get("to"), reason=c.get("reason", ""),
        ))
    return CycleTuningProposal(
        id=f"tuning_{uuid.uuid4().hex[:12]}",
        goal_id=goal_id,
        proposed_changes=parsed,
        source=source,
    )


def suggest_tuning_from_diagnostics(
    report: "CycleDiagnosticsReport",
    *,
    skip_suggest_threshold: int = DEFAULT_SKIP_SUGGEST_THRESHOLD,
) -> Optional[CycleTuningProposal]:
    """[方向 B 规则触发建议] 基于诊断报告里已经算出的信号，直接生成一份
    候选草案（`source="rule_suggested"`），不调用 LLM。命中多个信号时只
    生成一份草案，把多条改动打包在一起（比逐个信号各生成一份草案更符合
    "一次诊断看到的问题，一次决策处理"的使用场景）。没有命中任何规则时
    返回 None，调用方据此判断"当前没有需要提醒的调优建议"，不是报错。

    两类信号（与 execution_phase.check_phase_health 判定的两类健康问题
    对应，但落到的是"可执行的候选改动"而不是单纯的通知）：
      1. cron 连续跳过达到阈值，且 schedule 是 "interval:<sec>" 格式（cron
         表达式格式没有一种通用的"放宽"方式，不在这里猜，只处理确定性的
         interval 格式）→ 建议把间隔翻倍。
      2. 健康告警里出现 stuck_explore（长期卡在 explore 未收敛）→ 建议
         重新生成一份执行规范草稿，供用户对比是否要用新草案替换现状
         （不自动 confirm，只是生成草稿这一步）。
    """
    if not report.found:
        return None
    changes: list[dict] = []

    cron_health = report.cron_health or {}
    skip_count = cron_health.get("consecutive_skip_count") or 0
    schedule = cron_health.get("schedule") or report.schedule
    if skip_count >= skip_suggest_threshold and isinstance(schedule, str) and schedule.startswith("interval:"):
        try:
            sec = int(schedule.split(":", 1)[1])
            new_sec = sec * 2
            changes.append({
                "param": "schedule",
                "from": schedule,
                "to": f"interval:{new_sec}",
                "reason": (
                    f"cron 已连续跳过 {skip_count} 次触发（达到阈值 {skip_suggest_threshold}），"
                    f"当前触发间隔可能过于紧张，建议放宽到 {new_sec} 秒（原 {sec} 秒的 2 倍），"
                    "降低资源竞争导致的持续跳过。"
                ),
            })
        except (ValueError, IndexError):
            pass

    stuck_alerts = [a for a in report.recent_health_alerts if "explore" in a.get("message", "")]
    if stuck_alerts and report.execution_phase_mode == "auto" and not report.execution_phase_locked:
        changes.append({
            "param": "regenerate_spec",
            "from": None,
            "to": True,
            "reason": (
                "长期卡在 explore 阶段未能收敛，可能是执行规范里的产出要求不够清晰。"
                "建议重新生成一份执行规范草稿供人工对比、决定是否替换现状"
                "（本操作只生成草稿，不会自动确认生效）。"
            ),
        })

    if not changes:
        return None
    return build_tuning_proposal(report.goal_id, changes, source="rule_suggested")


# ── confirm / reject ────────────────────────────────────────────────────

def confirm_tuning_proposal(paths: "AgentPaths", goal_id: str, proposal_id: str) -> CycleTuningProposal:
    """确认草案本身，**此时仍未生效**——与 GoalExecutionSpec 的确认语义一致
    （确认的是"这份草案"，不代表立即执行，真正生效要另外调用 apply）。"""
    proposal = load_proposal(paths, goal_id, proposal_id)
    if proposal is None:
        raise ValueError(f"草案不存在：{goal_id}/{proposal_id}")
    if proposal.status != "draft":
        raise ValueError(f"草案当前状态是 '{proposal.status}'，只有 'draft' 状态可以确认")
    proposal.status = "confirmed"
    proposal.confirmed_at = time.time()
    save_proposal(paths, proposal)
    return proposal


def reject_tuning_proposal(
    paths: "AgentPaths", goal_backlog: "GoalBacklog", goal_id: str, proposal_id: str, reason: str = "",
) -> CycleTuningProposal:
    """拒绝草案，作废，不产生任何实际改动。留一条 progress_notes 记录
    "提出过但被拒绝"，避免下次诊断又提出同样的建议而用户不记得已经考虑过
    （§3.2 第 5 步）。"""
    proposal = load_proposal(paths, goal_id, proposal_id)
    if proposal is None:
        raise ValueError(f"草案不存在：{goal_id}/{proposal_id}")
    if proposal.status in ("applied", "rejected"):
        raise ValueError(f"草案当前状态是 '{proposal.status}'，无法再次拒绝")
    proposal.status = "rejected"
    proposal.reject_reason = reason
    save_proposal(paths, proposal)
    try:
        summary = "; ".join(f"{c.param}->{c.to_value}" for c in proposal.proposed_changes)
        note = f"调优草案已拒绝（{proposal.id}）：{summary}"
        if reason:
            note += f"，原因：{reason}"
        goal_backlog.append_progress_note(goal_id, note)
    except Exception:
        pass
    return proposal


# ── apply：逐项应用白名单参数的既有修改入口 ──────────────────────────────

def apply_tuning_proposal(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    cron_scheduler: Optional["CronScheduler"],
    goal_id: str,
    proposal_id: str,
    *,
    spec_builder_cfg=None,
) -> CycleTuningProposal:
    """真正应用一份已确认（status="confirmed"）的草案。逐项调用白名单表格
    对应的既有修改入口，某一项失败不影响其它项已经成功应用的部分，失败项
    在 `apply_results` 里明确标出，不静默吞掉（§3.2 第 4 步）。

    `spec_builder_cfg`：应用 `regenerate_spec` 改动时需要 `AppConfig` 来
    构造 `GoalExecutionSpecBuilder`（与 CLI `/agent goals spec generate`
    走的是同一条路径）。调用方（CLI/REST）通常可以通过
    `mini_agent.config.load_config()` 拿到；不传时这一项改动会失败并给出
    明确原因，不会静默跳过或猜一个默认配置。

    完成后（无论各项成功与否，只要草案本身状态允许应用）`status` 变为
    "applied"，并追加一条 progress_notes 留痕，与项目一贯的"关键决策留痕"
    风格一致。
    """
    proposal = load_proposal(paths, goal_id, proposal_id)
    if proposal is None:
        raise ValueError(f"草案不存在：{goal_id}/{proposal_id}")
    if proposal.status != "confirmed":
        raise ValueError(f"草案当前状态是 '{proposal.status}'，只有 'confirmed' 状态可以应用")

    node = goal_backlog.get(goal_id)
    if node is None or not getattr(node, "is_goal", False):
        raise ValueError(f"Goal 不存在：{goal_id}")

    results: list[dict] = []
    for change in proposal.proposed_changes:
        ok, detail = _apply_single_change(
            paths, goal_backlog, cron_scheduler, node, change, spec_builder_cfg=spec_builder_cfg,
        )
        results.append({"param": change.param, "to": change.to_value, "ok": ok, "detail": detail})

    proposal.apply_results = results
    proposal.status = "applied"
    proposal.applied_at = time.time()
    save_proposal(paths, proposal)

    try:
        ok_count = sum(1 for r in results if r["ok"])
        summary = "; ".join(f"{c.param}->{c.to_value}" for c in proposal.proposed_changes)
        note = f"根据诊断报告调优（{proposal.id}，{ok_count}/{len(results)} 项成功）：{summary}"
        goal_backlog.append_progress_note(goal_id, note)
    except Exception:
        pass
    return proposal


# ── Stage 3（可选，默认关闭）：LLM 自然语言解析层 ──────────────────────────
# next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md §3.2 第 1 步

def parse_nl_request_to_changes(
    text: str,
    report: "CycleDiagnosticsReport",
    llm_ask,
) -> Optional[list[dict]]:
    """把用户的自然语言改进意见解析成白名单参数改动列表。只在规则层无法
    直接解析（比如没有 `param=value` 这种明确结构）时才会被调用，本函数
    本身不做任何"猜测式"规则解析，纯粹是这一层可选的 LLM 增强。

    `llm_ask`：同 `cycle_diagnostics.summarize_report_with_llm`，
    `Callable[[str], str]` 约定，为 None 时直接返回 None。

    返回值：`list[dict]`（可直接喂给 `build_tuning_proposal()` 的
    `changes` 参数）或 `None`（解析失败/无法生成任何改动）。**不会**
    抛出异常——LLM 输出不可控，任何解析问题都归为"没能生成草案"，调用方
    据此提示用户改用具体的 `param=value` 命令，不强行猜一个可能有害的
    改动（方案 §3.2 第 1 步"失败可回退"）。

    白名单校验在这里就做一次（丢弃不在白名单内的条目，而不是让整份草案
    因为 LLM 编出一个不存在的参数名而失败）——真正的硬边界仍然在
    `build_tuning_proposal()` 里（双重校验，不依赖这一层"过滤干净"）。
    """
    if llm_ask is None or not (text or "").strip():
        return None
    context = {
        "goal_title": report.goal_title,
        "recurring": report.recurring,
        "schedule": report.schedule,
        "execution_phase_mode": report.execution_phase_mode,
        "cron_health": report.cron_health,
    }
    prompt = (
        "用户对下面这个周期性任务（Goal）提出了一条自然语言改进意见，请把它\n"
        "映射为若干条结构化的参数改动。只能使用以下参数名之一，不允许发明\n"
        f"新参数：{', '.join(WHITELIST_PARAMS)}。\n"
        "  - schedule：cron 触发间隔，格式如 'interval:3600'（秒）或标准 cron\n"
        "    表达式；用户说'暂停'/'不要再跑了'不属于改 schedule，这种情况\n"
        "    不要生成任何改动（应通过 unrecur 命令处理，不在本机制范围内）。\n"
        "  - priority：整数优先级。\n"
        "  - execution_phase：explore/converge/stable/tidy/auto 之一。\n"
        "  - task_template：cron 触发时注入的任务描述文本。\n"
        "  - regenerate_spec：值固定为 true，表示'重新生成一份执行规范草稿'。\n"
        "当前该 Goal 的上下文（供你理解意见里的相对表述，比如'加倍'/'放宽'）：\n"
        + json.dumps(context, ensure_ascii=False)
        + "\n用户的改进意见：" + text.strip()
        + "\n请只输出一个 JSON 数组，每个元素形如 "
          '{"param": "...", "to": ..., "reason": "..."}'
          "，reason 用一句话说明为什么这样映射。如果这条意见无法映射为上述\n"
          "任何白名单参数的改动，输出空数组 []。不要输出除 JSON 数组之外的\n"
          "任何文字。"
    )
    try:
        raw = llm_ask(prompt)
        parsed = json.loads(_extract_json_array(raw))
        if not isinstance(parsed, list):
            return None
        changes = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            param = item.get("param")
            if param not in WHITELIST_PARAMS:
                continue
            changes.append({"param": param, "to": item.get("to"), "reason": item.get("reason", "")})
        return changes or None
    except Exception:
        return None


def _extract_json_array(text: str) -> str:
    text = text or ""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return "[]"
    return text[start : end + 1]


def build_tuning_proposal_from_nl(
    goal_id: str,
    text: str,
    report: "CycleDiagnosticsReport",
    llm_ask,
) -> Optional[CycleTuningProposal]:
    """`parse_nl_request_to_changes()` + `build_tuning_proposal()` 的便捷
    组合：解析失败/无改动时返回 None，由调用方（CLI/REST）提示用户改用
    具体命令，不在这里报错。`source` 固定为 `"user_request"`——虽然经过了
    LLM 这一层转译，改动意图仍然来自用户，不是系统规则主动发现的问题。
    """
    changes = parse_nl_request_to_changes(text, report, llm_ask)
    if not changes:
        return None
    try:
        return build_tuning_proposal(goal_id, changes, source="user_request")
    except (WhitelistViolation, ValueError):
        return None


def _apply_single_change(paths, goal_backlog, cron_scheduler, node, change: TuningChange, *,
                          spec_builder_cfg=None) -> tuple[bool, str]:
    param = change.param
    try:
        if param == "schedule":
            if cron_scheduler is None:
                return False, "CronScheduler 不可用，无法修改 schedule"
            from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
            make_goal_recurring(goal_backlog, cron_scheduler, node.id, str(change.to_value))
            return True, f"schedule 已更新为 {change.to_value}"

        if param == "priority":
            goal_backlog.update_fields(node.id, priority=int(change.to_value))
            return True, f"priority 已更新为 {change.to_value}"

        if param == "execution_phase":
            from mini_agent.perception import execution_phase as ep
            ep.set_mode(paths, node.id, str(change.to_value), reason="tuning_proposal_apply")
            return True, f"execution_phase 已切换为 {change.to_value}"

        if param == "task_template":
            if cron_scheduler is None:
                return False, "CronScheduler 不可用，无法修改 task_template"
            job_id = node.recurrence_cron_job_id
            if not job_id:
                return False, "该 Goal 尚未绑定 cron job，无法修改 task_template"
            success = cron_scheduler.update_task_template(job_id, str(change.to_value))
            if not success:
                return False, f"cron job '{job_id}' 不存在"
            return True, "task_template 已更新"

        if param == "regenerate_spec":
            if spec_builder_cfg is None:
                return False, "未提供 AppConfig，无法调用 GoalExecutionSpecBuilder；请改用 `/agent goals spec generate`"
            from mini_agent.perception import goal_execution_spec as ges
            builder = ges.GoalExecutionSpecBuilder(spec_builder_cfg)
            spec = builder.build_draft(node.id, node.title, node.description)
            ges.save_spec(paths, node.id, spec)
            if spec.generation_error:
                return False, f"生成失败：{spec.generation_error}"
            return True, f"已生成新的执行规范草稿（第 {spec.version} 版），未确认，需另行 `/agent goals spec confirm`"

        return False, f"未知参数：{param}"
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.perception.cycle_tuning._apply_single_change')
        return False, str(e)
