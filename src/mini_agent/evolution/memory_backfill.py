"""
evolution/memory_backfill.py — 记忆回填（Memory Backfill）

[next_doc/memory_backfill_and_profile_update_plan.md 方向一]

背景：长期记忆(`MemoryEntry`)只有一条写入路径——`ProfileMixin.
_generate_and_save_summary()`，且只在 `save_session()` 内部、轮次达标
时才会触发（`agent/profile.py`/`agent/lifecycle.py`）。这导致大量
"有实质内容、但从未生成过摘要"的 session 长期存在：异常中断的进程、
LLM 调用失败没有重试的、以及 daemon/cron 自动运行的 session。成长顾问
（growth_advisor）这类"扫描记忆找信号"的机制因此长期无米下锅。

本模块只负责"存量 session"的回填（对应方案 M1）：扫描
`Session.summary == ""` 且轮次达标的 session，离线补跑一次摘要生成，
写入长期记忆并回写 `Session.summary`。cron/daemon 任务本身不产生可
回填 session 的问题（方案 2.4 节方案 A/B）不在本模块范围内，留给
`cron_job_executor.py` 收尾逻辑单独处理。

风格对齐 `session_cleanup.py`：纯 Python、确定性的扫描/分类逻辑，只有
"生成摘要"这一步需要调用 LLM。判定条件是 `Session.summary == ""`（是否
已经生成过摘要/记忆的直接判据），**不复用** `session_cleanup.py` 的
`knowledge_extracted` 标记——那是另一条独立的流水线（离线知识抽取产出
decision/entity/fact，不是 session 摘要/长期记忆，详见方案文档 0.1 节
的对照表）。

不做"最多回溯多少天"的时间窗口限制（方案第 4 节风险项 1 已评审确认）：
陈年 session 也应该被回填，靠 `max_sessions_per_run` 限流控制单轮开销，
候选按更新时间从旧到新处理，保证多轮扫描下最终能覆盖全部存量。
"""

from __future__ import annotations

import re as _re
import time
import difflib as _difflib
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.session import SessionManager, SessionMeta
    from mini_agent.llm.base import LLMClient
    from mini_agent.perception.memory_base import MemoryBackend


@dataclass
class BackfillItem:
    """单个 session 的回填结果，供报告展示和 CLI 输出共用。"""
    session_id: str
    title: str
    updated_at: str
    turns: int
    action: str            # "backfilled" | "skip_too_short" | "failed"
    reason: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "turns": self.turns,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class BackfillReport:
    dry_run: bool
    backfilled: list[BackfillItem] = field(default_factory=list)
    failed: list[BackfillItem] = field(default_factory=list)
    total_candidates: int = 0  # 扫描到的候选总数（可能大于本轮实际处理数）

    @property
    def total_processed(self) -> int:
        return len(self.backfilled) + len(self.failed)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "total_candidates": self.total_candidates,
            "total_processed": self.total_processed,
            "backfilled": [i.to_dict() for i in self.backfilled],
            "failed": [i.to_dict() for i in self.failed],
        }


# ── 候选扫描 ──────────────────────────────────────────────────────────────

def scan_sessions_for_backfill(
    session_manager: "SessionManager",
    *,
    exclude_ids: Optional[set[str]] = None,
    min_turns_for_backfill: int = 4,
) -> list["SessionMeta"]:
    """扫描"summary 为空、轮次达标"的候选 session，不限制时间窗口。

    按 `updated_at` 从旧到新排序（FIFO）：`max_sessions_per_run` 限流下，
    多轮扫描才能保证陈年 session 也总有机会被处理到，而不是每次都被
    "最近的"候选挤掉。

    只做扫描/分类，不执行任何生成/写入动作，供 `--dry-run` 和
    `backfill_sessions()` 复用。

    [session_backfill_index_incremental_plan.md] 底层走
    `SessionManager.get_backfill_candidate_metas()`——默认阈值（4）下直接
    读增量维护的候选索引，不再 `list_sessions(limit=100000)` 全量扫描 +
    逐个解析每一个历史 session 的 meta.json；候选数通常远小于 session
    总数，排序成本可以忽略。非默认阈值走原始全量扫描兜底（索引只按
    默认阈值维护成员资格）。
    """
    exclude_ids = exclude_ids or set()
    candidates = [
        m for m in session_manager.get_backfill_candidate_metas(min_turns_for_backfill=min_turns_for_backfill)
        if m.id not in exclude_ids
    ]
    return candidates


# ── 摘要生成（离线版，不依赖存活 Agent 实例）─────────────────────────────

def _extract_user_turns(history: list[dict]) -> list[str]:
    """从 session.history 里提取真实用户输入文本，逻辑对齐
    `agent/profile.py::_generate_and_save_summary` 的同名逻辑（那边读的
    是存活进程内的 `self._history`，这里读的是从磁盘加载的
    `Session.history`，两者结构一致，判定逻辑必须保持一致）。"""
    from mini_agent.history.entry import is_real_user_input

    return [
        m["content"] for m in history
        if is_real_user_input(m) and isinstance(m.get("content"), str)
    ]


def generate_summary_offline(
    history: list[dict], llm_client: "LLMClient",
) -> tuple[str, list[str]]:
    """离线生成一段 session 摘要文本。与
    `agent/profile.py::_generate_and_save_summary()` 里的摘要生成 prompt
    完全一致（同一套 `user/session_summary_request` +
    `system/summarizer` 模板），只是数据来源换成离线加载的 history，
    不依赖存活 Agent 实例的 `self._history`/`self.stats`。

    返回 `(summary, user_turns)`；`summary` 为空字符串表示没有可摘要的
    用户消息或 LLM 输出为空。`user_turns` 供调用方构造
    `MemoryEntry.key_outcomes`（对齐实时路径取 `user_turns[:3]` 的做法），
    调用方不需要重复调用一次 `_extract_user_turns`。
    """
    from mini_agent.prompts import pm

    user_turns = _extract_user_turns(history)
    if not user_turns:
        return "", user_turns

    turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[:10])
    prompt = pm.render("user/session_summary_request", turns_text=turns_text)
    resp = llm_client.chat_with_retry(
        messages=[{"role": "user", "content": prompt}],
        system=pm.render("system/summarizer"),
        tools=[],
        max_retries=10,
    )
    return (resp.text or "").strip(), user_turns


# ── [next_doc/growth_advisor_improvement_plan_v4.md 方向一 M3] cron 任务
# 收尾时直接产出记忆，不经过 Session/summary 中转 ─────────────────────────

def generate_summary_from_text(
    text: str, llm_client: "LLMClient", *, task_template: str = "", max_chars: int = 4000,
) -> str:
    """对一段已有文本（不是完整对话历史）做摘要，供 cron 任务收尾场景复用。

    跟 `generate_summary_offline()` 共享同一套 prompt 模板
    （`user/session_summary_request` + `system/summarizer`），只是输入侧从
    "用户发言列表"换成"单段任务产出文本"，避免为 cron 场景另建一套摘要
    prompt。`task_template`（job 本身的任务描述）会拼进摘要输入——否则
    摘要读起来会是"做了后续处理"这种没有上下文的碎片，看不出这段文本是
    在完成什么任务时产出的。

    `text` 为空或去除首尾空白后为空字符串时直接返回空字符串，不发起 LLM
    调用（调用方在 `run_job()` 收尾处已经做过 `last_text.strip()` 判断，
    这里再判断一次是为了让本函数本身也能被安全地独立调用/测试）。
    """
    text = (text or "").strip()
    if not text:
        return ""

    from mini_agent.prompts import pm

    turns_text = text[:max_chars]
    if task_template and task_template.strip():
        turns_text = f"[本次任务] {task_template.strip()[:200]}\n\n[任务产出] {turns_text}"

    prompt = pm.render("user/session_summary_request", turns_text=turns_text)
    resp = llm_client.chat_with_retry(
        messages=[{"role": "user", "content": prompt}],
        system=pm.render("system/summarizer"),
        tools=[],
        max_retries=10,
    )
    return (resp.text or "").strip()


def _is_similar_to_recent_cron_summary(
    memory_backend: "MemoryBackend", job_id: str, summary: str, *, similarity_threshold: float = 0.85,
) -> bool:
    """[1.5 节 幂等与去重] 同一个 job 连续多次触发如果任务模板高度相似
    （比如"每小时检查一次待办"这类几乎不变的任务），会持续产生高度雷同的
    记忆条目，稀释成长顾问信号扫描的信噪比。这里复用 `StuckDetector`
    同款的"文本相似度"判断思路（`difflib.SequenceMatcher`，规则实现，
    不是 embedding），只跟该 job 最近一条已生成的 cron 记忆摘要做比较，
    高度雷同则返回 True（调用方据此跳过本次记忆写入）。

    `memory_backend` 查询失败（比如后端不支持 `all_entries()`）时静默
    返回 False（不阻止本次记忆生成，去重是"锦上添花"，不能成为记忆
    生成路径上的一个新故障点）。
    """
    if not summary:
        return False
    try:
        entries = memory_backend.all_entries()
    except Exception:
        return False

    prefix = f"cron:{job_id}:"
    cron_entries = [e for e in entries if str(getattr(e, "session_id", "")).startswith(prefix)]
    if not cron_entries:
        return False
    latest = max(cron_entries, key=lambda e: getattr(e, "created_at", 0))
    ratio = _difflib.SequenceMatcher(None, latest.summary or "", summary).ratio()
    return ratio >= similarity_threshold


def backfill_cron_run(
    job_id: str,
    run_id: str,
    last_text: str,
    *,
    memory_backend: "MemoryBackend",
    llm_client: "LLMClient",
    model: str = "",
    task_template: str = "",
    similarity_threshold: float = 0.85,
) -> Optional["MemoryEntry"]:
    """cron 任务正常收尾（`STATUS_IDLE`）时，把最后一步的产出文本直接
    摘要成一条长期记忆，不经过 `Session`/`summary` 中转
    （`cron_agent_bridge.py` 的设计前提是"每次触发都重新构建 Agent，不
    跨触发保留 session 历史"，M1 的存量回填天然扫不到这类运行）。

    `session_id` 合成规则：`cron:<job_id>:<run_id>`（
    `memory_backfill_and_profile_update_plan.md` 第 4 节风险项 2 已核实——跟真实 `Session.id`
    取值空间不相交，`memory_store.py`/`growth_advisor.py` 对 `session_id`
    全部是字符串相等比较或展示切片，不做格式解析）。

    严格由调用方（`CronJobExecutor.run_job()`）保证只在
    `final_status == STATUS_IDLE` 且 `last_text` 非空时调用——本函数内部
    不重复判断收尾状态（不感知 `CronJobConfig`/`CronJobState` 这些
    cron 专属类型，保持跟 `backfill_sessions()` 一样"纯粹处理文本/记忆"
    的定位）。

    返回写入的 `MemoryEntry`；因为去重跳过、摘要为空（没有可摘要的实质
    内容）等原因未写入时返回 `None`。异常向上抛出，由调用方决定如何
    静默降级（`run_job()` 的收尾逻辑不能因为记忆生成失败而影响主流程，
    但这一点由调用方的 try/except 负责，不是本函数的职责）。
    """
    summary = generate_summary_from_text(last_text, llm_client, task_template=task_template)
    if not summary:
        return None

    if _is_similar_to_recent_cron_summary(
        memory_backend, job_id, summary, similarity_threshold=similarity_threshold,
    ):
        return None

    from mini_agent.perception.memory_store import MemoryEntry
    import re as _re2

    tags = list({w.lower() for w in _re2.findall(r"[a-zA-Z一-鿿]{3,}", summary)})[:8]
    entry = MemoryEntry(
        session_id=f"cron:{job_id}:{run_id}",
        summary=summary,
        key_outcomes=[last_text.strip()[:200]] if last_text.strip() else [],
        tags=tags,
        model=model,
    )
    memory_backend.upsert(entry)
    return entry


def _build_memory_entry(session_id: str, summary: str, user_turns: list[str], model: str) -> "MemoryEntry":
    from mini_agent.perception.memory_store import MemoryEntry

    tags = list({
        w.lower() for w in _re.findall(r"[a-zA-Z一-鿿]{3,}", summary)
    })[:8]
    return MemoryEntry(
        session_id=session_id,
        summary=summary,
        key_outcomes=user_turns[:3],
        tags=tags,
        model=model,
    )


# ── 主流程 ────────────────────────────────────────────────────────────────

def backfill_sessions(
    session_manager: "SessionManager",
    *,
    memory_backend: "MemoryBackend",
    llm_client: "LLMClient",
    model: str = "",
    exclude_ids: Optional[set[str]] = None,
    min_turns_for_backfill: int = 4,
    max_sessions_per_run: int = 20,
    dry_run: bool = True,
) -> BackfillReport:
    """扫描 + （可选）执行回填。

    每个候选 session 独立 try/except：单条失败不影响其它候选，失败的
    候选下次扫描会自然重新进入候选列表（判定条件仍是 summary 为空），
    不需要额外的重试计数器/失败标记。
    """
    candidates = scan_sessions_for_backfill(
        session_manager,
        exclude_ids=exclude_ids,
        min_turns_for_backfill=min_turns_for_backfill,
    )
    report = BackfillReport(dry_run=dry_run, total_candidates=len(candidates))

    for meta in candidates[:max_sessions_per_run]:
        try:
            session = session_manager.load(meta.id)
            if session is None:
                report.failed.append(BackfillItem(
                    session_id=meta.id, title=meta.title, updated_at=meta.updated_at,
                    turns=meta.turns, action="failed", reason="session 加载失败",
                ))
                continue

            summary, user_turns = generate_summary_offline(session.history or [], llm_client)
            if not summary:
                report.failed.append(BackfillItem(
                    session_id=meta.id, title=meta.title, updated_at=meta.updated_at,
                    turns=meta.turns, action="failed",
                    reason="没有可摘要的用户消息，或 LLM 输出为空",
                ))
                continue

            if not dry_run:
                entry = _build_memory_entry(
                    meta.id, summary, user_turns, model or session.model or "",
                )
                memory_backend.upsert(entry)
                session_manager.mark_summary_backfilled(
                    meta.id, summary, session.stats.get("turns", meta.turns),
                )

            report.backfilled.append(BackfillItem(
                session_id=meta.id, title=meta.title, updated_at=meta.updated_at,
                turns=meta.turns, action="backfilled",
                reason=f"补生成摘要（{len(summary)} 字）并写入长期记忆",
            ))
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.evolution.memory_backfill.backfill_sessions")
            report.failed.append(BackfillItem(
                session_id=meta.id, title=meta.title, updated_at=meta.updated_at,
                turns=meta.turns, action="failed", reason=str(exc)[:200],
            ))

    return report


def format_report_lines(report: BackfillReport) -> list[str]:
    """把 BackfillReport 渲染成人类可读的行列表，CLI/cron 复用同一份文案。"""
    verb = "将回填" if report.dry_run else "已回填"
    lines = [
        f"记忆回填{'（dry-run，不会实际写入）' if report.dry_run else ''}："
        f"共发现候选 {report.total_candidates} 个，本轮处理 {report.total_processed} 个"
        f"（受 max_sessions_per_run 限流），{verb} {len(report.backfilled)} 个，"
        f"失败/跳过 {len(report.failed)} 个。",
    ]
    for item in report.backfilled:
        lines.append(f"  [{verb}] {item.session_id}  {item.title}  — {item.reason}")
    for item in report.failed:
        lines.append(f"  [跳过] {item.session_id}  {item.title}  — {item.reason}")
    return lines
