"""R2 LLM 复核：对 R1 的候选做仲裁，并发现规则看不到的跨章问题。

三个阶段（均只读版本，只写 `_revision/review/rev_<版本>/`）：
  * C1 候选仲裁：每条 needs_adjudication 的候选，取它在各章的原文片段（前后 review_snippet_chars 字），
    判定 confirmed（确认为问题）/ false_positive（误报）/ uncertain（无法判定），并给出理由；
  * C2 衔接审阅：按窗口（review_window 章，相邻窗口重叠 1 章）审阅事实卡的转场与人物连续性；
  * C3 全书维度审阅：一次调用，输入全书章节摘要，只针对已启用的维度给出问题（节奏、冗余、文风默认关闭）。

要点：
  * 证据先行：LLM 给出的每条引文都经子串核对。核对不过的证据 verified=False；问题只要有一条证据不过，
    就标记 evidence_ok=False，界面默认折叠（R3 的批量采纳也不包含它们）；
  * 调用上限（review_max_llm_calls，0 = 不限）：达到上限即暂停。已完成的仲裁、窗口、全书判定都缓存在
    llm_state.json，续跑时从断点继续，不重复调用；
  * 缓存随输入失效：窗口与全书判定的键包含章节事实卡的 sha，章节变了就重新判定；
    不再存在的候选的仲裁结果会被清理；
  * LLM 只写 JSON 到指定文件，Python 严格校验，不合格带着错误重试（最多 MAX_ATTEMPTS 次）；
  * 问题池中的 LLM 来源问题每次按缓存重建，已做的处理（状态、备注）按问题编号保留。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow.common import FlowError, load_json, save_json, sub_dir
from novel_writer.textio import read_text_lenient

STATE_SCHEMA = 1
MAX_ATTEMPTS = 2
SKILL_ADJUDICATE = "consistency-checker"
SKILL_WINDOW = "consistency-checker"
SKILL_WHOLE = "revision-editor"
VERDICTS = ("confirmed", "false_positive", "uncertain")
VERDICT_LABEL = {"confirmed": "确认为问题", "false_positive": "误报", "uncertain": "无法判定"}
WINDOW_CATEGORIES = ("continuity", "character_logic", "timeline")          # C2：衔接相关
WHOLE_CATEGORIES = ("character_logic", "continuity", "pacing", "redundancy", "style_drift")   # C3
Invoker = Callable[..., Any]


class _Paused(Exception):
    """调用次数达到上限：停止本次运行，已完成的部分已缓存。"""


class _Budget:
    def __init__(self, limit: int):
        self.limit = int(limit or 0)     # 0 = 不限
        self.calls = 0

    def take(self) -> None:
        if self.limit and self.calls >= self.limit:
            raise _Paused()
        self.calls += 1


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


# ─────────────────────────────── 状态与缓存 ───────────────────────────────

def _state_path(ws, vid: str) -> Path:
    return RK.review_dir(ws, vid) / "llm_state.json"


def _load_state(ws, vid: str) -> Dict[str, Any]:
    data = load_json(_state_path(ws, vid), default=None)
    if isinstance(data, dict) and data.get("schema") == STATE_SCHEMA:
        for key in ("adjudications", "windows", "whole"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data
    return {"schema": STATE_SCHEMA, "adjudications": {}, "windows": {}, "whole": {}}


def _save_state(ws, vid: str, state: Dict[str, Any]) -> None:
    save_json(_state_path(ws, vid), state)


def _load_texts(ws, vid: str) -> Tuple[Dict[int, str], Dict[int, Path]]:
    texts: Dict[int, str] = {}
    paths: Dict[int, Path] = {}
    for src in RC.list_chapter_sources(ws, vid):
        texts[src["number"]], _ = read_text_lenient(src["path"])
        paths[src["number"]] = src["path"]
    return texts, paths


# ─────────────────────────────── 通用调用（重试 + 校验） ───────────────────────────────

def _ask(ws, vid: str, *, kind: str, key: str, skill: str, invoker: Invoker, budget: _Budget,
         make_prompt: Callable[[Path, List[str]], str], parse: Callable[[str], Any],
         say: Callable[[str], None]) -> Tuple[Optional[Any], List[str]]:
    """调用执行器并校验输出。不合格带着错误重试。返回 (结果或 None, 最后的错误列表)。"""
    errors: List[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget.take()                                   # 超上限时抛出 _Paused
        out_dir = sub_dir(ws, "review") / "work" / vid / kind / key / f"attempt{attempt:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "out.json"
        if out_file.exists():
            out_file.unlink()
        prompt = make_prompt(out_file, errors)
        say(f"  第{attempt}次尝试：调用模型")
        try:
            invoker(skill=skill, prompt=prompt, export_dir=out_dir, step_id=f"{kind}_{vid}_{key}_{attempt}")
        except Exception as exc:
            errors = [f"执行器异常：{exc}"]
            say(f"  执行器异常：{exc}")
            continue
        if not out_file.is_file():
            errors = ["没有写出 out.json"]
            say("  未写出结果文件，准备重试")
            continue
        try:
            return parse(out_file.read_text(encoding="utf-8")), []
        except (FlowError, UnicodeDecodeError) as exc:
            errors = [str(exc)]
            say(f"  结果不合法：{exc}")
    return None, errors


def _json_obj(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    if not isinstance(data, dict):
        raise FlowError("顶层必须是 JSON 对象")
    return data


def parse_verdict(raw: str) -> Dict[str, str]:
    data = _json_obj(raw)
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        raise FlowError(f"verdict 必须是 {list(VERDICTS)} 之一")
    reason = _s(data.get("reason"))
    if not reason:
        raise FlowError("缺少 reason")
    return {"verdict": verdict, "reason": reason}


def parse_issues(raw: str, allowed: Tuple[str, ...]) -> List[Dict[str, Any]]:
    data = _json_obj(raw)
    items = data.get("issues")
    if not isinstance(items, list):
        raise FlowError("issues 必须是列表（没有问题时写空列表）")
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            raise FlowError("issues 的每一项必须是对象")
        if it.get("category") not in allowed:
            raise FlowError(f"category 必须是 {list(allowed)} 之一")
        if it.get("severity") not in RK.SEVERITIES:
            raise FlowError(f"severity 必须是 {list(RK.SEVERITIES)} 之一")
        title, desc = _s(it.get("title")), _s(it.get("description"))
        if not title or not desc:
            raise FlowError("每个问题都需要 title 与 description")
        ev_raw = it.get("evidence")
        if not isinstance(ev_raw, list) or not ev_raw:
            raise FlowError("每个问题都需要至少一条 evidence（章号 + 该章原文引文）")
        evidence = []
        for e in ev_raw:
            if not isinstance(e, dict) or not isinstance(e.get("chapter"), int) or isinstance(e.get("chapter"), bool):
                raise FlowError("evidence 的 chapter 必须是整数章号")
            evidence.append({"chapter": e["chapter"], "quote": _s(e.get("quote"))})
        chapters = [c for c in (it.get("chapters") or []) if isinstance(c, int) and not isinstance(c, bool)]
        out.append({"category": it["category"], "severity": it["severity"], "title": title,
                    "description": desc, "chapters": chapters, "evidence": evidence})
    return out


def _llm_finding(stage: str, key: str, issue: Dict[str, Any], texts: Dict[int, str]) -> Dict[str, Any]:
    """把一条已校验的 LLM 问题转成问题池条目；逐条核对引文。"""
    evidence = []
    for e in issue["evidence"]:
        text = texts.get(e["chapter"], "")
        ok = bool(e["quote"]) and RC.quote_in_text(e["quote"], RC.normalize_for_match(text))
        evidence.append({"chapter": e["chapter"], "quote": e["quote"], "verified": ok, "source": "quote", "note": ""})
    chapters = sorted(set(issue["chapters"]) | {e["chapter"] for e in issue["evidence"]})
    return {
        "id": RK._fid(f"llm_{stage}", f"{key}|{issue['title']}"), "source": "llm", "check": f"{stage}_review",
        "category": issue["category"], "severity": issue["severity"], "chapters": chapters,
        "title": issue["title"], "description": issue["description"], "evidence": evidence,
        "evidence_ok": all(e["verified"] for e in evidence),
        "needs_adjudication": False, "options": [], "status": "open", "notes": "",
    }


# ─────────────────────────────── C1 候选仲裁 ───────────────────────────────

def _adj_prompt(finding: Dict[str, Any], contexts: List[Tuple[int, str]], out_file: Path, errors: List[str]) -> str:
    ev = "\n\n".join(f"【证据{i}｜第{c}章】\n{ctx}" for i, (c, ctx) in enumerate(contexts, 1)) or "（无可用原文片段）"
    parts = [
        "【任务】仲裁候选问题：下面是一条由规则发现的候选问题及其原文片段。请对照片段判断它是否真的是问题。只做判断，不要修改任何章节。",
        f"【候选问题】{finding['title']}\n{finding['description']}",
        f"【原文片段】\n{ev}",
        "【判定】confirmed：片段确实存在矛盾或问题；false_positive：误报（如回忆、比喻、只是被提及、场景合理转换、"
        "人物确实复活或发生了改变）；uncertain：片段不足以判断。reason 写明依据，可引用片段中的关键词。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n"
        "格式：{\"verdict\": \"confirmed|false_positive|uncertain\", \"reason\": \"判断理由\"}\n"
        "只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _adjudicate(ws, vid: str, cands: List[Dict[str, Any]], state: Dict[str, Any], texts: Dict[int, str],
                settings: Dict[str, Any], budget: _Budget, invoker: Invoker, say, failed: List[Dict[str, Any]]) -> None:
    snip = settings["review_snippet_chars"]
    pending = [f for f in cands if f["id"] not in state["adjudications"]]
    say(f"候选仲裁：共 {len(cands)} 条，待判定 {len(pending)} 条（其余已缓存）")
    for f in pending:
        contexts = []
        for e in f["evidence"]:
            if e["chapter"] in texts:
                contexts.append((e["chapter"], RC.context_for(e, texts[e["chapter"]], snip)))
        say(f"仲裁「{f['title']}」")
        res, errs = _ask(
            ws, vid, kind="adj", key=f["id"], skill=SKILL_ADJUDICATE, invoker=invoker, budget=budget,
            make_prompt=lambda out, errors, f=f, contexts=contexts: _adj_prompt(f, contexts, out, errors),
            parse=parse_verdict, say=say)
        if res is None:
            failed.append({"stage": "adjudicate", "id": f["id"], "errors": errs})
            say(f"  仲裁失败，该条下次运行再试：{'；'.join(errs)}")
            continue
        state["adjudications"][f["id"]] = {**res, "at": _now()}
        _save_state(ws, vid, state)
        say(f"  结论：{VERDICT_LABEL[res['verdict']]}")


# ─────────────────────────────── C2 衔接审阅 ───────────────────────────────

def _windows(numbers: List[int], size: int) -> List[List[int]]:
    """窗口切分：每个窗口 size 章，相邻窗口重叠 1 章。少于两章的窗口不产生。"""
    out: List[List[int]] = []
    if len(numbers) < 2:
        return out
    i = 0
    while True:
        w = numbers[i:i + size]
        if len(w) >= 2:
            out.append(w)
        if i + size >= len(numbers):
            break
        i += size - 1
    return out


def _window_key(chs: List[int], cards: Dict[int, Dict[str, Any]]) -> str:
    h = hashlib.sha1("|".join(cards[c]["source_sha256"] for c in chs).encode("utf-8")).hexdigest()[:8]
    return f"w{chs[0]:03d}-{chs[-1]:03d}-{h}"


def _card_brief(card: Dict[str, Any]) -> str:
    chars = "；".join(f"{c['name']}：{c['state_end'] or '—'}" for c in card["characters"]) or "—"
    return (f"  时间：{card['time_marker'] or '—'}；开头地点：{card['location_start'] or '—'}；"
            f"结尾地点：{card['location_end'] or '—'}\n"
            f"  开头局面：{card['opening_state'] or '—'}\n  结尾局面：{card['ending_state'] or '—'}\n"
            f"  人物末状态：{chars}")


def _window_prompt(chs: List[int], cards, paths, allowed, out_file: Path, errors: List[str]) -> str:
    blocks = [f"第{c}章《{cards[c]['title']}》　路径：{paths[c]}\n{_card_brief(cards[c])}" for c in chs]
    parts = [
        "【任务】衔接审阅：检查下列相邻章节之间的衔接是否存在问题（转场生硬或断裂、人物处境或状态前后不接、"
        "时间线跳动、人物行为与前一章不符）。只找有证据的问题，没有就写空列表。",
        "【范围】只审阅编号相邻的章节之间的衔接（如第3章与第4章）；编号不连续的章节不要当作相邻。"
        "不要评价章节内部的文风与节奏。",
        "【章节摘要】（来自事实卡）\n" + "\n\n".join(blocks),
        f"【允许的 category】{list(allowed)}；severity 取 严重 / 中 / 轻。",
        "【证据】每个问题必须带 evidence：章号 + 该章原文中逐字摘抄的一句话。需要核对时请读取上面的章节路径。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n"
        "格式：{\"issues\": [{\"category\": \"...\", \"severity\": \"严重|中|轻\", \"chapters\": [章号], "
        "\"title\": \"简短标题\", \"description\": \"问题说明\", \"evidence\": [{\"chapter\": 章号, \"quote\": \"原文\"}]}]}\n"
        "只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _window_stage(ws, vid: str, cards, texts, paths, allowed, wins, settings, state, budget, invoker, say,
                  failed: List[Dict[str, Any]]) -> None:
    if not allowed:
        return
    say(f"衔接审阅：{len(wins)} 个窗口（每窗 {settings['review_window']} 章，重叠 1 章）")
    for chs in wins:
        key = _window_key(chs, cards)
        if key in state["windows"]:
            say(f"窗口 {chs[0]}–{chs[-1]} 章：命中缓存")
            continue
        say(f"窗口 {chs[0]}–{chs[-1]} 章：审阅衔接")
        res, errs = _ask(
            ws, vid, kind="win", key=key, skill=SKILL_WINDOW, invoker=invoker, budget=budget,
            make_prompt=lambda out, errors, chs=chs: _window_prompt(chs, cards, paths, allowed, out, errors),
            parse=lambda raw: parse_issues(raw, allowed), say=say)
        if res is None:
            failed.append({"stage": "window", "id": key, "errors": errs})
            say(f"  窗口失败，下次运行再试：{'；'.join(errs)}")
            continue
        state["windows"][key] = {"at": _now(), "findings": [_llm_finding("window", key, i, texts) for i in res]}
        _save_state(ws, vid, state)
        say(f"  得到 {len(res)} 条问题")


# ─────────────────────────────── C3 全书维度审阅 ───────────────────────────────

def _whole_prompt(cards, paths, allowed, out_file: Path, errors: List[str]) -> str:
    lines = []
    for n in sorted(cards):
        c = cards[n]
        names = "、".join(x["name"] for x in c["characters"]) or "—"
        events = "；".join(c["events"][:6]) or "—"
        qs = "；".join(c["open_questions"][:3]) or "—"
        lines.append(f"第{n}章《{c['title']}》　路径：{paths[n]}\n  人物：{names}\n  事件：{events}\n"
                     f"  结尾局面：{c['ending_state'] or '—'}\n  悬念：{qs}")
    parts = [
        "【任务】全书维度审阅：根据全书章节摘要，找出跨章层面的问题。只做判断，不要修改任何章节。",
        f"【只允许的 category】{list(allowed)}\n"
        "- character_logic：人物弧线停滞、突变或动机断裂\n"
        "- continuity：主线悬空、信息披露顺序与前文矛盾\n"
        "- pacing：某段情节拖沓或节奏失衡（仅在 category 允许时输出）\n"
        "- redundancy：多章重复同一信息或情节（仅在 category 允许时输出）\n"
        "- style_drift：全书文风前后明显漂移（仅在 category 允许时输出）",
        "【全书摘要】（来自事实卡）\n" + "\n\n".join(lines),
        "【证据】每个问题必须带 evidence：章号 + 该章原文中逐字摘抄的一句话。需要核对时请读取对应章节路径。"
        "没有把握的不要写；宁缺毋滥。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n"
        "格式：{\"issues\": [{\"category\": \"...\", \"severity\": \"严重|中|轻\", \"chapters\": [章号], "
        "\"title\": \"简短标题\", \"description\": \"问题说明\", \"evidence\": [{\"chapter\": 章号, \"quote\": \"原文\"}]}]}\n"
        "只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _whole_key(cards, allowed) -> str:
    raw = "|".join(cards[n]["source_sha256"] for n in sorted(cards)) + "|" + ",".join(allowed)
    return "book-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def _whole_stage(ws, vid: str, cards, texts, paths, allowed, key, state, budget, invoker, say,
                 failed: List[Dict[str, Any]]) -> None:
    if not allowed or not key:
        return
    if key in state["whole"]:
        say("全书维度审阅：命中缓存")
        return
    say(f"全书维度审阅：维度 {list(allowed)}")
    res, errs = _ask(
        ws, vid, kind="whole", key=key, skill=SKILL_WHOLE, invoker=invoker, budget=budget,
        make_prompt=lambda out, errors: _whole_prompt(cards, paths, allowed, out, errors),
        parse=lambda raw: parse_issues(raw, allowed), say=say)
    if res is None:
        failed.append({"stage": "whole", "id": key, "errors": errs})
        say(f"  全书审阅失败，下次运行再试：{'；'.join(errs)}")
        return
    state["whole"][key] = {"at": _now(), "findings": [_llm_finding("whole", key, i, texts) for i in res]}
    _save_state(ws, vid, state)
    say(f"  得到 {len(res)} 条问题")


# ─────────────────────────────── 合并进问题池 ───────────────────────────────

def _merge_pool(ws, vid: str, state: Dict[str, Any], live_keys: List[str]) -> Dict[str, Any]:
    """把缓存中仍有效的 LLM 问题与仲裁结果合并进问题池，保留已做的处理，重写报告。"""
    data = RK.load_findings(ws, vid)
    old_llm = {f["id"]: f for f in data["findings"] if f.get("source") == "llm"}
    base = [f for f in data["findings"] if f.get("source") != "llm"]
    for f in base:
        adj = state["adjudications"].get(f["id"])
        if adj:
            f["adjudication"] = {**adj}
        else:
            f.pop("adjudication", None)
    llm: List[Dict[str, Any]] = []
    for key in live_keys:
        bucket = state["windows"].get(key) or state["whole"].get(key)
        for f in (bucket or {}).get("findings", []):
            prev = old_llm.get(f["id"])
            if prev:
                f["status"], f["notes"] = prev.get("status", "open"), prev.get("notes", "")
            llm.append(f)
    merged = base + llm
    merged.sort(key=lambda f: (RK._SEV_ORDER.get(f["severity"], 9), f["category"], f["chapters"][:1], f["id"]))
    data["findings"] = merged
    data["updated_at"] = _now()
    save_json(RK.findings_path(ws, vid), data)
    (RK.review_dir(ws, vid) / "report.md").write_text(RK.render_report(data), encoding="utf-8")
    return data


# ─────────────────────────────── 入口 ───────────────────────────────

def run_review(ws, version: Optional[str] = None, invoker: Optional[Invoker] = None, progress=None) -> Dict[str, Any]:
    """运行 LLM 复核。先刷新确定性检查（不耗调用），再依次做 C1 / C2 / C3。

    返回 {review_id, version, calls, paused, failed, counts, findings}。达到调用上限时 paused=True，
    已完成的部分保留，再次运行即从断点继续。
    """
    if invoker is None:
        raise FlowError("LLM 复核需要执行器")
    vid = RC.resolve_version(ws, version)
    say = progress or (lambda _t: None)
    cards, missing = RC.load_cards(ws, vid)
    if not cards:
        raise FlowError("没有可用的事实卡，请先生成事实卡（review-cards）")
    settings = CFG.load(ws)
    dims = set(settings["review_dimensions"])
    RK.run_checks(ws, vid)                                        # 先刷新确定性候选（不调用 LLM）
    pool = RK.load_findings(ws, vid)
    cands = [f for f in pool["findings"] if f.get("needs_adjudication") and f.get("source") == "deterministic"]
    texts, paths = _load_texts(ws, vid)
    state = _load_state(ws, vid)
    budget = _Budget(settings["review_max_llm_calls"])
    failed: List[Dict[str, Any]] = []
    # 本次有效的全部缓存键在任何调用之前就算好：中途暂停也不会误删仍然有效的缓存
    win_allowed = tuple(c for c in WINDOW_CATEGORIES if c in dims)
    wins = _windows(sorted(cards), settings["review_window"]) if win_allowed else []
    whole_allowed = tuple(c for c in WHOLE_CATEGORIES if c in dims)
    whole_key = _whole_key(cards, whole_allowed) if whole_allowed else None
    live_keys = [_window_key(c, cards) for c in wins] + ([whole_key] if whole_key else [])
    result: Dict[str, Any] = {"review_id": RK.review_id(vid), "version": vid, "paused": False}
    say(f"LLM 复核：版本 {vid}，事实卡 {len(cards)} 章，调用上限 {settings['review_max_llm_calls'] or '不限'}")
    try:
        _adjudicate(ws, vid, cands, state, texts, settings, budget, invoker, say, failed)
        _window_stage(ws, vid, cards, texts, paths, win_allowed, wins, settings, state, budget, invoker, say, failed)
        _whole_stage(ws, vid, cards, texts, paths, whole_allowed, whole_key, state, budget, invoker, say, failed)
    except _Paused:
        result["paused"] = True
        say(f"已达调用上限（{budget.limit}），暂停；再次运行会从断点继续")
    finally:
        # 只保留仍然有效的缓存：候选消失的仲裁、输入已变化（章节改动）的窗口与全书判定都清理掉
        live_ids = {f["id"] for f in cands}
        live = set(live_keys)
        state["adjudications"] = {k: v for k, v in state["adjudications"].items() if k in live_ids}
        state["windows"] = {k: v for k, v in state["windows"].items() if k in live}
        state["whole"] = {k: v for k, v in state["whole"].items() if k in live}
        _save_state(ws, vid, state)
        data = _merge_pool(ws, vid, state, live_keys)
    counts: Dict[str, int] = {}
    for f in data["findings"]:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    result.update({"calls": budget.calls, "failed": failed, "counts": counts, "findings": data["findings"],
                   "missing": missing})
    say(f"LLM 复核完成：调用 {budget.calls} 次，失败 {len(failed)} 处" + ("（已暂停）" if result["paused"] else ""))
    return result


def estimate_llm(ws, version: Optional[str] = None) -> Dict[str, Any]:
    """LLM 复核的粗略估算（不调用 LLM）。候选数取决于是否已运行 review-checks。"""
    vid = RC.resolve_version(ws, version)
    settings = CFG.load(ws)
    dims = set(settings["review_dimensions"])
    cards, _ = RC.load_cards(ws, vid)
    state = _load_state(ws, vid)
    pool = RK.load_findings(ws, vid)
    pending = [f for f in pool["findings"] if f.get("needs_adjudication") and f.get("source") == "deterministic"
               and f["id"] not in state["adjudications"]]
    windows = len(_windows(sorted(cards), settings["review_window"])) if any(
        c in dims for c in WINDOW_CATEGORIES) else 0
    whole = 1 if any(c in dims for c in WHOLE_CATEGORIES) and cards else 0
    return {"version": vid, "checks_run": bool(pool.get("updated_at")),
            "adjudication_pending": len(pending), "windows": windows, "whole_book": whole,
            "calls_typical": len(pending) + windows + whole,
            "max_llm_calls": settings["review_max_llm_calls"] or None,
            "note": "典型调用数不含重试；实际次数以运行结果为准，缓存命中的单元不再调用"}
