"""writing/draft.py — 逐节初稿与组装（方案 §8.1 第 3 步）。

每节只拿到本节的要点、依据来源（含摘录）与前文小节标题，降低漂移与幻觉。
模型输出的段落必须带证据标记；代码校验不通过时把具体问题回喂模型修复（修复轮数共享预算）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..digest import summarize as sm
from . import checks, outline

SYSTEM_HINT = "只输出 YAML：一个键 text，值为小节正文（Markdown 段落，不要标题）。"


def section_prompt(brief: Dict[str, Any], sec: Dict[str, Any], by_id: Dict[str, Dict[str, Any]],
                   prev_headings: List[str], hints: List[str]) -> str:
    allowed = [by_id[s] for s in sec["sources"] if s in by_id]
    src_block = "\n".join(f"[{s['id']}] {s['title']}（可信度 {s['credibility']}）\n摘录：{s['excerpt']}"
                          for s in allowed)
    terms = "；".join(f"{k}（别名：{'、'.join(v)}）" for k, v in (brief.get("terms") or {}).items()) or "无"
    forb = "、".join(brief["risk"]["forbidden_claims"]) or "无"
    out = "、".join(brief["scope"]["out"]) or "无"
    fb = ""
    if brief["feedback"]:
        fb = "编辑意见（必须落实）：\n" + "\n".join(f"- {f}" for f in brief["feedback"]) + "\n"
    hint_block = ("上一次输出的问题（必须修正）：\n" + "\n".join(f"- {h}" for h in hints) + "\n") if hints else ""
    return ("【阶段:正文】撰写小节「" + sec["heading"] + "」的正文（Markdown，不要写小节标题本身）。"
            + SYSTEM_HINT + "\n\n"
            "text: |\n  第一段……[^src-0001]\n  第二段……[^src-0002][^src-0003]\n\n"
            "规则：\n"
            "1. 每个段落至少包含一个证据标记 [^来源编号]，只能使用下列来源；\n"
            "2. 事实只能来自下列摘录，不得编造数字、版本、日期或来源之外的结论；\n"
            "3. 不得整句照抄摘录，用自己的话转述；\n"
            f"4. 本节要覆盖的要点：{'、'.join(sec['points']) or '（按标题展开）'}；\n"
            f"5. 用语必须统一：{terms}；\n"
            f"6. 禁止使用的说法：{forb}；不得写范围外内容：{out}；\n"
            f"7. 文体要求：{brief['kind']}——{checks.KIND_HINTS.get(brief['kind'], '')}\n"
            f"8. 语气：{brief['style']['tone']}；读者：{brief['audience'] or '未指定'}；层级：{brief['level']}\n"
            f"文章：{brief['title']}\n前文小节：{'、'.join(prev_headings) or '（这是第一节）'}\n"
            f"{fb}{hint_block}\n可用来源：\n{src_block}")


def section_validator(sec: Dict[str, Any], by_id: Dict[str, Dict[str, Any]], rules: Dict[str, Any]):
    allowed = [s for s in sec["sources"] if s in by_id]
    excerpts = {s: by_id[s]["excerpt"] for s in allowed}

    def _v(obj: Dict[str, Any]) -> List[str]:
        text = obj.get("text")
        if not isinstance(text, str):
            return ["应包含 text 字段（字符串）"]
        return checks.check_section(text, allowed=allowed, excerpts=excerpts, rules=rules)
    return _v


def draft_section(llm, brief, sec, by_id, budget: sm.RepairBudget, prev_headings: List[str],
                  hints: List[str], rules: Dict[str, Any]) -> str:
    prompt = section_prompt(brief, sec, by_id, prev_headings, hints)
    obj = sm.ask_validated(llm, prompt, section_validator(sec, by_id, rules), budget)
    return str(obj["text"]).strip()


def assemble(brief: Dict[str, Any], parts: List[Tuple[str, str]], by_id: Dict[str, Dict[str, Any]],
             library_links: List[Dict[str, str]]) -> str:
    """组装成完整 Markdown：标题、免责声明、AI 声明、各节正文、延伸阅读（库内互链 + 参考来源脚注）。"""
    lines: List[str] = [f"# {brief['title']}", ""]
    if brief["risk"]["disclaimer"]:
        lines += [f"> {brief['risk']['disclaimer']}", ""]
    if brief["style"]["ai_disclosure"]:
        lines += ["> 本文由 AI 辅助生成，基于下列来源整理。", ""]
    for heading, text in parts:
        lines += [f"## {heading}", "", text.strip(), ""]
    used = []
    joined = "\n".join(t for _, t in parts)
    for sid in re.findall(r"\[\^(src-\d{4})\]", joined):
        if sid not in used and sid in by_id:
            used.append(sid)
    lines += [f"## {outline.REFS_HEADING}", ""]
    if library_links:
        lines += ["### 库内互链", ""]
        lines += [f"- {x['title']}（{x['id']}）" for x in library_links]
        lines += [""]
    if used:
        lines += ["### 参考来源", ""]
        for sid in used:
            s = by_id[sid]
            where = s["url"] or "手动提供的资料"
            lines.append(f"[^{sid}]: {s['title']} — {where}（可信度 {s['credibility']}）")
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"
