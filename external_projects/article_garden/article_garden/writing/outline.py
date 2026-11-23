"""writing/outline.py — 大纲（方案 §8.1 第 2 步）。模型给出小节、各节要点与依据来源；代码校验：
小节数量、标题唯一、必备小节齐全、每个必须覆盖的要点至少出现在一个小节、每节至少依据一个真实来源。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..digest import summarize as sm
from . import checks

SECTIONS_MIN, SECTIONS_MAX = 3, 8
REFS_HEADING = "延伸阅读"


def is_refs(heading: str) -> bool:
    return REFS_HEADING in heading


def prompt(brief: Dict[str, Any]) -> str:
    src = "\n".join(f"[{s['id']}] {s['title']}（可信度 {s['credibility']}）" for s in brief["sources"])
    musts = "、".join(brief["must_cover"]) or "（无）"
    must_have = "、".join(brief["style"]["must_have"]) or "无"
    return ("【阶段:大纲】为下面的选题设计文章大纲。只输出 YAML，格式：\n"
            "sections:\n"
            "  - heading: 小节标题\n"
            "    points: [本节覆盖的要点，取自必须覆盖的要点]\n"
            "    sources: [依据的来源编号，如 src-0001]\n\n"
            f"要求：{SECTIONS_MIN}–{SECTIONS_MAX} 个小节；必备小节标题须包含：{must_have}；"
            f"每个必须覆盖的要点至少出现在一个小节的 points 中；每节至少依据一个下列来源编号；"
            f"「{REFS_HEADING}」小节由系统生成正文，仍须列出。\n"
            f"文体：{brief['kind']}（{checks.KIND_HINTS.get(brief['kind'], '')}）；读者：{brief['audience'] or '未指定'}；"
            f"层级：{brief['level']}；语气：{brief['style']['tone']}\n"
            f"选题：{brief['title']}\n角度：{brief['angle'] or '无'}\n必须覆盖的要点：{musts}\n\n"
            f"来源：\n{src}")


def validator(brief: Dict[str, Any]):
    ids = {s["id"] for s in brief["sources"]}
    must_cover = list(brief["must_cover"])
    must_have = list(brief["style"]["must_have"])

    def _v(obj: Dict[str, Any]) -> List[str]:
        secs = obj.get("sections")
        if not isinstance(secs, list):
            return ["sections 应为列表"]
        if not (SECTIONS_MIN <= len(secs) <= SECTIONS_MAX):
            return [f"小节数应在 {SECTIONS_MIN}–{SECTIONS_MAX} 之间，当前 {len(secs)}"]
        issues: List[str] = []
        headings: List[str] = []
        all_points: List[str] = []
        for sec in secs:
            if not isinstance(sec, dict) or not str(sec.get("heading") or "").strip():
                issues.append("每个小节需要 heading")
                continue
            h = str(sec["heading"]).strip()
            if h in headings:
                issues.append(f"小节标题重复：「{h}」")
            headings.append(h)
            pts = sec.get("points") or []
            if not isinstance(pts, list):
                issues.append(f"「{h}」的 points 应为列表")
                pts = []
            all_points += [str(p) for p in pts]
            srcs = sec.get("sources")
            if not isinstance(srcs, list) or not srcs:
                issues.append(f"「{h}」至少需要一个依据来源")
            else:
                bad = [str(x) for x in srcs if str(x) not in ids]
                if bad:
                    issues.append(f"「{h}」引用了不存在的来源：{'、'.join(bad)}")
        for item in must_have:
            if item and not any(item in h for h in headings):
                issues.append(f"缺少必备小节「{item}」")
        for m in must_cover:
            if not any(m == p or m in p for p in all_points):
                issues.append(f"必须覆盖的要点「{m}」没有出现在任何小节中")
        return issues
    return _v


def make(llm, brief: Dict[str, Any], budget: sm.RepairBudget) -> List[Dict[str, Any]]:
    obj = sm.ask_validated(llm, prompt(brief), validator(brief), budget)
    out = []
    for sec in obj["sections"]:
        out.append({"heading": str(sec["heading"]).strip(),
                    "points": [str(p).strip() for p in (sec.get("points") or []) if str(p).strip()],
                    "sources": [str(x) for x in sec["sources"] if str(x) in {s["id"] for s in brief["sources"]}]})
    return out
