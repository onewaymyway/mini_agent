"""world_forge/generate.py — 模型任务：提示词组装、JSON 解析与结构校验。

所有任务返回已校验的 dict（或字符串）。结构不合格时带错误信息重试一次，仍失败则抛 GenerationError，
调用方据此决定是否放弃本次结果（不写入任何内容）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

SYSTEM = (
    "你是一位架空世界的设定编辑与故事作者。默认使用中文。"
    "凡要求输出 JSON 时，只输出一个合法 JSON 对象，不要任何解释、不要 Markdown 代码块。"
)


class GenerationError(RuntimeError):
    pass


def build_prompt(task: str, context: Dict[str, Any], instruction: str) -> str:
    ctx = json.dumps(context, ensure_ascii=False, indent=2)
    return (f"TASK: {task}\n<<CONTEXT>>\n{ctx}\n<<END>>\n\n{instruction.strip()}\n")


def parse_json(text: str) -> Any:
    """容错解析：去掉代码围栏，截取首个 JSON 对象；标准解析失败时尝试 json_repair。"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    start = min([i for i in (s.find("{"), s.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("输出中没有 JSON")
    body = s[start:]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    try:
        import json_repair  # type: ignore

        return json_repair.loads(body)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"JSON 无法解析：{exc}") from exc


def _req_list(d: Dict[str, Any], key: str, errs: List[str]) -> List[Any]:
    v = d.get(key)
    if v is None:
        d[key] = []
        return d[key]
    if not isinstance(v, list):
        errs.append(f"{key} 必须是数组")
        return []
    return v


def _validate_charter(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not isinstance(d.get("hard_rules"), list) or not d["hard_rules"]:
        errs.append("hard_rules 必须是非空数组")
    _req_list(d, "initial_entities", errs)
    _req_list(d, "initial_characters", errs)
    return errs


def _validate_advance(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    for k in ("new_characters", "entities", "relations", "canon_updates", "events"):
        _req_list(d, k, errs)
    for ev in d["events"]:
        if not isinstance(ev, dict) or not ev.get("title"):
            errs.append("每个事件必须有 title")
            break
    return errs


def _validate_arc(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not d.get("title"):
        errs.append("缺少 title")
    if not isinstance(d.get("acts"), list) or not d["acts"]:
        errs.append("acts 必须是非空数组")
    return errs


def _validate_storyboard(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    pages = d.get("pages")
    if not isinstance(pages, list) or not pages:
        return ["pages 必须是非空数组"]
    for pg in pages:
        panels = pg.get("panels") if isinstance(pg, dict) else None
        if not isinstance(panels, list) or not (1 <= len(panels) <= 6):
            errs.append("每页 panels 必须为 1–6 格")
            break
        for p in panels:
            if not p.get("visual_en"):
                errs.append("每格必须有 visual_en（英文画面描述）")
                break
    return errs


def call_json(llm, task: str, context: Dict[str, Any], instruction: str,
              validate: Callable[[Dict[str, Any]], List[str]]) -> Dict[str, Any]:
    prompt = build_prompt(task, context, instruction)
    last_err = ""
    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + f"\n\n上一次输出不合格：{last_err}。请只输出修正后的 JSON。"
        raw = llm.ask(p, system=SYSTEM)
        try:
            data = parse_json(raw)
        except ValueError as exc:
            last_err = str(exc)
            continue
        if not isinstance(data, dict):
            last_err = "顶层必须是对象"
            continue
        errs = validate(data)
        if not errs:
            return data
        last_err = "；".join(errs)
    raise GenerationError(f"{task} 生成失败：{last_err}")


def charter_draft(llm, *, concept: str, title: str = "", genre: str = "", tone: str = "") -> Dict[str, Any]:
    return call_json(llm, "charter", {"concept": concept, "title": title, "genre": genre, "tone": tone},
                     "请根据概念写出世界宪章草案，JSON 字段：title, genre, tone, summary, hard_rules（字符串数组，"
                     "写明不可违背的世界规则）, initial_entities（数组，每项含 kind(地理/势力/物种/技术/魔法/历史事件/物品/组织/其它), "
                     "name, summary, attrs）, initial_characters（数组，每项含 name, role(主角/重要配角/配角/次要), "
                     "faction_name, motive, summary）。给出 3–6 个实体与 2–4 个角色。", _validate_charter)


def gap_fill_draft(llm, *, context: Dict[str, Any], target_name: str, gap_label: str) -> Dict[str, Any]:
    ctx = {**context, "target_name": target_name, "gap": gap_label}
    return call_json(llm, "gap_fill", ctx,
                     "请为缺口补全设定，与宪章和已有设定保持一致，不要与已有实体重名。JSON 字段：summary（字符串）、"
                     "attrs（对象，可为空）。", lambda d: [] if isinstance(d.get("summary"), str) else ["缺少 summary"])


def advance_draft(llm, *, context: Dict[str, Any]) -> Dict[str, Any]:
    return call_json(llm, "advance", context,
                     "请推进世界历史一个时间片。规则：1) 事件必须遵守宪章硬规则，违反时在 violates_rules 中写明；"
                     "2) 只能引用 context 中已存在的实体或角色（按名字），需要新人物请放入 new_characters；"
                     "3) 已死亡角色不得参与事件；4) 每次生成 1–3 个事件，事件之间有因果关系更好。"
                     "JSON 字段：new_characters（数组：name, role, faction_name, motive, summary），"
                     "entities（数组：kind, name, summary, attrs）, relations（数组：src_name, dst_name, type, note），"
                     "canon_updates（数组：target_name, target_type(entity|character), payload 对象），"
                     "events（数组：title, summary, involves（名字数组）, caused_by（事件标题数组，可空）, tags, "
                     "changes（数组：op(update_entity|update_character|create_relation), target_name, payload）, "
                     "violates_rules（字符串数组，无则为空））。", _validate_advance)


def arc_draft(llm, *, context: Dict[str, Any]) -> Dict[str, Any]:
    return call_json(llm, "arc", context,
                     "请把给定事件整理为一条小故事线。JSON 字段：title, premise（一句话前提）, protagonist_names"
                     "（字符串数组）, acts（数组：title, summary）, ending。acts 为 2–4 幕。", _validate_arc)


def short_draft(llm, *, context: Dict[str, Any], form: str) -> str:
    instr = ("请创作一篇中文短篇小说，约 2000–4000 字，有明确的开头、冲突与结尾，直接输出正文（Markdown），"
             "不要输出解释。" if form == "novel" else
             "请写一篇中文故事说明与分镜素材，直接输出正文（Markdown）。")
    raw = llm.ask(build_prompt("short", context, instr), system=SYSTEM)
    text = (raw or "").strip()
    if not text:
        raise GenerationError("短篇生成为空")
    return text


def storyboard_draft(llm, *, context: Dict[str, Any]) -> Dict[str, Any]:
    return call_json(llm, "storyboard", context,
                     "请把故事改写为漫画分镜。JSON 字段：pages（数组，每页：no, panels）；panels 为 1–6 格，每格："
                     "narration（旁白，可空）, dialogue（数组：speaker, text）, characters（出场角色名数组）, "
                     "visual_en（英文画面描述，写清构图、人物动作与场景，不要要求图中出现文字）。", _validate_storyboard)


def portrait_draft(llm, *, context: Dict[str, Any]) -> Dict[str, Any]:
    return call_json(llm, "portrait", context,
                     "请为角色写一段英文立绘描述，用于图像模型。JSON 字段：visual_en（英文，描述外貌、服饰、"
                     "气质与年龄感，单人半身像，纯色背景，不要文字）。",
                     lambda d: [] if isinstance(d.get("visual_en"), str) and d["visual_en"].strip()
                     else ["缺少 visual_en"])


def relation_draft(llm, *, context: Dict[str, Any], target_name: str, target_kind: str,
                   options: List[str]) -> Dict[str, Any]:
    return call_json(llm, "relations", {**context, "target_name": target_name, "target_kind": target_kind,
                                        "options": options},
                     "请为目标实体建议 1–3 条与世界其它实体的关系，只能从 options 中选择对象（用名字）。"
                     "JSON 字段：relations（数组：name（必须是 options 中的名字）, type（关系类型，如 统治/贸易/敌对/依附）, "
                     "note（一句话说明））。",
                     lambda d: [] if isinstance(d.get("relations"), list) else ["relations 必须是数组"])


def overview_draft(llm, *, context: Dict[str, Any]) -> str:
    instr = ("请为读者写一篇中文的世界观背景介绍，约 800–1500 字，用 Markdown 小标题分段，依次涵盖："
             "世界概况与基调、历史脉络、主要地理与势力、社会与技术/魔法体系（遵守硬规则）、当前局势、"
             "近期重大事件、值得关注的人物。语言生动但要忠于已有设定，不要编造与设定冲突的内容，直接输出正文。")
    raw = llm.ask(build_prompt("overview", context, instr), system=SYSTEM)
    text = (raw or "").strip()
    if not text:
        raise GenerationError("世界观简介生成为空")
    return text
