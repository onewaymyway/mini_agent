"""visual/structures.py — 分镜改进三期（B1–B5）的纯函数：递进、概念、公式符号、插图主体、覆盖报告。

计划文档：next_doc/article_to_video_storyboard_case_harness_plan.md 第 10 节。

约定：本模块不调用模型，只从原文块（blocks.json）与镜头（scenes）中抽取或校验，
所有画面文字都取自原文，不编造。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..validators.fidelity import norm_key

MAX_STAGES = 4                 # 递进卡：每镜最多阶段数（超出则不提供递进卡，并在覆盖报告中说明）
MAX_STAGE_LABELS = 8           # 递进卡：每个阶段最多标签数
MAX_SYMBOLS = 3                # 公式符号卡：每镜最多符号数
MAX_MEANING_CHARS = 12         # 符号含义的字数上限（取自原句的短语）
DEFINITION_LIMIT = 60          # 定义卡的定义句字数上限

# 文章里常见的具象类比（插图主体的候选）。只用于「主体须出自原文」的检查与提示，不是白名单：
# 主体整体出现在原文中同样合格。
ANALOGY_WORDS = (
    "人", "大脑", "神经系统", "神经", "身体", "器官", "心脏", "血液", "电网", "电力", "交通系统", "道路",
    "城市", "大楼", "地基", "操作系统", "灵魂", "生命", "种子", "森林", "河流", "眼睛", "手",
)

_PROGRESSION_WORDS = re.compile(r"过去|以前|曾经|然后|接下来|之后|随后|最后|首先|其次|现在|未来|最终|起初|如今")
_COLON_END = re.compile(r"[：:]\s*$")
_CLAUSE_END = re.compile(r"[，,；;。！？!?：:、]")
_OP_SPLIT = re.compile(r"\s*(?:≈|×|\+|=|≠|\*|/|\(|\)|\^|\\\w+|\{|\})\s*")


# ---------- 原文文本 ----------

def _block_text(b: Dict[str, Any]) -> str:
    kind = b.get("kind")
    if kind == "list":
        return " ".join(str(x.get("text") if isinstance(x, dict) else x) for x in b.get("items") or [])
    if kind == "table":
        cells = [c for r in (b.get("rows") or []) for c in r] + list(b.get("header") or [])
        return " ".join(str(c) for c in cells)
    if kind == "code":
        return str(b.get("content") or b.get("text") or "")
    return str(b.get("plain") or b.get("text") or b.get("content") or "")


def article_text(blocks: Sequence[Dict[str, Any]]) -> str:
    """整篇文章的文字（用于判断插图主体是否出自原文）。"""
    return "\n".join(_block_text(b) for b in blocks)


def analogy_words_in(text: str) -> List[str]:
    """文章中出现过的具象类比词（按词表顺序）。"""
    key = norm_key(text)
    return [w for w in ANALOGY_WORDS if norm_key(w) in key]


def subject_grounded(subject: str, article_norm: str, words: Sequence[str]) -> bool:
    """插图主体是否出自原文：主体整体出现在原文，或主体含有原文出现过的具象类比词。"""
    s = norm_key(subject)
    if not s:
        return False
    if s in article_norm:
        return True
    return any(norm_key(w) in s for w in words)


# ---------- 递进（B2） ----------

def _stage_from_hint(b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = str(b.get("hint_text") or "").strip()
    labels = [str(x).strip() for x in b.get("items") or [] if str(x).strip()]
    if not title or not labels:
        return None
    return {"title": title, "labels": labels, "refs": [b.get("id")]}


def _progression_pair(lead: Dict[str, Any], nxt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lead_text = str(lead.get("plain") or lead.get("text") or "").strip().strip("*").strip()
    if not (lead.get("kind") == "paragraph" and len(lead_text) <= 20 and _COLON_END.search(lead_text)
            and _PROGRESSION_WORDS.search(lead_text) and nxt.get("kind") == "quote"):
        return None
    q = str(nxt.get("plain") or nxt.get("text") or "").strip().strip("*").strip()
    if not q or len(q) > 24 or "\n" in q:
        return None
    return {"title": _COLON_END.sub("", lead_text), "labels": [q], "refs": [lead.get("id"), nxt.get("id")]}


def detect_progression(refs_blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """文字形式的递进：「过去…：」> Compute /「然后：」> Model /「接下来：」> Application。

    规则（保守）：短段落（以冒号结尾、含递进词）+ 紧随的短引语（≤ 24 字）成对出现，且不少于 3 对。
    返回阶段列表（标题为引导句，标签为引语）；不满足时返回空。
    """
    pairs: List[Dict[str, Any]] = []
    seq = list(refs_blocks)
    i = 0
    while i < len(seq):
        pair = _progression_pair(seq[i], seq[i + 1]) if i + 1 < len(seq) else None
        if pair:
            pairs.append(pair)
            i += 2
            continue
        if len(pairs) >= 3:
            break
        pairs = []
        i += 1
    return pairs if len(pairs) >= 3 else []


def stage_groups(refs_blocks: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
    """一段内的递进结构。返回 (阶段列表, 不提供递进卡的原因；可用时原因为空)。

    来源一：文本代码块被分类为 stages（如「早期 Harness / 中期 Harness / 高级 Harness」各一块），按原文顺序合并；
    来源二：文字递进（见 detect_progression）。
    """
    hinted = [s for s in (_stage_from_hint(b) for b in refs_blocks
                          if b.get("kind") == "code" and b.get("hint") == "stages") if s]
    stages = hinted if len(hinted) >= 2 else detect_progression(refs_blocks)
    if len(stages) < 2:
        return [], ""
    if len(stages) > MAX_STAGES:
        return [], f"递进共 {len(stages)} 个阶段，超过每镜上限 {MAX_STAGES}，本期不生成递进卡"
    big = [s["title"] for s in stages if len(s["labels"]) > MAX_STAGE_LABELS]
    if big:
        return [], f"递进阶段「{big[0][:12]}」的标签超过 {MAX_STAGE_LABELS} 个，本期不生成递进卡"
    return stages, ""


# ---------- 概念（B5） ----------

def clip_definition(text: str, limit: int = DEFINITION_LIMIT) -> str:
    """定义句过长时截取：优先在限长内最后一个分句标点处截断，否则硬截。取自原文，不改写。"""
    t = re.sub(r"[*`]+", "", str(text or "")).strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    cut = max((m.start() for m in _CLAUSE_END.finditer(head)), default=-1)
    return (head[:cut] if cut >= 12 else head).rstrip("，,；;：:、 ")


def concepts_of(refs_blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """段落所含的概念条目（英文一级标题，及其后第一句定义）。只返回有定义的条目。"""
    out: List[Dict[str, Any]] = []
    for b in refs_blocks:
        if b.get("kind") == "heading" and b.get("concept") and str(b.get("definition") or "").strip():
            out.append({"id": b.get("id"), "term": re.sub(r"[*`]+", "", str(b.get("text") or "")).strip(),
                        "definition": clip_definition(b["definition"])})
    return out


# ---------- 公式符号（B3） ----------

def formula_terms(formula: str) -> List[str]:
    """公式中的符号（按运算符切分）。"""
    f = re.sub(r"\$+", "", str(formula or ""))
    return [t.strip() for t in _OP_SPLIT.split(f) if t.strip()]


def symbol_items(raw_symbols: Any, formula: str, texts: Sequence[str], span: str) \
        -> Tuple[List[str], List[str]]:
    """校验模型给出的公式符号映射，返回 (条目「符号 — 含义」, 错误)。

    符号必须出自公式；含义必须是本镜头句子中的原文片段（不超过 MAX_MEANING_CHARS 字）。
    """
    errs: List[str] = []
    items: List[str] = []
    if raw_symbols in (None, "", []):
        return items, errs
    if not isinstance(raw_symbols, list):
        return items, [f"镜头 {span}：formula_symbols 应为数组"]
    if len(raw_symbols) > MAX_SYMBOLS:
        errs.append(f"镜头 {span}：公式符号 {len(raw_symbols)} 个，超过每镜上限 {MAX_SYMBOLS}")
    fkey = norm_key(formula)
    joined = norm_key("".join(texts))
    for x in raw_symbols[:MAX_SYMBOLS]:
        if not isinstance(x, dict):
            errs.append(f"镜头 {span}：formula_symbols 中存在非对象条目")
            continue
        sym, meaning = str(x.get("symbol") or "").strip(), str(x.get("meaning") or "").strip()
        if not sym or not meaning:
            errs.append(f"镜头 {span}：公式符号需要 symbol 与 meaning")
            continue
        if norm_key(sym) not in fkey:
            errs.append(f"镜头 {span}：符号「{sym[:16]}」不在原文公式中")
            continue
        if len(meaning) > MAX_MEANING_CHARS:
            errs.append(f"镜头 {span}：「{sym[:16]}」的含义超过 {MAX_MEANING_CHARS} 字，请截取原句中的短语")
            continue
        if norm_key(meaning) not in joined:
            errs.append(f"镜头 {span}：「{sym[:16]}」的含义「{meaning[:12]}」不是本镜头句子中的原文，请取自原句")
            continue
        items.append(f"{sym} — {meaning}")
    return items, errs


# ---------- 覆盖报告（B1 / C2 雏形） ----------

_TYPES_OF = {"chain": ("diagram", "stack"), "table": ("table",), "list": ("list", "timeline"),
             "math": ("formula",), "stages": ("stages",), "concept": ("definition",)}
_LABEL = {"chain": "流程或堆叠层级", "table": "表格", "list": "列表", "math": "公式",
          "stages": "递进", "concept": "概念定义"}


def structure_blocks(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """段落里需要被镜头承载的结构块：[{family, id, total}]。"""
    out: List[Dict[str, Any]] = []
    if ctx.get("chain"):
        out.append({"family": "chain", "id": ctx["chain"].get("id"), "total": len(ctx["chain"]["nodes"])})
    if ctx.get("table"):
        out.append({"family": "table", "id": ctx["table"].get("id"), "total": len(ctx["table"].get("rows") or [])})
    if ctx.get("lst"):
        out.append({"family": "list", "id": ctx["lst"].get("id"), "total": len(ctx["lst"]["items"])})
    if ctx.get("math"):
        out.append({"family": "math", "id": ctx["math"].get("id"), "total": 1})
    if ctx.get("stages"):
        out.append({"family": "stages", "id": (ctx["stages"][0].get("refs") or [None])[0],
                    "total": len(ctx["stages"])})
    for c in ctx.get("concepts") or []:
        out.append({"family": "concept", "id": c["id"], "total": 1, "term": c["term"]})
    return out


def _shown(scenes: Sequence[Dict[str, Any]], fam: str, total: int) -> set:
    shown: set = set()
    for s in scenes:
        if s.get("type") not in _TYPES_OF[fam]:
            continue
        if fam in ("math", "concept"):
            shown.add(0)
        elif s.get("part"):
            shown.update(range(s["part"][0] - 1, s["part"][1]))
        else:
            shown.update(range(len(s.get("items") or [])))
    return shown


def coverage_report(scenes: Sequence[Dict[str, Any]], script: Dict[str, Any],
                    ctxs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """结构块 → 承载它的镜头。返回 {"blocks": [...], "uncovered": [...说明...], "notes": [...]}。

    notes 是未能生成（因超限等）的说明，不算未承载。
    """
    entries: List[Dict[str, Any]] = []
    uncovered: List[str] = []
    notes: List[str] = []
    for b in script["beats"]:
        bid = b["id"]
        ctx = ctxs[bid]
        own = [s for s in scenes if s.get("beat") == bid]
        if ctx.get("stages_note"):
            notes.append(f"{bid}：{ctx['stages_note']}")
        for sb in structure_blocks(ctx):
            fam, total = sb["family"], sb["total"]
            carriers = [s["id"] for s in own if s.get("type") in _TYPES_OF[fam]]
            shown = _shown(own, fam, total)
            missing = sorted(set(range(total)) - shown)
            entry = {"beat": bid, "family": fam, "block": sb["id"], "total": total,
                     "shots": carriers, "shown": len(shown & set(range(total)))}
            if sb.get("term"):
                entry["term"] = sb["term"]
            entries.append(entry)
            name = _LABEL[fam] + (f"「{sb['term'][:16]}」" if sb.get("term") else "")
            if not carriers:
                uncovered.append(f"{bid}：{name}未被任何镜头承载")
            elif missing:
                uncovered.append(f"{bid}：{name}共 {total} 项，只承载了 {total - len(missing)} 项")
    return {"blocks": entries, "uncovered": uncovered, "notes": notes}
