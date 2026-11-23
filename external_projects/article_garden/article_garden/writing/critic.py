"""writing/critic.py — 独立评审（方案 §8.1 第 6 步）：另起一次模型调用，只看成稿与来源摘录，
不看写作过程。输出的问题按小节归属，交给修复循环处理。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..digest import summarize as sm

KINDS = ("无证据支持", "与证据矛盾", "与库内冲突", "其它")


def prompt(body: str, sources: List[Dict[str, Any]], library_snippets: List[str]) -> str:
    src = "\n".join(f"[{s['id']}] {s['title']}\n摘录：{s['excerpt']}" for s in sources)
    lib = "\n".join(f"- {t}" for t in library_snippets) or "（无）"
    return ("【阶段:评审】你是独立审稿人，只看下面的成稿与来源摘录。找出：\n"
            "- 无证据支持：句子中的事实在来源摘录中找不到依据；\n"
            "- 与证据矛盾：句子与来源摘录的说法相反；\n"
            "- 与库内冲突：与库内已有文章的明显矛盾。\n"
            "只输出 YAML：\nissues:\n  - section: 小节标题（与成稿一致）\n"
            "    kind: 无证据支持|与证据矛盾|与库内冲突|其它\n    note: 具体指出哪一句、为什么有问题\n"
            "没有问题则输出 issues: []。\n\n"
            f"<draft>\n{body}\n</draft>\n\n来源摘录：\n{src}\n\n库内相关文章：\n{lib}")


def validator(obj: Dict[str, Any]) -> List[str]:
    items = obj.get("issues")
    if not isinstance(items, list):
        return ["issues 应为列表（没有问题则为 []）"]
    for it in items:
        if not isinstance(it, dict) or not str(it.get("section") or "").strip() \
                or not str(it.get("note") or "").strip() or it.get("kind") not in KINDS:
            return ["每条问题需含 section、kind（" + "/".join(KINDS) + "）与 note"]
    return []


def review(llm, body: str, sources: List[Dict[str, Any]], library_snippets: List[str],
           max_rounds: int = 3) -> List[Dict[str, str]]:
    obj = sm.ask_validated(llm, prompt(body, sources, library_snippets), validator,
                           sm.RepairBudget(max_rounds))
    return [{"section": str(i["section"]).strip(), "kind": i["kind"], "note": str(i["note"]).strip()}
            for i in obj.get("issues") or []]
