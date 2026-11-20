"""R0 章节事实卡：全书评估与大纲偏离检查共用的底座。

每章一次 LLM 调用，产出结构化「事实卡」（时间、地点、人物末状态、关键事件、新确立的事实、伏笔动作、
开头/结尾状态）。后续的确定性检查、LLM 复核、大纲偏离检查都只读事实卡，不再反复读全文。

要点：
  * 只读：只读取版本目录里的章节，只写 `_revision/review/cards/`；不改版本、不写流水线状态；
  * 缓存：键 = 章节文件 sha256 前 8 位 + 卡片 schema 版本。章节没变就不重读，改了几章只重读那几章；
  * 证据先行：卡片里所有带 quote 的条目，quote 必须是章节原文的子串（空白与标点归一化后比对）。
    核对不过的条目丢弃，记入卡片的 dropped（含原因），不进入后续比较，也不会被当作事实；
  * LLM 只写 JSON 到指定文件，Python 严格校验，不合格带着错误重试（最多 MAX_ATTEMPTS 次）；
  * 单章失败不阻塞其它章，失败章节记入返回结果（评估时会明确提示「该章未纳入」）。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import (
    FlowError, file_sha256, load_json, save_json, sub_dir, version_dir)
from novel_writer.textio import read_text_lenient
from novel_writer.workspace import Workspace

SKILL = "memory-manager"
CARD_SCHEMA = 1            # 卡片结构变化时加一，旧缓存自动失效
MAX_ATTEMPTS = 2
Invoker = Callable[..., Any]

FORESHADOW_ACTIONS = ("plant", "advance", "resolve")
_PAREN = re.compile(r"[（(][^）)]*[）)]")
# 归一化比对时忽略的字符：空白与常见中英文标点
_IGNORE = re.compile(r"[\s　，。、；：！？“”‘’\"'「」『』（）()\[\]【】《》〈〉—…\-·,.;:!?]")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# ─────────────────────────────── 引文核对 ───────────────────────────────

def normalize_for_match(text: str) -> str:
    return _IGNORE.sub("", text or "")


def quote_in_text(quote: str, text_norm: str) -> bool:
    """quote 去掉空白与标点后，是否是（同样归一化的）章节正文的子串。空引文视为不通过。"""
    q = normalize_for_match(quote)
    return bool(q) and q in text_norm


def locate_span(quote: str, text: str) -> Optional[Tuple[int, int]]:
    """在原文中定位 quote（同样忽略空白与标点），返回原文中的 (起, 止) 下标；找不到返回 None。"""
    q = normalize_for_match(quote)
    if not q:
        return None
    idx = [i for i, ch in enumerate(text) if not _IGNORE.match(ch)]
    norm = "".join(text[i] for i in idx)
    pos = norm.find(q)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(q) - 1] + 1


def context_for(evidence: Dict[str, Any], text: str, snip: int) -> str:
    """取一条证据在章节原文中的上下文（前后各 snip 字）：优先引文，其次定位词，都没有则取章首。"""
    span = locate_span(evidence.get("quote") or "", text) if evidence.get("quote") else None
    if span:
        return text[max(0, span[0] - snip): min(len(text), span[1] + snip)]
    term = (evidence.get("term") or "").strip()
    if term:
        i = text.find(term)
        if i >= 0:
            return text[max(0, i - snip): min(len(text), i + len(term) + snip)]
    return text[: snip * 2]


# ─────────────────────────────── 缓存 ───────────────────────────────

def cards_dir(ws) -> Path:
    return sub_dir(ws, "review") / "cards"


def card_path(ws, number: int, sha: str) -> Path:
    return cards_dir(ws) / f"ch{number:03d}.{sha[:8]}.json"


def load_card(ws, number: int, sha: str) -> Optional[Dict[str, Any]]:
    data = load_json(card_path(ws, number, sha), default=None)
    if isinstance(data, dict) and data.get("schema") == CARD_SCHEMA and data.get("source_sha256") == sha:
        return data
    return None


# ─────────────────────────────── 提示词 ───────────────────────────────

_FORMAT = """{
  "chapter": 章号（整数）,
  "time_marker": "本章的时间标记（原文表述，没有则空串）",
  "location_start": "本章开头所在地点",
  "location_end": "本章结尾所在地点",
  "characters": [{"name": "人物名", "role_in_chapter": "本章作用", "state_end": "本章末该人物的状态（伤势、情绪、持有物、所知信息、是否在场/离场/死亡）"}],
  "events": ["关键事件，按发生顺序"],
  "facts_stated": [{"subject": "主体", "attribute": "属性", "value": "取值", "quote": "支撑它的原文句子（必须逐字摘自本章）"}],
  "foreshadow": [{"ref": "伏笔编号或简述", "action": "plant|advance|resolve", "quote": "原文句子（逐字摘自本章）"}],
  "opening_state": "本章开头的局面（人物处境、承接上一章的状态）",
  "ending_state": "本章结尾的局面（留给下一章的状态）",
  "open_questions": ["本章留下的悬念"]
}"""


def build_prompt(*, number: int, title: str, chapter_path: Path, out_file: Path, errors: List[str]) -> str:
    parts = [
        f"【任务】阅读第{number}章《{title}》，为它提取一张结构化的「事实卡」。只做提取，不要评价，不要修改章节。",
        f"【章节路径】{chapter_path}（请用文件读取工具完整阅读）",
        "【要求】\n"
        "- 只记录本章正文里确实写到的内容，不要推测、不要补全。\n"
        "- facts_stated 记录本章新确立或重申的、以后可能与别处冲突的事实"
        "（外貌、年龄、身份、关系、持有物、地点方位、规则、数字、时间等）。\n"
        "- 凡带 quote 的条目，quote 必须是章节原文里的一句话，逐字摘抄；摘不出原文的条目不要写。\n"
        "- 人物名使用正文里的称呼。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n格式：\n{_FORMAT}\n只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验，请修正后重新写出】\n" + "\n".join(f"- {e}" for e in errors[:20]))
    return "\n\n".join(parts) + "\n"


# ─────────────────────────────── 校验 ───────────────────────────────

def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _str_list(v: Any, field: str) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise FlowError(f"{field} 必须是字符串列表")
    return [x.strip() for x in v if x.strip()]


def _list_of_dicts(v: Any, field: str) -> List[Dict[str, Any]]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise FlowError(f"{field} 必须是对象列表")
    return v


def known_names(ws, version: str) -> Dict[str, str]:
    """已登记人物：名字 → 规范名（读版本内的角色档案；读不到则返回空，不影响卡片生成）。"""
    try:
        from novel_writer import character_store as CS
        recs, _ = CS.load_records(version_dir(ws, version) / "大纲规划" / "人物设定" / "角色")
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for rec in (recs or {}).values():
        name = _s(rec.get("name")) if isinstance(rec, dict) else ""
        if name:
            out[name] = name
            for al in (rec.get("aliases") or []) if isinstance(rec.get("aliases"), list) else []:
                if isinstance(al, str) and al.strip():
                    out[al.strip()] = name
    return out


def canon_name(name: str, names: Dict[str, str]) -> Tuple[str, bool]:
    """去掉括号注释后归一到规范名。返回 (名字, 是否已登记)；没有登记表时一律视为已登记（不误报）。"""
    base = _PAREN.sub("", name or "").strip()
    if not names:
        return base, True
    return (names[base], True) if base in names else (base, False)


def parse_card(raw: str, number: int, chapter_text: str, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """校验并规范化一张事实卡。结构不合法抛 FlowError；引文核对不过的条目丢弃并记入 dropped。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    if not isinstance(data, dict):
        raise FlowError("顶层必须是 JSON 对象")
    if data.get("chapter") != number or isinstance(data.get("chapter"), bool):
        raise FlowError(f"chapter 必须是 {number}")
    names = names or {}
    norm = normalize_for_match(chapter_text)
    dropped: List[Dict[str, str]] = []

    characters: List[Dict[str, Any]] = []
    unregistered: List[str] = []
    for c in _list_of_dicts(data.get("characters"), "characters"):
        nm = _s(c.get("name"))
        if not nm:
            raise FlowError("characters 中的条目缺少 name")
        canon, ok = canon_name(nm, names)
        if not ok and canon not in unregistered:
            unregistered.append(canon)
        characters.append({"name": canon, "role_in_chapter": _s(c.get("role_in_chapter")),
                           "state_end": _s(c.get("state_end"))})

    facts: List[Dict[str, str]] = []
    for f in _list_of_dicts(data.get("facts_stated"), "facts_stated"):
        subj, attr, val, quote = _s(f.get("subject")), _s(f.get("attribute")), _s(f.get("value")), _s(f.get("quote"))
        if not (subj and attr and val):
            raise FlowError("facts_stated 的每一项都需要 subject、attribute、value")
        if not quote_in_text(quote, norm):
            dropped.append({"kind": "fact", "text": f"{subj}.{attr}={val}", "reason": "引文不在本章原文中" if quote else "缺少引文"})
            continue
        facts.append({"subject": canon_name(subj, names)[0], "attribute": attr, "value": val, "quote": quote})

    foreshadow: List[Dict[str, str]] = []
    for f in _list_of_dicts(data.get("foreshadow"), "foreshadow"):
        ref, action, quote = _s(f.get("ref")), _s(f.get("action")), _s(f.get("quote"))
        if not ref or action not in FORESHADOW_ACTIONS:
            raise FlowError(f"foreshadow 的每一项都需要 ref，action 取值 {list(FORESHADOW_ACTIONS)}")
        if not quote_in_text(quote, norm):
            dropped.append({"kind": "foreshadow", "text": f"{action}:{ref}", "reason": "引文不在本章原文中" if quote else "缺少引文"})
            continue
        foreshadow.append({"ref": ref, "action": action, "quote": quote})

    return {
        "schema": CARD_SCHEMA, "chapter": number,
        "time_marker": _s(data.get("time_marker")),
        "location_start": _s(data.get("location_start")), "location_end": _s(data.get("location_end")),
        "characters": characters, "unregistered_characters": unregistered,
        "events": _str_list(data.get("events"), "events"),
        "facts_stated": facts, "foreshadow": foreshadow,
        "opening_state": _s(data.get("opening_state")), "ending_state": _s(data.get("ending_state")),
        "open_questions": _str_list(data.get("open_questions"), "open_questions"),
        "dropped": dropped,
    }


# ─────────────────────────────── 章节清单与估算 ───────────────────────────────

def resolve_version(ws, version: Optional[str]) -> str:
    """评估只在完稿后可用：必须已有 v001；version 缺省取 head。"""
    if not V.is_baseline_ready(ws):
        raise FlowError("评估只在完稿后可用：请先创建完稿基线 v001")
    vid = version or V.load_index(ws)["head"]
    if not vid:
        raise FlowError("没有可评估的版本")
    V.get_entry(ws, vid)
    return vid


def list_chapter_sources(ws, version: str) -> List[Dict[str, Any]]:
    """[{number, title, path, sha}]，按章号。"""
    out = []
    for ch in Workspace(version_dir(ws, version)).list_chapters():
        out.append({"number": ch.number, "title": ch.title, "path": ch.path, "sha": file_sha256(ch.path)})
    return out


def estimate(ws, version: Optional[str] = None) -> Dict[str, Any]:
    """评估前的成本估算（不调用 LLM）。事实卡的次数是精确的（总章数 − 缓存命中）；其余阶段只给量级。"""
    vid = resolve_version(ws, version)
    chapters = list_chapter_sources(ws, vid)
    settings = CFG.load(ws)
    cached = sum(1 for c in chapters if load_card(ws, c["number"], c["sha"]) is not None)
    to_generate = len(chapters) - cached
    window = settings["review_window"]
    windows = max(1, -(-max(len(chapters) - 1, 1) // max(window - 1, 1))) if chapters else 0
    return {
        "version": vid, "chapters": len(chapters), "cards_cached": cached, "cards_to_generate": to_generate,
        "cards_calls": to_generate,
        "later_stages_hint": {"windows": windows, "whole_book": 5,
                              "adjudication": "取决于确定性检查产生的候选数，事先无法精确估计"},
        "max_llm_calls": settings["review_max_llm_calls"] or None,   # None = 不设上限
    }


# ─────────────────────────────── 生成 ───────────────────────────────

def generate_cards(ws, version: Optional[str], invoker: Invoker, progress=None,
                   chapters: Optional[List[int]] = None, force: bool = False) -> Dict[str, Any]:
    """为版本的各章生成事实卡（命中缓存的跳过）。chapters 限定章号；force 忽略缓存重新生成。

    返回 {version, total, cached, generated, failed:[{chapter, errors}], calls, paused}。
    review_max_llm_calls > 0 且已达上限时停止并 paused=True（缓存已保存的卡片保留，重跑从断点继续）。
    """
    vid = resolve_version(ws, version)
    say = progress or (lambda _t: None)
    settings = CFG.load(ws)
    limit = settings["review_max_llm_calls"]
    sources = list_chapter_sources(ws, vid)
    if chapters is not None:
        wanted = set(chapters)
        missing = wanted - {c["number"] for c in sources}
        if missing:
            raise FlowError(f"版本 {vid} 中找不到章节：{sorted(missing)}")
        sources = [c for c in sources if c["number"] in wanted]
    names = known_names(ws, vid)
    result: Dict[str, Any] = {"version": vid, "total": len(sources), "cached": 0, "generated": 0,
                              "failed": [], "calls": 0, "paused": False}
    say(f"事实卡：版本 {vid}，共 {len(sources)} 章" + ("（强制重新生成）" if force else ""))
    for src in sources:
        n = src["number"]
        if not force and load_card(ws, n, src["sha"]) is not None:
            result["cached"] += 1
            say(f"第{n}章：命中缓存，跳过")
            continue
        text, _ = read_text_lenient(src["path"])
        errors: List[str] = []
        card: Optional[Dict[str, Any]] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if limit and result["calls"] >= limit:
                result["paused"] = True
                say(f"已达调用上限 {limit}，暂停；再次运行会从第{n}章继续")
                return result
            out_dir = sub_dir(ws, "review") / "work" / vid / f"ch{n:03d}" / f"attempt{attempt:02d}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "card.json"
            if out_file.exists():
                out_file.unlink()
            prompt = build_prompt(number=n, title=src["title"], chapter_path=src["path"], out_file=out_file, errors=errors)
            result["calls"] += 1
            say(f"第{n}章（{src['title']}）第{attempt}次尝试：调用模型提取事实卡")
            try:
                invoker(skill=SKILL, prompt=prompt, export_dir=out_dir, step_id=f"card_{vid}_{n}_{attempt}")
            except Exception as exc:
                errors = [f"执行器异常：{exc}"]
                say(f"  执行器异常：{exc}")
                continue
            if not out_file.is_file():
                errors = ["没有写出 card.json"]
                say("  未写出事实卡文件，准备重试")
                continue
            try:
                card = parse_card(out_file.read_text(encoding="utf-8"), n, text, names)
                break
            except (FlowError, UnicodeDecodeError) as exc:
                errors = [str(exc)]
                card = None
                say(f"  事实卡不合法：{exc}")
        if card is None:
            result["failed"].append({"chapter": n, "errors": errors})
            say(f"第{n}章：事实卡生成失败，该章不会纳入后续评估")
            continue
        card.update({"source_sha256": src["sha"], "version": vid, "title": src["title"], "generated_at": _now()})
        save_json(card_path(ws, n, src["sha"]), card)
        result["generated"] += 1
        extra = f"，丢弃 {len(card['dropped'])} 条无法核对引文的条目" if card["dropped"] else ""
        say(f"第{n}章：事实卡完成（事实 {len(card['facts_stated'])}、伏笔 {len(card['foreshadow'])}{extra}）")
    say(f"事实卡完成：新生成 {result['generated']}，缓存 {result['cached']}，失败 {len(result['failed'])}，调用 {result['calls']} 次")
    return result


def load_cards(ws, version: Optional[str] = None) -> Tuple[Dict[int, Dict[str, Any]], List[int]]:
    """读取版本当前各章对应的卡片。返回 ({章号: 卡片}, 缺卡章号列表)。"""
    vid = resolve_version(ws, version)
    cards: Dict[int, Dict[str, Any]] = {}
    missing: List[int] = []
    for src in list_chapter_sources(ws, vid):
        card = load_card(ws, src["number"], src["sha"])
        if card is None:
            missing.append(src["number"])
        else:
            cards[src["number"]] = card
    return cards, missing
