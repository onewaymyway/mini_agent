"""大纲先行 O2：简述的版本、编辑、单章反馈改写、整幕重排与确认冻结。

  * 版本：每次改动前，旧版本写入 大纲规划/章节简述/_history/第NNN章.vNNN.json（含原因与时间），可回退。
  * 编辑：作者直接改字段；先校验（同生成时的规则），通过才保存，并让确认失效。
  * 单章反馈：把作者意见与当前简述、前后章结尾交给 LLM 改写本章，同时返回 LLM 判断可能受影响的章节，由作者决定。
  * 整幕重排：整幕带着作者意见重新生成（改动前全部旧版本入历史）。
  * 确认：检查点处于 waiting_review 时，记录每章简述的哈希到 _confirmed.json，并批准 brief_review。
    简述任何改动都会让检查点变为 stale（它的输入包含全部简述文件），需要重新运行到确认点后再确认。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import brief as BR
from novel_writer import state as st_state
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace

CONFIRM_FILE = "_confirmed.json"
_HIST_RE = re.compile(r"^第(\d{3})章\.v(\d{3})\.json$")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _hist_dir(ws: Workspace) -> Path:
    d = BR.brief_dir(ws) / "_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _brief_or_raise(ws: Workspace, n: int) -> Dict[str, Any]:
    b = BR.load_brief(ws, n)
    if b is None:
        raise FlowError(f"第{n}章还没有简述")
    return b


def _chapter_title(ws: Workspace, n: int) -> str:
    return str(_brief_or_raise(ws, n).get("title") or "")


def _total(ws: Workspace) -> int:
    from novel_writer import blueprint_store as BS
    from novel_writer.pipeline import total_chapters
    return total_chapters(ws, BS.load_for_ws(ws, strict=False))


# ─────────────────────────────── 版本 ───────────────────────────────

def versions(ws: Workspace, n: int) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(_hist_dir(ws).glob(f"第{n:03d}章.v*.json")):
        m = _HIST_RE.match(p.name)
        if not m:
            continue
        data = read_json(p, default={}) or {}
        out.append({"ver": int(m.group(2)), "reason": data.get("reason", ""), "at": data.get("at", "")})
    return out


def snapshot(ws: Workspace, n: int, reason: str) -> Optional[int]:
    cur = BR.load_brief(ws, n)
    if cur is None:
        return None
    ver = max([v["ver"] for v in versions(ws, n)] or [0]) + 1
    atomic_write_json(_hist_dir(ws) / f"第{n:03d}章.v{ver:03d}.json",
                      {"ver": ver, "reason": reason, "at": _now(), "brief": cur})
    return ver


def load_version(ws: Workspace, n: int, ver: int) -> Dict[str, Any]:
    data = read_json(_hist_dir(ws) / f"第{n:03d}章.v{ver:03d}.json", default=None)
    if not isinstance(data, dict) or "brief" not in data:
        raise FlowError(f"第{n}章 v{ver:03d} 不存在")
    return data["brief"]


# ─────────────────────────────── 确认失效 ───────────────────────────────

def _invalidate(ws: Workspace, note: str) -> None:
    confirm = BR.brief_dir(ws) / CONFIRM_FILE
    if confirm.exists():
        confirm.unlink()
    state = st_state.get_step_state(ws, BR.BRIEF_REVIEW_ID)
    if state.get("status") in (st_state.ST_PASSED, st_state.ST_WAITING_REVIEW):
        st_state.update_step(ws, BR.BRIEF_REVIEW_ID, status=st_state.ST_STALE, note=note)


# ─────────────────────────────── 编辑 ───────────────────────────────

def check_edit(ws: Workspace, n: int, brief: Dict[str, Any]) -> List[str]:
    b = dict(brief)
    b["chapter"] = n
    b.setdefault("act", _brief_or_raise(ws, n).get("act"))
    b["deviations"] = _brief_or_raise(ws, n).get("deviations", [])
    return BR.validate_brief(b, chapter=n, title=str(b.get("title") or ""),
                             names=BR.known_names(ws), foreshadow=BR.known_foreshadow(ws))


def save_edit(ws: Workspace, n: int, brief: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """作者编辑保存。校验失败返回错误列表且不写盘；通过则先存旧版本，再保存并让确认失效。"""
    old = _brief_or_raise(ws, n)
    b = dict(brief)
    b["chapter"] = n
    b["act"] = old.get("act")
    b["deviations"] = old.get("deviations", [])
    errs = check_edit(ws, n, b)
    if errs:
        return {"ok": False, "errors": errs}
    ver = snapshot(ws, n, reason)
    BR.save_brief(ws, b)
    BR._write_index(ws)
    _invalidate(ws, f"第{n}章简述已修改，需重新确认")
    return {"ok": True, "version": ver}


def restore(ws: Workspace, n: int, ver: int) -> Dict[str, Any]:
    old = load_version(ws, n, ver)
    return save_edit(ws, n, old, reason=f"回退到 v{ver:03d}")


# ─────────────────────────────── 单章反馈改写（LLM） ───────────────────────────────

def _neighbor_tail(ws: Workspace, n: int) -> Optional[str]:
    b = BR.load_brief(ws, n)
    if not b:
        return None
    return f"第{n}章 {b.get('title', '')}：章末钩子「{b['key']['must_hook']}」"


def _rewrite_prompt(ws: Workspace, n: int, cur: Dict[str, Any], feedback: str, out_file: Path,
                    errors: List[str]) -> str:
    total = _total(ws)
    neighbors = [t for t in (_neighbor_tail(ws, k) for k in (n - 1, n + 1) if 1 <= k <= total) if t]
    parts = [
        f"【任务】根据作者意见，改写第{n}章的章节简述（叙述 300–600 字，关键内容必须具体）。"
        "只改与意见相关的部分，其余保持原样；章节号与幕号不变。",
        "【当前简述（JSON）】\n" + json.dumps(cur, ensure_ascii=False, indent=2),
        f"【作者意见】\n{feedback.strip()}",
        "【前后章衔接】\n" + ("\n".join(neighbors) if neighbors else "（无）"),
        "【输出】把一个 JSON 对象写入文件：" + str(out_file) + "\n"
        "格式：{\"brief\": {\"chapter\": %d, \"title\": \"...\", \"narrative\": \"...\", "
        "\"key\": {\"must_events\": [...], \"must_states\": [{\"who\": \"...\", \"change\": \"...\"}], "
        "\"must_foreshadow\": [...], \"must_hook\": \"...\"}}, \"affected\": [可能受本次修改影响的其它章节号整数，没有则为空列表]}"
        % n,
        "只写 JSON，不要代码围栏。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def rewrite_chapter(ws: Workspace, n: int, feedback: str, invoker) -> Dict[str, Any]:
    if invoker is None:
        raise FlowError("未配置执行器，无法改写")
    if not feedback.strip():
        raise FlowError("请写下修改意见")
    cur = _brief_or_raise(ws, n)
    out_dir = ws.root / "_studio" / "brief_rewrites" / f"ch{n:03d}" / _now().replace(":", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "rewrite.json"
    errors: List[str] = []
    for attempt in range(1, BR.MAX_ATTEMPTS + 1):
        prompt = _rewrite_prompt(ws, n, cur, feedback, out_file, errors)
        invoker(skill="novel-planner", prompt=prompt, export_dir=out_dir, step_id=f"brief_rewrite:{n}#{attempt}")
        if not out_file.is_file():
            errors = ["没有写出 rewrite.json"]
            continue
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors = [f"不是合法 JSON：{exc}"]
            continue
        b = data.get("brief") if isinstance(data, dict) else None
        if not isinstance(b, dict):
            errors = ["缺少 brief 对象"]
            continue
        b["chapter"] = n
        b["act"] = cur.get("act")
        b["deviations"] = []
        errors = BR.validate_brief(b, chapter=n, title=str(b.get("title") or cur.get("title", "")),
                                   names=BR.known_names(ws), foreshadow=BR.known_foreshadow(ws))
        if errors:
            continue
        ver = snapshot(ws, n, f"作者反馈改写：{feedback.strip()[:60]}")
        BR.save_brief(ws, b)
        BR._write_index(ws)
        _invalidate(ws, f"第{n}章简述已改写，需重新确认")
        total = _total(ws)
        affected = []
        for k in (data.get("affected") or []):
            if isinstance(k, int) and not isinstance(k, bool) and k != n and 1 <= k <= total and BR.load_brief(ws, k):
                affected.append(k)
        return {"chapter": n, "version": ver, "affected": sorted(set(affected)), "attempts": attempt}
    raise FlowError(f"第{n}章改写未通过校验：" + "；".join(errors[:6]))


# ─────────────────────────────── 整幕重排（LLM） ───────────────────────────────

def regenerate_act(ws: Workspace, act: int, start: int, end: int, feedback: str, invoker) -> Dict[str, Any]:
    for n in range(start, end + 1):
        if BR.load_brief(ws, n):
            snapshot(ws, n, f"整幕重排前（第{act}幕）")
    result = BR.generate_act(ws, act, start, end, invoker, feedback=feedback, step_id=f"brief_regen:{act}")
    _invalidate(ws, f"第{act}幕简述已重排，需重新确认")
    return result


# ─────────────────────────────── 确认与冻结 ───────────────────────────────

def _hash_brief(b: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(b, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def check_all(ws: Workspace) -> Dict[int, List[str]]:
    total = _total(ws)
    problems: Dict[int, List[str]] = {}
    for n in range(1, total + 1):
        b = BR.load_brief(ws, n)
        if b is None:
            problems[n] = ["缺少简述"]
            continue
        errs = BR.validate_brief(b, chapter=n, title=str(b.get("title") or ""),
                                 names=BR.known_names(ws), foreshadow=BR.known_foreshadow(ws))
        if errs:
            problems[n] = errs
    return problems


def confirm(ws: Workspace) -> Dict[str, Any]:
    state = st_state.get_step_state(ws, BR.BRIEF_REVIEW_ID)
    if state.get("status") != st_state.ST_WAITING_REVIEW:
        raise FlowError(f"简述确认尚未就绪（当前状态：{state.get('status')}）。请先运行到「章节简述确认」步骤。")
    problems = check_all(ws)
    if problems:
        raise FlowError(f"还有 {len(problems)} 章简述未通过校验：第{min(problems)}章 等")
    total = _total(ws)
    hashes = {str(n): _hash_brief(BR.load_brief(ws, n)) for n in range(1, total + 1)}
    atomic_write_json(BR.brief_dir(ws) / CONFIRM_FILE, {"confirmed_at": _now(), "hashes": hashes})
    st_state.update_step(ws, BR.BRIEF_REVIEW_ID, status=st_state.ST_PASSED, approved_at=st_state.now_iso())
    return {"chapters": total, "confirmed_at": _now()}


def confirmation_status(ws: Workspace) -> Dict[str, Any]:
    rec = read_json(BR.brief_dir(ws) / CONFIRM_FILE, default=None)
    if not isinstance(rec, dict):
        return {"confirmed": False, "changed": []}
    changed = []
    for k, h in (rec.get("hashes") or {}).items():
        b = BR.load_brief(ws, int(k))
        if b is None or _hash_brief(b) != h:
            changed.append(int(k))
    return {"confirmed": not changed, "confirmed_at": rec.get("confirmed_at"), "changed": changed}
