"""第一期测试辅助：模型版分镜的两步假模型（章级策划 + 分窗落镜），以及从提示词中解析段落与句子。"""
import json
import re
from collections import OrderedDict

PLAN_MARK = "【章级策划】"
WIN_MARK = "【分窗落镜】"

_BEAT = re.compile(r"^\[(P\d+\.b\d+)\]", re.M)
_SENT = re.compile(r"^\s+\[(P\d+\.b\d+\.s\d+)\] (.*)$", re.M)


def beats_in(prompt):
    """提示词中出现的段落 ID（按顺序，去重）。"""
    out = []
    for b in _BEAT.findall(prompt):
        if b not in out:
            out.append(b)
    return out


def sentence_rows(prompt):
    """[(句子编号, 文本, 是否金句)]，按提示词中的顺序。"""
    rows = []
    for m in _SENT.finditer(prompt):
        text = m.group(2)
        quote = "（金句）" in text
        rows.append((m.group(1), text.replace("（金句）", ""), quote))
    return rows


def beat_blocks(prompt):
    """段落 ID → 该段在提示词中的文字块（到下一段为止）。"""
    out = OrderedDict()
    parts = re.split(r"(?m)^(?=\[P\d+\.b\d+\])", prompt)
    for part in parts:
        m = _BEAT.match(part)
        if m:
            out[m.group(1)] = part
    return out


def shot(first, last, kind, **extra):
    d = {"sentences": [first, last], "kind": kind}
    d.update(extra)
    return d


def shots_json(shots):
    return json.dumps({"shots": shots}, ensure_ascii=False)


def default_plan(prompt, _n=None):
    """每段：角色 narrative，要点取该段首句的前 12 字（必为原文子串）。"""
    beats = []
    texts = {}
    for sid, text, _q in sentence_rows(prompt):
        texts.setdefault(sid.rsplit(".s", 1)[0], text)
    for bid in beats_in(prompt):
        beats.append({"beat": bid, "role": "narrative", "points": [texts.get(bid, "要点")[:12]]})
    return json.dumps({"beats": beats}, ensure_ascii=False)


_NOTE_FLOW = re.compile(r"原文流程：.*?（共 (\d+) 个节点")
_NOTE_STACK = re.compile(r"原文堆叠层级[^：]*：.*?（共 (\d+) 层）")
_NOTE_TABLE = re.compile(r"原文表格（共 (\d+) 行）")
_NOTE_LIST = re.compile(r"原文列表（共 (\d+) 项）")
from article_to_video.visual.director import MAX_DIAGRAM_NODES, MAX_LIST_ITEMS, MAX_TABLE_ROWS  # noqa: E402

# 与分镜规则的每镜上限保持一致（列表上限在 R27 中提高，假模型按新上限分页）
CAPS = {"flow": MAX_DIAGRAM_NODES, "stack": MAX_DIAGRAM_NODES, "table": MAX_TABLE_ROWS, "list": MAX_LIST_ITEMS}


def _structure_pages(block, items):
    """段落中的原文结构：按每镜上限分页，每页占用一句（测试用的假模型只做结构覆盖，不做语义判断）。

    返回 (镜头列表, 已占用的句子数)。
    """
    found = []
    for kind, pat in (("flow", _NOTE_FLOW), ("stack", _NOTE_STACK), ("table", _NOTE_TABLE), ("list", _NOTE_LIST)):
        m = pat.search(block)
        if m:
            found.append((kind, int(m.group(1))))
    out, used = [], 0
    for kind, total in found:
        if kind in ("flow", "stack"):          # 流程节点必须在镜头句子中出现，假模型无法保证，故不生成
            continue
        cap = CAPS[kind]
        for start in range(0, total, cap):
            if used >= len(items):
                return out, used
            sid = items[used][0]
            out.append(shot(sid, sid, kind, part=[start + 1, min(total, start + cap)]))
            used += 1
    return out, used


def default_shots(prompt, special=None):
    """每段：金句单独成镜，其余句子合并为一个卡片镜头（条目取首句前 20 字）。

    special(bid, block) 可返回该段的镜头列表以覆盖默认值（返回 None 则用默认）。
    """
    rows = sentence_rows(prompt)
    blocks = beat_blocks(prompt)
    by_beat = OrderedDict()
    for sid, text, q in rows:
        by_beat.setdefault(sid.rsplit(".s", 1)[0], []).append((sid, text, q))
    out = []
    for bid, items in by_beat.items():
        custom = special(bid, blocks.get(bid, "")) if special else None
        if custom is not None:
            out += custom
            continue
        struct, used = _structure_pages(blocks.get(bid, ""), items)
        out += struct
        items = items[used:]
        i = 0
        while i < len(items):
            sid, text, q = items[i]
            if q:
                out.append(shot(sid, sid, "quote"))
                i += 1
                continue
            j = i
            while j + 1 < len(items) and not items[j + 1][2]:
                j += 1
            out.append(shot(items[i][0], items[j][0], "keypoint", items=[items[i][1][:20]]))
            i = j + 1
    return out


def default_window(prompt, _wid=None, _n=None):
    return shots_json(default_shots(prompt))


class ShotLLM:
    """两步假模型。plan_fn(prompt, 第几次策划) / win_fn(prompt, 窗口号, 该窗口第几次) → 字符串。

    记录：calls（总次数）、plan_calls、win_calls（窗口号列表）、prompts（全部提示词）。
    """

    def __init__(self, plan_fn=None, win_fn=None):
        self.plan_fn = plan_fn or default_plan
        self.win_fn = win_fn or default_window
        self.calls = 0
        self.plan_calls = 0
        self.win_calls = []
        self.prompts = []

    def ask(self, prompt, system=""):
        self.calls += 1
        self.prompts.append(prompt)
        if PLAN_MARK in prompt:
            self.plan_calls += 1
            return self.plan_fn(prompt, self.plan_calls)
        wid = re.search(r"WINDOW=(W\d+)", prompt).group(1)
        self.win_calls.append(wid)
        return self.win_fn(prompt, wid, self.win_calls.count(wid))

    def window_prompts(self, wid):
        return [p for p in self.prompts if f"WINDOW={wid}\n" in p]
