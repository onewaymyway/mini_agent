"""prompts.py — G-2 / G-3 的提示词构造（集中管理，便于审阅与修改）。"""

from __future__ import annotations

import json
from typing import Any, Dict

SYSTEM_VISUAL = (
    "你是短剧制作的角色视觉与配音设定助手。严格只输出一个 JSON 对象，"
    "不要任何解释、前后缀或 Markdown 代码块。"
)


def character_prompt(char: Dict[str, Any], persona_excerpt: str) -> str:
    known_gender = char.get("gender")
    info = {
        "kind": "character",
        "name": (char.get("names") or [""])[0],
        "role": char.get("role"),
        "age_range": char.get("age_range"),
        "gender": known_gender,
        "description_zh": (char.get("description_zh") or "")[:1200],
        "speech_pattern": char.get("speech_pattern"),
    }
    return (
        "为下列角色补全短剧制作所需的两项设定。\n"
        "要求：\n"
        "1. gender：若输入 gender 不为空则原样返回；为空时仅依据资料推断 male 或 female，无法判断返回 unknown。\n"
        "2. visual_anchor_en：英文一句话（不超过 40 词），描述年龄段、性别、体型、发型发色、"
        "标志性服装与显著特征。只描述外观，不写人名、不写剧情。必须与资料一致，不得臆造资料中没有的标志物。\n"
        "3. voice_profile：中文一句话（不超过 60 字），描述音色、语速与语气，供 TTS 选音使用，"
        "依据 speech_pattern 与性格资料推断。\n"
        "输出格式：{\"gender\": ..., \"visual_anchor_en\": ..., \"voice_profile\": ...}\n\n"
        f"人设卡节选：\n{persona_excerpt[:1500] or '（无）'}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


def location_prompt(loc: Dict[str, Any]) -> str:
    info = {
        "kind": "location",
        "name": loc.get("name"),
        "location_type": loc.get("location_type"),
        "description_zh": (loc.get("description_zh") or "")[:1500],
    }
    return (
        "为下列地点补全短剧制作所需的视觉锚点。\n"
        "要求：visual_anchor_en 为英文一句话（不超过 40 词），描述建筑或环境类型、光照氛围、"
        "1-2 个标志性视觉细节。只描述环境，不写人物，不写剧情，不臆造资料中没有的设施。\n"
        "输出格式：{\"visual_anchor_en\": ...}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


def portrait_prompt(art_style: str, anchor: str, *, kind: str, name: str = "") -> str:
    if kind == "character":
        return (f"{art_style}. Character design reference sheet, full body, front view, "
                f"{anchor}. Plain neutral background, soft even lighting, "
                "no text, no letters, no watermark.")
    return (f"{art_style}. Establishing location art of {name}. {anchor}. "
            "Wide shot, no people, no text, no letters, no watermark.")
