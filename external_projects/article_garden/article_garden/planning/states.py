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
