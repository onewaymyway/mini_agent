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

产出保护（始终开启）：会话的 output/ 下有文件时保留该会话，不删除用户产出。

workflow_sessions/<wid>/（工作流运行目录）由 workflow_run_cleanup.py 处理，
cleanup_sessions(include_workflow_runs=True) 时一并清理，判定规则见该模块。
include_orphans=True 时还会清理没有任何会话/工作流目录引用的孤立 raw_results。
详见 next_doc/session_cleanup_scope_expansion_plan.md。
"""

from __future__ import annotations

import shutil
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
DEFAULT_ADHOC_TEMP_MIN_AGE_DAYS = 7

# 孤儿目录（有目录、没 meta.json）专用阈值：
# session 目录在 raw_history.set_path() 时就已创建，meta.json 要等一轮对话
# 跑完才写入（见 turn_loop.py::save_session 的调用点）。所以"刚创建几分钟"
# 的孤儿目录很可能是正在跑的第一轮，不是真孤儿——必须给一个最小年龄安全网，
# 否则会把正在进行中的 session 删掉。
DEFAULT_ORPHAN_MIN_AGE_HOURS = 6.0


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
    # 该 session 对应的 <project_root>/.agent/raw_results/<session_id>/ 目录大小
    # （字节）。dry-run 时是"将释放"，实际执行时是"已释放"；没有该目录则为 0。
    raw_results_bytes: int = 0
    # sessions/<id>/output/ 下的产出文件数（>0 时受产出保护，不会被删除）
    output_files: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "turns": self.turns,
            "action": self.action,
            "reason": self.reason,
            "extracted_now": self.extracted_now,
            "raw_results_bytes": self.raw_results_bytes,
            "output_files": self.output_files,
        }


@dataclass
class RawOrphanItem:
    """raw_results/<name>/ 中没有任何会话/工作流目录引用的孤立目录（见 scan_orphan_raw_results）。"""
    dir_name: str
    size_bytes: int
    action: str            # "keep" | "delete" | "failed"
    reason: str

    def to_dict(self) -> dict:
        return {
            "dir_name": self.dir_name,
            "size_bytes": self.size_bytes,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class OrphanItem:
    """孤儿目录（有目录、没 meta.json）的判定结果——一轮对话都没跑完就
    中断（daemon 重启/被杀/cron 子 agent 提前失败）留下的残留目录，对
    list_sessions()/cleanup_sessions() 完全不可见，需要单独扫描处理。"""
    dir_name: str
    last_activity: str   # ISO 时间字符串，取目录内文件最后修改时间（没有文件则取目录本身 mtime）
    size_bytes: int
    action: str            # "keep" | "delete"
    reason: str
    # 孤儿目录同名的 raw_results/<dir_name>/ 目录大小（字节），含义同 CleanupItem
    raw_results_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "dir_name": self.dir_name,
            "last_activity": self.last_activity,
            "size_bytes": self.size_bytes,
            "action": self.action,
            "reason": self.reason,
            "raw_results_bytes": self.raw_results_bytes,
        }


@dataclass
class CleanupReport:
    dry_run: bool
    kept: list[CleanupItem] = field(default_factory=list)
    skipped_pending_extraction: list[CleanupItem] = field(default_factory=list)
    deleted: list[CleanupItem] = field(default_factory=list)
    failed: list[CleanupItem] = field(default_factory=list)  # 尝试删除/抽取但失败

    # 孤儿目录（有目录、没 meta.json）单独统计，不与上面几个列表混在一起——
    # 它们不是"session"（没有 meta 就无法构成 SessionMeta），是磁盘残留。
    orphan_kept: list[OrphanItem] = field(default_factory=list)
    orphan_deleted: list[OrphanItem] = field(default_factory=list)
    orphan_failed: list[OrphanItem] = field(default_factory=list)

    # 工作流运行目录（.agent/workflow_sessions/<wid>/），见 workflow_run_cleanup.py
    workflow_kept: list = field(default_factory=list)      # list[WorkflowRunItem]
    workflow_deleted: list = field(default_factory=list)
    workflow_failed: list = field(default_factory=list)

    # adhoc/<key>/temp 过期临时条目（output/ 永不触碰）
    adhoc_temp_deleted: list[RawOrphanItem] = field(default_factory=list)

    # 孤立 raw_results 目录（仅 include_orphans=True 时扫描）
    raw_orphan_kept: list[RawOrphanItem] = field(default_factory=list)
    raw_orphan_deleted: list[RawOrphanItem] = field(default_factory=list)
    raw_orphan_failed: list[RawOrphanItem] = field(default_factory=list)

    @property
    def output_protected_count(self) -> int:
        """因含产出文件而被保留的会话数（会话 + 工作流运行）。"""
        n = sum(1 for i in self.kept if i.output_files > 0)
        n += sum(1 for i in self.workflow_kept if i.output_files > 0)
        return n

    @property
    def workflow_total_scanned(self) -> int:
        return len(self.workflow_kept) + len(self.workflow_deleted) + len(self.workflow_failed)

    @property
    def total_scanned(self) -> int:
        return (
            len(self.kept) + len(self.skipped_pending_extraction)
            + len(self.deleted) + len(self.failed)
        )

    @property
    def orphan_total_scanned(self) -> int:
        return len(self.orphan_kept) + len(self.orphan_deleted) + len(self.orphan_failed)

    @property
    def raw_results_total_bytes(self) -> int:
        """被删（或 dry-run 将被删）的 session / 孤儿目录对应的 raw_results 总大小。"""
        return (
            sum(i.raw_results_bytes for i in self.deleted)
            + sum(i.raw_results_bytes for i in self.orphan_deleted)
            + sum(i.raw_results_bytes for i in self.workflow_deleted)
        )

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "total_scanned": self.total_scanned,
            "kept": [i.to_dict() for i in self.kept],
            "skipped_pending_extraction": [i.to_dict() for i in self.skipped_pending_extraction],
            "deleted": [i.to_dict() for i in self.deleted],
            "failed": [i.to_dict() for i in self.failed],
            "orphan_total_scanned": self.orphan_total_scanned,
            "orphan_kept": [i.to_dict() for i in self.orphan_kept],
            "orphan_deleted": [i.to_dict() for i in self.orphan_deleted],
            "orphan_failed": [i.to_dict() for i in self.orphan_failed],
            "raw_results_total_bytes": self.raw_results_total_bytes,
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


# ── 孤儿目录（有目录、没 meta.json）─────────────────────────────────────────

def _dir_last_activity(path: Path) -> float:
    """目录内所有文件里最新的 mtime；目录为空/不可读则退化为目录本身 mtime。
    不能只看目录自身 mtime——在已存在的文件里 append（比如持续写
    raw_history.jsonl）不会更新父目录的 mtime，会把"还在写"的目录误判成
    "早就不动了"。"""
    latest = path.stat().st_mtime
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    latest = max(latest, f.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return latest


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


# ── raw_results 联动清理 ──────────────────────────────────────────────────
#
# 工具结果原文会落盘到 <project_root>/.agent/raw_results/<session_id>/
# （perception/raw_result_store.py），只有对应 session 的历史会引用它。
# SessionManager.delete() 只删 sessions/<id>/，不会碰 raw_results，session 被清理
# 后这些原文就成了无人引用的残留，所以清理 session 时要一并带走。

_RAW_RESULTS_SUBDIR = ".agent/raw_results"


def _raw_results_dir_for(project_root, session_id: str) -> Optional[Path]:
    """返回 session 对应的 raw_results 目录（存在才返回，否则 None）。

    session_id 必须是单层目录名（不含路径分隔符 / 上跳），且解析后确实落在
    raw_results 根目录的直接子级——防止脏数据里的 id 让删除指向别处。
    """
    if not project_root or not session_id:
        return None
    if session_id in (".", "..") or Path(session_id).name != session_id:
        return None
    try:
        root = (Path(project_root) / _RAW_RESULTS_SUBDIR).resolve()
        target = (root / session_id).resolve()
    except (OSError, ValueError):
        return None
    if target.parent != root or not target.is_dir():
        return None
    return target


def _clear_raw_results(project_root, session_id: str, *, dry_run: bool) -> tuple[int, str]:
    """清理（dry_run 时只统计）某个 session 的 raw_results 目录。

    返回 (大小字节, 失败说明)。没有对应目录 → (0, "")；命中受保护清单或
    删除失败 → (0, 说明)——调用方把说明追加进 reason，不影响 session 本身的
    删除结果（session 已经删掉了，raw_results 残留只是少清了一点）。
    """
    target = _raw_results_dir_for(project_root, session_id)
    if target is None:
        return 0, ""
    size = _dir_size(target)
    if dry_run:
        return size, ""
    try:
        from mini_agent.utils.protected_files_guard import is_protected as _is_protected
        if _is_protected(target, project_root):
            return 0, "raw_results 命中受保护文件清单，已保留"
        shutil.rmtree(target, ignore_errors=False)
        return size, ""
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup._clear_raw_results')
        return 0, "raw_results 清理失败"


def scan_orphan_session_dirs(
    session_manager: "SessionManager",
    project_root,
    *,
    exclude_ids: Optional[set[str]] = None,
    min_age_hours: float = DEFAULT_ORPHAN_MIN_AGE_HOURS,
) -> tuple[list[OrphanItem], list[Path]]:
    """扫描 session_dir 下"有目录、没 meta.json"的孤儿目录，只分类不删除。

    保留规则（命中任意一条即保留）：
      1. exclude_ids           —— 当前正在使用的 session id（正常 session 也可能
                                   命中，多一层保险不影响正确性）
      2. goal 仍在进行           —— 目录下 goal_state.json 存在且 status 未终结
                                    （沿用 _running_goal_session_ids，它本身按
                                    goal_state.json 扫描，不依赖 meta.json）
      3. 目录年龄 < min_age_hours —— 安全网：meta.json 要等一轮对话跑完才写入，
                                     太新的目录很可能是正在进行中的第一轮，
                                     不是真孤儿

    Returns:
        (items, delete_paths) —— items 是全部孤儿目录的分类结果（含 keep 和
        delete）；delete_paths 是其中判定为 delete 的目录路径，供
        cleanup_orphan_session_dirs() 复用，避免重复扫描一次磁盘。
    """
    exclude_ids = exclude_ids or set()
    protected_by_goal = _running_goal_session_ids(project_root)
    now = time.time()
    age_cutoff = now - min_age_hours * 3600.0

    items: list[OrphanItem] = []
    delete_paths: list[Path] = []

    try:
        candidates = [d for d in session_manager.session_dir.iterdir() if d.is_dir()]
    except OSError:
        return items, delete_paths

    for d in candidates:
        if (d / "meta.json").exists():
            continue  # 不是孤儿，正常 session，交给 scan_sessions_for_cleanup 处理

        last_activity = _dir_last_activity(d)
        from datetime import datetime, timezone
        last_activity_str = datetime.fromtimestamp(last_activity, tz=timezone.utc).isoformat()
        size_bytes = _dir_size(d)

        if d.name in exclude_ids:
            items.append(OrphanItem(
                dir_name=d.name, last_activity=last_activity_str, size_bytes=size_bytes,
                action="keep", reason="当前正在使用的 session",
            ))
            continue
        if d.name in protected_by_goal:
            items.append(OrphanItem(
                dir_name=d.name, last_activity=last_activity_str, size_bytes=size_bytes,
                action="keep", reason="goal 仍在进行（running/stuck）",
            ))
            continue
        if last_activity >= age_cutoff:
            items.append(OrphanItem(
                dir_name=d.name, last_activity=last_activity_str, size_bytes=size_bytes,
                action="keep",
                reason=f"目录年龄 < {min_age_hours} 小时，可能是正在进行中的第一轮，暂不判定为孤儿",
            ))
            continue

        items.append(OrphanItem(
            dir_name=d.name, last_activity=last_activity_str, size_bytes=size_bytes,
            action="delete",
            reason="有目录无 meta.json（一轮对话未跑完即中断），超过安全窗口，判定为孤儿目录",
        ))
        delete_paths.append(d)

    return items, delete_paths


def cleanup_orphan_session_dirs(
    session_manager: "SessionManager",
    project_root,
    *,
    exclude_ids: Optional[set[str]] = None,
    min_age_hours: float = DEFAULT_ORPHAN_MIN_AGE_HOURS,
    dry_run: bool = True,
) -> tuple[list[OrphanItem], list[OrphanItem], list[OrphanItem]]:
    """扫描 + （可选）执行孤儿目录清理。返回 (kept, deleted, failed)。"""
    items, delete_paths = scan_orphan_session_dirs(
        session_manager, project_root, exclude_ids=exclude_ids, min_age_hours=min_age_hours,
    )
    delete_by_name = {p.name: p for p in delete_paths}

    kept = [i for i in items if i.action != "delete"]
    deleted: list[OrphanItem] = []
    failed: list[OrphanItem] = []

    for item in items:
        if item.action != "delete":
            continue
        if dry_run:
            item.raw_results_bytes, _ = _clear_raw_results(
                project_root, item.dir_name, dry_run=True)
            deleted.append(item)
            continue
        path = delete_by_name.get(item.dir_name)
        try:
            if path is not None and path.is_dir():
                from mini_agent.utils.protected_files_guard import is_protected as _is_protected
                if _is_protected(path, project_root):
                    item.reason += "；命中受保护文件清单，跳过删除"
                    failed.append(item)
                    continue
                shutil.rmtree(path, ignore_errors=False)
                item.raw_results_bytes, _rr_note = _clear_raw_results(
                    project_root, item.dir_name, dry_run=False)
                if _rr_note:
                    item.reason += "；" + _rr_note
                deleted.append(item)
            else:
                item.reason += "；目录已不存在"
                failed.append(item)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup.cleanup_orphan_session_dirs')
            item.reason += "；删除失败"
            failed.append(item)

    if not dry_run and deleted:
        _invalidate_metas_cache_safe(session_manager)

    return kept, deleted, failed


def _invalidate_metas_cache_safe(session_manager: "SessionManager") -> None:
    """孤儿目录删除不影响 metas 缓存内容（孤儿本来就不在缓存里），但保险起见
    走一次标准失效路径，避免未来 orphan 判定逻辑变化后出现缓存不一致。"""
    try:
        from mini_agent.session import _invalidate_metas_cache
        _invalidate_metas_cache(session_manager.session_dir)
    except Exception:
        pass


# ── 孤立 raw_results（raw_results/<name>/ 无任何会话/工作流目录引用）─────────

def scan_orphan_raw_results(
    project_root,
    sessions_dir,
    *,
    min_age_hours: float = DEFAULT_ORPHAN_MIN_AGE_HOURS,
    dry_run: bool = True,
) -> tuple[list[RawOrphanItem], list[RawOrphanItem], list[RawOrphanItem]]:
    """扫描并（可选）删除没有任何会话目录引用的 raw_results/<name>/。

    已知 id 集合 = sessions_dir 下所有目录名 ∪ workflow_sessions 下所有子目录名
    （工作流 step 子 Agent 的 session id 位于其中，它们的 raw_results 不算孤立）。
    目录最后活动时间早于 min_age_hours 才判定为孤立，避免误删刚写入的原文。

    Returns: (kept, deleted, failed)
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.utils.protected_files_guard import is_protected as _is_protected

    kept: list[RawOrphanItem] = []
    deleted: list[RawOrphanItem] = []
    failed: list[RawOrphanItem] = []

    if not project_root:
        return kept, deleted, failed
    try:
        root = (Path(project_root) / _RAW_RESULTS_SUBDIR).resolve()
    except (OSError, ValueError):
        return kept, deleted, failed
    if not root.is_dir():
        return kept, deleted, failed

    known: set[str] = set()
    if sessions_dir and Path(sessions_dir).is_dir():
        known.update(d.name for d in Path(sessions_dir).iterdir() if d.is_dir())
    try:
        wf_base = AgentPaths(Path(project_root)).workflow_sessions_dir
        if wf_base.is_dir():
            known.update(d.name for d in wf_base.rglob("*") if d.is_dir())
    except Exception:
        # 无法可靠枚举工作流目录时，保守起见不做任何删除
        return kept, deleted, failed

    cutoff = time.time() - min_age_hours * 3600.0
    try:
        candidates = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return kept, deleted, failed

    for d in candidates:
        size = _dir_size(d)
        if d.name in known:
            continue  # 仍被会话或工作流引用，不是孤立
        if _dir_last_activity(d) >= cutoff:
            kept.append(RawOrphanItem(
                dir_name=d.name, size_bytes=size, action="keep",
                reason=f"无对应会话，但目录年龄 < {min_age_hours} 小时，暂不判定为孤立"))
            continue
        item = RawOrphanItem(
            dir_name=d.name, size_bytes=size, action="delete",
            reason="raw_results 无任何会话/工作流目录引用，超过安全窗口，判定为孤立")
        if dry_run:
            deleted.append(item)
            continue
        try:
            if _is_protected(d, project_root):
                item.action = "failed"
                item.reason += "；命中受保护文件清单，跳过删除"
                failed.append(item)
                continue
            shutil.rmtree(d, ignore_errors=False)
            deleted.append(item)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc,
                          where='mini_agent.evolution.session_cleanup.scan_orphan_raw_results')
            item.action = "failed"
            item.reason += "；删除失败"
            failed.append(item)

    return kept, deleted, failed


# ── adhoc 临时目录（.agent/adhoc/<key>/temp/）─────────────────────────────────

def cleanup_adhoc_temp(project_root, *, min_age_days: float = DEFAULT_ADHOC_TEMP_MIN_AGE_DAYS,
                       dry_run: bool = True) -> list[RawOrphanItem]:
    """删除 .agent/adhoc/<key>/temp/ 下超过 min_age_days 未活动的条目。

    只碰 temp/：output/ 是产出，永不触碰。目录内最后活动时间（递归取最新 mtime）
    未过期的条目保留，避免删除正在被无会话任务使用的临时文件。
    返回删除（或 dry-run 将删除）的条目列表，dir_name 形如 "unbound/temp/<name>"。
    """
    from mini_agent.utils.protected_files_guard import is_protected as _is_protected

    out: list[RawOrphanItem] = []
    if not project_root:
        return out
    adhoc_root = Path(project_root) / ".agent" / "adhoc"
    if not adhoc_root.is_dir():
        return out
    cutoff = time.time() - min_age_days * 86400.0
    for key_dir in adhoc_root.iterdir():
        temp = key_dir / "temp"
        if not key_dir.is_dir() or not temp.is_dir():
            continue
        for entry in temp.iterdir():
            label = f"{key_dir.name}/temp/{entry.name}"
            if _dir_last_activity(entry) >= cutoff:
                continue
            size = _dir_size(entry) if entry.is_dir() else entry.stat().st_size
            item = RawOrphanItem(
                dir_name=label, size_bytes=size, action="delete",
                reason=f"adhoc temp 超过 {min_age_days} 天未活动",
            )
            if dry_run:
                out.append(item)
                continue
            try:
                if _is_protected(entry, project_root):
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=False)
                else:
                    entry.unlink()
                out.append(item)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc,
                              where='mini_agent.evolution.session_cleanup.cleanup_adhoc_temp')
    return out


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
    include_orphans: bool = False,
    orphan_min_age_hours: float = DEFAULT_ORPHAN_MIN_AGE_HOURS,
    protect_outputs: bool = False,
    include_workflow_runs: bool = True,
    include_adhoc_temp: bool = True,
    adhoc_temp_min_age_days: float = DEFAULT_ADHOC_TEMP_MIN_AGE_DAYS,
) -> CleanupReport:
    """扫描 + （可选）执行清理。

    protect_outputs（默认 False，用户决定关闭产出保护）：True 时会话的 output/ 下
    有文件就保留该会话；False 时照常删除，但每条删除项都会记录 output_files，
    预览与报告会明确提示"将删除产出文件 N 个"。
    include_workflow_runs：同时清理 <project_root>/.agent/workflow_sessions/ 下的
    工作流运行目录（判定见 workflow_run_cleanup.py）。project_root 为 None 或
    不含 workflow_sessions 时自动跳过。

    extract_first=True 但缺少 llm_client/cfg 时会自动退化为"跳过待抽取的
    session、只清理已抽取或内容过少的"，不会报错——保证这个函数在任何调用
    环境下都是安全的、可预测的。

    include_orphans=True 时额外扫描"有目录、没 meta.json"的孤儿目录（见
    scan_orphan_session_dirs 的说明），结果写在 report.orphan_kept /
    orphan_deleted / orphan_failed，与正常 session 的清理结果分开统计，
    不影响 report.total_scanned 等原有字段（向后兼容旧调用方）。
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
        if protect_outputs:
            out_n = _session_output_file_count(session_manager, m.id)
            if out_n:
                report.kept.append(CleanupItem(
                    session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
                    action="keep", reason=f"含产出文件 {out_n} 个，受保护",
                    output_files=out_n,
                ))
                continue
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
            _finish_deletion(session_manager, item, report, dry_run, project_root)
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
            _finish_deletion(session_manager, item, report, dry_run, project_root)
        else:
            report.failed.append(CleanupItem(
                session_id=m.id, title=m.title, updated_at=m.updated_at, turns=m.turns,
                action="skip_pending_extraction",
                reason="离线抽取失败，本次跳过删除（下次清理会重试）",
            ))

    if include_orphans:
        orphan_kept, orphan_deleted, orphan_failed = cleanup_orphan_session_dirs(
            session_manager, project_root,
            exclude_ids=exclude_ids,
            min_age_hours=orphan_min_age_hours,
            dry_run=dry_run,
        )
        report.orphan_kept = orphan_kept
        report.orphan_deleted = orphan_deleted
        report.orphan_failed = orphan_failed

    if include_workflow_runs and project_root is not None:
        try:
            from mini_agent.evolution.workflow_run_cleanup import cleanup_workflow_runs
            wf_items = cleanup_workflow_runs(
                project_root,
                keep_recent_days=keep_recent_days,
                keep_recent_count=keep_recent_count,
                orphan_min_age_hours=orphan_min_age_hours,
                dry_run=dry_run,
                protect_outputs=protect_outputs,
            )
            for it in wf_items:
                if it.action == "delete":
                    report.workflow_deleted.append(it)
                elif it.action == "failed":
                    report.workflow_failed.append(it)
                else:
                    report.workflow_kept.append(it)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup.cleanup_sessions.workflow')

    if include_orphans and project_root is not None:
        rk, rd, rf = scan_orphan_raw_results(
            project_root, session_manager.session_dir,
            min_age_hours=orphan_min_age_hours, dry_run=dry_run,
        )
        report.raw_orphan_kept = rk
        report.raw_orphan_deleted = rd
        report.raw_orphan_failed = rf

    if include_adhoc_temp and project_root is not None:
        try:
            report.adhoc_temp_deleted = cleanup_adhoc_temp(
                project_root, min_age_days=adhoc_temp_min_age_days, dry_run=dry_run,
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup.cleanup_sessions.adhoc')

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


def _session_output_file_count(session_manager, session_id: str) -> int:
    """sessions/<id>/output/ 下的文件数（产出保护用）。目录不存在返回 0。"""
    out = Path(session_manager.session_dir) / session_id / "output"
    if not out.is_dir():
        return 0
    return _count_files_under(out)


def _count_files_under(path: Path) -> int:
    n = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                n += 1
    except OSError:
        pass
    return n


def _finish_deletion(session_manager, item: CleanupItem, report: CleanupReport, dry_run: bool,
                     project_root=None) -> None:
    item.output_files = _session_output_file_count(session_manager, item.session_id)
    if dry_run:
        item.raw_results_bytes, _ = _clear_raw_results(project_root, item.session_id, dry_run=True)
        report.deleted.append(item)
        return
    try:
        ok = session_manager.delete(item.session_id)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.session_cleanup._finish_deletion')
        ok = False
    if ok:
        # session 目录已删，联动清理它的 raw_results；这一步失败/被保护不改变
        # "session 已删除"的结论，只把说明追加进 reason。
        item.raw_results_bytes, _rr_note = _clear_raw_results(
            project_root, item.session_id, dry_run=False)
        if _rr_note:
            item.reason += "；" + _rr_note
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
    if report.raw_results_total_bytes:
        lines.append(
            f"  同时{'将清理' if report.dry_run else '已清理'}对应的 raw_results 约 "
            f"{report.raw_results_total_bytes / 1024 / 1024:.1f} MB。"
        )

    if report.output_protected_count:
        lines.append(
            f"产出保护：{report.output_protected_count} 个会话/工作流运行含产出文件，已保留。"
        )
    lost = sum(i.output_files for i in report.deleted) + sum(
        i.output_files for i in report.workflow_deleted)
    if lost:
        lines.append(
            f"⚠️ 产出文件：本次{'将' if report.dry_run else '已'}删除 {lost} 个产出文件"
            f"（产出保护已关闭），请确认是否需要先导出。"
        )
    stale_n = sum(1 for i in report.workflow_kept if getattr(i, "stale", False))
    if stale_n:
        lines.append(
            f"⏸ 疑似中断遗留的工作流运行 {stale_n} 个（状态未终结且长期未更新）："
            "未删除，请到工作流页面确认后 mark_interrupted 再清理。"
        )
    if report.adhoc_temp_deleted:
        lines.append(
            f"adhoc 临时目录：{verb} {len(report.adhoc_temp_deleted)} 个过期 temp 条目"
            f"（约 {sum(i.size_bytes for i in report.adhoc_temp_deleted) / 1024 / 1024:.1f} MB）。"
        )

    if report.workflow_total_scanned:
        wf_size = sum(i.size_bytes for i in report.workflow_deleted)
        lines.append(
            f"工作流运行目录：共扫描 {report.workflow_total_scanned} 个，"
            f"保留 {len(report.workflow_kept)} 个，{verb} {len(report.workflow_deleted)} 个"
            f"（约 {wf_size / 1024 / 1024:.1f} MB），失败 {len(report.workflow_failed)} 个。"
        )
        for item in report.workflow_deleted:
            lines.append(f"  [{verb}·工作流] {item.run_id}  — {item.reason}")
        for item in report.workflow_failed:
            lines.append(f"  [失败·工作流] {item.run_id}  — {item.reason}")

    if report.raw_orphan_deleted or report.raw_orphan_failed:
        raw_size = sum(i.size_bytes for i in report.raw_orphan_deleted)
        lines.append(
            f"孤立 raw_results：{verb} {len(report.raw_orphan_deleted)} 个"
            f"（约 {raw_size / 1024 / 1024:.1f} MB），失败 {len(report.raw_orphan_failed)} 个。"
        )

    if report.orphan_total_scanned:
        orphan_size = sum(i.size_bytes for i in report.orphan_deleted)
        lines.append(
            f"孤儿目录（有目录无 meta.json）：共扫描 {report.orphan_total_scanned} 个，"
            f"保留 {len(report.orphan_kept)} 个，{verb} {len(report.orphan_deleted)} 个"
            f"（约 {orphan_size / 1024 / 1024:.1f} MB），失败 {len(report.orphan_failed)} 个。"
        )
        for item in report.orphan_deleted:
            lines.append(f"  [{verb}·孤儿] {item.dir_name}  — {item.reason}")
        for item in report.orphan_failed:
            lines.append(f"  [失败·孤儿] {item.dir_name}  — {item.reason}")

    return lines


# ── 外部项目范围（next_doc/session_cleanup_external_projects_plan.md）─────────
#
# 每个已注册的外部项目都有自己的 <项目根>/.agent/sessions/（Workspace.sessions_dir），
# 目录格式与主项目完全一致，所以 cleanup_sessions() 本体不需要任何改动：
# 只要把"外部项目自己的 SessionManager + 项目根"传进去，pin / goal 在跑 /
# 最近 N 个 / 最近 N 天 / 孤儿目录年龄安全网 / 知识抽取门槛全部原样生效。

class ExternalProjectSessionError(Exception):
    """外部项目会话环境解析失败。

    kind:
      - "not_registered"   注册表里没有这个名字
      - "root_missing"     已注册，但项目根目录在磁盘上不存在（路径被移走/删除）
      - "no_sessions_dir"  项目根存在，但还没有 .agent/sessions/（没产生过会话，无事可清）
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def resolve_external_project_session_env(name: str, registry=None):
    """已注册外部项目的注册名 → (SessionManager, 项目根, sessions 目录)。

    只接受**注册名**，不接受调用方传路径：路径一律从注册表解析，调用方
    （HTTP 客户端）没法借这个入口让服务端去删任意目录。
    已注册但未启用自动调度（enabled=False）的项目同样可以清理——
    enabled 只管 cron 自动调度，与会话清理无关。

    不会创建目录：SessionManager.__init__ 会 mkdir，所以先确认 sessions 目录
    存在再构造。
    """
    from mini_agent.external_projects.registry import (
        ExternalProjectRegistry,
        ExternalProjectRegistryError,
    )
    from mini_agent.session import SessionManager
    from mini_agent.storage.paths import AgentPaths

    registry = registry if registry is not None else ExternalProjectRegistry()
    try:
        record = registry.get(name)
    except ExternalProjectRegistryError as exc:
        raise ExternalProjectSessionError("not_registered", str(exc)) from exc

    root = Path(record.path).expanduser()
    if not root.is_dir():
        raise ExternalProjectSessionError(
            "root_missing", f"外部项目 '{name}' 的目录不存在：{root}")
    root = root.resolve()
    sessions_dir = AgentPaths(root).sessions_dir
    if not sessions_dir.is_dir():
        raise ExternalProjectSessionError(
            "no_sessions_dir", f"外部项目 '{name}' 还没有会话目录：{sessions_dir}")
    return SessionManager(session_dir=sessions_dir), root, sessions_dir
