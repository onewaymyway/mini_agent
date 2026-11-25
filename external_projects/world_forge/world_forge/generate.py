"""world_forge/generate.py — 模型任务：提示词组装、JSON 解析与结构校验。

所有任务返回已校验的 dict（或字符串）。结构不合格时带错误信息重试一次，仍失败则抛 GenerationError，
调用方据此决定是否放弃本次结果（不写入任何内容）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from .comic_layout import CAMERAS, GAZES, NARRATION_MAX

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


SHOTS = ("远景", "中景", "近景", "特写")
BUBBLE_POSITIONS = ("左上", "右上", "左下", "右下", "中上", "中下")


DIALOGUE_MAX_CHARS = 40          # 单句对白的硬上限（提示词要求 24 字，超过 40 字视为不合格）
KINDS = ("speech", "thought", "shout")
SIDES = ("左", "中", "右")


def normalize_panel(p: Dict[str, Any]) -> List[str]:
    """补齐格子的缺省字段（原地修改），返回错误列表。"""
    errs: List[str] = []
    if not p.get("visual_en") or not isinstance(p.get("visual_en"), str):
        errs.append("每格必须有 visual_en（英文画面描述）")
    if p.get("shot") not in SHOTS:
        p["shot"] = "中景"
    try:
        span = int(p.get("span") or 1)
    except (TypeError, ValueError):
        span = 1
    p["span"] = 2 if span >= 2 else 1
    dialogue = p.get("dialogue") or []
    if not isinstance(dialogue, list):
        dialogue = []
    fixed = []
    for i, dl in enumerate(dialogue):
        if not isinstance(dl, dict) or not dl.get("text"):
            continue
        text = str(dl["text"]).strip()
        if len(text) > DIALOGUE_MAX_CHARS:
            errs.append(f"对白过长（{len(text)} 字，上限 {DIALOGUE_MAX_CHARS} 字）：{text[:12]}…")
        pos = dl.get("position")
        if pos not in BUBBLE_POSITIONS:
            pos = ("右上", "左上", "右下", "左下")[i % 4]   # 缺省时按序分散到四角
        side = dl.get("speaker_side")
        if side not in SIDES:
            side = {"左上": "左", "左下": "左", "右上": "右", "右下": "右"}.get(pos, "中")
        kind = dl.get("kind") if dl.get("kind") in KINDS else "speech"
        fixed.append({"speaker": str(dl.get("speaker") or ""), "text": text, "position": pos,
                      "speaker_side": side, "kind": kind})
    p["dialogue"] = fixed
    p["characters"] = [str(c) for c in (p.get("characters") or []) if c]
    p["narration"] = str(p.get("narration") or "")
    p["sfx"] = str(p.get("sfx") or "").strip()
    p["location"] = str(p.get("location") or "").strip()
    # P5：镜头表字段
    p["camera"] = p.get("camera") if p.get("camera") in CAMERAS else "平视"
    gaze = p.get("gaze") if isinstance(p.get("gaze"), dict) else {}
    p["gaze"] = {str(k): v for k, v in gaze.items() if v in GAZES}
    names = set(p["characters"])
    order = [str(n) for n in (p.get("screen_order") or []) if str(n) in names]
    p["screen_order"] = order if len(order) >= 2 else []
    p["action"] = str(p.get("action") or "").strip()
    p["composition"] = str(p.get("composition") or "").strip()
    p["connects_prev"] = str(p.get("connects_prev") or "").strip()[:40]
    if len(p["narration"]) > NARRATION_MAX:
        errs.append(f"旁白过长（{len(p['narration'])} 字，上限 {NARRATION_MAX} 字）：{p['narration'][:10]}…")
    p["emotion"] = str(p.get("emotion") or "").strip()
    return errs


def _validate_beats(d: Dict[str, Any], expect: Optional[int] = None) -> List[str]:
    pages = d.get("pages")
    if not isinstance(pages, list) or not pages:
        return ["pages 必须是非空数组"]
    if expect is not None and len(pages) != expect:
        return [f"pages 必须正好 {expect} 页（当前 {len(pages)} 页）"]
    for i, pg in enumerate(pages, start=1):
        if not isinstance(pg, dict) or not str(pg.get("purpose") or "").strip():
            return [f"第 {i} 页缺少 purpose（本页叙事作用）"]
        if not str(pg.get("focus") or "").strip():
            return [f"第 {i} 页缺少 focus（画面焦点）"]
        pg["no"] = i
        pg["purpose"] = str(pg["purpose"]).strip()
        pg["focus"] = str(pg["focus"]).strip()
        pg["hook"] = str(pg.get("hook") or "").strip()
        pg["palette"] = str(pg.get("palette") or "").strip()
    return []


def check_axis(pages: List[Dict[str, Any]]) -> List[str]:
    """越轴检查：相邻出现的两个以上角色，左右顺序翻转而未标注反打，即判为越轴。"""
    errs: List[str] = []
    prev_order: List[str] = []
    for pg in pages:
        for p in pg.get("panels") or []:
            order = p.get("screen_order") or []
            if order and prev_order:
                common = [n for n in order if n in prev_order]
                if len(common) >= 2:
                    cur = [n for n in order if n in common]
                    before = [n for n in prev_order if n in common]
                    if cur != before and p.get("camera") != "反打":
                        errs.append(f"越轴：第 {pg.get('no')} 页的 {'、'.join(common)} 左右位置与上一格相反，"
                                    "但未标注 camera=反打（对话双方交替时才允许翻转）")
            if order:
                prev_order = order
    return errs


def _validate_storyboard(d: Dict[str, Any], expect: Optional[int] = None) -> List[str]:
    errs: List[str] = []
    pages = d.get("pages")
    if not isinstance(pages, list) or not pages:
        return ["pages 必须是非空数组"]
    if expect is not None and len(pages) != expect:
        return [f"pages 必须正好 {expect} 页（节拍表为 {expect} 页，当前 {len(pages)} 页）"]
    for pg in pages:
        panels = pg.get("panels") if isinstance(pg, dict) else None
        if not isinstance(panels, list) or not (1 <= len(panels) <= 5):
            errs.append("每页 panels 必须为 1–5 格")
            break
        for p in panels:
            if not isinstance(p, dict):
                errs.append("每格必须是对象")
                break
            errs.extend(normalize_panel(p))
        if errs:
            break
        # 一页内的格子宽度：整行格（span=2）最多占一行，连续的半行格两两配对
        pg["no"] = int(pg.get("no") or 1)
    if not errs:
        errs.extend(check_axis(pages))
    return errs


def _note(log, msg: str) -> None:
    if log is not None:
        log.log("retry", msg, level="warn")


def call_json(llm, task: str, context: Dict[str, Any], instruction: str,
              validate: Callable[[Dict[str, Any]], List[str]], log=None) -> Dict[str, Any]:
    prompt = build_prompt(task, context, instruction)
    last_err = ""
    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + f"\n\n上一次输出不合格：{last_err}。请只输出修正后的 JSON。"
        raw = llm.ask(p, system=SYSTEM)
        try:
            data = parse_json(raw)
        except ValueError as exc:
            last_err = str(exc)
            _note(log, f"{task} 第 {attempt + 1} 次输出无法解析：{last_err}" +
                  ("，将重试一次" if attempt == 0 else ""))
            continue
        if not isinstance(data, dict):
            last_err = "顶层必须是对象"
            _note(log, f"{task} 第 {attempt + 1} 次输出顶层不是对象" + ("，将重试一次" if attempt == 0 else ""))
            continue
        errs = validate(data)
        if not errs:
            return data
        last_err = "；".join(errs)
        _note(log, f"{task} 第 {attempt + 1} 次输出结构不合格：{last_err}" +
              ("，将重试一次" if attempt == 0 else ""))
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


def advance_draft(llm, *, context: Dict[str, Any], log=None) -> Dict[str, Any]:
    return call_json(llm, "advance", context,
                     "请推进世界历史一个时间片。规则：1) 事件必须遵守宪章硬规则，违反时在 violates_rules 中写明；"
                     "2) 只能引用 context 中已存在的实体或角色（按名字），需要新人物请放入 new_characters；"
                     "3) 已死亡角色不得参与事件；4) 每次生成 1–3 个事件，事件之间有因果关系更好。"
                     "JSON 字段：new_characters（数组：name, role, faction_name, motive, summary），"
                     "entities（数组：kind, name, summary, attrs）, relations（数组：src_name, dst_name, type, note），"
                     "canon_updates（数组：target_name, target_type(entity|character), payload 对象），"
                     "events（数组：title, summary, involves（名字数组）, caused_by（事件标题数组，可空）, tags, "
                     "changes（数组：op(update_entity|update_character|create_relation), target_name, payload）, "
                     "violates_rules（字符串数组，无则为空））。", _validate_advance, log=log)


def arc_draft(llm, *, context: Dict[str, Any], log=None) -> Dict[str, Any]:
    return call_json(llm, "arc", context,
                     "请把给定事件整理为一条小故事线。若 context.focus 存在，则以该主体的视角展开：它是主角，"
                     "情节围绕它的经历、选择与变化。JSON 字段：title, premise（一句话前提）, protagonist_names"
                     "（字符串数组，若有 focus 则第一个为 focus）, acts（数组：title, summary）, ending。acts 为 2–4 幕。",
                     _validate_arc, log=log)


def short_draft(llm, *, context: Dict[str, Any], form: str) -> str:
    instr = ("请创作一篇中文短篇小说，约 2000–4000 字，有明确的开头、冲突与结尾，直接输出正文（Markdown），"
             "不要输出解释。" if form == "novel" else
             "请写一篇中文故事说明与分镜素材，直接输出正文（Markdown）。")
    if context.get("focus"):
        instr += f"若 context.focus 存在，请以「{context['focus']}」的视角叙述。"
    raw = llm.ask(build_prompt("short", context, instr), system=SYSTEM)
    text = (raw or "").strip()
    if not text:
        raise GenerationError("短篇生成为空")
    return text


def beats_draft(llm, *, context: Dict[str, Any], pages: Optional[int] = None, log=None) -> Dict[str, Any]:
    """节拍表：先规划每页的叙事作用、画面焦点与页末钩子，再据此生成分镜。pages=None 表示按故事长度自动决定。"""
    count = (f"必须正好 {pages} 页" if pages is not None
             else "页数由故事实际长度决定（通常每 3–5 个情节点一页），不设固定页数")
    ctx = {**context, "pages_target": pages if pages is not None else "auto"}
    return call_json(llm, "beats", ctx,
                     "请为这个故事规划漫画节拍表：" + count + "。整体结构为：开头铺垫、中段升级、一个转折、"
                     "一个高潮、结尾收束。每页说明：purpose（本页叙事作用，以 铺垫/升级/转折/高潮/收束 之一开头，"
                     "并用一句话说明）、focus（本页画面焦点，一句话）、hook（页末钩子：让读者想翻到下一页的悬念或情绪，"
                     "最后一页可为空）、palette（本幕配色，英文，写明具体颜色与明暗，如 cold blue and grey, low contrast；可空）。"
                     "JSON 字段：pages（数组：no, purpose, focus, hook, palette）。",
                     lambda d: _validate_beats(d, pages), log=log)


STYLE_KEYS = ("summary", "line", "palette", "lighting")


def _validate_style(d: Dict[str, Any]) -> List[str]:
    errs = [f"缺少 {k}" for k in STYLE_KEYS if not isinstance(d.get(k), str) or not d[k].strip()]
    return errs


def style_draft(llm, *, context: Dict[str, Any], log=None) -> Dict[str, Any]:
    return call_json(llm, "style", context,
                     "请为这个世界写一份漫画风格指南，用于图像模型保持全书画风一致。JSON 字段：summary（中文，"
                     "一两句话概括画风）、line（英文：线条与绘画技法，如 clean ink linework, cel shading）、"
                     "palette（英文：全书主色调，3–5 种颜色，描述要具体）、lighting（英文：光照与氛围）。",
                     _validate_style, log=log)


def _validate_scene(d: Dict[str, Any]) -> List[str]:
    return [] if isinstance(d.get("visual_en"), str) and d["visual_en"].strip() else ["缺少 visual_en（英文场景描述）"]


def scene_draft(llm, *, context: Dict[str, Any], log=None) -> Dict[str, Any]:
    return call_json(llm, "scene", context,
                     "请为这个地点写一段英文场景描述，用于图像模型生成场景设定图（不要出现人物）。"
                     "JSON 字段：visual_en（英文，描述建筑、地貌、光线与氛围）。", _validate_scene, log=log)


def storyboard_draft(llm, *, context: Dict[str, Any], log=None, pages: Optional[int] = None) -> Dict[str, Any]:
    return call_json(llm, "storyboard", context,
                     "请把故事改写为漫画分镜（分页脚本）。ctx.beats 是已确定的节拍表：pages 的页数与 beats 一一对应，"
                     "每页服务于对应节拍的 purpose 与 focus，页末最后一格要承接该页的 hook。"
                     "0) 若 context.focus 存在，镜头以该主体为中心，它应出现在多数格中；"
                     "1) 每页 1–5 格，按阅读顺序排列；"
                     "2) 相邻格必须承接：connects_prev 用不超过 20 字说明本格如何承接上一格（动作的前后、视线的指向、场景的变化）；"
                     "同一场景的连续动作用相近的镜头，不要每格都是同一景别；"
                     "3) 高潮与转折用 span=2（整行）的大格，其余用 span=1；"
                     "4) 出场角色必须用 context.characters 中的名字，并在 visual_en 中保持其外观描述一致；"
                     "5) camera 取 平视/俯视/仰视/过肩/特写镜头/推近/拉远/反打 之一。反打=与上一格相反的机位，用于两人对话时交替拍摄，"
                     "只有反打才允许画面中两人的左右位置翻转；其余情况同一场景内左右位置保持不变；"
                     "6) screen_order 按画面从左到右列出本格出场的角色名（至少两人时才填，否则为空数组）；"
                     "7) gaze 为每个出场角色的视线，对象为 {名字: 朝左/朝右/朝画外/朝观众/向下/向上/看向对方/低头}；"
                     "8) action 用一句话写主体的具体动作与姿态；composition 写构图与留白区，如「主体居左，右上留白」，留白用于放气泡；"
                     "9) 对白每句不超过 24 字，短句为佳；kind 取 speech（说话，默认）/thought（心声）/shout（呼喊）；"
                     "speaker_side 取 左/中/右（说话人在画面中的大致位置）；position 取 左上/右上/左下/右下/中上/中下 之一；"
                     "对白语气要符合该角色的身份（见 context.characters 的 summary），口语化，可以有潜台词，不要说明性的长句；"
                     "10) narration 只写画面无法表达的信息（时间、地点、心理），不超过 30 字，不要复述画面；"
                     "11) sfx 为画面中的音效字（如「轰」「咔嗒」，没有则为空）；12) emotion 为该格主角的情绪，一个词；"
                     "13) location 为该格所在地点的名字，必须取自 context.locations 中的名字，没有则为空。"
                     "JSON 字段：pages（数组，每页：no, panels）；panels（每格）：shot（远景/中景/近景/特写）, camera, "
                     "span（1 或 2）, connects_prev, action, composition, screen_order（数组）, gaze（对象）, "
                     "narration（可空）, dialogue（数组：speaker, text, kind, speaker_side, position）, sfx（可空）, "
                     "emotion（可空）, location（可空）, characters（出场角色名数组）, "
                     "visual_en（英文背景与环境描述；人物姿态与视线由 action 与 gaze 表达，不要写在这里；不要要求图中出现文字或气泡）。",
                     lambda d: _validate_storyboard(d, pages), log=log)


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


def _validate_expansion(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    for k in ("entities", "relations", "canon_updates", "new_characters"):
        _req_list(d, k, errs)
    d.setdefault("events", [])
    return errs


def expansion_draft(llm, *, context: Dict[str, Any], request: str = "", count: int = 3) -> Dict[str, Any]:
    """扩展世界观。request 为空时为自动完善（优先补缺口）；否则按用户要求扩展。"""
    if request.strip():
        goal = (f"用户要求：{request.strip()}。请据此扩展世界观，只写与要求直接相关的内容，"
                f"新增实体不超过 {count} 个。")
    else:
        goal = (f"请自动完善世界观：优先处理 context.gaps 中列出的薄弱环节，新增约 {count} 个实体"
                "（地理/势力/物种/技术魔法/组织/物品等），并为新实体建立与现有实体的关系；"
                "必要时可用 canon_updates 补充已有实体的描述。")
    instr = (goal + " 硬性要求：遵守宪章硬规则；不得与 context 中已有的名字重复；不要编写历史事件（events 留空）。"
             "JSON 字段：entities（数组：kind(地理/势力/物种/技术/魔法/历史事件/物品/组织/其它), name, summary, attrs），"
             "relations（数组：src_name, dst_name, type, note，名字可指向本次新增或已有实体），"
             "canon_updates（数组：target_name, target_type(entity|character), payload 对象），"
             "new_characters（数组：name, role, faction_name, motive, summary，可为空）。")
    return call_json(llm, "expand", {**context, "request": request.strip(), "count": count},
                     instr, _validate_expansion)


def _validate_modification(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    changes = d.get("changes")
    if not isinstance(changes, list):
        return ["changes 必须是数组"]
    for c in changes:
        if not isinstance(c, dict) or not c.get("target_name"):
            errs.append("每条修改必须有 target_name")
            break
        if c.get("target_type") not in ("entity", "character"):
            errs.append("target_type 只能是 entity 或 character")
            break
        if not isinstance(c.get("payload"), dict):
            errs.append("payload 必须是对象")
            break
    return errs


def modification_draft(llm, *, context: Dict[str, Any], request: str, scoped: bool) -> Dict[str, Any]:
    """根据用户要求给出设定修改。scoped=True 时只能修改 context.targets 中的对象。"""
    scope = ("只能修改 context.targets 中列出的对象，不得涉及其它对象。" if scoped else
             "自动判断需要修改哪些对象（可以是多个实体或角色），只做满足要求所必需的最小修改，其它内容保持不变。")
    instr = (f"用户要求：{request.strip()}。{scope}"
             "硬性要求：修改后必须遵守宪章硬规则，不得与其它已有名字重复，不得改动历史事件，不要编写新实体（新增请用扩展功能）。"
             "JSON 字段：changes（数组，每项：target_name（名字）, target_type(entity|character), "
             "payload（对象，只包含需要修改的字段）, reason（一句话说明修改原因））。"
             "实体可修改字段：name, kind, summary, attrs；角色可修改字段：name, role, motive, summary, attrs, status, "
             "faction_name（所属势力名字）。若无需修改，changes 为空数组。")
    return call_json(llm, "modify", {**context, "request": request.strip()}, instr, _validate_modification)
