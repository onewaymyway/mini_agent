"""wiki/normalize.py — 概念归一（方案 §5.2 候选合并）。

打分规则（无 embedding）：
  1. 去空白与标点后相同，或同指章程术语表中的同一规范词 → 直接合并（分 1.0）。
  2. 字符串相似度（difflib）≥ AUTO_MERGE → 直接合并。
  3. 一方包含另一方（且较短者 ≥ 2 字）→ 至少 0.8，进入下一步。
  4. 分数在 [ASK_MIN, AUTO_MERGE) 之间的候选对，批量交给 LLM 逐对判断是否同指；拿不准应答 false。
  5. 低于 ASK_MIN 的不合并。

簇的规范名：优先章程术语表的规范词；否则取出现文章数最多的写法。
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List, Set, Tuple

from . import validate_util as vu

AUTO_MERGE = 0.92
ASK_MIN = 0.6
ASK_BATCH = 30


class _UF:
    def __init__(self, items: Iterable[str]):
        self.p = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def alias_index(terms: Dict[str, List[str]]) -> Dict[str, str]:
    """章程术语表 → {归一键: 规范词归一键}。"""
    idx: Dict[str, str] = {}
    for canon, aliases in (terms or {}).items():
        ck = vu.key(canon)
        if not ck:
            continue
        idx[ck] = ck
        for a in aliases or []:
            if vu.key(a):
                idx[vu.key(a)] = ck
    return idx


def pair_score(a: str, b: str, aindex: Dict[str, str]) -> float:
    ka, kb = vu.key(a), vu.key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if aindex.get(ka) and aindex.get(ka) == aindex.get(kb):
        return 1.0
    score = difflib.SequenceMatcher(None, ka, kb).ratio()
    if min(len(ka), len(kb)) >= 2 and (ka in kb or kb in ka):
        score = max(score, 0.8)
    return score


def ask_prompt(pairs: List[Tuple[str, str, str]]) -> str:
    lines = [f"{pid}: {a} ｜ {b}" for pid, a, b in pairs]
    return ("【阶段:归一】下面每一行是一对概念名称，判断两者是否指同一个概念（只依据名称本身及其在文章中的含义；"
            "拿不准时答 false）。只输出 YAML，格式：\n"
            "decisions:\n  - {id: p1, same: true}\n"
            "必须对每个 id 给出一条判断，不要遗漏。\n\n" + "\n".join(lines))


def ask_validator(expected: Set[str]):
    def _v(obj: Dict[str, Any]) -> List[str]:
        dec = obj.get("decisions")
        if not isinstance(dec, list):
            return ["decisions 应为列表"]
        got = {}
        for d in dec:
            if not isinstance(d, dict) or not isinstance(d.get("same"), bool):
                return ["每条判断应含 id 与 same（true/false）"]
            got[str(d.get("id"))] = d["same"]
        missing = expected - set(got)
        if missing:
            return [f"缺少这些 id 的判断：{', '.join(sorted(missing))}"]
        return []
    return _v


def cluster(surfaces: Dict[str, Set[str]], terms: Dict[str, List[str]], ask_fn,
            progress=None) -> List[Dict[str, Any]]:
    """surfaces: {写法: {文章 id…}}。ask_fn(prompt_pairs) -> {pid: bool}，由调用方提供（含修复循环）。

    返回簇列表：[{canonical, surfaces[], articles[]}]，按规范名排序。
    """
    names = sorted(surfaces)
    aindex = alias_index(terms)
    uf = _UF(names)
    ask: List[Tuple[str, str, str]] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            s = pair_score(a, b, aindex)
            if s >= AUTO_MERGE:
                uf.union(a, b)
            elif s >= ASK_MIN:
                ask.append((f"p{len(ask) + 1}", a, b))
    total_batches = (len(ask) + ASK_BATCH - 1) // ASK_BATCH
    for k, start in enumerate(range(0, len(ask), ASK_BATCH), 1):
        batch = ask[start:start + ASK_BATCH]
        if progress:
            progress(k, total_batches, len(batch))
        decisions = ask_fn(batch)
        for pid, a, b in batch:
            if decisions.get(pid):
                uf.union(a, b)

    groups: Dict[str, List[str]] = {}
    for n in names:
        groups.setdefault(uf.find(n), []).append(n)
    out = []
    for members in groups.values():
        arts: Set[str] = set()
        for m in members:
            arts |= surfaces[m]
        canon = _canonical(members, surfaces, aindex, terms)
        out.append({"canonical": canon, "surfaces": sorted(members), "articles": sorted(arts)})
    return sorted(out, key=lambda c: c["canonical"])


def _canonical(members: List[str], surfaces: Dict[str, Set[str]], aindex: Dict[str, str],
               terms: Dict[str, List[str]]) -> str:
    for m in members:
        k = vu.key(m)
        if k in aindex:
            for canon in terms:
                if vu.key(canon) == aindex[k]:
                    return canon
    return max(members, key=lambda m: (len(surfaces[m]), -len(m), m))
