"""确定性的幻灯片规划（第一阶段）：把一章的文章块组织成幻灯片。

规则：
  * 标题块开始新的一页；章节开头没有标题时，页标题取章名；
  * 列表 → 列表元素；流程 → 流程元素；公式 → 公式元素；表格 → 表格元素；
    单行引语 → 金句元素；多行引语与段落 → 要点元素（段落按句子切分）；
  * 一页的条目数超过 MAX_SLIDE_ITEMS 时续页（标题加“（续）”）；单个元素超过 MAX_ELEMENT_ITEMS 时拆分；
  * 每页的预计讲解秒数按字数估算（SPEECH_CHARS_PER_SEC），用于控制页面时长。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

DECK_VERSION = "deck-rules-v5"
MAX_SLIDE_ITEMS = 12         # 一页的条目上限（实测：多元素同页时仍可放下；超过则续页）
MAX_ELEMENT_ITEMS = 12        # 一个元素的条目上限（超过则拆分到多个元素）
MAX_PAGE_ELEMENTS = 6         # 一页的元素个数上限（R31：元素太多时卡片内边距与间距占掉大半高度，字会很小；超过则续页）
RETRY_CAPS = ((12, 12), (8, 8), (5, 5))   # 规划不合格时依次改用更细的分页重试
SPEECH_CHARS_PER_SEC = 4.2    # 讲解语速估算（字/秒）
PAGE_OVERHEAD_SEC = 2.0       # 每页的固定停顿
MAX_PAGE_SEC = 60.0           # 单页讲解时长上限（超过则续页）
MAX_PAGE_CHARS = int((MAX_PAGE_SEC - PAGE_OVERHEAD_SEC) * SPEECH_CHARS_PER_SEC)
_SENT = re.compile(r"[^。！？；!?;]+[。！？；!?;]?")


def _text(b: Dict[str, Any]) -> str:
    return re.sub(r"[*`]+", "", str(b.get("plain") or b.get("text") or "")).strip()


def _sentences(text: str) -> List[str]:
    out = [m.group(0).strip() for m in _SENT.finditer(text)]
    return [x for x in out if x]


def _chunks(seq: List[str], n: int) -> List[List[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)] or []


def elements_of(b: Dict[str, Any], cap: int = MAX_ELEMENT_ITEMS) -> List[Dict[str, Any]]:
    """一个文章块转换成若干页面元素（标题与无内容的块返回空列表）。cap 为一个元素的条目上限。"""
    kind = b.get("kind")
    bid = b.get("id")
    if kind in ("heading", "rule"):
        return []
    if kind == "list":
        items = [str(x).strip() for x in b.get("items") or [] if str(x).strip()]
        return [{"kind": "list", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(items, cap)]
    if kind == "table":
        header = [str(h) for h in b.get("header") or []]
        rows = [[str(c) for c in r] for r in b.get("rows") or []]
        if not rows:
            return []
        return [{"kind": "table", "header": header, "rows": c, "items": [], "emphasis": [], "source": bid}
                for c in _chunks(rows, cap)]                       # 表格超过一页时按行拆分，每段保留表头
    if kind == "code":
        shape = b.get("shape")
        nodes = [str(x).strip() for x in b.get("nodes") or [] if str(x).strip()]
        if shape == "chain" and nodes:
            # 流程超过一个元素的上限时拆成多段（每段一个流程），按顺序续页
            return [{"kind": "diagram", "items": c, "loop": bool(b.get("loop")) and len(nodes) <= cap,
                     "emphasis": [], "source": bid} for c in _chunks(nodes, cap)]
        if b.get("hint") == "formula":
            text = str((b.get("items") or [b.get("content", "")])[0]).strip()
            return [{"kind": "formula", "text": text, "items": [], "emphasis": [], "source": bid}] if text else []
        items = [str(x).strip() for x in (b.get("items") or nodes) if str(x).strip()]
        if not items:
            items = [ln.strip() for ln in str(b.get("content", "")).splitlines() if ln.strip()]
        return [{"kind": "keypoint", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(items, cap)]
    if kind == "quote":
        lines = [ln.strip().strip("“”\"「」").strip() for ln in _text(b).split("\n") if ln.strip()]
        if len(lines) == 1:
            return [{"kind": "quote", "items": lines, "emphasis": [], "source": bid}]
        return [{"kind": "keypoint", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(lines, cap)] if lines else []
    if kind == "paragraph":
        sents = _sentences(_text(b))
        return [{"kind": "keypoint", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(sents, cap)] if sents else []
    return []


def element_count(el: Dict[str, Any]) -> int:
    """元素在页面上占用的条目数（表格按行数，公式记为 1）。"""
    if el["kind"] == "table":
        return len(el.get("rows") or [])
    if el["kind"] == "formula":
        return 1
    return len(el.get("items", []))


def _fallback(b: Dict[str, Any], cap: int = MAX_ELEMENT_ITEMS) -> List[Dict[str, Any]]:
    """块没能转换成任何元素，但原文中有内容：回退为要点，保证原文内容不会因此丢失。"""
    if b.get("kind") in ("heading", "rule"):
        return []
    items = [str(x).strip() for x in (b.get("items") or b.get("nodes") or []) if str(x).strip()]
    if not items:
        items = _sentences(_text(b))
    if not items:
        items = [ln.strip() for ln in str(b.get("content", "")).splitlines() if ln.strip()]
    return [{"kind": "keypoint", "items": c, "emphasis": [], "source": b.get("id")}
            for c in _chunks(items, cap)]


def _take_budget(el: Dict[str, Any], budget: int):
    """取元素的前若干条，使字数不超过 budget（至少取一条）。返回 (前段, 余下段)；无法再拆时余下段为 None。"""
    items = list(el.get("items") or [])
    take, used = [], 0
    for item in items:
        n = len(str(item))
        if take and used + n > budget:
            break
        take.append(item)
        used += n
    if len(take) >= len(items):
        return el, None
    return dict(el, items=take), dict(el, items=items[len(take):])


def _take_fit(el: Dict[str, Any], item_budget: int, char_budget: int):
    """取元素的前若干条，使条目数与字数都不超过预算（至少取一条）。返回 (前段, 余下段或 None)。"""
    items = list(el.get("items") or [])
    take, used = [], 0
    for item in items:
        n = len(str(item))
        if take and (len(take) + 1 > item_budget or used + n > char_budget):
            break
        take.append(item)
        used += n
    if len(take) >= len(items):
        return el, None
    return dict(el, items=take), dict(el, items=items[len(take):])


def _split_by_time(el: Dict[str, Any]) -> List[Dict[str, Any]]:
    """元素本身的讲解时长超过单页上限时，按字数把条目拆成多个元素（只拆文字类元素）。"""
    if el.get("kind") not in ("keypoint", "list", "quote") or _element_chars(el) <= MAX_PAGE_CHARS:
        return [el]
    out, cur, cur_n = [], [], 0
    for item in el.get("items") or []:
        n = len(str(item))
        if cur and cur_n + n > MAX_PAGE_CHARS:
            out.append(dict(el, items=cur))
            cur, cur_n = [], 0
        cur.append(item)
        cur_n += n
    if cur:
        out.append(dict(el, items=cur))
    return out


def _element_chars(el: Dict[str, Any]) -> int:
    if el["kind"] == "table":
        return sum(len(c) for c in el.get("header", [])) + sum(len(c) for r in el.get("rows", []) for c in r)
    if el["kind"] == "formula":
        return len(el.get("text", ""))
    return sum(len(x) for x in el.get("items", []))


def _slide(sid: str, title: str, sections: List[str]) -> Dict[str, Any]:
    return {"id": sid, "title": title, "section_ids": [], "source_blocks": [], "elements": [], "chars": 0,
            "est_sec": 0.0, "_sections": sections}


def _finish(s: Dict[str, Any]) -> Dict[str, Any]:
    s["section_ids"] = list(dict.fromkeys(s.pop("_sections")))
    s["chars"] = len(s["title"]) + sum(_element_chars(e) for e in s["elements"])
    s["est_sec"] = round(s["chars"] / SPEECH_CHARS_PER_SEC + PAGE_OVERHEAD_SEC, 1)
    return s


# ---------- 分页标准（人类阅读习惯） ----------
#  1. 一级、二级标题开始新的一页（同一页不混入两个节）；
#  2. 标题、前言（以“：”“如下”“以下”结尾的段落）、三级小标题与它引出的内容放在同一页，绝不单独留在页尾；
#  3. 页面不能只有标题或前言，每页至少有一项实际内容；
#  4. 超过一页时从逻辑段落之间断开，续页标题加“（续）”；列表、流程、表格等整体单元能不拆就不拆；
#  5. 同一节的续页如果只剩很少内容（不超过 MIN_TAIL_ITEMS 条），并入前一页（放得下时）。
MIN_TAIL_ITEMS = 2
LEAD_MAX_CHARS = 60
QUESTION_LEAD_CHARS = 12


def is_lead_in(b: Dict[str, Any]) -> bool:
    """前言段落：较短，并以冒号结尾或引出后文（如“如下”“以下”）。"""
    if b.get("kind") != "paragraph":
        return False
    t = _text(b)
    if 0 < len(t) <= QUESTION_LEAD_CHARS and t.endswith(("？", "?")):   # “为什么？”——答案紧随其后，不能分开
        return True
    return 0 < len(t) <= LEAD_MAX_CHARS and (t.endswith(("：", ":")) or "如下" in t or "以下" in t)


def _units(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把文章块组织为“单元”：标题（若干连续标题）+ 前言 + 内容，组成不可拆开的一组。"""
    units: List[Dict[str, Any]] = []
    heads: List[Dict[str, Any]] = []
    for b in blocks:
        k = b.get("kind")
        if k == "rule":
            continue
        if k == "heading":
            if (b.get("level", 2) or 2) <= 2:
                heads.append(b)
                continue
            # 三级及以下标题：作为小标题，与后续内容绑定
            sub = {"kind": "paragraph", "plain": _text(b), "id": b.get("id"), "section": b.get("section"),
                   "_lead": True}
            units.append({"sec": b.get("section"), "heads": [], "blocks": [sub], "lead": True})
            continue
        lead = is_lead_in(b)
        if lead:
            b = dict(b, _lead=True)
        units.append({"sec": b.get("section"), "heads": heads, "blocks": [b], "lead": lead})
        heads = []
    # 末尾没有内容的标题不单独成页（没有可承载的内容）
    # 前言单元并入下一个内容单元
    merged: List[Dict[str, Any]] = []
    carry_blocks: List[Dict[str, Any]] = []
    carry_heads: List[Dict[str, Any]] = []
    for u in units:
        if u["lead"]:                      # 前言：连同它的标题一起，并入后面的内容单元
            carry_blocks += u["blocks"]
            carry_heads += u["heads"]
            continue
        if carry_blocks or carry_heads:
            u = dict(u, blocks=carry_blocks + u["blocks"], heads=carry_heads + u["heads"], lead=False)
            carry_blocks, carry_heads = [], []
        merged.append(u)
    if carry_blocks:                       # 文末的前言没有后续内容：它不再是前言，并入上一单元，不丢内容
        plain_blocks = [dict(b, _lead=False) for b in carry_blocks]
        if merged:
            merged[-1] = dict(merged[-1], blocks=merged[-1]["blocks"] + plain_blocks)
        else:
            merged.append({"sec": carry_blocks[-1].get("section"), "heads": carry_heads,
                           "blocks": plain_blocks, "lead": False})
    return merged


def _groups(elements: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """把“前言 + 它引出的内容”组成一组，组内不能分开。"""
    out: List[List[Dict[str, Any]]] = []
    i = 0
    while i < len(elements):
        e = elements[i]
        if e.get("lead_in") and i + 1 < len(elements):
            # 连续的前言（如三级标题后紧跟前言段）要与它们最终引出的内容一起成组
            j = i
            while j + 1 < len(elements) and elements[j].get("lead_in"):
                j += 1
            out.append(elements[i:j + 1])
            i = j + 1
        else:
            out.append([e])
            i += 1
    return out


def _unit_groups(u: Dict[str, Any], element_cap: int, page_cap: int) -> List[List[Dict[str, Any]]]:
    els: List[Dict[str, Any]] = []
    prev_lead = False
    for b in u["blocks"]:
        # 前言之后的内容要与前言同页：它的容量需要让出前言占用的条目
        cap = min(element_cap, page_cap - 1) if prev_lead else element_cap
        for el in (elements_of(b, cap) or _fallback(b, cap)):
            el = dict(el, lead_in=bool(b.get("_lead")), source=b.get("id"))
            els.append(el)
        prev_lead = bool(b.get("_lead"))
    return _groups(els)


def plan_slides(part: Dict[str, Any], blocks: List[Dict[str, Any]], part_id: str, *,
                page_cap: int = MAX_SLIDE_ITEMS, element_cap: int = MAX_ELEMENT_ITEMS) -> Dict[str, Any]:
    """按分页标准规划幻灯片。blocks 为本章的文章块（已按原文顺序）。
    page_cap：一页的条目上限；element_cap：一个元素的条目上限（超过则拆分）。"""
    base_title = part.get("title", "")
    blocks = heading_punchlines(blocks)
    # 1. 按一、二级标题切成节；每节内的“组”（前言 + 它引出的内容）是分页的最小单位
    sections: List[Dict[str, Any]] = []
    for u in units_of(blocks):
        if any((h.get("level", 2) or 2) <= 2 for h in u["heads"]):
            base_title = _text([h for h in u["heads"] if (h.get("level", 2) or 2) <= 2][-1]) or base_title
            sections.append({"title": base_title, "groups": []})
        elif not sections:
            sections.append({"title": base_title, "groups": []})
        for grp in _unit_groups(u, element_cap, page_cap):
            sections[-1]["groups"].append((grp, u["sec"]))
    # 2. 每节内用动态规划选分页点：页尽量充实，并避开语义上不该断开的位置（见 _break_cost）
    pages: List[Dict[str, Any]] = []
    for sec in sections:
        groups = _split_oversize(sec["groups"], page_cap)
        for k, run in enumerate(_paginate(groups, sec["title"], page_cap)):
            title = sec["title"] if k == 0 else f"{sec['title']}（续）"
            pg = {"title": title, "sec": None, "cont": k > 0, "els": [], "items": 0, "chars": len(title),
                  "blocks": []}
            for grp, usec in run:
                for e in grp:
                    pg["els"].append(e)
                    pg["items"] += element_count(e)
                    pg["chars"] += _element_chars(e)
                    if e.get("source") and e["source"] not in pg["blocks"]:
                        pg["blocks"].append(e["source"])
                pg["sec"] = usec
            pages.append(pg)
    slides = []
    for pg in pages:
        if not pg["els"]:
            continue
        slides.append(_slide(f"{part_id}.S{len(slides) + 1:02d}", pg["title"], [pg["sec"]]))
        slides[-1]["elements"] = [dict(e) for e in pg["els"]]          # 保留 lead_in 标记，供校验
        slides[-1]["source_blocks"] = pg["blocks"]
        slides[-1]["_sections"] = [pg["sec"]]
    finished = [_finish(s) for s in slides]
    for i, s in enumerate(finished, 1):
        s["id"] = f"{part_id}.S{i:02d}"
    total_sec = round(sum(s["est_sec"] for s in finished), 1)
    return {"schema": 1, "part": part_id, "title": part.get("title", ""), "mode": "deck",
            "slides": finished, "total_est_sec": total_sec, "version": DECK_VERSION,
            "page_cap": page_cap, "element_cap": element_cap}


# ---------- 语义分页（R33） ----------
CONTINUATIONS = ("还有", "另外", "同时", "此外", "而", "但", "然而", "因此", "所以", "于是", "这意味着", "最终",
                 "换句话说", "也就是说", "比如", "例如", "然后", "再往后", "进一步")
SHORT_PARA_CHARS = 30


def heading_punchlines(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """“空章节”的标题其实是上一句的答案（如“但还有一个更加重要的问题：”后面紧跟的标题“语义带宽。”）：
    一、二级标题后面没有任何内容就是下一个标题，且它前面是前言、或它本身是一句完整的话（以句末标点结束）时，
    把它当作金句放回正文流里，而不是新开一个只有标题的页（那样标题会丢失、前言被甩到下一章）。"""
    out: List[Dict[str, Any]] = []
    n = len(blocks)
    for i, b in enumerate(blocks):
        if b.get("kind") == "heading" and (b.get("level", 2) or 2) <= 2 and out:
            nxt = next((x for x in blocks[i + 1:] if x.get("kind") != "rule"), None)
            empty = nxt is None or (nxt.get("kind") == "heading" and (nxt.get("level", 2) or 2) <= 2)
            prev = next((x for x in reversed(out) if x.get("kind") != "rule"), None)
            text = _text(b)
            sentence = text.endswith(("。", "！", "？", "!", "?"))
            if empty and text and prev is not None and prev.get("kind") != "heading" and (
                    is_lead_in(prev) or sentence):
                out.append({"id": b.get("id"), "kind": "quote", "plain": text, "section": prev.get("section"),
                            "_from_heading": True})
                continue
        out.append(b)
    return out


def _group_stats(grp: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    return (sum(element_count(e) for e in grp), sum(_element_chars(e) for e in grp), len(grp))


def _split_oversize(groups, page_cap: int):
    """单个组放不进一个空页时，拆分其中的内容元素（前言留在第一段）；无法再拆的整块元素独占一页。"""
    out = []
    queue = list(groups)
    while queue:
        grp, sec = queue.pop(0)
        n, c, k = _group_stats(grp)
        title_room = 40
        if n <= page_cap and c + title_room <= MAX_PAGE_CHARS and k <= MAX_PAGE_ELEMENTS:
            out.append((grp, sec))
            continue
        content = grp[-1]
        used_items = sum(element_count(x) for x in grp[:-1])
        used_chars = sum(_element_chars(x) for x in grp[:-1]) + title_room
        head, rest = _take_fit(content, max(1, page_cap - used_items), MAX_PAGE_CHARS - used_chars)
        if rest is not None and content.get("kind") in ("keypoint", "list", "quote"):
            out.append((grp[:-1] + [head], sec))
            queue.insert(0, ([rest], sec))
        else:
            out.append((grp, sec))                 # 无法再拆：独占一页
    return out


def _first_text(e: Dict[str, Any]) -> str:
    items = e.get("items") or []
    return str(items[0]) if items else str(e.get("text", ""))


def _last_text(e: Dict[str, Any]) -> str:
    items = e.get("items") or []
    return str(items[-1]) if items else str(e.get("text", ""))


def _break_cost(a_grp: List[Dict[str, Any]], b_grp: List[Dict[str, Any]]) -> float:
    """在两个组之间分页的代价：越小越适合断开。完整的结论/列表之后断开最好；
    问句与它的回答之间、短过渡句之后、同一原文块中间、以“还有/但/因此…”接续上文的地方不宜断开。"""
    a, b = a_grp[-1], b_grp[0]
    cost = 1.0
    atext = _last_text(a)
    if a.get("kind") in ("quote", "list", "table", "diagram", "formula"):
        cost = 0.2
    elif atext.endswith(("？", "?")):
        cost += 3.0
    elif len(atext) <= SHORT_PARA_CHARS:
        cost += 1.5
    if b.get("lead_in") and _first_text(b).startswith(CONTINUATIONS):
        cost += 1.5
    if a.get("source") and a.get("source") == b.get("source"):
        cost += 4.0                                 # 同一段/同一列表被切开
    return cost


def _paginate(groups, title: str, page_cap: int) -> List[List[Tuple[List[Dict[str, Any]], Any]]]:
    """把一节的组按顺序切成页。动态规划：最小化 页面空疏代价 + 断点代价。
    页面容量：条目 ≤ page_cap、字数 ≤ MAX_PAGE_CHARS、元素 ≤ MAX_PAGE_ELEMENTS；放不下的单个组独占一页。"""
    m = len(groups)
    if m == 0:
        return []
    stats = [_group_stats(g) for g, _ in groups]
    INF = float("inf")
    best = [INF] * (m + 1)
    prev = [0] * (m + 1)
    best[0] = 0.0
    tl = len(title) + 3
    for j in range(1, m + 1):
        n = c = k = 0
        for i in range(j - 1, max(-1, j - 1 - MAX_PAGE_ELEMENTS), -1):
            n += stats[i][0]
            c += stats[i][1]
            k += stats[i][2]
            alone = (i == j - 1)
            if not alone and (n > page_cap or c + tl > MAX_PAGE_CHARS or k > MAX_PAGE_ELEMENTS):
                break
            r = min(1.0, max(k / MAX_PAGE_ELEMENTS, n / page_cap, (c + tl) / MAX_PAGE_CHARS))
            fill = 2.0 * (1.0 - r) ** 2
            if j == m:
                fill *= 0.4                          # 节的最后一页允许不满
            if k <= 1 and m > 1:
                fill += 1.0                          # 避免只有一个元素的孤页
            brk = 0.0 if j == m else _break_cost(groups[j - 1][0], groups[j][0])
            cost = best[i] + fill + brk
            if cost < best[j]:
                best[j], prev[j] = cost, i
    runs, j = [], m
    while j > 0:
        i = prev[j]
        runs.append(groups[i:j])
        j = i
    return runs[::-1]


def units_of(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _units(blocks)


def _merge_tails(pages: List[Dict[str, Any]], page_cap: int) -> List[Dict[str, Any]]:
    """同一节的续页只剩很少内容时，并入前一页（放得下才并）。"""
    out: List[Dict[str, Any]] = []
    for pg in pages:
        prev = out[-1] if out else None
        if (prev is not None and pg["cont"] and prev["sec"] == pg["sec"] and pg["items"] <= MIN_TAIL_ITEMS
                and prev["items"] + pg["items"] <= page_cap
                and len(prev["els"]) + len(pg["els"]) <= MAX_PAGE_ELEMENTS
                and prev["chars"] + pg["chars"] - len(pg["title"]) <= MAX_PAGE_CHARS):
            prev["els"] += pg["els"]
            prev["items"] += pg["items"]
            prev["chars"] += pg["chars"] - len(pg["title"])
            prev["blocks"] += [x for x in pg["blocks"] if x not in prev["blocks"]]
            continue
        out.append(pg)
    return out
