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
    """
    exclude_ids = exclude_ids or set()
    all_metas = session_manager.list_sessions(limit=100000)

    def _parse_updated_at(updated_at: str) -> float:
        if not updated_at:
            return 0.0
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0.0

    candidates = [
        m for m in all_metas
        if m.id not in exclude_ids
        and not (m.summary or "").strip()
        and m.turns >= min_turns_for_backfill
    ]
    candidates.sort(key=lambda m: _parse_updated_at(m.updated_at))
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
