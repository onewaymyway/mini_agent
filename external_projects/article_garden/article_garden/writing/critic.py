"""writing/critic.py — 独立评审（方案 §8.1 第 6 步）：另起一次模型调用，只看成稿与来源摘录，
不看写作过程。输出的问题按小节归属，交给修复循环处理。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..digest import summarize as sm

KINDS = ("无证据支持", "与证据矛盾", "与库内冲突", "其它")
SEVERITIES = ("严重", "轻微")
LEVELS = ("严格", "标准", "宽松")      # 评审严格度：严格=所有问题都要修；标准=只修严重问题；宽松=只记录不修


def prompt(body: str, sources: List[Dict[str, Any]], library_snippets: List[str]) -> str:
    src = "\n".join(f"[{s['id']}] {s['title']}\n摘录：{s['excerpt']}" for s in sources)
    lib = "\n".join(f"- {t}" for t in library_snippets) or "（无）"
    return ("【阶段:评审】你是独立审稿人，只看下面的成稿与来源摘录。找出：\n"
            "- 无证据支持：句子中的事实在来源摘录中找不到依据；\n"
            "- 与证据矛盾：句子与来源摘录的说法相反；\n"
            "- 与库内冲突：与库内已有文章的明显矛盾。\n"
            "来源摘录只是节选，不要因为摘录里没有逐字出现就判有问题。以下情况不要报：领域内的常识性说明与过渡句、"
            "对「摘录中没有提供某信息」的如实说明、措辞上的轻微概括或改写、不含具体事实的论述。\n"
            "每条问题标注 severity：严重＝具体事实（数字、名称、因果结论、明确的断言）没有依据或与来源相反；"
            "轻微＝表述稍有拔高或不够严谨，但不改变事实。\n"
            "只输出 YAML：\nissues:\n  - section: 小节标题（与成稿一致）\n"
            "    kind: 无证据支持|与证据矛盾|与库内冲突|其它\n    severity: 严重|轻微\n    note: 具体指出哪一句、为什么有问题\n"
            "没有问题则输出 issues: []。\n\n"
            f"<draft>\n{body}\n</draft>\n\n来源摘录：\n{src}\n\n库内相关文章：\n{lib}")


def validator(obj: Dict[str, Any]) -> List[str]:
    items = obj.get("issues")
    if not isinstance(items, list):
        return ["issues 应为列表（没有问题则为 []）"]
    for it in items:
        if not isinstance(it, dict) or not str(it.get("section") or "").strip() \
                or not str(it.get("note") or "").strip() or it.get("kind") not in KINDS:
            return ["每条问题需含 section、kind（" + "/".join(KINDS) + "）、severity（" + "/".join(SEVERITIES) + "）与 note"]
        if it.get("severity") not in (None,) + SEVERITIES:
            return ["severity 只能是：" + "/".join(SEVERITIES)]
    return []


def review(llm, body: str, sources: List[Dict[str, Any]], library_snippets: List[str],
           max_rounds: int = 3) -> List[Dict[str, str]]:
    obj = sm.ask_validated(llm, prompt(body, sources, library_snippets), validator,
                           sm.RepairBudget(max_rounds))
    # 未标注 severity 的按「严重」处理（保守）
    return [{"section": str(i["section"]).strip(), "kind": i["kind"], "severity": i.get("severity") or "严重",
             "note": str(i["note"]).strip()}
            for i in obj.get("issues") or []]


def actionable(issues: List[Dict[str, str]], level: str) -> List[Dict[str, str]]:
    """按评审严格度挑出需要修复的问题；其余只作为提示记录。"""
    if level == "严格":
        return list(issues)
    if level == "宽松":
        return []
    return [i for i in issues if i.get("severity", "严重") == "严重"]
