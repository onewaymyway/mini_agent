"""
evolution/session_cleanup.py — Session 清理（长期运行后 sessions 目录膨胀治理）

背景：`.agent/sessions/<id>/` 每个 session 一个目录，长期运行（尤其 daemon
7x24 模式）后会越积越多，其中大部分早已用不到。本模块提供一套纯 Python、
确定性的扫描/清理逻辑（风格对齐 consolidation.py::prune_skills），不依赖
LLM 做判断——"删不删"是规则问题，不是需要模型裁量的问题；只有"删之前要不要
先补一次知识抽取"这一步会调用 LLM（可选）。

判定规则（保留优先级从高到低，命中任意一条即保留，不删）：
  1. exclude_ids           —— 调用方显式排除（典型：当前正在运行的 session）
  2. pinned                —— meta.json.pinned=true，用户手动置顶保护
  3. goal 仍在进行          —— 目录下 goal_state.json 存在且 status 未终结
                               （running/stuck，见 goal_mode/state.py）
  4. keep_recent_count      —— 按 updated_at 倒序，最近 N 个总是保留（安全网）
  5. keep_recent_days       —— updated_at 在最近 N 天内的总是保留（安全网）

不满足以上任何一条的 session 才进入"候选删除"，候选删除的 session 还要过
一次知识抽取门槛：
  - turns < min_turns_for_extraction              → 视为内容太少，无需抽取，直接可删
  - meta.json.knowledge_extracted == True          → 已经抽取过，可删
  - 否则                                            → "待抽取"
      * extract_first=False（默认，manual 场景更保守）：跳过，不删，只报告
      * extract_first=True （cron 场景，用户已确认默认开启）：离线跑一次抽取
        （HistoryManager.dispatch_extraction_for_entries，复用现有的
        decision/world extraction pipeline），成功后标记
        knowledge_extracted=True 再删除；抽取失败则跳过、不删（下次再试）。

cron 任务不属于以上任何保留规则的来源——cron 任务本身不复用/不持有用户会话
的 session（见 evolution/cron_agent_bridge.py 顶部说明：每次触发都重新构建
Agent，不跨触发保留 session 历史），所以不需要为它单独扫描"是否被 cron 引用"。

workflow_sessions/（工作流每个 step 的子 Agent 数据）是完全独立的目录树，
不在 `.agent/sessions/` 下，天然不受本模块影响。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.session import SessionManager, SessionMeta
    from mini_agent.llm.base import LLMClient


# ── 默认阈值（与设计方案确认一致）───────────────────────────────────────────

DEFAULT_KEEP_RECENT_DAYS = 30
DEFAULT_KEEP_RECENT_COUNT = 20
DEFAULT_MIN_TURNS_FOR_EXTRACTION = 3


@dataclass
class CleanupItem:
    """单个 session 的清理判定结果，供报告展示和实际执行共用。"""
    session_id: str
    title: str
    updated_at: str
    turns: int
    action: str            # "keep" | "skip_pending_extraction" | "delete"
    reason: str             # 人类可读原因
    extracted_now: bool = False  # 本次是否临时触发了离线抽取

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "turns": self.turns,
            "action": self.action,
            "reason": self.reason,
            "extracted_now": self.extracted_now,
        }


@dataclass
class CleanupReport:
    dry_run: bool
    kept: list[CleanupItem] = field(default_factory=list)
    skipped_pending_extraction: list[CleanupItem] = field(default_factory=list)
    deleted: list[CleanupItem] = field(default_factory=list)
    failed: list[CleanupItem] = field(default_factory=list)  # 尝试删除/抽取但失败

    @property
    def total_scanned(self) -> int:
        return (
            len(self.kept) + len(self.skipped_pending_extraction)
            + len(self.deleted) + len(self.failed)
        )

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "total_scanned": self.total_scanned,
            "kept": [i.to_dict() for i in self.kept],
            "skipped_pending_extraction": [i.to_dict() for i in self.skipped_pending_extraction],
            "deleted": [i.to_dict() for i in self.deleted],
            "failed": [i.to_dict() for i in self.failed],
        }


# ── 保留判定 ──────────────────────────────────────────────────────────────

def _running_goal_session_ids(project_root) -> set[str]:
    """有未终结 goal（running/stuck）挂在自己身上的 session id 集合。"""
    try:
        from mini_agent.goal_mode.state import list_resumable_sessions
        entries = list_resumable_sessions(project_root, include_stuck=True)
        return {e["session_id"] for e in entries if e.get("session_id")}
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup._running_goal_session_ids')
        return set()


def _parse_updated_at(updated_at: str) -> Optional[float]:
    """把 meta.age_str 同款的 updated_at 字符串解析成 epoch 秒，解析失败返回 None
    （None 会被当作"无法判断新旧"，出于安全起见按"最近"处理，不参与删除）。"""
    if not updated_at:
        return None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


# ── 主流程 ────────────────────────────────────────────────────────────────

def scan_sessions_for_cleanup(
    session_manager: "SessionManager",
    project_root,
    *,
    exclude_ids: Optional[set[str]] = None,
    keep_recent_days: float = DEFAULT_KEEP_RECENT_DAYS,
    keep_recent_count: int = DEFAULT_KEEP_RECENT_COUNT,
    min_turns_for_extraction: int = DEFAULT_MIN_TURNS_FOR_EXTRACTION,
) -> tuple[list["SessionMeta"], list["SessionMeta"]]:
    """只做扫描和分类，不执行任何抽取/删除动作（供 --dry-run 和上层复用）。

    Returns:
        (kept_metas, candidate_metas) —— candidate_metas 是"不满足任何保留
        规则、进入候选删除范围"的 session 列表，还没有区分是否需要先抽取。
    """
    exclude_ids = exclude_ids or set()
    all_metas = session_manager.list_sessions(limit=100000)
    protected_by_goal = _running_goal_session_ids(project_root)

    now = time.time()
    keep_days_cutoff = now - keep_recent_days * 86400.0

    # 按更新时间倒序（list_sessions 本身已是倒序，这里显式排序防止上游行为变化）
    def _sort_key(m: "SessionMeta") -> float:
        ts = _parse_updated_at(m.updated_at)
        return ts if ts is not None else now  # 解析失败当作"刚刚"，天然进最近窗口

    all_metas = sorted(all_metas, key=_sort_key, reverse=True)

    kept: list["SessionMeta"] = []
    candidates: list["SessionMeta"] = []

    for idx, m in enumerate(all_metas):
        if m.id in exclude_ids:
            kept.append(m)
            continue
        if m.pinned:
            kept.append(m)
            continue
        if m.id in protected_by_goal:
            kept.append(m)
            continue
        if idx < keep_recent_count:
            kept.append(m)
            continue
        ts = _parse_updated_at(m.updated_at)
        if ts is None or ts >= keep_days_cutoff:
            kept.append(m)
            continue
        candidates.append(m)

    return kept, candidates


def cleanup_sessions(
    session_manager: "SessionManager",
    project_root,
    *,
    exclude_ids: Optional[set[str]] = None,
    keep_recent_days: float = DEFAULT_KEEP_RECENT_DAYS,
    keep_recent_count: int = DEFAULT_KEEP_RECENT_COUNT,
    min_turns_for_extraction: int = DEFAULT_MIN_TURNS_FOR_EXTRACTION,
    extract_first: bool = False,
    llm_client: Optional["LLMClient"] = None,
    cfg=None,
    dry_run: bool = True,
) -> CleanupReport:
    """扫描 + （可选）执行清理。

    extract_first=True 但缺少 llm_client/cfg 时会自动退化为"跳过待抽取的
    session、只清理已抽取或内容过少的"，不会报错——保证这个函数在任何调用
    环境下都是安全的、可预测的。
    """
    kept_metas, candidate_metas = scan_sessions_for_cleanup(
        session_manager,
        project_root,
        exclude_ids=exclude_ids,
        keep_recent_days=keep_recent_days,
        keep_recent_count=keep_recent_count,
        min_turns_for_extraction=min_turns_for_extraction,
    )

    report = CleanupReport(dry_run=dry_run)
    for m in kept_metas:
        report.kept.append(CleanupItem(
            session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
            action="keep", reason="在用/受保护/在最近保留窗口内",
        ))

    can_extract = extract_first and llm_client is not None and cfg is not None

    for m in candidate_metas:
        needs_extraction = (
            m.turns >= min_turns_for_extraction and not m.knowledge_extracted
        )
        if not needs_extraction:
            item = CleanupItem(
                session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
                action="delete",
                reason=(
                    "已抽取过知识" if m.knowledge_extracted
                    else f"内容过少（turns={m.turns} < {min_turns_for_extraction}），无需抽取"
                ),
            )
            _finish_deletion(session_manager, item, report, dry_run)
            continue

        if not can_extract:
            report.skipped_pending_extraction.append(CleanupItem(
                session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
                action="skip_pending_extraction",
                reason="尚未抽取知识，且本次未启用 --extract-first，保守跳过",
            ))
            continue

        ok = _extract_then_mark(session_manager, m.id, llm_client, cfg, dry_run=dry_run)
        if ok:
            item = CleanupItem(
                session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
                action="delete", reason="已补跑离线知识抽取后删除", extracted_now=True,
            )
            _finish_deletion(session_manager, item, report, dry_run)
        else:
            report.failed.append(CleanupItem(
                session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
                action="skip_pending_extraction",
                reason="离线抽取失败，本次跳过删除（下次清理会重试）",
            ))

    return report


def _extract_then_mark(session_manager, session_id: str, llm_client, cfg, *, dry_run: bool) -> bool:
    """加载 session 的 raw_history，跑一次离线抽取，成功则标记 knowledge_extracted。"""
    try:
        from mini_agent.history_manager import HistoryManager

        session = session_manager.load(session_id)
        if session is None:
            return False

        session_dir = Path(session_manager.session_dir) / session.id
        raw_path = session_dir / "raw_history.jsonl"
        raw_entries: list[dict] = []
        if raw_path.exists():
            from mini_agent.history.raw_history import RawHistory
            rh = RawHistory()
            rh.load_from_file(raw_path)
            raw_entries = list(rh.entries)
        if not raw_entries:
            # 没有 raw_history（比如极老的旧格式 session），退化用 history.json
            raw_entries = list(session.history or [])

        hist = HistoryManager(cfg=cfg)
        success = hist.dispatch_extraction_for_entries(
            raw_entries, llm_client, trigger_reason="session_cleanup",
        )
        if success and not dry_run:
            session_manager.mark_knowledge_extracted(session_id, True)
        return success
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup._extract_then_mark')
        return False


def _finish_deletion(session_manager, item: CleanupItem, report: CleanupReport, dry_run: bool) -> None:
    if dry_run:
        report.deleted.append(item)
        return
    try:
        ok = session_manager.delete(item.session_id)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup._finish_deletion')
        ok = False
    if ok:
        report.deleted.append(item)
    else:
        item.reason += "；删除失败"
        report.failed.append(item)


def format_report_lines(report: CleanupReport) -> list[str]:
    """把 CleanupReport 渲染成人类可读的行列表，CLI/cron 复用同一份文案。"""
    verb = "将删除" if report.dry_run else "已删除"
    lines = [
        f"Session 清理{'（dry-run，不会实际删除）' if report.dry_run else ''}："
        f"共扫描 {report.total_scanned} 个，保留 {len(report.kept)} 个，"
        f"{verb} {len(report.deleted)} 个，"
        f"待抽取跳过 {len(report.skipped_pending_extraction)} 个，"
        f"失败 {len(report.failed)} 个。",
    ]
    for item in report.deleted:
        extra = "（先补抽取）" if item.extracted_now else ""
        lines.append(f"  [{verb}]{extra} {item.session_id}  {item.title}  — {item.reason}")
    for item in report.skipped_pending_extraction:
        lines.append(f"  [跳过] {item.session_id}  {item.title}  — {item.reason}")
    for item in report.failed:
        lines.append(f"  [失败] {item.session_id}  {item.title}  — {item.reason}")
    return lines
