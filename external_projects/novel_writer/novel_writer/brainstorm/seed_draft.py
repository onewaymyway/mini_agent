"""一句话起稿 S0：构思稿的数据结构、校验、版本存取、差异与卡片映射（不调用模型）。

构思稿是「从一个简单想法一次生成的整体雏形」，存放在草稿层 `_studio/seed_draft/vNNN.json`，
**不直接写卡片**；满意后由 S3 转成提议，经作者逐项确认才进入卡片。

版本文件结构：
  {ver, kind: seed|revise|restore, reason, at, seed: {idea, constraints}, draft: {...}}
draft 的六个部分（SECTIONS）：
  premise         核心命题（文字）
  storyline       故事走向：logline（一句话主线）+ opening/development/turn/ending（起承转合）
  world           世界观，结构同卡片 world（rules/locations/factions/timeline/taboos）
  cast            角色，结构同卡片 cast（id → 角色）
  plot            核心剧情，结构同卡片 plot（acts/twists/endings）
  open_questions  待定问题，结构同卡片 open_questions

版本只增不删；回退 = 把旧版内容记为一个新版本。
"""
from __future__ import annotations

import copy
import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace, now_iso
from novel_writer.fsutil import atomic_write_json, read_json

SECTIONS = ("premise", "storyline", "world", "cast", "plot", "open_questions")
SECTION_LABELS = {"premise": "核心命题", "storyline": "故事走向", "world": "世界观", "cast": "角色",
                  "plot": "核心剧情", "open_questions": "待定问题"}
STORYLINE_KEYS = ("logline", "opening", "development", "turn", "ending")
STORYLINE_LABELS = {"logline": "一句话主线", "opening": "起", "development": "承", "turn": "转", "ending": "合"}
KINDS = ("seed", "revise", "restore")
_VER_RE = re.compile(r"^v(\d{3})\.json$")


def draft_dir(ws: BrainstormWorkspace) -> Path:
    return ws.root / "_studio" / "seed_draft"


# ─────────────────────────────── 规范化与校验 ───────────────────────────────

def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def normalize_draft(raw: Any) -> Dict[str, Any]:
    """把模型常见的松散写法整理成规范结构（不做业务校验）：
    cast 列表 → id 字典；open_questions 文字列表 → 带 id 的对象；world 的 rules/locations/factions 文字 → {text}。"""
    if not isinstance(raw, dict):
        raise BrainstormError("构思稿必须是对象")
    out: Dict[str, Any] = copy.deepcopy(raw)
    cast = out.get("cast")
    if isinstance(cast, list):
        out["cast"] = {}
        for i, card in enumerate(cast, 1):
            if not isinstance(card, dict):
                raise BrainstormError(f"角色 #{i} 必须是对象")
            card = dict(card)
            cid = _text(card.pop("id", "")) or f"c{i}"
            if cid in out["cast"]:
                raise BrainstormError(f"角色 id 重复：{cid}")
            out["cast"][cid] = card
    oq = out.get("open_questions")
    if isinstance(oq, list):
        fixed = []
        for i, q in enumerate(oq, 1):
            if isinstance(q, str):
                fixed.append({"id": f"q{i}", "text": q})
            elif isinstance(q, dict):
                fixed.append({"id": _text(q.get("id")) or f"q{i}", **{k: v for k, v in q.items() if k != "id"}})
            else:
                fixed.append(q)
        out["open_questions"] = fixed
    world = out.get("world")
    if isinstance(world, dict):
        for key in ("rules", "locations", "factions"):
            items = world.get(key)
            if isinstance(items, list):
                world[key] = [{"text": it} if isinstance(it, str) else it for it in items]
    return out


def validate_draft(raw: Any) -> Dict[str, Any]:
    """校验并返回规范化后的构思稿；六个部分必须齐全，且各自合法。不合法抛 BrainstormError（列出全部问题）。"""
    data = normalize_draft(raw)
    errors: List[str] = []
    missing = [s for s in SECTIONS if s not in data]
    if missing:
        errors.append("缺少部分：" + "、".join(SECTION_LABELS[s] for s in missing))
    unknown = [k for k in data if k not in SECTIONS]
    if unknown:
        errors.append("含未知部分：" + "、".join(unknown))
    clean: Dict[str, Any] = {}
    for sec in SECTIONS:
        if sec not in data:
            continue
        try:
            if sec == "storyline":
                clean[sec] = _validate_storyline(data[sec])
            else:
                clean[sec] = C.validate_module(sec, data[sec])
                if sec == "premise" and not clean[sec]:
                    raise BrainstormError("核心命题不能为空")
                if sec == "cast" and not clean[sec]:
                    raise BrainstormError("至少需要一个角色")
                if sec == "plot" and not clean[sec]["acts"]:
                    raise BrainstormError("核心剧情至少需要一幕")
        except BrainstormError as exc:
            errors.append(f"{SECTION_LABELS[sec]}：{exc}")
    if errors:
        raise BrainstormError("构思稿未通过校验：" + "；".join(errors))
    return {s: clean[s] for s in SECTIONS}


def _validate_storyline(v: Any) -> Dict[str, str]:
    if not isinstance(v, dict):
        raise BrainstormError("故事走向必须是对象")
    unknown = set(v) - set(STORYLINE_KEYS)
    if unknown:
        raise BrainstormError("含未知字段：" + "、".join(sorted(unknown)))
    out = {}
    for k in STORYLINE_KEYS:
        t = _text(v.get(k))
        if not t:
            raise BrainstormError(f"缺少「{STORYLINE_LABELS[k]}」")
        out[k] = t
    return out


# ─────────────────────────────── 版本 ───────────────────────────────

def versions(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    out = []
    d = draft_dir(ws)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("v*.json")):
        m = _VER_RE.match(p.name)
        data = read_json(p, default=None)
        if m and isinstance(data, dict):
            out.append({"ver": int(m.group(1)), "kind": data.get("kind", ""), "reason": data.get("reason", ""),
                        "at": data.get("at", "")})
    return out


def latest_version(ws: BrainstormWorkspace) -> int:
    return max([v["ver"] for v in versions(ws)] or [0])


def load(ws: BrainstormWorkspace, ver: Optional[int] = None) -> Dict[str, Any]:
    """读取某一版（默认最新）。返回完整版本记录。"""
    n = ver if ver is not None else latest_version(ws)
    if not n:
        raise BrainstormError("还没有构思稿")
    data = read_json(draft_dir(ws) / f"v{n:03d}.json", default=None)
    if not isinstance(data, dict) or "draft" not in data:
        raise BrainstormError(f"构思稿 v{n:03d} 不存在")
    return data


def save_version(ws: BrainstormWorkspace, draft: Any, *, kind: str, reason: str,
                 seed: Optional[Dict[str, str]] = None) -> int:
    """校验后写入新版本，返回版本号。不合法则不写任何文件。seed 缺省时沿用上一版的。"""
    if kind not in KINDS:
        raise BrainstormError(f"kind 必须是 {list(KINDS)} 之一")
    clean = validate_draft(draft)
    if seed is None:
        prev = latest_version(ws)
        seed = load(ws, prev)["seed"] if prev else {"idea": "", "constraints": ""}
    if kind == "seed" and not _text(seed.get("idea")):
        raise BrainstormError("起稿需要一句话想法")
    n = latest_version(ws) + 1
    path = draft_dir(ws) / f"v{n:03d}.json"
    if path.exists():
        raise BrainstormError(f"版本文件已存在：v{n:03d}")
    draft_dir(ws).mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"ver": n, "kind": kind, "reason": (reason or "").strip(), "at": now_iso(),
                             "seed": {"idea": _text(seed.get("idea")), "constraints": _text(seed.get("constraints"))},
                             "draft": clean})
    ws.touch()
    return n


def restore(ws: BrainstormWorkspace, ver: int) -> int:
    old = load(ws, ver)
    return save_version(ws, old["draft"], kind="restore", reason=f"回退到 v{ver:03d}", seed=old["seed"])


# ─────────────────────────────── 差异 ───────────────────────────────

def changed_sections(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """两份构思稿中内容不同的部分（按 SECTIONS 顺序）。"""
    return [s for s in SECTIONS if old.get(s) != new.get(s)]


def section_text(draft: Dict[str, Any], sec: str) -> str:
    v = draft.get(sec)
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2, sort_keys=True)


def section_diff(old: Dict[str, Any], new: Dict[str, Any], sec: str) -> str:
    a, b = section_text(old, sec).splitlines(), section_text(new, sec).splitlines()
    return "\n".join(difflib.unified_diff(a, b, "旧", "新", lineterm=""))


# ─────────────────────────────── 与卡片的映射（S3 使用） ───────────────────────────────

def storyline_text(story: Dict[str, str]) -> str:
    return "\n".join(f"{STORYLINE_LABELS[k]}：{story[k]}" for k in STORYLINE_KEYS)


def to_card_modules(draft: Dict[str, Any]) -> Dict[str, Any]:
    """构思稿 → 可与卡片比较/写入的模块数据。storyline 没有对应卡片，单独以文字给出，由采用步骤处理。"""
    clean = validate_draft(draft)
    return {"premise": clean["premise"], "world": clean["world"], "cast": clean["cast"],
            "plot": clean["plot"], "open_questions": clean["open_questions"]}


# ─────────────────────────────── 阅读视图（S4 使用，只读） ───────────────────────────────

_CAST_FIELD_LABELS = {"role": "定位", "want": "想要", "need": "需要", "flaw": "缺陷", "lie": "谎言", "arc": "弧线"}
_WORLD_LABELS = {"rules": "规则", "locations": "地点", "factions": "势力", "timeline": "时间线", "taboos": "禁忌"}


def _item_line(it: Any) -> str:
    if isinstance(it, str):
        return it
    if isinstance(it, dict):
        text = _text(it.get("text")) or "（空）"
        return f"{text}（代价：{it['cost']}）" if _text(it.get("cost")) else text
    return str(it)


def draft_markdown(draft: Dict[str, Any]) -> str:
    """把构思稿渲染成阅读用的 Markdown。只读：不改动数据，不做校验（存储时已校验）。"""
    out: List[str] = []
    out.append("### 核心命题\n\n" + (_text(draft.get("premise")) or "（空）") + "\n")

    story = draft.get("storyline") or {}
    out.append("### 故事走向\n")
    for k in STORYLINE_KEYS:
        out.append(f"- **{STORYLINE_LABELS[k]}**：{_text(story.get(k)) or '（空）'}")

    world = draft.get("world") or {}
    out.append("\n### 世界观\n")
    for key, label in _WORLD_LABELS.items():
        items = world.get(key) or []
        out.append(f"**{label}**")
        if items:
            out.extend(f"- {_item_line(it)}" for it in items)
        else:
            out.append("- （无）")
        out.append("")

    out.append("### 角色\n")
    for cid, card in (draft.get("cast") or {}).items():
        card = card if isinstance(card, dict) else {}
        head = _text(card.get("name")) or cid
        parts = [f"{lab}：{_text(card.get(k))}" for k, lab in _CAST_FIELD_LABELS.items() if _text(card.get(k))]
        out.append(f"- **{head}**" + (f"（{'；'.join(parts)}）" if parts else ""))

    plot = draft.get("plot") or {}
    out.append("\n### 核心剧情\n")
    for act in plot.get("acts") or []:
        out.append(f"**{_text(act.get('title')) or '（未命名幕）'}**")
        out.extend(f"- {b}" for b in act.get("beats") or [])
        out.append("")
    out.append("**转折**")
    out.extend(f"- {t}" for t in plot.get("twists") or [])
    out.append("")
    out.append("**结局候选**")
    out.extend(f"- {e}" for e in plot.get("endings") or [])

    out.append("\n### 待定问题\n")
    for q in draft.get("open_questions") or []:
        mark = "（已关闭）" if isinstance(q, dict) and q.get("closed") else ""
        out.append(f"- {q.get('text', '') if isinstance(q, dict) else q}{mark}")
    return "\n".join(out) + "\n"
