"""prompts.py — G-2 / G-3 的提示词构造（集中管理，便于审阅与修改）。"""

from __future__ import annotations

import json
from typing import Any, Dict

SYSTEM_VISUAL = (
    "你是短剧制作的角色视觉与配音设定助手。严格只输出一个 JSON 对象，"
    "不要任何解释、前后缀或 Markdown 代码块。"
)


# 剧本是纯文本，不是 JSON。第 1 章曾因剧本调用沿用 SYSTEM_VISUAL（要求只输出 JSON 对象），
# 模型把剧本包进 {"script": "..."}，导致校验在第 1 行失败（见计划文档 14.13）。
SYSTEM_SCRIPT = (
    "你是短剧编剧助手。只输出剧本正文纯文本：第一行必须是“### 场次 N｜…”场次标题，"
    "不要使用 JSON，不要使用 Markdown 代码块，不要任何解释、前言或后记。"
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


def location_prompt(loc: Dict[str, Any], parent: Dict[str, Any] | None = None) -> str:
    """补全地点的视觉锚点。parent 不为空时，这是区域（parent）里的一个具体场景点：继承区域风格，再写本场所细节。"""
    info = {
        "kind": "location",
        "name": loc.get("name"),
        "location_type": loc.get("location_type"),
        "description_zh": (loc.get("description_zh") or "")[:1500],
    }
    if parent:
        kind = {"interior": "内景（室内）", "exterior": "外景（室外）"}.get(loc.get("spot_kind") or "", "")
        info["parent"] = {"name": parent.get("name"), "description_zh": (parent.get("description_zh") or "")[:600],
                          "visual_anchor_en": parent.get("visual_anchor_en")}
        info["spot_kind"] = loc.get("spot_kind")
        return (
            f"为下列具体场景点补全短剧制作所需的视觉锚点。它位于「{parent.get('name')}」之中"
            + (f"，是{kind}" if kind else "") + "。\n"
            "要求：visual_anchor_en 为英文一句话（不超过 45 词）：先沿用所属区域的风格关键词（建筑风格、色调、时代、"
            "光照氛围，见 parent.visual_anchor_en），再写本场所自己的 2-3 个标志性细节。"
            "内景只写室内（家具、陈设、光源、窗外一笔即可），不写天空；外景只写街道/空间与周边建筑。"
            "只描述环境，不写人物，不写剧情，不臆造资料与上下文中没有的设施。\n"
            "输出格式：{\"visual_anchor_en\": ...}\n\n"
            f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
        )
    return (
        "为下列地点补全短剧制作所需的视觉锚点。\n"
        "要求：visual_anchor_en 为英文一句话（不超过 40 词），描述建筑或环境类型、光照氛围、"
        "1-2 个标志性视觉细节。只描述环境，不写人物，不写剧情，不臆造资料中没有的设施。\n"
        "输出格式：{\"visual_anchor_en\": ...}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


def prop_prompt(prop: Dict[str, Any]) -> str:
    """道具的视觉锚点（G-2 补全 / C-5 草拟共用）。道具是静物：只描述物品本身。"""
    info = {
        "kind": "prop",
        "name": prop.get("name"),
        "description_zh": (prop.get("description_zh") or "")[:1500],
    }
    return (
        "为下列道具补全短剧制作所需的视觉锚点。\n"
        "要求：visual_anchor_en 为英文一句话（不超过 40 词），描述物品的类别、材质、颜色、尺寸感与"
        "1-2 个显著细节。只描述物品本身，不写人物、不写剧情，不臆造资料中没有的特征。\n"
        "输出格式：{\"visual_anchor_en\": ...}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


def look_prompt(name: str, base_anchor: str, label: str, context_lines: list) -> str:
    """角色的一个新形象（换装/受伤/伪装等）：在基础外观上只改变与该形象相关的部分，输出完整锚点。"""
    info = {"task": "look", "name": name, "label": label, "base_anchor": base_anchor,
            "context": context_lines[:6]}
    return (
        f"TASK=look\n角色“{name}”在剧本中有一个不同于基础外观的形象“{label}”。请起草这个形象的设定。\n"
        "要求：visual_anchor_en 是完整的英文一句话（不超过 40 词）：保留基础外观里不变的特征（脸型、发型发色、体型、年龄段），"
        "只改变与该形象相关的部分（服装、伤痕、道具等），必须与剧本上下文一致，不得臆造。\n"
        "只输出 JSON：{\"description_zh\": str（中文一句话）, \"visual_anchor_en\": str}\n\n"
        f"基础外观：{base_anchor or '（无）'}\n剧本上下文：{json.dumps(context_lines[:6], ensure_ascii=False)}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


def portrait_prompt(art_style: str, anchor: str, *, kind: str, name: str = "", spot_kind: str | None = None) -> str:
    if kind == "prop":
        return (f"{art_style}. Prop reference image of {name}. {anchor}. "
                "Single object centered, plain neutral background, soft even lighting, "
                "no people, no hands, no text, no letters, no watermark.")
    if kind == "character":
        return (f"{art_style}. Character design reference sheet, full body, front view, "
                f"{anchor}. Plain neutral background, soft even lighting, "
                "no text, no letters, no watermark.")
    if spot_kind == "interior":
        return (f"{art_style}. Interior view of {name}. {anchor}. "
                "Wide interior shot showing the whole room, no people, no text, no letters, no watermark.")
    if spot_kind == "exterior":
        return (f"{art_style}. Exterior view of {name}. {anchor}. "
                "Wide street-level shot, no people, no text, no letters, no watermark.")
    return (f"{art_style}. Establishing location art of {name}. {anchor}. "
            "Wide shot, no people, no text, no letters, no watermark.")


# ---------------------------------------------------------------- Phase 2：章节级

SCRIPT_RULES = """剧本格式（严格遵守）：
1. 按场划分，每场一行标题：`### 场次 N｜地点·时间（内景/外景）`，场次号从 1 连续递增；地点时间无法确定时写“未知”，不得编造。
2. 旁白行以“旁白：”开头，内容为环境、动作、神态、心理及间接转述，可适度口播化改写，不得新增情节。
3. 对白行格式为“角色名：台词”，台词必须从原文逐字摘录，不得改写。
4. 原文中没有直接引语的转述一律写成旁白，不得标为对白。
5. 每个非空行都必须是场次标题、“旁白：…”或“角色名：…”三者之一。
6. 只输出剧本正文纯文本：不要 JSON，不要代码块，不要解释；第一行必须是场次标题。"""

SCRIPT_EXAMPLE = """输出格式示例（仅示意格式，内容以原文为准）：
### 场次 1｜铁城街道·清晨（外景）
旁白：……
角色名：……
### 场次 2｜公寓·清晨（内景）
旁白：……"""


def script_budget(target_chars: int) -> Dict[str, int]:
    """口播字数预算：目标 ±20%，以及建议的场次上限。"""
    return {"target": target_chars, "lo": int(round(target_chars * 0.8)),
            "hi": int(round(target_chars * 1.2)), "max_scenes": max(2, int(round(target_chars / 180)))}


def _length_requirement(source_len: int, target_chars: int) -> str:
    """篇幅说明。target_chars 为 0 表示不限制时长：不给字数预算，按原文实际情况改编。"""
    if not target_chars:
        return (f"【篇幅】原文约 {source_len} 字。本章不限制时长与字数：请按原文的实际情况完整改编，"
                "保留原文的主要情节、关键对白与必要的场景，不要为了凑短而大幅删减，也不要无故拉长。")
    b = script_budget(target_chars)
    return (f"【篇幅要求（最重要）】原文约 {source_len} 字；本集口播（旁白+对白，不含场次标题）目标约 {b['target']} 字，"
            f"允许 {b['lo']}–{b['hi']} 字，绝不能超过 {b['hi']} 字。必须大幅压缩：只保留主线与关键对白，"
            f"删减次要描写与重复的心理活动，场次建议不超过 {b['max_scenes']} 场。"
            "写完后自行估算口播字数，超出就继续删减。")


def script_prompt(chapter_no: int, title: str, text: str, target_chars: int) -> str:
    """target_chars：本章口播（旁白+对白）的目标总字数，由 目标时长 × 语速 得出。
    原文远长于目标时长，必须压缩：保留主线与关键对白，删减次要描写。

    原文只出现一次（RULE_INPUT 不再重复携带 text，离线 RuleTextLLM 从“原文：”之后取）。"""
    info = {"task": "script", "chapter_no": chapter_no, "title": title, "target_chars": target_chars}
    return (
        f"TASK=script\n将第 {chapter_no} 章《{title}》改编为一集短剧的剧本。\n\n"
        f"{_length_requirement(len(text), target_chars)}\n\n"
        f"{SCRIPT_RULES}\n\n{SCRIPT_EXAMPLE}\n\n"
        f"原文：\n{text}\n\nRULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


def script_fix_prompt(chapter_no: int, title: str, text: str, target_chars: int,
                      prev_script: str, issues: list) -> str:
    """校验失败后的修正请求（仅在项目选项 script_max_retries>0 时使用）。"""
    info = {"task": "script_fix", "chapter_no": chapter_no, "title": title, "target_chars": target_chars}
    listed = "\n".join(f"- {i['message']}" for i in issues if i.get("level") == "error") or "- （无）"
    return (
        f"TASK=script_fix\n上一版第 {chapter_no} 章《{title}》剧本校验未通过，请修正后输出完整的新剧本。\n\n"
        f"校验未通过的问题：\n{listed}\n\n"
        f"{_length_requirement(len(text), target_chars)}；对白必须逐字来自原文。\n\n"
        f"{SCRIPT_RULES}\n\n上一版剧本：\n{prev_script}\n\n"
        f"原文：\n{text}\n\nRULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )


PROP_RULE = ("9. prop_names 列出该镜头画面中需要特写或对剧情有意义的关键道具（如信物、武器、证件、关键文件），"
             "名称优先使用 props 库中已有的名称，库里没有时直接写剧本里的叫法；不要列出无关紧要的日常物品，"
             "每个镜头最多 3 个，没有就返回空数组；\n")


LOOK_RULE = ("11. character_looks（可选）：只有当某个角色在该镜头里的服装/外观与基础外观明显不同时"
             "（换装、受伤、伪装、年龄变化），才列出 [{\"character_id\": id, \"look\": 形象标签}]；"
             "优先沿用 characters[].looks 里已有的标签，新标签用简短中文（如“战斗装”“病中”）；"
             "外观没有变化就不要写，绝大多数镜头不需要；\n")


LOCATION_RULE_PLAIN = ("4. location_id 从 locations 中选；剧本地点不在 locations 中时，location_id 置 null，并在 location_name 中填写剧本里的地点名（不要编造）；场景切换时必须换镜头；\n")

LOCATION_RULE_SPOTS = (
    "4. 地点（逐镜头判断）：先看剧本场次标题和镜头内容，确定这场戏**真正发生的具体场所**（办公室、街道、卧室、码头……）。\n"
    "   a) locations 里有这个具体场所 → location_id 选它；\n"
    "   b) locations 里只有它所在的城市/区域（level=area），或别的对不上的地点 → 现有地点**不能直接使用**，要规划新场景点："
    "location_id 置 null，location_name 写具体场所名（取剧本里的叫法，不要编造），parent_location_id 填所属区域的 id"
    "（找不到所属区域就置 null），spot_kind 填 interior（室内）或 exterior（室外），location_reason 用一句话说明为什么现有地点不能用；\n"
    "   c) 区域级地点只用于纯粹的城市/区域全景镜头，不得拿它代替具体场所；\n"
    "   d) 同一场所在本章内只用同一个名字；只有剧情持续发生的场所才算新场景点，路过的背景不算；场景切换时必须换镜头；\n")

SPOT_FIELDS = ('\"location_name\": str|null, \"parent_location_id\": str|null, \"spot_kind\": \"interior|exterior|null\", '
               '\"location_reason\": str, ')


def _spot_locs(locations: list) -> list:
    out = []
    for l in locations:
        e = {"id": l["id"], "name": l["name"], "level": l.get("level") or "area"}
        if l.get("parent_id"):
            e["parent_id"] = l["parent_id"]
        if l.get("location_type"):
            e["type"] = l["location_type"]
        out.append(e)
    return out


def shots_prompt(script: str, characters: list, locations: list, *, props: list | None = None,
                 looks_enabled: bool = False,
                 max_chars_per_shot: int = 0, expected_shots: int = 0, min_chars_per_shot: int = 0,
                 spots_enabled: bool = False) -> str:
    chars = [{"id": c["id"], "name": (c.get("names") or [""])[0], "gender": c.get("gender"),
              **({"looks": [l["label"] for l in c.get("looks") or []]} if looks_enabled else {})}
             for c in characters]
    locs = _spot_locs(locations) if spots_enabled else [{"id": l["id"], "name": l["name"]} for l in locations]
    info = {"task": "shots", "characters": chars, "locations": locs,
            "props": [{"id": p["id"], "name": p.get("name")} for p in props or []]}
    return (
        "TASK=shots\n"
        "把剧本拆分为小场景镜头（shots）。每个镜头的口播约 4–12 秒（建议值，差几秒可以接受），包含若干 blocks。\n"
        "要求：\n"
        "1. blocks 的 type 为 narration（旁白）或 dialogue（对白）；\n"
        "2. dialogue 的 text 必须是剧本对白行中的连续原文：可以只截取原句中连续的一段，但不能删减中间的字、"
        "不能改写、不能把不相邻的句子拼在一起。原句太长时，拆成多个镜头，每个镜头各取一段连续原文；\n"
        "3. dialogue 的 speaker_id 从 characters 中选；若剧本中的说话人不在 characters 中，speaker_id 置 null 并填 speaker_name；\n"
        + (LOCATION_RULE_PLAIN if not spots_enabled else LOCATION_RULE_SPOTS) +
        "5. visual_hint_zh 为中文画面描述，visual_hint_en 为对应英文画面描述（不写人名，只写动作与构图）；\n"
        + (f"6. 每个镜头的口播（旁白+对白）建议在 {min_chars_per_shot}–{max_chars_per_shot} 字左右（约 4–12 秒，差几秒可以接受）；"
           f"一场戏通常需要多个镜头，不要把整场戏写成一个镜头。整章预计约 {expected_shots} 个镜头。\\n"
           if max_chars_per_shot else "")
        + "7. 同一场次内相邻镜头的 visual_hint 必须不同：景别（远景/中景/近景/特写）、机位或主体动作至少一项不同，"
          "且描述的是该镜头自己的口播内容，不要把整场戏的画面复述到每个镜头里；拆分出的镜头画面必须各不相同；\n"
          "8. character_ids 列出该镜头画面中出现的所有角色，包括画面里出现但没有台词的角色（例如旁白描写林远的动作，就要包含林远）；\n"
          + PROP_RULE + (LOOK_RULE if looks_enabled else "") +
          "10. 只输出 JSON：{\"shots\": [{\"scene_no\": int, \"location_id\": str, "
        + (SPOT_FIELDS if spots_enabled else "") + "\"character_ids\": [..], \"prop_names\": [..], "
        "\"blocks\": [{\"type\": ..., \"text\": ..., \"speaker_id\": ..., \"speaker_name\": ...}], "
        "\"visual_hint_zh\": str, \"visual_hint_en\": str}]}\n\n"
        f"剧本：\n{script}\n\nRULE_INPUT={json.dumps({**info, 'script': script}, ensure_ascii=False)}"
    )


def shots_fix_prompt(script: str, characters: list, locations: list, prev_output: str, issues: list, *,
                    props: list | None = None, looks_enabled: bool = False, max_chars_per_shot: int = 0, expected_shots: int = 0, min_chars_per_shot: int = 0,
                    spots_enabled: bool = False) -> str:
    """镜头规划校验失败后的修正请求：列出全部问题（含剧本里最接近的原句），要求输出完整的新 JSON。"""
    chars = [{"id": c["id"], "name": (c.get("names") or [""])[0], "gender": c.get("gender")}
             for c in characters]
    locs = _spot_locs(locations) if spots_enabled else [{"id": l["id"], "name": l["name"]} for l in locations]
    info = {"task": "shots_fix", "characters": chars, "locations": locs,
            "props": [{"id": p["id"], "name": p.get("name")} for p in props or []]}
    listed = "\n".join(f"- {i['message']}" for i in issues[:12])
    more = f"\n（另有 {len(issues) - 12} 个同类问题未列出）" if len(issues) > 12 else ""
    limit = (f"每个镜头的口播字数必须在 {min_chars_per_shot}–{max_chars_per_shot} 字之间（4–12 秒，硬要求）；"
             if max_chars_per_shot else "")
    return (
        "TASK=shots_fix\n上一次的镜头规划没有通过校验。请逐条修正下列问题，其余内容尽量保持不变，"
        "输出完整的新镜头规划，格式与之前完全相同，只输出 JSON。\n\n"
        f"校验未通过的问题：\n{listed}{more}\n\n"
        "修正原则：\n"
        "1. 对白 text 必须是剧本对白行中的连续原文（可截取连续的一段，不能删字、改字、拼接不相邻的句子）；"
        "做不到时，把这句改为旁白或拆成多个镜头各取一段连续原文；\n"
        f"2. {limit}同一场次内相邻镜头的画面描述必须不同；character_ids 要列全画面中出现的角色；\n"
        "3. dialogue 的 speaker_id 从 characters 中选，不在其中时 speaker_id 置 null 并填 speaker_name；\n"
        "4. 保留每个镜头的 prop_names（关键道具名称数组，没有就是空数组）"
        + ("；保留需要的 character_looks。\n" if looks_enabled else "。\n")
        + (LOCATION_RULE_SPOTS + "   输出字段：" + SPOT_FIELDS + "\n" if spots_enabled else "")
        + "\n" + f"上一次的输出：\n{prev_output}\n\n"
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
