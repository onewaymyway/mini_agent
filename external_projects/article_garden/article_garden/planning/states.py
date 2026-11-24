"""planning/states.py — 选题状态机（方案 §6.4）。

  proposed ─放行─▶ approved ─▶ researching ─▶ researched ─▶ drafting ─▶ drafted ─▶ reviewed ─▶ published
     │                 │             ▲            │  ▲        │  │         │            │
     └─拒绝/放弃─▶ rejected / dropped │      drafting◀┘  └─ failed ◀┘         └─退回重写─▶ drafting
                                     （调研不足 → dropped）   （成文不通过 → failed，可重试）

P4 实现 proposed/approved/rejected/dropped；P5 实现调研段；P6 实现成文与审阅段。
异常恢复（停止、报错）只用 revert_to，不走转换表，但会记录原因。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .. import store

PROPOSED, APPROVED, REJECTED, DROPPED = "proposed", "approved", "rejected", "dropped"
RESEARCHING, RESEARCHED = "researching", "researched"
DRAFTING, DRAFTED, FAILED, REVIEWED, PUBLISHED = "drafting", "drafted", "failed", "reviewed", "published"
TERMINAL = {REJECTED, DROPPED, PUBLISHED}
REJECT_REASONS = ("重复已有内容", "超出范围", "证据不足", "暂不需要", "其它")
LABELS = {PROPOSED: "待放行", APPROVED: "已放行", REJECTED: "已拒绝", DROPPED: "已放弃",
          RESEARCHING: "调研中", RESEARCHED: "已调研", DRAFTING: "成文中", FAILED: "成文失败",
          DRAFTED: "已成稿", REVIEWED: "已审阅", PUBLISHED: "已发布"}
# 手动修改状态时允许的目标（转换表的超集：允许“恢复待放行”“重新放行”，以便人工纠正）
MANUAL_EXTRA = {
    APPROVED: {PROPOSED, DROPPED},
    REJECTED: {PROPOSED, APPROVED},
    DROPPED: {PROPOSED, APPROVED},
}
TRANSITIONS = {
    PROPOSED: {APPROVED, REJECTED, DROPPED},
    APPROVED: {REJECTED, RESEARCHING},
    RESEARCHING: {RESEARCHED, DROPPED},
    RESEARCHED: {RESEARCHING, DROPPED, DRAFTING},
    DRAFTING: {DRAFTED, FAILED, RESEARCHED},
    FAILED: {DRAFTING, DROPPED},
    DRAFTED: {REVIEWED, DRAFTING, DROPPED},
    REVIEWED: {PUBLISHED, DRAFTING, DROPPED},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _queue_path(ctx):
    return ctx.path("plan", "topics.jsonl")


def _rejected_path(ctx):
    return ctx.path("plan", "rejected.jsonl")


def _counters_path(ctx):
    return ctx.path("plan", "counters.json")


def load_topics(ctx) -> List[Dict[str, Any]]:
    return _read(_queue_path(ctx))


def save_topics(ctx, rows: List[Dict[str, Any]]) -> None:
    store.write_text_atomic(_queue_path(ctx), "".join(_dump(r) for r in rows))


def load_rejected(ctx) -> List[Dict[str, Any]]:
    return _read(_rejected_path(ctx))


def save_rejected(ctx, rows) -> None:
    store.write_text_atomic(_rejected_path(ctx), "".join(_dump(r) for r in rows))


def alloc_topic_id(ctx) -> str:
    c = store.read_json(_counters_path(ctx), default={}) or {}
    n = int(c.get("next_topic", 1))
    c["next_topic"] = n + 1
    store.write_json_atomic(_counters_path(ctx), c)
    return f"t-{n:04d}"


def get(ctx, tid: str) -> Dict[str, Any]:
    for r in load_topics(ctx):
        if r["id"] == tid:
            return r
    raise ValueError(f"选题不存在：{tid}")


def _replace(ctx, tid: str, fn) -> Dict[str, Any]:
    rows = load_topics(ctx)
    for r in rows:
        if r["id"] == tid:
            fn(r)
            r["updated_at"] = _now()
            save_topics(ctx, rows)
            return r
    raise ValueError(f"选题不存在：{tid}")


def _move(r: Dict[str, Any], to: str, note: str = "") -> None:
    if to not in TRANSITIONS.get(r["status"], set()):
        raise ValueError(f"不能从「{r['status']}」转为「{to}」")
    r.setdefault("history", []).append({"from": r["status"], "to": to, "at": _now(), "note": note})
    r["status"] = to


TRANSIENT = {RESEARCHING, DRAFTING}


def allowed_targets(status: str) -> List[str]:
    """手动修改时可选的目标状态（不含进行中的状态）。"""
    targets = (TRANSITIONS.get(status, set()) | MANUAL_EXTRA.get(status, set())) - TRANSIENT
    targets.discard(status)
    order = [PROPOSED, APPROVED, REJECTED, DROPPED, RESEARCHED, DRAFTED, REVIEWED, PUBLISHED, FAILED]
    return sorted(targets, key=lambda x: order.index(x) if x in order else 99)


def set_status(ctx, tid: str, to: str, note: str = "") -> Dict[str, Any]:
    """人工修改选题状态。拒绝或放弃必须给出原因；恢复待放行或重新放行时，清除对应的拒绝记录，
    以便该缺口能够重新参与判断。"""
    note = (note or "").strip()
    current = get(ctx, tid)["status"]
    if to not in allowed_targets(current):
        raise ValueError(f"「{LABELS.get(current, current)}」不能直接改为「{LABELS.get(to, to)}」")
    if to == REJECTED:
        return reject(ctx, tid, note or "人工拒绝")
    if to == DROPPED and not note:
        raise ValueError("改为「已放弃」必须填写原因")
    def _manual(r):
        # 人工修改只在本函数内允许 MANUAL_EXTRA 中的额外变化；自动流程的转换表保持不变（已拒绝仍是终态）
        r.setdefault("history", []).append({"from": r["status"], "to": to, "at": _now(),
                                            "note": note or f"人工改为{LABELS.get(to, to)}", "manual": True})
        r["status"] = to
    t = _replace(ctx, tid, _manual)
    if current in (REJECTED, DROPPED) and to in (PROPOSED, APPROVED) and t.get("gap_key"):
        save_rejected(ctx, [x for x in load_rejected(ctx) if x.get("gap_key") != t["gap_key"]])
    return t


# 可删除的状态：尚未产生调研/成文产物的选题，以及已被放弃、拒绝的选题。
# 进行中与已有产物的选题（已调研、成文失败、已成稿、已审阅、已发布）不能删除，避免孤立产物。
DELETABLE = {PROPOSED, APPROVED, REJECTED, DROPPED}


def _deleted_path(ctx):
    return ctx.path("plan", "deleted.jsonl")


def load_deleted(ctx) -> List[Dict[str, Any]]:
    return _read(_deleted_path(ctx))


def delete_topics(ctx, tids: List[str], *, allow_repropose: bool = False, note: str = "") -> List[Dict[str, Any]]:
    """人工删除选题（可批量）。全部校验通过才执行，否则一个都不删。

    - 只能删除 DELETABLE 中的状态；
    - 默认保守：被删除选题对应的缺口记入拒绝记录，之后「检测缺口」不会再次提出；
      `allow_repropose=True` 时清除该缺口的拒绝记录，下一次检测可以重新提出；
    - 被删除的选题整行追加到 plan/deleted.jsonl，便于追溯与手动找回；
    - 只删除选题记录，已生成的调研、草稿文件不动。
    """
    tids = list(dict.fromkeys(tids or []))
    if not tids:
        raise ValueError("请至少选择一个选题")
    rows = load_topics(ctx)
    by_id = {r["id"]: r for r in rows}
    for t in tids:
        r = by_id.get(t)
        if r is None:
            raise ValueError(f"选题不存在：{t}")
        if r["status"] not in DELETABLE:
            raise ValueError(f"{t}「{r['title']}」处于「{LABELS.get(r['status'], r['status'])}」，不能删除")
    gone = [by_id[t] for t in tids]
    keys = {r.get("gap_key") for r in gone if r.get("gap_key")}
    rej = load_rejected(ctx)
    if allow_repropose:
        rej = [x for x in rej if x.get("gap_key") not in keys]
    else:
        have = {x.get("gap_key") for x in rej}
        for r in gone:
            if r.get("gap_key") and r["gap_key"] not in have:
                rej.append({"gap_key": r["gap_key"], "title": r["title"], "reason": "已删除选题", "at": _now()})
                have.add(r["gap_key"])
    stamp = _now()
    log = load_deleted(ctx) + [{**r, "deleted_at": stamp, "delete_note": (note or "").strip(),
                                "allow_repropose": allow_repropose} for r in gone]
    # 先写追溯记录，再写拒绝记录，最后改队列：中途失败不会丢失选题
    store.write_text_atomic(_deleted_path(ctx), "".join(_dump(r) for r in log))
    save_rejected(ctx, rej)
    save_topics(ctx, [r for r in rows if r["id"] not in set(tids)])
    return gone


def approve(ctx, tid: str) -> Dict[str, Any]:
    return _replace(ctx, tid, lambda r: _move(r, APPROVED))


def reject(ctx, tid: str, reason: str) -> Dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("拒绝选题必须给出原因")
    def _do(r):
        _move(r, REJECTED, reason)
        r["reason_rejected"] = reason
        rej = load_rejected(ctx)
        rej.append({"gap_key": r.get("gap_key"), "title": r["title"], "reason": reason, "at": _now()})
        save_rejected(ctx, rej)
    return _replace(ctx, tid, _do)


def begin_research(ctx, tid: str) -> Dict[str, Any]:
    return _replace(ctx, tid, lambda r: _move(r, RESEARCHING, "开始调研"))


def end_research(ctx, tid: str, to: str, note: str) -> Dict[str, Any]:
    if to not in (RESEARCHED, DROPPED):
        raise ValueError("调研只能结束为 researched 或 dropped")
    return _replace(ctx, tid, lambda r: _move(r, to, note))


def revert_research(ctx, tid: str, to: str, note: str) -> Dict[str, Any]:
    """调研中途出错或被停止：退回调研前的状态（不走转换表，仅用于异常恢复），并记录原因。"""
    def _do(r):
        r.setdefault("history", []).append({"from": r["status"], "to": to, "at": _now(), "note": note})
        r["status"] = to
    return _replace(ctx, tid, _do)


def begin_draft(ctx, tid: str, note: str) -> Dict[str, Any]:
    return _replace(ctx, tid, lambda r: _move(r, DRAFTING, note))


def end_draft(ctx, tid: str, to: str, note: str) -> Dict[str, Any]:
    if to not in (DRAFTED, FAILED):
        raise ValueError("成文只能结束为 drafted 或 failed")
    return _replace(ctx, tid, lambda r: _move(r, to, note))


def move(ctx, tid: str, to: str, note: str) -> Dict[str, Any]:
    """审阅动作（通过 / 发布 / 放弃 / 退回重写）：走转换表。"""
    return _replace(ctx, tid, lambda r: _move(r, to, note))


def revert_to(ctx, tid: str, to: str, note: str) -> Dict[str, Any]:
    """停止或异常时退回之前的状态（不走转换表，仅用于异常恢复），并记录原因。"""
    def _do(r):
        r.setdefault("history", []).append({"from": r["status"], "to": to, "at": _now(), "note": note})
        r["status"] = to
    return _replace(ctx, tid, _do)


def link_article(ctx, tid: str, article_id: str) -> Dict[str, Any]:
    """记录选题对应的成稿文章 id（重写时复用同一 id）。"""
    def _do(r):
        r["article_id"] = article_id
    return _replace(ctx, tid, _do)


def set_title(ctx, tid: str, title: str) -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    def _do(r):
        if r["status"] != PROPOSED:
            raise ValueError("只能修改待审选题的标题")
        r["title"] = title
    return _replace(ctx, tid, _do)


def _dump(r: Dict[str, Any]) -> str:
    import json
    return json.dumps(r, ensure_ascii=False) + "\n"


def _read(path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    import json
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
