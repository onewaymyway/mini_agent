"""收件箱「过期项」自动归档（可撤销）。

每次收件箱计算时同步一次：满足过期规则的项写入 `_studio/inbox/archived.json`（附规则名、原因、时间），
不再显示；条件不再成立（例如步骤又回到计划中）时自动恢复。只改收件箱的归档记录，不删除任何状态历史。

规则：
  orphan   孤立步骤：状态里有记录，但当前计划里已不存在。
  waiting  被修订取代的等待项：等待答复 / 批准 / 升级人工，但它之后已有构思修订完成。
           这种情况下步骤会被改为「修订影响、需重跑」，否则 worker 会停在等待状态、整本书卡住。
  failed   已封存章节或超出总章数的失败步骤。
  budget   预算暂停已过期：暂停之后已有新的进展。
  patrol   巡检整改项已过期：所属章节已封存或超出总章数，或已有更新一轮的巡检覆盖同一章节。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

ARCHIVE_FILE = "archived.json"
RULE_ORPHAN = "orphan"
RULE_WAITING = "waiting"
RULE_FAILED = "failed"
RULE_BUDGET = "budget"
RULE_PATROL = "patrol"
_BUDGET_REASONS = {"budget_chapters", "budget_calls", "budget_time"}
_WAITING_KIND = {st.ST_WAITING_REVIEW: "approve", st.ST_WAITING_USER: "answer"}


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _archive_path(ws: Workspace):
    return ws.path("studio_inbox") / ARCHIVE_FILE


def load_archive(ws: Workspace) -> Dict[str, Dict[str, Any]]:
    data = read_json(_archive_path(ws), default={})
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def archived_keys(ws: Workspace) -> set:
    return set(load_archive(ws))


def _latest_revision_time(ws: Workspace) -> str:
    """最近一次构思修订（含撤销）完成的时间；没有则返回空串。"""
    from novel_writer import ondemand as OD

    times = [r.get("finished_at") or r.get("created_at") or ""
             for r in OD.list_requests(ws, kind="concept") if r.get("result")]
    return max(times, default="")


def _total_chapters(ws: Workspace) -> int:
    """全书计划章数：蓝图的 total_chapters 优先，其次项目配置的 total_chapters_planned。
    未知时返回 0。注意不能用计划里的最大章号：计划是滚动生成的，后面的章节还没进入计划。"""
    from novel_writer import blueprint_store as BS

    bp = BS.load_for_ws(ws, strict=False) or {}
    total = int(((bp.get("structure") or {}).get("total_chapters")) or 0)
    if not total:
        total = int(ws.workspace_config().get("total_chapters_planned") or 0)
    return total


def _candidates(ws: Workspace, plan, states, worker) -> Dict[str, Dict[str, Any]]:
    from novel_writer import patrol_findings as PF

    out: Dict[str, Dict[str, Any]] = {}
    rows = {s.id for s in plan}
    total = _total_chapters(ws)            # 0 = 未知：「超出总章数」规则不生效，避免误判
    sealed = st.sealed_upto(plan, states)

    def beyond(ch: int) -> bool:
        return bool(total) and ch > total

    # 规则 1：孤立步骤
    for sid in states:
        if sid not in rows:
            out[f"orphan:{sid}"] = {"rule": RULE_ORPHAN, "step_id": sid,
                                    "reason": "计划中已不存在该步骤（章节或设定已调整）"}

    # 规则 2：被修订取代的等待项
    revised_at = _latest_revision_time(ws)
    for step in plan:
        entry = states.get(step.id, {})
        status = entry.get("status")
        if status not in _WAITING_KIND:
            continue
        kind = "escalate" if (status == st.ST_WAITING_USER and step.handler == "revise_loop") else _WAITING_KIND[status]
        if revised_at and (entry.get("updated_at") or "") < revised_at:
            out[f"{kind}:{step.id}"] = {"rule": RULE_WAITING, "step_id": step.id, "kind": kind,
                                        "reason": "构思修订之后上游已变化，这一步的等待已被取代（已改为需重跑）"}

    # 规则 3：已封存章节或超出总章数的失败步骤
    for step in plan:
        if states.get(step.id, {}).get("status") != st.ST_FAILED or not isinstance(step.chapter, int):
            continue
        if step.chapter <= sealed or beyond(step.chapter):
            where = "已封存" if step.chapter <= sealed else "已超出全书总章数"
            out[f"failed:{step.id}"] = {"rule": RULE_FAILED, "step_id": step.id,
                                        "reason": f"所属第 {step.chapter} 章{where}"}

    # 规则 4：预算暂停之后已有新的进展
    if worker and not st.worker_alive(ws) and worker.get("reason") in _BUDGET_REASONS:
        ended = worker.get("finished_at") or worker.get("heartbeat") or ""
        latest = max((e.get("updated_at") or "" for e in states.values()), default="")
        if latest and latest > ended:
            out["budget_pause"] = {"rule": RULE_BUDGET, "reason": "暂停之后已有新的进展，这条暂停记录已过期"}

    # 规则 5：过期的巡检整改项
    ledger = PF.read_ledger(ws)["findings"]
    for f in PF.pending_items(ws):
        chs = [c for c in (f.get("chapters") or []) if isinstance(c, int)]
        reason = None
        if chs and sealed and max(chs) <= sealed:
            reason = f"所属章节已封存（截至第 {sealed} 章）"
        elif chs and beyond(max(chs)):
            reason = "所属章节已超出全书总章数"
        else:
            newer = [g for g in ledger if g.get("id") != f.get("id") and g.get("scope") == f.get("scope")
                     and (g.get("patrol_chapter") or 0) > (f.get("patrol_chapter") or 0)
                     and set(g.get("chapters") or []) & set(chs)]
            if chs and newer:
                reason = f"已有更新一轮的巡检（{newer[0].get('id')}）覆盖同一章节"
        if reason:
            out[f"patrol:{f['id']}"] = {"rule": RULE_PATROL, "finding_id": f["id"], "reason": reason}
    return out


def sync(ws: Workspace) -> Dict[str, List[str]]:
    """计算过期项并同步归档记录：新过期的归档；不再过期的恢复。返回 {archived, restored}。"""
    plan = P.build_plan(ws)
    states = st.load_state(ws)["steps"]
    worker = st.read_worker(ws)
    cands = _candidates(ws, plan, states, worker)
    archive = load_archive(ws)

    new_archive: Dict[str, Dict[str, Any]] = {}
    archived: List[str] = []
    for key, cand in cands.items():
        if key in archive:
            new_archive[key] = archive[key]
            continue
        new_archive[key] = {**cand, "archived_at": _now()}
        archived.append(key)
        # 被修订取代的等待项：改为需重跑，否则 worker 会停在等待状态
        if cand["rule"] == RULE_WAITING:
            st.update_step(ws, cand["step_id"], status=st.ST_STALE, note=st.REVISION_NOTE)
    restored = [k for k in archive if k not in cands]

    if archived or restored:
        atomic_write_json(_archive_path(ws), {"updated_at": _now(), "items": new_archive})
    return {"archived": archived, "restored": restored}
