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


# ---------------------------------------------------------------- Phase 2：章节级

SCRIPT_RULES = """剧本格式（严格遵守）：
1. 按场划分，每场一行标题：`### 场次 N｜地点·时间（内景/外景）`，地点时间无法确定时写“未知”，不得编造。
2. 旁白行以“旁白：”开头，内容为环境、动作、神态、心理及间接转述，可适度口播化改写，不得新增情节。
3. 对白行格式为“角色名：台词”，台词必须从原文逐字摘录，不得改写。
4. 原文中没有直接引语的转述一律写成旁白，不得标为对白。
5. 只输出剧本正文，不要解释。"""


def script_prompt(chapter_no: int, title: str, text: str, target_chars: int) -> str:
    """target_chars：本章口播（旁白+对白）的目标总字数，由 目标时长 × 语速 得出。
    原文远长于目标时长，必须压缩：保留主线与关键对白，删减次要描写。"""
    info = {"task": "script", "chapter_no": chapter_no, "title": title, "target_chars": target_chars}
    return (
        f"TASK=script\n将第 {chapter_no} 章《{title}》改编为一集短剧的剧本。\n{SCRIPT_RULES}\n"
        f"6. 口播总字数（旁白+对白，不含场次标题）目标约 {target_chars} 字，允许上下浮动 20%，"
        "超出时删减次要情节与描写，优先保留主线与关键对白。\n\n"
        f"原文：\n{text}\n\nRULE_INPUT={json.dumps({**info, 'text': text}, ensure_ascii=False)}"
    )


def shots_prompt(script: str, characters: list, locations: list) -> str:
    chars = [{"id": c["id"], "name": (c.get("names") or [""])[0], "gender": c.get("gender")}
             for c in characters]
    locs = [{"id": l["id"], "name": l["name"]} for l in locations]
    info = {"task": "shots", "characters": chars, "locations": locs}
    return (
        "TASK=shots\n"
        "把剧本拆分为小场景镜头（shots）。每个镜头 4–10 秒口播内容，包含若干 blocks。\n"
        "要求：\n"
        "1. blocks 的 type 为 narration（旁白）或 dialogue（对白）；\n"
        "2. dialogue 的 text 必须逐字来自剧本对应对白行，不得改写；\n"
        "3. dialogue 的 speaker_id 从 characters 中选；若剧本中的说话人不在 characters 中，speaker_id 置 null 并填 speaker_name；\n"
        "4. location_id 从 locations 中选，场景切换时必须换镜头；\n"
        "5. visual_hint_zh 为中文画面描述，visual_hint_en 为对应英文画面描述（不写人名，只写动作与构图）；\n"
        "6. 只输出 JSON：{\"shots\": [{\"scene_no\": int, \"location_id\": str, \"character_ids\": [..], "
        "\"blocks\": [{\"type\": ..., \"text\": ..., \"speaker_id\": ..., \"speaker_name\": ...}], "
        "\"visual_hint_zh\": str, \"visual_hint_en\": str}]}\n\n"
        f"剧本：\n{script}\n\nRULE_INPUT={json.dumps({**info, 'script': script}, ensure_ascii=False)}"
    )


def entity_draft_prompt(name: str, context_lines: list, kind_hint: str = "character") -> str:
    info = {"task": "entity", "name": name, "context": context_lines[:6]}
    return (
        f"TASK=entity\n剧本中出现了一个原人设库没有的角色“{name}”。根据以下剧本台词推断最小必要设定，"
        "只补充外观与声音所需字段，不得臆造身份与剧情。\n"
        "只输出 JSON：{\"gender\": \"male|female|unknown\", \"age_range\": \"child|teen|youth|middle_aged|elderly|unknown\", "
        "\"description_zh\": str, \"visual_anchor_en\": str（40 词以内）, \"voice_profile\": str（中文一句话）}\n\n"
        f"台词上下文：{json.dumps(context_lines[:6], ensure_ascii=False)}\n\nRULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )
