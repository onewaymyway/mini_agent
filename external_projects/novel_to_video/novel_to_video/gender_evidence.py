"""gender_evidence.py — 检索性别相关证据，并给出“建议 + 理由”，供用户确认。

核心约束（来自真实数据的验证）：
  * 正文中代词与称谓很难可靠地归属到具体角色：
      - “他想起了妹妹林星”中的“他”指林远，却与“林星”同句；
      - “林远记得父亲”中的“父亲”是林远的父亲，不是林远本人；
      - “林星看着哥哥”中的“哥”是林星称呼林远。
    因此规则不能据此自动判定性别。
  * 所以本模块不下结论，只做两件事：
      1) 检索证据：列出含角色名、且与性别相关的句子（代词/称谓/亲属词），附章节号与原句；
      2) 给出建议：只有人设卡中明确写出“性别：男/女”时才给出高置信建议；
         其余一律 unknown，并把证据列给用户看，由用户选择。
  * 写入项目的唯一途径是用户确认（见 steps/global_visual.confirm_gender）。
  * 非人类实体（AI、无人机、投影、盖亚等系统级存在）不做性别建议，只提示人工确认。

输出可复现：同样的输入得到同样的结果。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

MALE, FEMALE, UNKNOWN, NON_HUMAN = "male", "female", "unknown", "non_human"

# 与性别相关的线索词（用于检索，不用于判定）
PRONOUN_RE = re.compile(r"[他她]")
KIN_TERMS = ("父亲", "母亲", "爸", "妈", "哥", "姐", "弟", "妹", "儿子", "女儿", "丈夫", "妻子",
             "先生", "小姐", "女士", "少年", "少女", "男人", "女人")
NON_HUMAN_MARKERS = ("AI", "ai", "无人机", "投影", "盖亚", "系统", "机器")
PERSONA_GENDER_RE = re.compile(r"性别\s*[：:]\s*(男|女)")
SENT_SPLIT = re.compile(r"[。！？!?\n]+")
QUOTE_RE = re.compile(r"“[^”]*”|\"[^\"]*\"")


@dataclass
class Evidence:
    kind: str            # persona（人设卡明确字段） / pronoun（代词） / kin（亲属/称谓词）
    where: str           # 章节号或人设卡文件名
    quote: str           # 原句（截断，不含对话引号内的内容也保留，便于用户判断）

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Suggestion:
    entity_id: str
    name: str
    suggested: str           # male / female / unknown / non_human
    basis: str               # persona_field / none / non_human
    persona_gender: Optional[str]
    evidence: List[Evidence] = field(default_factory=list)
    evidence_total: int = 0  # 证据总条数（列出的为前若干条）
    note: str = ""

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


def is_non_human(names: Sequence[str]) -> bool:
    return any(m in n for n in names for m in NON_HUMAN_MARKERS)


def _persona_gender(persona_texts: Sequence[Tuple[str, str]]) -> Tuple[Optional[str], Optional[str]]:
    """返回 (性别, 来源文件)。仅接受明确的“性别：男/女”字段；多个来源冲突时返回 None。"""
    found: List[Tuple[str, str]] = []
    for where, text in persona_texts:
        for line in text.splitlines():
            m = PERSONA_GENDER_RE.search(line)
            if m:
                found.append((MALE if m.group(1) == "男" else FEMALE, where))
    genders = {g for g, _ in found}
    if len(genders) == 1:
        return genders.pop(), found[0][1]
    return None, None


def _kin_or_title(sentence: str) -> List[str]:
    return [t for t in KIN_TERMS if t in sentence]


def collect_evidence(names: Sequence[str], chapters: Sequence[Tuple[str, str]],
                     limit: int = 8) -> Tuple[List[Evidence], int]:
    """检索含角色名的句子，列出亲属/称谓与代词线索。

    展示策略（为了让用户看到全书而不只是前几章）：
      * 亲属/称谓类优先于代词类（它们更直接，但仍可能指向他人，需用户判断）；
      * 每类在全书范围内等距抽样，覆盖不同章节；
      * 排序稳定，同一输入得到同一结果。
    返回 (抽样列表, 证据总条数)。
    """
    kin: List[Evidence] = []
    pron: List[Evidence] = []
    for where, text in chapters:
        for sent in SENT_SPLIT.split(text):
            sent = sent.strip()
            if not sent or not any(n in sent for n in names):
                continue
            if _kin_or_title(sent):
                kin.append(Evidence("kin", where, sent[:120]))
            if PRONOUN_RE.search(sent):
                pron.append(Evidence("pronoun", where, sent[:120]))
    total = len(kin) + len(pron)
    picked = _spread(kin, limit) + _spread(pron, max(0, limit - min(len(kin), limit)))
    return picked[:limit], total


def _spread(items: List[Evidence], k: int) -> List[Evidence]:
    """从列表中等距抽取 k 条，覆盖全书（列表按章节有序）。"""
    if k <= 0 or not items:
        return []
    if len(items) <= k:
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def suggest(entity: Dict, chapters: Sequence[Tuple[str, str]],
            persona_texts: Sequence[Tuple[str, str]] = ()) -> Suggestion:
    eid = entity["id"]
    names = list(entity.get("names") or [])
    primary = names[0] if names else eid
    if is_non_human(names):
        return Suggestion(eid, primary, NON_HUMAN, "non_human", None, [], 0,
                          note="非人类或系统级实体，不做性别建议，请人工确认")
    pg, pg_src = _persona_gender(persona_texts)
    evid, total = collect_evidence(names, chapters)
    if pg is not None:
        return Suggestion(eid, primary, pg, "persona_field", pg, evid, total,
                          note=f"人设卡明确写明性别（来源：{pg_src}）")
    return Suggestion(eid, primary, UNKNOWN, "none", None, evid, total,
                      note="人设卡没有明确性别，正文线索不足以可靠判定。注意：证据句中的代词与亲属词未必指向该角色（例如“他”可能指同句中的另一人），请结合原句判断")
