"""大纲先行 O4：简述修改后，已写章节的「简述已变更」标记与处理。

数据：`大纲规划/章节简述/_sync.json`
  written   {章号: {hash, brief, at}}  该章正文写作时（关键内容核对通过那一刻）所依据的简述及其哈希
  baseline  {章号: {hash, brief, at}}  简述确认时的快照，仅在没有 written 记录时作为回退基准（只填不覆盖）
  decisions {章号: {action, hash, at}} 作者对「当前简述哈希」的处理结果（keep / ignore / rewrite）

判定：章节已有正文 + 有基准（written 优先，其次 baseline）+ 当前简述哈希 ≠ 基准哈希 + 作者没有对「当前哈希」做过处理
      → 该章「简述已变更」。作者之后再次修改简述，哈希又变，会重新标记。

处理（每章三选一，`ask` 策略下由作者逐章决定）：
  keep    保留：以当前简述重新记为写作依据（正文视为符合新简述）
  ignore  忽略此次变更：基准不动，仅对这一版简述不再提示；再改一次会重新提示
  rewrite 重写：为本章创建一个「作者反馈修订循环」请求（修改 → 核查，由 worker 按请求队列逐章执行），
          意见由「旧简述 → 新简述」的差异生成；重写完成后「关键内容核对」会重新运行以验证新简述。
`auto_rewrite` 策略：确认简述时自动对全部受影响章节执行 rewrite。

与步骤状态的关系：写作步骤与核对步骤的输入包含简述 JSON，简述一改它们就会变成「需重跑」，
流水线若直接运行会整章重新写作——这正是 O4 要避免的。处理动作会把写作步骤（以及 keep/ignore 时的核对步骤）
重新基线为已通过；rewrite 时核对步骤保持「需重跑」，由流水线在重写后验证。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any, Dict, List, Optional

from novel_writer import brief as BR
from novel_writer import state as st_state
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace

SYNC_FILE = "_sync.json"
ACTIONS = ("keep", "ignore", "rewrite")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def hash_brief(b: Dict[str, Any]) -> str:
    """与 brief_review._hash_brief 一致（偏离记录在简述 JSON 之外，不影响哈希）。"""
    return hashlib.sha256(json.dumps(b, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _load(ws: Workspace) -> Dict[str, Any]:
    data = read_json(BR.brief_dir(ws) / SYNC_FILE, default=None)
    if not isinstance(data, dict):
        data = {}
    for k in ("written", "baseline", "decisions"):
        if not isinstance(data.get(k), dict):
            data[k] = {}
    return data


def _save(ws: Workspace, data: Dict[str, Any]) -> None:
    BR.brief_dir(ws).mkdir(parents=True, exist_ok=True)
    atomic_write_json(BR.brief_dir(ws) / SYNC_FILE, data)


def _entry(b: Dict[str, Any]) -> Dict[str, Any]:
    return {"hash": hash_brief(b), "brief": b, "at": _now()}


# ─────────────────────────────── 记录 ───────────────────────────────

def record_written(ws: Workspace, n: int) -> bool:
    """记录第 n 章正文所依据的简述（关键内容核对通过时调用）。同时清除该章旧的处理结果。"""
    b = BR.load_brief(ws, n)
    if b is None:
        return False
    data = _load(ws)
    data["written"][str(n)] = _entry(b)
    data["decisions"].pop(str(n), None)
    _save(ws, data)
    return True


def record_baseline(ws: Workspace, chapters: List[int]) -> int:
    """确认简述时调用：为还没有基准的章节记下当前简述（只填不覆盖）。返回新增数量。"""
    data = _load(ws)
    added = 0
    for n in chapters:
        key = str(n)
        if key in data["baseline"] or key in data["written"]:
            continue
        b = BR.load_brief(ws, n)
        if b is not None:
            data["baseline"][key] = _entry(b)
            added += 1
    if added:
        _save(ws, data)
    return added


def adopt_current_as_written(ws: Workspace, chapters: Optional[List[int]] = None) -> List[int]:
    """旧书（O4 之前写的章节没有记录）：把「当前简述」记为这些已写章节的写作依据。
    仅在确认当前简述与已写正文一致时使用；传入 None 表示全部已有正文且无记录的章节。"""
    written_chapters = _chapters_with_text(ws)
    data = _load(ws)
    targets = chapters if chapters is not None else [n for n in written_chapters if str(n) not in data["written"]]
    done = []
    for n in targets:
        if n not in written_chapters:
            continue
        b = BR.load_brief(ws, n)
        if b is None:
            continue
        data["written"][str(n)] = _entry(b)
        data["decisions"].pop(str(n), None)
        done.append(n)
    if done:
        _save(ws, data)
    return sorted(done)


def _chapters_with_text(ws: Workspace) -> List[int]:
    return sorted({ch.number for ch in ws.list_chapters()})


# ─────────────────────────────── 差异与受影响章节 ───────────────────────────────

def diff_lines(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """把两版简述的差异写成可读的行（用于界面展示与重写意见）。"""
    out: List[str] = []
    if (old.get("title") or "") != (new.get("title") or ""):
        out.append(f"章名：{old.get('title')} → {new.get('title')}")
    if (old.get("narrative") or "").strip() != (new.get("narrative") or "").strip():
        out.append("叙述简述已改。\n  旧：" + (old.get("narrative") or "").strip()
                   + "\n  新：" + (new.get("narrative") or "").strip())
    ok, nk = old.get("key") or {}, new.get("key") or {}
    for field, label in (("must_events", "必须发生的事件"), ("must_foreshadow", "伏笔动作")):
        o, n_ = list(ok.get(field) or []), list(nk.get(field) or [])
        for x in n_:
            if x not in o:
                out.append(f"{label}新增：{x}")
        for x in o:
            if x not in n_:
                out.append(f"{label}删除：{x}")
    fmt = lambda s: f"{s.get('who')}：{s.get('change')}"  # noqa: E731
    o_s = [fmt(s) for s in ok.get("must_states") or []]
    n_s = [fmt(s) for s in nk.get("must_states") or []]
    out += [f"人物状态新增：{x}" for x in n_s if x not in o_s]
    out += [f"人物状态删除：{x}" for x in o_s if x not in n_s]
    if (ok.get("must_hook") or "") != (nk.get("must_hook") or ""):
        out.append(f"章末钩子：{ok.get('must_hook')} → {nk.get('must_hook')}")
    return out


def affected(ws: Workspace) -> List[Dict[str, Any]]:
    """当前「简述已变更」且作者尚未处理的已写章节。"""
    data = _load(ws)
    written = set(_chapters_with_text(ws))
    rows: List[Dict[str, Any]] = []
    for n in sorted(written):
        key = str(n)
        base = data["written"].get(key) or data["baseline"].get(key)
        cur = BR.load_brief(ws, n)
        if base is None or cur is None:
            continue
        h = hash_brief(cur)
        if h == base["hash"]:
            continue
        dec = data["decisions"].get(key)
        if dec and dec.get("hash") == h:
            continue
        rows.append({"chapter": n, "title": cur.get("title", ""), "diff": diff_lines(base["brief"], cur),
                     "basis": "written" if key in data["written"] else "baseline"})
    return rows


def decisions(ws: Workspace) -> Dict[str, Dict[str, Any]]:
    return _load(ws)["decisions"]


# ─────────────────────────────── 处理 ───────────────────────────────

def _rebaseline_steps(ws: Workspace, n: int, *, include_check: bool, note: str) -> List[str]:
    from novel_writer.pipeline import build_plan
    ids = [f"ch{n:02d}.write_chapter"] + ([f"ch{n:02d}.brief_check"] if include_check else [])
    return st_state.rebaseline_stale(ws, build_plan(ws), ids, note=note)


def _require_affected(ws: Workspace, n: int) -> Dict[str, Any]:
    row = next((r for r in affected(ws) if r["chapter"] == n), None)
    if row is None:
        raise FlowError(f"第{n}章当前没有待处理的「简述已变更」")
    return row


def _decide(ws: Workspace, n: int, action: str) -> None:
    cur = BR.load_brief(ws, n)
    data = _load(ws)
    data["decisions"][str(n)] = {"action": action, "hash": hash_brief(cur), "at": _now()}
    _save(ws, data)


def keep(ws: Workspace, n: int) -> Dict[str, Any]:
    _require_affected(ws, n)
    cur = BR.load_brief(ws, n)
    data = _load(ws)
    data["written"][str(n)] = _entry(cur)
    data["decisions"][str(n)] = {"action": "keep", "hash": hash_brief(cur), "at": _now()}
    _save(ws, data)
    steps = _rebaseline_steps(ws, n, include_check=True, note="作者确认：保留已写章节（简述变更不重写）")
    return {"chapter": n, "action": "keep", "rebaselined": steps}


def ignore(ws: Workspace, n: int) -> Dict[str, Any]:
    _require_affected(ws, n)
    _decide(ws, n, "ignore")
    steps = _rebaseline_steps(ws, n, include_check=True, note="作者确认：忽略此次简述变更")
    return {"chapter": n, "action": "ignore", "rebaselined": steps}


def _feedback_text(n: int, row: Dict[str, Any]) -> str:
    return (f"第{n}章的章节简述已修改，请按新简述改写本章；关键内容必须在正文中实现，与简述差异无关的部分保持原样。\n"
            "简述变更如下：\n" + "\n".join(f"- {d}" for d in row["diff"]))


def rewrite(ws: Workspace, n: int) -> Dict[str, Any]:
    """为本章创建「作者反馈修订循环」请求。请求由 worker 按队列执行；这里不调用模型。"""
    from novel_writer import views
    row = _require_affected(ws, n)
    req = views.request_revision_cycle(ws, n, _feedback_text(n, row))
    _decide(ws, n, "rewrite")
    steps = _rebaseline_steps(ws, n, include_check=False, note="已提交按新简述重写，写作步骤不再整章重跑")
    return {"chapter": n, "action": "rewrite", "request": req, "rebaselined": steps}


def apply_policy(ws: Workspace) -> Dict[str, Any]:
    """按 brief_change_policy 处理：auto_rewrite → 对全部受影响章节提交重写；ask → 不做任何事。"""
    policy = (ws.settings() or {}).get("brief_change_policy", "ask")
    if policy != "auto_rewrite":
        return {"policy": policy, "submitted": [], "failed": {}}
    submitted, failed = [], {}
    for row in affected(ws):
        try:
            rewrite(ws, row["chapter"])
            submitted.append(row["chapter"])
        except Exception as exc:  # 单章失败不影响其它章（失败隔离），原因留给作者
            failed[row["chapter"]] = str(exc)
    return {"policy": policy, "submitted": submitted, "failed": failed}
