"""novel_writer/patrol_findings.py — 巡检整改项：把巡检报告变成可被程序认领的结构化条目。

流程（全部可选，由设置 `patrol_feedback` 分级开启，默认全关）：
  patrol:N          巡检（LLM，原有步骤，只出 md 报告）
  patrol_extract:N  提取（LLM，只读已有报告，写出整改项 JSON；便宜，也可用于已生成的旧报告）
  patrol_apply:N    分流（确定性 Python）：校验后并入账本 `修订记录/巡检整改项.json`

整改项按范围分流：
  forward   约束后续章节 → 注入上下文包（作者采纳后）
  style     要避免的套路/短语 → 注入风格提示，并作为机器校验的「提示」（不阻断）
  blueprint 需要调整蓝图 → 收件箱待办，由作者决定
  retro     已写章节需回改 → 收件箱待办，作者确认后才创建修订请求，绝不自动改正文

状态：proposed（待作者决定）→ adopted（已采纳）| dismissed（已忽略）→ done（已处理）。
forward 与 style 默认自动采纳（`auto_adopt`），不需要作者任何操作；blueprint、retro 涉及改蓝图/改已写正文，
只进收件箱供作者决定（不阻塞流水线），系统不会自动改动。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

SCOPE_FORWARD = "forward"
SCOPE_STYLE = "style"
SCOPE_BLUEPRINT = "blueprint"
SCOPE_RETRO = "retro"
SCOPES = (SCOPE_FORWARD, SCOPE_STYLE, SCOPE_BLUEPRINT, SCOPE_RETRO)
SCOPE_LABEL = {SCOPE_FORWARD: "约束后续章节", SCOPE_STYLE: "避免套路", SCOPE_BLUEPRINT: "蓝图待办",
               SCOPE_RETRO: "回改已写章节"}

SEVERITIES = ("严重", "一般")
LEVELS = ("L1", "L2", "L3", "L4")

S_PROPOSED = "proposed"
S_ADOPTED = "adopted"
S_DISMISSED = "dismissed"
S_DONE = "done"
STATUSES = (S_PROPOSED, S_ADOPTED, S_DISMISSED, S_DONE)
STATUS_LABEL = {S_PROPOSED: "待决定", S_ADOPTED: "已采纳", S_DISMISSED: "已忽略", S_DONE: "已处理"}

AUTO_ADOPT_SCOPES = (SCOPE_FORWARD, SCOPE_STYLE)

MAX_DIRECTIVE = 300
MAX_PHRASE = 20
MAX_INJECT_ITEMS = 5          # 单章最多注入的整改指令条数
MAX_INJECT_CHARS = 700        # 注入文本的总字符上限

# 默认全开：整条链路自动运行，不需要作者操作；任何一项都可在界面或设置里单独关闭
DEFAULT_FEEDBACK = {"extract": True, "auto_adopt": True, "inject_forward": True, "inject_style": True, "to_inbox": True}


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def feedback_settings(settings: Dict[str, Any]) -> Dict[str, bool]:
    raw = (settings or {}).get("patrol_feedback")
    out = dict(DEFAULT_FEEDBACK)
    if isinstance(raw, dict):
        for k in out:
            out[k] = bool(raw.get(k, DEFAULT_FEEDBACK[k]))
    return out


# ── 校验 ───────────────────────────────────────────────────────────────────


def _as_int_list(value: Any) -> Optional[List[int]]:
    if not isinstance(value, list):
        return None
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            return None
        out.append(v)
    return out


def problems(data: Any) -> List[str]:
    """整改项 JSON 的结构问题（空列表 = 合格）。顶层须是 {"findings": [...]}。"""
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return ['顶层必须是 JSON 对象，且含数组字段 "findings"']
    out: List[str] = []
    for i, f in enumerate(data["findings"], 1):
        tag = f"第 {i} 条"
        if not isinstance(f, dict):
            out.append(f"{tag}必须是对象")
            continue
        for key in ("dimension", "title", "directive"):
            if not isinstance(f.get(key), str) or not f[key].strip():
                out.append(f"{tag}缺少非空字符串字段 {key}")
        if isinstance(f.get("directive"), str) and len(f["directive"]) > MAX_DIRECTIVE:
            out.append(f"{tag}的 directive 超过 {MAX_DIRECTIVE} 字，请压缩成一句可执行的指令")
        if f.get("severity") not in SEVERITIES:
            out.append(f"{tag}的 severity 必须是 {list(SEVERITIES)} 之一")
        scope = f.get("scope")
        if scope not in SCOPES:
            out.append(f"{tag}的 scope 必须是 {list(SCOPES)} 之一")
            continue
        chapters = f.get("chapters")
        if chapters is not None and _as_int_list(chapters) is None:
            out.append(f"{tag}的 chapters 必须是章号整数数组")
        if scope == SCOPE_RETRO:
            if not chapters or _as_int_list(chapters) is None:
                out.append(f"{tag}（retro）必须用 chapters 列出要回改的章号")
            if f.get("level") not in LEVELS:
                out.append(f"{tag}（retro）的 level 必须是 {list(LEVELS)} 之一")
        if scope == SCOPE_STYLE:
            phrases = f.get("avoid_phrases")
            if (not isinstance(phrases, list) or not phrases
                    or any(not isinstance(p, str) or not p.strip() or len(p) > MAX_PHRASE for p in phrases)):
                out.append(f"{tag}（style）必须给出 avoid_phrases：非空字符串数组，每个不超过 {MAX_PHRASE} 字")
        for key in ("foreshadowing", "characters"):
            v = f.get(key)
            if v is not None and (not isinstance(v, list) or any(not isinstance(x, str) for x in v)):
                out.append(f"{tag}的 {key} 必须是字符串数组")
        until = f.get("until_chapter")
        if until is not None and (isinstance(until, bool) or not isinstance(until, int) or until < 1):
            out.append(f"{tag}的 until_chapter 必须是正整数")
    return out


# ── 账本 ───────────────────────────────────────────────────────────────────


def read_ledger(ws: Workspace) -> Dict[str, Any]:
    data = read_json(ws.path("patrol_ledger"), default=None)
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return {"findings": []}
    return data


def _write_ledger(ws: Workspace, ledger: Dict[str, Any]) -> None:
    atomic_write_json(ws.path("patrol_ledger"), ledger)


def _normalise(f: Dict[str, Any], patrol_n: int, k: int) -> Dict[str, Any]:
    scope = f["scope"]
    rec = {
        "id": f"P{patrol_n}-{k}",
        "patrol_chapter": patrol_n,
        "dimension": f["dimension"].strip(),
        "severity": f["severity"],
        "scope": scope,
        "title": f["title"].strip(),
        "directive": f["directive"].strip(),
        "chapters": list(f.get("chapters") or []),
        "foreshadowing": [s.strip() for s in (f.get("foreshadowing") or []) if s.strip()],
        "characters": [s.strip() for s in (f.get("characters") or []) if s.strip()],
        "avoid_phrases": [s.strip() for s in (f.get("avoid_phrases") or []) if s.strip()] if scope == SCOPE_STYLE else [],
        "level": f.get("level") if scope == SCOPE_RETRO else None,
        "until_chapter": f.get("until_chapter"),
        "status": S_PROPOSED,
        "decided_by": None,
        "updated_at": _now(),
    }
    return rec


def merge(ledger: Dict[str, Any], data: Dict[str, Any], patrol_n: int, *, auto_adopt: bool) -> Dict[str, Any]:
    """把一次提取结果并入账本（幂等）。

    同一次巡检重复提取时：标题相同的条目沿用已有状态（作者的决定不被覆盖），标题没了的旧条目被丢弃，
    只有「待决定」与「自动采纳」的才会被丢弃；作者亲自采纳/忽略/处理过的条目一律保留。
    """
    old = [f for f in ledger["findings"] if f.get("patrol_chapter") == patrol_n]
    others = [f for f in ledger["findings"] if f.get("patrol_chapter") != patrol_n]
    by_title = {f["title"]: f for f in old}
    kept: List[Dict[str, Any]] = []
    seen_titles = set()
    for k, raw in enumerate(data["findings"], 1):
        rec = _normalise(raw, patrol_n, k)
        prev = by_title.get(rec["title"])
        if prev is not None:
            rec["status"] = prev.get("status", S_PROPOSED)
            rec["decided_by"] = prev.get("decided_by")
        elif auto_adopt and rec["scope"] in AUTO_ADOPT_SCOPES:
            rec["status"], rec["decided_by"] = S_ADOPTED, "auto"
        seen_titles.add(rec["title"])
        kept.append(rec)
    for prev in old:
        if prev["title"] not in seen_titles and prev.get("decided_by") == "author":
            kept.append(prev)
    ids = set()
    for rec in kept:  # 保留下来的旧条目可能与新编号冲突
        base, n = rec["id"], 0
        while rec["id"] in ids:
            n += 1
            rec["id"] = f"{base}.{n}"
        ids.add(rec["id"])
    return {"findings": others + kept}


def apply_extract(ws: Workspace, patrol_n: int, data: Dict[str, Any], auto_adopt: bool = True) -> Dict[str, int]:
    ledger = read_ledger(ws)
    merged = merge(ledger, data, patrol_n, auto_adopt=bool(auto_adopt))
    _write_ledger(ws, merged)
    mine = [f for f in merged["findings"] if f.get("patrol_chapter") == patrol_n]
    return {"total": len(mine), "adopted": sum(1 for f in mine if f["status"] == S_ADOPTED)}


def set_status(ws: Workspace, finding_id: str, status: str) -> Dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"非法状态 {status!r}")
    ledger = read_ledger(ws)
    for f in ledger["findings"]:
        if f.get("id") == finding_id:
            f["status"], f["decided_by"], f["updated_at"] = status, "author", _now()
            _write_ledger(ws, ledger)
            return f
    raise KeyError(f"找不到整改项 {finding_id}")


# ── 供各接入点读取 ─────────────────────────────────────────────────────────


def active_for_chapter(ws: Workspace, n: int, scope: str) -> List[Dict[str, Any]]:
    """第 n 章应遵守的、已采纳的整改项（只对巡检章之后的章节生效；严重的排前面）。"""
    out = []
    for f in read_ledger(ws)["findings"]:
        if f.get("scope") != scope or f.get("status") != S_ADOPTED:
            continue
        if n <= int(f.get("patrol_chapter") or 0):
            continue
        until = f.get("until_chapter")
        if until is not None and n > until:
            continue
        out.append(f)
    out.sort(key=lambda f: (0 if f.get("severity") == "严重" else 1, f.get("id", "")))
    return out


def render_directives(items: List[Dict[str, Any]]) -> str:
    """把整改项渲染成注入上下文包的文本；条数与总字数有上限。"""
    lines: List[str] = []
    total = 0
    for f in items[:MAX_INJECT_ITEMS]:
        extra = []
        if f.get("characters"):
            extra.append("人物：" + "、".join(f["characters"][:4]))
        if f.get("foreshadowing"):
            extra.append("伏笔：" + "、".join(f["foreshadowing"][:6]))
        line = f"- [{f['severity']}·{f['dimension']}] {f['directive']}" + (f"（{'；'.join(extra)}）" if extra else "")
        if total + len(line) > MAX_INJECT_CHARS:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def avoid_phrases_for_chapter(ws: Workspace, n: int) -> List[str]:
    seen: List[str] = []
    for f in active_for_chapter(ws, n, SCOPE_STYLE):
        for p in f.get("avoid_phrases") or []:
            if p not in seen:
                seen.append(p)
    return seen


def pending_items(ws: Workspace) -> List[Dict[str, Any]]:
    return [f for f in read_ledger(ws)["findings"] if f.get("status") == S_PROPOSED]
