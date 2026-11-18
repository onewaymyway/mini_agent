"""planning/prompts.py — S1 分节摘要与 S2 大纲的提示词。

提示词里保留机器可读的行（GROUP_ID= / SECTION_IDS= / TARGET_MINUTES=），便于测试与排查。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

PROMPT_VERSION = "p1-v1"

DIGEST_SYSTEM = (
    "你是视频讲解的编辑。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
    "不得添加原文没有的事实、数字或引语。"
)

SPINE_SYSTEM = (
    "你是讲解视频的主编。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
    "每个小节必须且只能出现在一个章或取舍报告中。"
)

DIGEST_SCHEMA = """{
  "summary": "本组一段话摘要（<=120字）",
  "core_claims": ["核心论点1", "核心论点2"],
  "key_numbers": ["原文中出现的数字或数量（逐字）"],
  "terms": ["需要统一译法的术语"],
  "analogies": ["原文中的类比（简述）"],
  "quotable": ["可作金句的原文逐字片段（<=60字）"],
  "relation_to_prev": "与上文的关系（一句话）"
}"""

SPINE_SCHEMA = """{
  "thesis": "全文主旨一句话",
  "audience": "目标观众",
  "tone": "语气风格",
  "arc": "论证弧线（提出问题→…→落点）",
  "hook_candidates": ["开场钩子候选1", "开场钩子候选2", "开场钩子候选3"],
  "selected_hook": 0,
  "parts": [
    {"title": "章标题", "role": "intro|argument|example|turn|conclusion",
     "one_line": "本章一句话", "section_ids": ["s01", "s02"], "weight": 1.0}
  ],
  "glossary": [{"term": "原文术语", "zh": "统一译法"}],
  "golden_quotes": ["必须是摘要 quotable 中的原文逐字片段"],
  "cuts": [{"section_ids": ["s10"], "reason": "舍弃理由"}]
}"""


def _retry_hint(issues: List[str]) -> str:
    bullets = "\n".join(f"- {x}" for x in issues)
    return f"\n\n【上次输出的问题，必须修正后重新输出完整 JSON】\n{bullets}\n"


def digest_prompt(group: Dict[str, Any], article_title: str, issues: List[str] = ()) -> str:
    body = (
        "【分节摘要任务】\n"
        f"GROUP_ID={group['id']}\n"
        f"SECTION_IDS={json.dumps(group['section_ids'], ensure_ascii=False)}\n"
        f"文章：{article_title}\n\n"
        "请阅读下面这一组原文，输出如下结构的 JSON：\n"
        f"{DIGEST_SCHEMA}\n\n"
        "要求：quotable 与 key_numbers 必须是原文逐字出现的片段；只概括本组内容。\n\n"
        "【原文】\n" + group["text"]
    )
    return body + (_retry_hint(list(issues)) if issues else "")


def spine_prompt(
    digests: List[Dict[str, Any]],
    catalog: List[Dict[str, Any]],
    meta: Dict[str, Any],
    issues: List[str] = (),
) -> str:
    compact = [
        {"group_id": d["group_id"], "section_ids": d["section_ids"], "summary": d["summary"],
         "core_claims": d["core_claims"][:4], "key_numbers": d["key_numbers"],
         "quotable": d["quotable"]}
        for d in digests
    ]
    body = (
        "【主线大纲任务】\n"
        f"TARGET_MINUTES={meta['target_minutes']}\n"
        f"MODE={meta['mode']}\n"
        f"SECTION_IDS={json.dumps([c['id'] for c in catalog], ensure_ascii=False)}\n"
        f"文章：{meta['title']}　作者：{meta.get('author') or '未填写'}\n\n"
        "下面是每组原文的摘要。请据此输出如下结构的 JSON：\n"
        f"{SPINE_SCHEMA}\n\n"
        "要求：\n"
        f"1. 章数 5～7 章（小节少于 5 个时按小节数）；每章 section_ids 连续、按原文顺序。\n"
        "2. 每个 SECTION_IDS 中的小节必须且只能出现一次：要么在某章，要么在 cuts 中并给出理由。\n"
        "3. weight 表示该章内容的相对分量（正数，可取字数比例）。\n"
        "4. golden_quotes 只能从摘要的 quotable 中选取，逐字复制。\n"
        "5. 给出 3 个开场钩子候选，取自原文最强的问题或反差。\n\n"
        "【各组摘要】\n" + json.dumps(compact, ensure_ascii=False, indent=1)
    )
    return body + (_retry_hint(list(issues)) if issues else "")
