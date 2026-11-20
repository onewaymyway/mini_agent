"""visual/director_llm.py — S4 分镜（模型版，第一期结构改造）。

依据：next_doc/article_to_video_storyboard_model_redesign_plan.md 第 4 节。

两步调用：
  1. 章级策划（1 次）：每段的视觉角色与需要被看见的要点。只做判断，不写镜头。
  2. 分窗落镜（每窗 1 次）：窗口按段落边界切分，约 2500 字以内；输出镜头列表（句子范围、画面类型、内容）。
     窗口之间衔接：第一镜紧接上一窗最后一镜。

局部修复：校验失败只重做出错的窗口（最多 MAX_ATTEMPTS 次）；已通过的窗口保留，
下次运行若输入未变则直接复用（缓存：parts/<章>/director_cache/）。

边界（沿用“不编造”原则）：
  * 结构内容（流程节点、表格、原文列表、公式）取自原文，模型只选择句子范围与画面类型；
  * 数字与英文词必须出现在本段口播或原文结构中；
  * 流程节点与图表标签必须出现在镜头自己的句子中（画面上的词，口播里要说到）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import cancel, joblog
from ..planning.errors import PlanError
from ..planning.jsonio import parse_json_object
from ..project_store import read_json, write_json_atomic
from ..validators.visual import validate_storyboard
from .director import (MAX_DIAGRAM_NODES, MAX_LIST_ITEMS, MAX_TABLE_COLS, MAX_TABLE_ROWS, TEXT_LIMIT,
                       _emphasis, _first, _refs_blocks, _text_of, _year_like, plain)
from .sharing import allocate_shared_images, count_groups
from .prompting import MAX_COMPOSITION as MAX_COMPOSITION_CHARS
from .prompting import MAX_SCENE as MAX_SCENE_CHARS
from .prompting import check_illustration
from .shots import (MAX_POINTS, MAX_SHOT_ITEMS, POINT_LIMIT, ROLES, SHOT_KINDS, beat_of, mention, plan_windows,
                    scene_reveal, sentence_id, sentence_index)
from .states import RECAP_CAP
from .structures import (analogy_words_in, article_text, concepts_of, contrast_of, coverage_report, recap_of,
                         stage_groups, subject_grounded, symbol_items)
from ..validators.fidelity import norm_key

MAX_ATTEMPTS = 3
LLM_PROMPT_VERSION = "s4-llm-v7"
SYSTEM = ("你是讲解视频的视觉导演。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
          "只使用给定口播与原文结构中已有的词，不得添加原文没有的数字、年份或英文词。")
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9\-\.]*")
_DIGIT = re.compile(r"\d")
MAX_SUBJECT_CHARS = 24
CHART_HEADER = ["项目", "数值"]
MAX_CHART_ITEMS = 3
MAX_FORMULA_CHARS = 120
MAX_SYMBOLS_PROMPT = 3

ROLE_HINT = {
    "argument": "关键论点", "data": "数据与对比", "structure": "结构与流程", "formula": "公式",
    "quote": "金句", "narrative": "叙述过渡", "image": "比喻与场景", "hold": "沿用上一画面",
}


# ---------- 段落上下文（原文结构与允许的画面类型） ----------

def beat_context(beat: Dict[str, Any], blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    refs_blocks = _refs_blocks(beat.get("source_refs") or [], blocks)
    chain = _first(refs_blocks, lambda b: b["kind"] == "code" and b.get("shape") == "chain")
    table = _first(refs_blocks, lambda b: b["kind"] == "table" and b.get("rows"))
    math_b = _first(refs_blocks, lambda b: b["kind"] == "math" and b.get("content"))
    lst = _first(refs_blocks, lambda b: b["kind"] == "list" and b.get("items"))
    # 文本代码块的自动分类结果（第二期）：公式式表达视同公式，标签视同列表
    if math_b is None:
        hinted = _first(refs_blocks, lambda b: b["kind"] == "code" and b.get("hint") == "formula")
        if hinted:
            math_b = {"kind": "math", "content": hinted["hint_text"], "id": hinted["id"]}
    if lst is None:
        labels = _first(refs_blocks, lambda b: b["kind"] == "code" and b.get("hint") == "labels")
        if labels:
            lst = {"kind": "list", "items": [{"text": x} for x in labels["items"]], "id": labels["id"]}
    stages, stages_note = stage_groups(refs_blocks)             # 三期 B2：递进
    concepts = concepts_of(refs_blocks)                          # 三期 B5：概念定义
    contrast = contrast_of(refs_blocks)                          # 四期 B6：对比卡
    recap = recap_of(refs_blocks)                                # 四期 B7：回顾卡
    allowed = ["keypoint", "illustration", "hold"]
    notes: List[str] = []
    if any(s.get("quote") for s in beat["sentences"]):
        allowed.append("quote")
    if chain and len(chain.get("nodes") or []) >= 2:
        nodes_all = [str(n) for n in chain["nodes"]]
        if chain.get("direction") == "up":
            allowed.append("stack")
            notes.append(f"原文堆叠层级（自上而下书写，最后一项为底层，用 stack，不要用 flow）："
                         f"{' / '.join(nodes_all)}（共 {len(nodes_all)} 层）")
        else:
            allowed.append("flow")
            notes.append(f"原文流程：{' → '.join(nodes_all)}（共 {len(nodes_all)} 个节点"
                         + ("，首尾相接，为回路" if chain.get("loop") else "") + "）")
    if table:
        allowed.append("table")
        notes.append(f"原文表格（共 {len(table.get('rows') or [])} 行）：" + " | ".join(plain(h) for h in table.get("header") or []))
    if math_b:
        allowed.append("formula")
        notes.append("原文公式：" + str(math_b["content"]).replace("\n", " "))
    if lst:
        items = [_text_of(x) for x in lst["items"]]
        allowed.append("list")
        notes.append(f"原文列表（共 {len(items)} 项）：" + "；".join(items))
    if stages:
        allowed.append("stages")
        notes.append("原文递进（按顺序，用 stages）：" + " → ".join(
            f"{s['title']}（{'、'.join(s['labels'])}）" for s in stages))
    if concepts:
        allowed.append("definition")
        notes.append("概念术语（首次出现，可用 definition 生成定义卡）：" + "；".join(
            f"{c['term']}：{c['definition']}" for c in concepts))
    if contrast:
        allowed.append("contrast")
        head = (f"左「{contrast['left_title']}」" if contrast["left_title"] else "左") + \
               (f"、右「{contrast['right_title']}」" if contrast["right_title"] else "、右")
        notes.append(f"原文对照（用 contrast 对比卡，{head}）：左 " + "；".join(contrast["left"])
                     + " ｜ 右 " + "；".join(contrast["right"]))
    if recap:
        allowed.append("recap")
        notes.append(f"原文多行总结（共 {len(recap['lines'])} 条，用 recap 回顾卡，每镜最多 {RECAP_CAP} 条，"
                     "用 part 分页展示全部条目）：" + "；".join(recap["lines"]))
    if any(_DIGIT.search(s["text"]) for s in beat["sentences"]):
        allowed.append("chart")
    return {"allowed": allowed, "chain": chain, "table": table, "math": math_b, "lst": lst,
            "stages": stages, "stages_note": stages_note, "concepts": concepts, "grounding": None,
            "contrast": contrast, "recap": recap,
            "notes": notes, "refs": list(beat.get("source_refs") or [])}


def _grounding(ctxs: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for c in ctxs.values():
        return c.get("grounding")
    return None


def _structure_kinds(ctxs: Dict[str, Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for c in ctxs.values():
        for key, name in (("chain", "chain"), ("table", "table"), ("math", "math"), ("lst", "list")):
            if c[key] and name not in out:
                out.append(name)
    return out


def _tokens_missing(text: str, source: str) -> List[str]:
    return [t for t in _TOKEN.findall(text) if t not in source]


def _source_of(beat: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """可追溯来源：本段口播 + 本段原文公式（公式符号与变量名来自原文，不算编造）。"""
    src = "".join(s["text"] for s in beat["sentences"])
    if ctx.get("math"):
        src += " " + str(ctx["math"].get("content") or "")
    return src


def _groups(n_illustrations: int, shots: int) -> int:
    return math.ceil(n_illustrations / max(1, shots))


def beat_chars_of(beat: Dict[str, Any]) -> int:
    return sum(len(s["text"]) for s in beat["sentences"])


# ---------- 缓存 ----------

def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _cache_get(cdir: Optional[Path], name: str, force: bool) -> Optional[Dict[str, Any]]:
    if cdir is None or force:
        return None
    p = cdir / name
    return read_json(p) if p.is_file() else None


def _cache_put(cdir: Optional[Path], name: str, obj: Dict[str, Any]) -> None:
    if cdir is None:
        return
    cdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cdir / name, obj)


# ---------- 第一步：章级策划 ----------

def _plan_payload(script: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]], allowance: int, shots: int) -> Dict[str, Any]:
    return {"v": LLM_PROMPT_VERSION, "stage": "plan", "allowance": allowance, "shots": shots,
            "beats": [{"id": b["id"], "s": [[s["text"], bool(s.get("quote"))] for s in b["sentences"]],
                       "notes": ctxs[b["id"]]["notes"]} for b in script["beats"]]}


def build_plan_prompt(part: Dict[str, Any], script: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]], *,
                      allowance: int, shots: int, issues: List[str]) -> str:
    lines = [
        "【章级策划】",
        f"PART_ID={script['part']}",
        f"章标题：{part['title']}",
        f"图片额度：本章最多生成 {allowance} 张图（每图可用于 {shots} 个镜头）。",
        "任务：为每一段判断视觉角色，并列出需要被看见的要点。这一步不写镜头，不写提示词。",
        "视觉角色只能是：" + "、".join(ROLES) + "。含义：" + "；".join(f"{k} {v}" for k, v in ROLE_HINT.items()) + "。",
        f"每段 1–{MAX_POINTS} 条要点，每条不超过 {POINT_LIMIT} 字，只用本段口播或原文结构中已有的词。",
        "",
        "各段内容（按顺序）：",
    ]
    for beat in script["beats"]:
        lines.append(f"[{beat['id']}]")
        for i, s in enumerate(beat["sentences"]):
            tag = "（金句）" if s.get("quote") else ""
            lines.append(f"  [{sentence_id(beat['id'], i)}] {s['text']}{tag}")
        for n in ctxs[beat["id"]]["notes"]:
            lines.append(f"  {n}")
    schema = {"beats": [{"beat": "P01.b01", "role": "argument", "points": ["要点"]}]}
    lines += ["", "输出 JSON（每段恰好一条，按段落顺序）：", json.dumps(schema, ensure_ascii=False, indent=2)]
    if issues:
        lines += ["", "【上次输出的问题，必须修正后重新输出完整 JSON】"] + [f"- {x}" for x in issues]
    return "\n".join(lines)


def parse_plan(obj: Any, script: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]]) \
        -> Tuple[Dict[str, Dict[str, Any]], List[str], List[str]]:
    """返回 (按段的策划, 结构性错误（需重试）, 被剔除的要点说明（仅记录）)。"""
    raw = obj.get("beats") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return {}, ["缺少 beats 数组"], []
    beat_by = {b["id"]: b for b in script["beats"]}
    plan: Dict[str, Dict[str, Any]] = {}
    errs: List[str] = []
    drops: List[str] = []
    for item in raw:
        if not isinstance(item, dict):
            errs.append("beats 中存在非对象条目")
            continue
        bid = str(item.get("beat") or "")
        if bid not in beat_by:
            errs.append(f"出现了不存在的段落 {bid or '（空）'}")
            continue
        if bid in plan:
            errs.append(f"{bid} 重复出现")
            continue
        role = str(item.get("role") or "")
        if role not in ROLES:
            errs.append(f"{bid}：视觉角色 {role!r} 不合法")
            continue
        pts = [str(p).strip() for p in item.get("points") or [] if str(p).strip()]
        if len(pts) > MAX_POINTS:
            errs.append(f"{bid}：要点 {len(pts)} 条，超过 {MAX_POINTS} 条")
        if any(len(p) > POINT_LIMIT for p in pts):
            errs.append(f"{bid}：有要点超过 {POINT_LIMIT} 字")
        src = _source_of(beat_by[bid], ctxs[bid])
        kept: List[str] = []
        for p in pts[:MAX_POINTS]:
            missing = _tokens_missing(p, src)
            if missing:
                drops.append(f"{bid}：要点「{p[:20]}」含原文没有的词「{missing[0]}」，已剔除")
                continue
            kept.append(p[:POINT_LIMIT])
        plan[bid] = {"role": role, "points": kept}
    for bid in beat_by:
        if bid not in plan:
            errs.append(f"缺少段落 {bid} 的视觉角色")
    return plan, errs, drops


def _plan_stage(script: Dict[str, Any], part: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]], *,
                allowance: int, shots: int, llm, cdir: Optional[Path], force: bool) \
        -> Tuple[Dict[str, Dict[str, Any]], bool, int]:
    """返回 (策划, 是否来自缓存, 模型调用次数)。"""
    pid = script["part"]
    name = f"plan-{_sha(_plan_payload(script, ctxs, allowance, shots))}.json"
    hit = _cache_get(cdir, name, force)
    if hit is not None:
        joblog.bound_log(f"{pid}：章级策划输入未变，复用（不调用模型）")
        return hit["plan"], True, 0
    issues: List[str] = []
    raw = ""
    for attempt in range(MAX_ATTEMPTS):
        cancel.check()
        prompt = build_plan_prompt(part, script, ctxs, allowance=allowance, shots=shots, issues=issues)
        n = attempt + 1
        why = "" if n == 1 else f"（上次问题 {len(issues)} 条，已附在提示词末尾）"
        joblog.bound_log(f"{pid}：章级策划第 {n}/{MAX_ATTEMPTS} 次（提示词 {len(prompt)} 字）{why}")
        t0 = time.monotonic()
        raw = llm.ask(prompt, system=SYSTEM)
        cancel.check()
        joblog.bound_log(f"{pid}：策划返回 {len(raw)} 字，用时 {time.monotonic() - t0:.1f} 秒")
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            issues = [f"JSON 解析失败：{exc}"]
            joblog.bound_log(f"{pid}：策划输出不是有效 JSON（{exc}），将重试", level="warn")
            continue
        plan, errs, drops = parse_plan(obj, script, ctxs)
        for d in drops[:12]:
            joblog.bound_log(f"  · {d}", level="warn")
        if errs:
            issues = errs[:12]
            joblog.bound_log(f"{pid}：策划校验发现 {len(errs)} 处问题，将重试：", level="warn")
            for e in errs[:12]:
                joblog.bound_log(f"  · {e}", level="warn")
            continue
        counts: Dict[str, int] = {}
        for v in plan.values():
            counts[v["role"]] = counts.get(v["role"], 0) + 1
        n_pts = sum(len(v["points"]) for v in plan.values())
        joblog.bound_log(f"{pid}：章级策划通过：角色 " + "、".join(f"{k}×{v}" for k, v in counts.items())
                         + f"；要点 {n_pts} 条", level="ok")
        _cache_put(cdir, name, {"schema": 1, "plan": plan})
        return plan, False, attempt + 1
    joblog.bound_log(f"{pid}：章级策划 {MAX_ATTEMPTS} 次仍不合格，本章未写入结果", level="error")
    raise PlanError(f"{pid} 章级策划 {MAX_ATTEMPTS} 次仍不合格：" + "；".join(issues[:6]), raw=raw)


# ---------- 第二步：分窗落镜 ----------

def _prev_desc(prev: Optional[Dict[str, Any]]) -> str:
    if not prev:
        return "无（本窗口是章首）"
    return f"段 {prev['beat']}，句 {prev['sentences'][0]}–{prev['sentences'][1]}，画面类型 {prev['kind']}"


def build_window_prompt(part: Dict[str, Any], script: Dict[str, Any], wbeats: Sequence[Dict[str, Any]],
                        ctxs: Dict[str, Dict[str, Any]], plan: Dict[str, Dict[str, Any]], *, wid: str,
                        allowance: int, shots: int, ill_used: int, prev: Optional[Dict[str, Any]],
                        issues: List[str]) -> str:
    remaining = max(0, allowance - _groups(ill_used, shots))
    lines = [
        "【分窗落镜】",
        f"PART_ID={script['part']}",
        f"WINDOW={wid}",
        f"章标题：{part['title']}",
        f"本窗口覆盖段落：{wbeats[0]['id']} 至 {wbeats[-1]['id']}（共 {len(wbeats)} 段）",
        f"图片额度余量：本章还可新增 {remaining} 张图（每图可用于 {shots} 个镜头）。",
        f"上一窗口最后一镜：{_prev_desc(prev)}",
        "",
        "各段内容（按顺序）：",
    ]
    for beat in wbeats:
        ctx = ctxs[beat["id"]]
        p = plan.get(beat["id"], {"role": "narrative", "points": []})
        pts = "；".join(p["points"]) or "无"
        lines.append(f"[{beat['id']}] 视觉角色 {p['role']}；要点：{pts}")
        for i, s in enumerate(beat["sentences"]):
            tag = "（金句）" if s.get("quote") else ""
            sid = sentence_id(beat["id"], i)
            t = (ctx.get("timing") or {}).get(sid)
            when = f"（配音 {t[0]:.1f}–{t[1]:.1f} 秒，时长 {t[1] - t[0]:.1f} 秒）" if t else ""
            lines.append(f"  [{sid}] {s['text']}{tag}{when}")
        for n in ctx["notes"]:
            lines.append(f"  {n}")
        lines.append(f"  可选画面类型：{'、'.join(ctx['allowed'])}")
    if any(ctxs[b["id"]].get("timing") for b in wbeats):
        lines += [
            "",
            "时间规则（句子括号内是真实配音时间）：",
            "  T1. 画面必须与口播同步：一个镜头只使用它所覆盖句子里讲到的内容，要点的顺序与口播的讲述顺序一致。",
            "  T2. 要点出现在它被讲到的那一句；不要把后面才讲到的要点提前放在前一句的镜头里。",
            "  T3. 时长很短的句子（不超过 3 秒）只放 1 到 2 个要点；时长较长的句子可放更多要点，但不超过上限。",
        ]
    schema = {"shots": [{"sentences": ["P01.b01.s01", "P01.b01.s03"], "kind": "keypoint",
                         "items": ["短条目"], "emphasis": ["句中的词"]}]}
    lines += [
        "",
        "输出 JSON（按句子顺序的镜头列表）：",
        json.dumps(schema, ensure_ascii=False, indent=2),
        "规则：",
        "1. 镜头按句子顺序排列，合起来恰好覆盖本窗口的全部句子：不遗漏、不重叠；每个镜头只能包含同一段的句子。",
        "2. sentences 只写该镜头的首句与末句编号（只有一句时两个编号相同）。",
        "3. kind 只能从该段的可选画面类型中选择。hold 表示沿用上一镜，只能用在同段内、且前面已有镜头。",
        "4. keypoint / illustration 的 items 不超过 3 条，每条不超过 60 字；chart 用 data（2–3 项，每项 label 与 value）。",
        "5. flow（流程）的节点与 chart（图表）的标签必须出现在本镜头的句子中，不够就扩大句子范围。",
        "6. 数字与英文词只用本段口播或原文结构中的词；flow、table、list 的内容由原文决定，只需选择句子范围与类型。",
        "7. illustration 只用于比喻、场景与抽象概念的具体化；数据、流程、定义应使用 chart、flow 或 keypoint。"
        "illustration 必须给出 image_subject（具体的物体或场景，不得是抽象词如“效率”“趋势”），"
        "可选 image_scene（所处环境）与 image_composition（画面安排，如“远景，主体靠左”）。"
        "这些字段都不得要求出现文字、标题、字幕或标志。",
        "8. formula 的 formula_explanation 可以使用原文公式中的符号与变量名。",
        "9. flow 表示先后或因果（→ 或 ↓）；stack 表示原文用 ↑ 书写的堆叠层级（自上而下书写，最后一项为底层），不得用 flow 表示。",
        "10. flow、stack、table、list 可用 part: [起, 止] 指定本镜展示的项目序号（从 1 开始，闭区间）。"
        "超过每镜上限（流程与层级 6 项、表格 6 行、列表 5 项）时，用多个连续镜头（同一类型，part 依次覆盖剩余项目）完整展示，"
        "不得省略任何项目；未写 part 时只展示前 N 项。",
        "11. stages（递进卡）与 definition（定义卡）的内容由原文决定，只需选择句子范围；"
        "definition 有多个概念时用 term 指定。有原文递进或概念术语的段落应使用它们。",
        "12. formula 可用 formula_symbols: [{\"symbol\": \"公式中的符号\", \"meaning\": \"本镜头句子中的原文短语\"}]"
        f"（不超过 {MAX_SYMBOLS_PROMPT} 个，meaning 必须逐字取自本镜头的句子且不超过 12 字，symbol 必须出自原文公式），"
        "它优先于 formula_explanation。",
        "13. recap（回顾卡）：原文末尾的多行总结，内容由原文决定；每镜最多 3 条，用 part 分页展示全部条目（同列表）。",
        "14. contrast（对比卡）：原文的「不是…而是…」对照，左右两栏内容由原文决定，无需 items，只需选择句子范围。",
    ]
    g = _grounding(ctxs)
    if g:
        lines.append("15. illustration 的 image_subject 必须出自原文：用原文已出现的具象类比或物体（"
                     + "、".join(g["words"]) + "），抽象概念（如 Harness、Identity）不作主体，改用 definition、formula 或 keypoint。")
    if issues:
        lines += ["", "【上次输出的问题，必须修正后重新输出完整 JSON】"] + [f"- {x}" for x in issues]
    return "\n".join(lines)


def _window_payload(wbeats, ctxs, plan, *, allowance, shots, ill_used, prev) -> Dict[str, Any]:
    return {"v": LLM_PROMPT_VERSION, "stage": "window", "allowance": allowance, "shots": shots,
            "ill_used": ill_used, "prev": _prev_desc(prev) if prev else None,
            "ground": (_grounding(ctxs) or {}).get("words"),
            "beats": [{"id": b["id"], "s": [[s["text"], bool(s.get("quote"))] for s in b["sentences"]],
                       "t": [list(ctxs[b["id"]].get("timing", {}).get(sentence_id(b["id"], i), ()))
                             for i in range(len(b["sentences"]))],
                       "notes": ctxs[b["id"]]["notes"], "allowed": ctxs[b["id"]]["allowed"],
                       "plan": plan.get(b["id"])} for b in wbeats]}


def _base_scene(beat_id: str, refs: List[str]) -> Dict[str, Any]:
    return {"id": "", "beat": beat_id, "type": "keypoint", "kind": "keypoint", "sentences": [], "refs": list(refs),
            "items": [], "emphasis": [], "header": [], "rows": [], "loop": False, "formula": "",
            "image": None, "reveal": []}


def _clean_items(raw_items: Any) -> List[str]:
    if not isinstance(raw_items, list):
        return []
    return [str(x).strip() for x in raw_items if str(x).strip()]


def _item_errors(items: List[str], source: str, span: str) -> List[str]:
    errs: List[str] = []
    for it in items:
        if len(it) > TEXT_LIMIT:
            errs.append(f"镜头 {span}：条目 {len(it)} 字，超过 {TEXT_LIMIT} 字")
        missing = _tokens_missing(it, source)
        if missing:
            errs.append(f"镜头 {span}：条目「{it[:20]}」含原文没有的词「{missing[0]}」，请只用本段口播中的词")
    return errs


def _build_shot(cut: Dict[str, Any], beat: Dict[str, Any], ctx: Dict[str, Any]) \
        -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """把一条已切分的镜头（段内 [a, b]）换算为场景。返回 (场景或 None, 错误)。"""
    bid = beat["id"]
    sents = beat["sentences"]
    raw = cut["raw"]
    kind = cut["kind"]
    a, b = sentence_index(cut["first"]), sentence_index(cut["last"])
    texts = [sents[i]["text"] for i in range(a, b + 1)]
    source = _source_of(beat, ctx)
    span = f"{cut['first']}–{cut['last']}"
    errs: List[str] = []
    fallback_from: Optional[str] = None
    if kind == "hold":                       # 未能并入上一镜（段首）：按卡片处理
        fallback_from, kind = "hold", "keypoint"
    elif kind not in ctx["allowed"]:
        fallback_from, kind = kind, "keypoint"
    if kind == "quote" and not any(sents[i].get("quote") for i in range(a, b + 1)):
        fallback_from, kind = "quote", "keypoint"

    sc = _base_scene(bid, ctx["refs"])
    emph_src = [str(e) for e in raw.get("emphasis") or [] if str(e) and str(e) in source]
    if kind == "quote":
        q = next(i for i in range(a, b + 1) if sents[i].get("quote"))
        sc.update(type="quote", items=[sents[q]["text"][:TEXT_LIMIT]])
    elif kind in ("flow", "stack"):
        chain = ctx["chain"]
        total = len(chain["nodes"])
        a0, a1 = _page(raw, total, MAX_DIAGRAM_NODES, span, errs)
        nodes = [plain(n)[:24] for n in chain["nodes"][a0:a1]]
        missing = [n for n in nodes if not mention(n, texts)]
        if missing:
            errs.append(f"镜头 {span}：流程节点「{missing[0]}」未在本镜头的句子中出现，请扩大句子范围或换用其它画面")
        whole = a0 == 0 and a1 == total
        sc.update(type="diagram" if kind == "flow" else "stack", items=nodes,
                  loop=bool(chain.get("loop")) and whole, part=[a0 + 1, a1])
    elif kind == "stages":
        stages = [{"title": plain(s["title"])[:30], "labels": [plain(x)[:30] for x in s["labels"]]}
                  for s in ctx["stages"]]
        sc.update(type="stages", stages=stages, items=[s["title"] for s in stages])
    elif kind == "definition":
        concepts = ctx["concepts"]
        want = str(raw.get("term") or "").strip()
        pick = next((c for c in concepts if want and norm_key(want) == norm_key(c["term"])), None)
        if pick is None:
            if len(concepts) > 1:
                errs.append(f"镜头 {span}：本段有多个概念（{'、'.join(c['term'] for c in concepts)}），请用 term 指定其一")
            pick = concepts[0]
        sc.update(type="definition", term=pick["term"][:40], items=[pick["definition"]])
    elif kind == "recap":                                              # 四期 B7：回顾卡（分页与列表相同）
        lines = ctx["recap"]["lines"]
        i0, i1 = _page(raw, len(lines), RECAP_CAP, span, errs)
        sc.update(type="recap", items=[x[:TEXT_LIMIT] for x in lines[i0:i1]], part=[i0 + 1, i1])
    elif kind == "contrast":                                           # 四期 B6：对比卡（单状态，两侧取自原文）
        c = ctx["contrast"]
        sc.update(type="contrast", left=list(c["left"]), right=list(c["right"]),
                  left_title=c["left_title"], right_title=c["right_title"], items=[])
    elif kind == "table":
        t = ctx["table"]
        header = [plain(h)[:30] for h in (t.get("header") or [])][:MAX_TABLE_COLS]
        total = len(t["rows"])
        r0, r1 = _page(raw, total, MAX_TABLE_ROWS, span, errs)
        rows = [[plain(c)[:30] for c in (r or [])][:MAX_TABLE_COLS] for r in t["rows"][r0:r1]]
        sc.update(type="table", header=header, rows=rows, items=["、".join(r) for r in rows], part=[r0 + 1, r1])
    elif kind == "list":
        total = len(ctx["lst"]["items"])
        i0, i1 = _page(raw, total, MAX_LIST_ITEMS, span, errs)
        items = [_text_of(x)[:TEXT_LIMIT] for x in ctx["lst"]["items"][i0:i1]]
        sc.update(type="timeline" if _year_like(items) else "list", items=items, part=[i0 + 1, i1])
    elif kind == "chart":
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        pairs = [(str(x.get("label") or "").strip(), str(x.get("value") or "").strip())
                 for x in data if isinstance(x, dict)]
        pairs = [(lab, val) for lab, val in pairs if lab and val]
        if not (2 <= len(pairs) <= MAX_CHART_ITEMS):
            errs.append(f"镜头 {span}：图表需要 2–{MAX_CHART_ITEMS} 项数据，当前 {len(pairs)} 项")
        for lab, _val in pairs:
            if not mention(lab, texts):
                errs.append(f"镜头 {span}：图表标签「{lab[:12]}」未在本镜头的句子中出现")
                break
        for _lab, val in pairs:
            missing = _tokens_missing(val, source)
            if missing:
                errs.append(f"镜头 {span}：数值「{val}」含原文没有的数字或词「{missing[0]}」")
                break
        sc.update(type="table", header=list(CHART_HEADER), rows=[[lab, val] for lab, val in pairs],
                  items=[f"{lab}：{val}" for lab, val in pairs])
    elif kind == "formula":
        if not ctx.get("math"):
            errs.append(f"镜头 {span}：本段没有原文公式")
        formula = ""
        if ctx.get("math"):
            formula = re.sub(r"\$+", "", str(ctx["math"]["content"])).replace("\n", " ").strip()[:MAX_FORMULA_CHARS]
        sym_items, sym_errs = symbol_items(raw.get("formula_symbols"), formula, texts, span)
        errs += sym_errs
        if sym_items or raw.get("formula_symbols"):             # 三期 B3：符号卡优先于自由解释
            items = sym_items
        else:
            items = _clean_items(raw.get("formula_explanation") or [])[:MAX_SHOT_ITEMS]
            errs += _item_errors(items, source, span)
        sc.update(type="formula", formula=formula, items=items)
    else:  # keypoint / illustration
        items = _clean_items(raw.get("items") or [])
        if len(items) > MAX_SHOT_ITEMS:
            errs.append(f"镜头 {span}：条目 {len(items)} 个，超过每镜上限 {MAX_SHOT_ITEMS}")
            items = items[:MAX_SHOT_ITEMS]
        if not items and fallback_from:
            items = [texts[0][:TEXT_LIMIT]]
        if not items and kind == "keypoint":
            errs.append(f"镜头 {span}：keypoint 缺少条目")
        errs += _item_errors(items, source, span)
        sc.update(type=kind, items=items)
        if kind == "illustration":
            subject = str(raw.get("image_subject") or "").strip()[:MAX_SUBJECT_CHARS]
            emph = _emphasis(sents[a:b + 1])
            image = {"subject": subject or (emph[0] if emph else (items[0][:MAX_SUBJECT_CHARS] if items else bid)),
                     "scene": str(raw.get("image_scene") or "").strip()[:MAX_SCENE_CHARS],
                     "composition": str(raw.get("image_composition") or "").strip()[:MAX_COMPOSITION_CHARS],
                     "focus_x": 0.5, "version": 0, "file": None}
            errs += [f"镜头 {span}：{e}" for e in check_illustration(image)]   # 抽象主体、禁用词：整窗重做
            g = ctx.get("grounding")                                           # 三期 B4：主体须出自原文（默认关闭）
            if g and not subject_grounded(image["subject"], g["article_norm"], g["words"]):
                errs.append(f"镜头 {span}：插图主体「{image['subject'][:12]}」未出现在原文中，"
                            f"请改用原文已有的具象类比或物体（如 {'、'.join(g['words'][:6])}）")
            sc["image"] = image
    sc["kind"] = kind
    sc["sentences"] = [sentence_id(bid, a), sentence_id(bid, b)]
    sc["emphasis"] = list(dict.fromkeys(emph_src))[:8] or _emphasis(sents[a:b + 1])
    sc["reveal"] = scene_reveal(sc, texts, start=a)
    if fallback_from:
        sc["fallback_from"] = fallback_from
    return (sc if not errs else None), errs


_FAMILY_KINDS = {"chain": ("flow", "stack"), "table": ("table",), "list": ("list",), "recap": ("recap",)}
_PAGED_TYPES = ("diagram", "stack", "table", "list", "timeline", "recap")


def _page(raw: Dict[str, Any], total: int, cap: int, span: str, errs: List[str]) -> Tuple[int, int]:
    """镜头展示的结构项范围（从 0 起、右开）。raw.part 为 [起, 止]（从 1 起、闭区间）；缺省取前 cap 项。"""
    part = raw.get("part")
    if part is None:
        return 0, min(total, cap)
    if not (isinstance(part, list) and len(part) == 2 and all(isinstance(x, int) for x in part)):
        errs.append(f"镜头 {span}：part 应为 [起, 止] 两个整数")
        return 0, min(total, cap)
    a, b = part
    if not (1 <= a <= b <= total) or b - a + 1 > cap:
        errs.append(f"镜头 {span}：part {part} 超出范围（共 {total} 项，每镜最多 {cap} 项）")
        return 0, min(total, cap)
    return a - 1, b


def _structure_totals(ctx: Dict[str, Any]) -> Dict[str, Tuple[int, int, str]]:
    out: Dict[str, Tuple[int, int, str]] = {}
    if ctx.get("chain"):
        out["chain"] = (len(ctx["chain"]["nodes"]), MAX_DIAGRAM_NODES, "流程或堆叠层级")
    if ctx.get("table"):
        out["table"] = (len(ctx["table"].get("rows") or []), MAX_TABLE_ROWS, "表格行")
    if ctx.get("lst"):
        out["list"] = (len(ctx["lst"]["items"]), MAX_LIST_ITEMS, "列表项")
    if ctx.get("recap"):
        out["recap"] = (len(ctx["recap"]["lines"]), RECAP_CAP, "回顾条目")
    return out


def _coverage_errors(cuts: List[Dict[str, Any]], wbeats: Sequence[Dict[str, Any]],
                     ctxs: Dict[str, Dict[str, Any]]) -> List[str]:
    """原文结构被镜头展示了一部分（分页不完整）时报错，要求模型补齐续镜。

    结构在本段完全没有出现在镜头中（口播未提及或模型选择了卡片）不在这里报错，
    由 absence_warnings 在整章层面提示，避免一处内容问题导致整窗反复重做。
    """
    out: List[str] = []
    for b in wbeats:
        bid = b["id"]
        for fam, (total, cap, label) in _structure_totals(ctxs[bid]).items():
            covered = set()
            used = False
            for c in cuts:
                if c["beat"] != bid or c["kind"] not in _FAMILY_KINDS[fam]:
                    continue
                used = True
                a0, a1 = _page(c["raw"], total, cap, f"{c['first']}–{c['last']}", [])
                covered.update(range(a0, a1))
            if not used:
                continue
            if -(-total // cap) > len(b["sentences"]):   # 句子不够分页（每页需占用一个句子范围）：由整章提示说明
                continue
            missing = [i + 1 for i in range(total) if i not in covered]
            if missing:
                out.append(f"段 {bid} 的{label}共 {total} 项，镜头未展示第 {'、'.join(str(i) for i in missing)} 项："
                           "请用同一类型的续镜补齐（part 指向剩余项目，每镜不超过上限），不得省略项目")
    return out


_FAMILY_TYPES = {"chain": ("diagram", "stack"), "table": ("table",), "list": ("list", "timeline"), "recap": ("recap",)}


def absence_warnings(scenes: List[Dict[str, Any]], script: Dict[str, Any],
                     ctxs: Dict[str, Dict[str, Any]]) -> List[str]:
    """整章层面：原文结构在其段落中完全没有出现在画面里时给出提示（不阻止生成）。"""
    out: List[str] = []
    for b in script["beats"]:
        bid = b["id"]
        own = [s for s in scenes if s.get("beat") == bid]
        types = {s.get("type") for s in own}
        for fam, (total, _cap, label) in _structure_totals(ctxs[bid]).items():
            if not types & set(_FAMILY_TYPES[fam]):
                out.append(f"{bid}：原文的{label}未在本段画面中出现（口播未提及，或模型选择了卡片），请在分镜页检查")
                continue
            shown = set()
            for s in own:
                if s.get("type") not in _FAMILY_TYPES[fam]:
                    continue
                if s.get("part"):
                    shown.update(range(s["part"][0] - 1, s["part"][1]))
                else:
                    shown.update(range(len(s.get("items") or [])))
            if len(shown) < total:
                out.append(f"{bid}：原文的{label}共 {total} 项，本段画面只展示了 {len(shown)} 项"
                           "（句子数不足以分页，请在分镜页手动调整句子范围或拆分段落）")
    return out


def _mark_pages(scenes: List[Dict[str, Any]]) -> None:
    """同一段内的结构分页：标注页码，渲染时显示“1 / 3”。"""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for sc in scenes:
        if sc.get("part") and sc.get("type") in _PAGED_TYPES:
            fam = "chain" if sc["type"] in ("diagram", "stack") else sc["type"]
            groups.setdefault((sc["beat"], fam), []).append(sc)
    for members in groups.values():
        if len(members) > 1:
            for j, sc in enumerate(members, 1):
                sc["page"] = {"index": j, "total": len(members)}


def parse_window(obj: Any, wbeats: Sequence[Dict[str, Any]], ctxs: Dict[str, Dict[str, Any]], *,
                 prev: Optional[Dict[str, Any]], ill_used: int, allowance: int, shots: int) \
        -> Tuple[List[Dict[str, Any]], List[str], int]:
    """返回 (本窗场景列表, 错误列表, 本窗新增的插图镜头数)。错误非空时场景列表为空。"""
    raw = obj.get("shots") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return [], ["缺少 shots 数组"], 0
    beat_by = {b["id"]: b for b in wbeats}
    seq = [sentence_id(b["id"], i) for b in wbeats for i in range(len(b["sentences"]))]
    pos = 0
    errs: List[str] = []
    cuts: List[Dict[str, Any]] = []
    for k, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            errs.append(f"第 {k} 个镜头不是对象")
            break
        sents = item.get("sentences")
        if isinstance(sents, str):
            sents = [sents]
        if not isinstance(sents, list) or not sents:
            errs.append(f"第 {k} 个镜头缺少句子范围")
            break
        first, last = str(sents[0]), str(sents[-1])
        if first not in seq or last not in seq:
            errs.append(f"第 {k} 个镜头的句子编号不在本窗口内（{first}–{last}）")
            break
        if beat_of(first) != beat_of(last):
            errs.append(f"第 {k} 个镜头跨段（{first}–{last}），镜头只能在同一段内")
            break
        if seq.index(first) != pos:
            errs.append(f"句子 {seq[pos]} 未被覆盖或镜头重叠（第 {k} 个镜头从 {first} 开始）")
            break
        end = seq.index(last)
        if end < pos:
            errs.append(f"第 {k} 个镜头的句子范围倒置（{first}–{last}）")
            break
        kind = str(item.get("kind") or "")
        if kind not in SHOT_KINDS:
            errs.append(f"第 {k} 个镜头（{first}–{last}）的类型 {kind!r} 不合法")
            break
        if kind == "hold" and cuts and cuts[-1]["beat"] == beat_of(first):
            cuts[-1]["last"] = last               # 沿用上一镜：并入同段的上一镜
            pos = end + 1
            continue
        cuts.append({"beat": beat_of(first), "first": first, "last": last, "kind": kind, "raw": item})
        pos = end + 1
    if not errs and pos != len(seq):
        errs.append(f"句子 {seq[pos]} 未被任何镜头覆盖")

    scenes: List[Dict[str, Any]] = []
    if not errs:
        for cut in cuts:
            sc, e = _build_shot(cut, beat_by[cut["beat"]], ctxs[cut["beat"]])
            errs += e
            if sc is not None:
                cut["kind"] = sc["kind"]              # 覆盖检查按实际构建的类型计算（回退为卡片的不算覆盖结构）
                scenes.append(sc)

    if not errs:
        errs += _coverage_errors(cuts, wbeats, ctxs)     # 覆盖检查在分镜成功构建之后进行，避免掩盖具体错误
    if not errs:
        _mark_pages(scenes)
    n_ill = sum(1 for s in scenes if s.get("type") == "illustration")
    remaining = max(0, allowance - _groups(ill_used, shots))
    if not errs and _groups(ill_used + n_ill, shots) - _groups(ill_used, shots) > remaining:
        errs.append(f"本窗口新增 {n_ill} 个插图镜头，超过图片额度余量 {remaining} 张")
    return (scenes if not errs else []), errs, n_ill


def _window_stage(script: Dict[str, Any], part: Dict[str, Any], wbeats: Sequence[Dict[str, Any]],
                  ctxs: Dict[str, Dict[str, Any]], plan: Dict[str, Dict[str, Any]], *, wid: str,
                  allowance: int, shots: int, ill_used: int, prev: Optional[Dict[str, Any]],
                  llm, cdir: Optional[Path], force: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    pid = script["part"]
    span = f"{wbeats[0]['id']}–{wbeats[-1]['id']}"
    chars = sum(beat_chars_of(b) for b in wbeats)
    payload = _window_payload(wbeats, ctxs, plan, allowance=allowance, shots=shots, ill_used=ill_used, prev=prev)
    name = f"win-{_sha(payload)}.json"
    hit = _cache_get(cdir, name, force)
    info_base = {"id": wid, "beats": [b["id"] for b in wbeats], "chars": chars}
    if hit is not None:
        joblog.bound_log(f"{pid} {wid}（{span}）：输入未变，复用 {len(hit['scenes'])} 个镜头（不调用模型）")
        return hit["scenes"], dict(info_base, shots=len(hit["scenes"]), calls=0, cached=True)
    issues: List[str] = []
    raw = ""
    calls = 0
    for attempt in range(MAX_ATTEMPTS):
        cancel.check()
        prompt = build_window_prompt(part, script, wbeats, ctxs, plan, wid=wid, allowance=allowance, shots=shots,
                                     ill_used=ill_used, prev=prev, issues=issues)
        n = attempt + 1
        calls += 1
        why = "" if n == 1 else f"（上次问题 {len(issues)} 条）"
        joblog.bound_log(f"{pid} {wid}（{span}，{chars} 字）：调用模型落镜第 {n}/{MAX_ATTEMPTS} 次，"
                         f"提示词 {len(prompt)} 字{why}")
        t0 = time.monotonic()
        raw = llm.ask(prompt, system=SYSTEM)
        cancel.check()
        joblog.bound_log(f"{pid} {wid}：模型返回 {len(raw)} 字，用时 {time.monotonic() - t0:.1f} 秒")
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            issues = [f"JSON 解析失败：{exc}"]
            joblog.bound_log(f"{pid} {wid}：输出不是有效 JSON（{exc}），只重做本窗口", level="warn")
            continue
        scenes, errs, _n_ill = parse_window(obj, wbeats, ctxs, prev=prev, ill_used=ill_used,
                                            allowance=allowance, shots=shots)
        if errs:
            issues = errs[:12]
            joblog.bound_log(f"{pid} {wid}：校验发现 {len(errs)} 处问题，只重做本窗口：", level="warn")
            for e in errs[:12]:
                joblog.bound_log(f"  · {e}", level="warn")
            if len(errs) > 12:
                joblog.bound_log(f"  · 其余 {len(errs) - 12} 处略", level="warn")
            continue
        for sc in scenes:
            if sc.get("fallback_from"):
                joblog.bound_log(f"  {sc['sentences'][0]}：模型选择的 {sc['fallback_from']} 不适用于本镜头，已改为卡片",
                                 level="warn")
        _cache_put(cdir, name, {"schema": 1, "scenes": scenes})
        joblog.bound_log(f"{pid} {wid} 通过：{len(scenes)} 个镜头（{_kind_summary(scenes)}）", level="ok")
        return scenes, dict(info_base, shots=len(scenes), calls=calls, cached=False)
    joblog.bound_log(f"{pid} {wid}：落镜 {MAX_ATTEMPTS} 次仍不合格；已通过的窗口已缓存，下次只重做本窗口",
                     level="error")
    raise PlanError(f"{pid} {wid} 分镜模型输出 {MAX_ATTEMPTS} 次仍不合格：" + "；".join(issues[:6]), raw=raw)


def _kind_summary(scenes: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for sc in scenes:
        k = sc.get("kind") or sc["type"]
        counts[k] = counts.get(k, 0) + 1
    return "、".join(f"{k}×{v}" for k, v in counts.items())


def _log_overview(part: Dict[str, Any], script: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]],
                  allowance: int, shots: int, windows: List[List[Dict[str, Any]]]) -> None:
    n_sent = sum(len(b["sentences"]) for b in script["beats"])
    chars = sum(beat_chars_of(b) for b in script["beats"])
    joblog.bound_log(f"{part['id']}：输入 {len(script['beats'])} 段 / {n_sent} 句 / {chars} 字，分 {len(windows)} 窗，"
                     f"图片额度 {allowance} 张（每图 {shots} 镜头）")
    for beat in script["beats"]:
        c = ctxs[beat["id"]]
        joblog.bound_log(f"  {beat['id']}：可选画面 {'、'.join(c['allowed'])}"
                         + (f"；原文结构 {'；'.join(c['notes'])}" if c["notes"] else ""))


def direct_part_llm(script: Dict[str, Any], blocks: List[Dict[str, Any]], *, part: Dict[str, Any],
                    chapter: Dict[str, str], allowance: int, shots_per_image: int, llm,
                    images_enabled: bool, style_anchor: str,
                    cache_dir: Optional[Path] = None, force: bool = False,
                    strict_coverage: bool = False, subject_from_source: bool = False,
                    timing: Optional[Dict[str, Tuple[float, float]]] = None) -> Dict[str, Any]:
    """模型版分镜（两步调用 + 分窗 + 局部修复）。失败抛 PlanError，不写结果。

    timing：句ID → (开始秒, 结束秒)，来自配音时间轴；有则提示词标注每句的真实时长，并纳入缓存键。

    cache_dir：缓存目录（策划与各窗口的结果）；force=True 时忽略缓存、全部重新调用模型。
    strict_coverage：结构块（流程、表格、列表、公式、递进、概念）未被任何镜头承载时整章失败（默认只提示）。
    subject_from_source：插图主体必须出自原文（默认关闭）。
    """
    ctxs = {b["id"]: beat_context(b, blocks) for b in script["beats"]}
    for b in script["beats"]:
        ctxs[b["id"]]["timing"] = {sentence_id(b["id"], i): tuple(timing[sentence_id(b["id"], i)])
                                   for i in range(len(b["sentences"]))
                                   if timing and sentence_id(b["id"], i) in timing}
    if subject_from_source:
        text = article_text(blocks)
        words = analogy_words_in(text)
        if words:
            g = {"article_norm": norm_key(text), "words": words}
            for c in ctxs.values():
                c["grounding"] = g
        else:
            joblog.bound_log("插图主体须出自原文：原文中没有可识别的具象类比词，本次不做该检查", level="warn")
    shots = max(1, int(shots_per_image))
    pid = part["id"]
    cdir = Path(cache_dir) if cache_dir is not None else None
    windows = plan_windows(script["beats"])
    _log_overview(part, script, ctxs, allowance, shots, windows)

    plan, plan_cached, plan_calls = _plan_stage(script, part, ctxs, allowance=allowance, shots=shots,
                                                llm=llm, cdir=cdir, force=force)

    scenes_all: List[Dict[str, Any]] = []
    win_info: List[Dict[str, Any]] = []
    prev: Optional[Dict[str, Any]] = None
    ill_used = 0
    for wi, wbeats in enumerate(windows, 1):
        wid = f"W{wi:02d}"
        scenes_w, info = _window_stage(script, part, wbeats, ctxs, plan, wid=wid, allowance=allowance,
                                       shots=shots, ill_used=ill_used, prev=prev, llm=llm, cdir=cdir, force=force)
        if scenes_w:
            last = scenes_w[-1]
            prev = {"beat": last["beat"], "sentences": list(last["sentences"]),
                    "kind": last.get("kind", last["type"])}
        ill_used += sum(1 for s in scenes_w if s.get("type") == "illustration")
        scenes_all += scenes_w
        win_info.append(info)

    for i, sc in enumerate(scenes_all, 1):
        sc["id"] = f"{pid}.shot{i:02d}"
    allocate_shared_images(scenes_all, shots)
    sb = {
        "schema": 2, "part": script["part"], "title": script["title"], "chapter": chapter,
        "scenes": scenes_all, "source_structure": _structure_kinds(ctxs),
        "image_allowance": allowance, "shots_per_image": shots,
        "images_planned": count_groups(scenes_all), "warnings": [],
        "director": LLM_PROMPT_VERSION, "engine": "llm",
        "plan": {"beats": plan}, "windows": win_info,
    }
    v = validate_storyboard(sb, script, allowance=allowance, style_anchor=style_anchor,
                            images_enabled=images_enabled, shots_per_image=shots)
    if v["errors"]:
        joblog.bound_log(f"{pid}：整章校验发现 {len(v['errors'])} 处问题，本章未写入结果", level="error")
        raise PlanError(f"{pid} 分镜整章校验不通过：" + "；".join(v["errors"][:6]))
    cov = coverage_report(scenes_all, script, ctxs)
    sb["coverage"] = cov
    if strict_coverage and cov["uncovered"]:
        for u in cov["uncovered"][:12]:
            joblog.bound_log(f"  · {u}", level="error")
        raise PlanError(f"{pid} 结构块未被镜头承载（严格覆盖已开启）：" + "；".join(cov["uncovered"][:6]))
    new_family = [u for u in cov["uncovered"] if any(k in u for k in ("公式", "递进", "概念定义", "对比", "回顾"))]
    sb["warnings"] = v["warnings"] + absence_warnings(scenes_all, script, ctxs) + new_family + cov["notes"]
    cached = sum(1 for w in win_info if w["cached"])
    total_calls = plan_calls + sum(w["calls"] for w in win_info)
    joblog.bound_log(f"{pid}：模型分镜通过校验：{len(scenes_all)} 个镜头（{_kind_summary(scenes_all)}），"
                     f"图片 {sb['images_planned']}/{allowance}；窗口 {len(win_info)} 个（复用 {cached} 个），"
                     f"模型调用 {total_calls} 次（策划{'复用' if plan_cached else '1 次起'}）", level="ok")
    for w in v["warnings"][:6]:
        joblog.bound_log(f"  ! {w}", level="warn")
    return sb
