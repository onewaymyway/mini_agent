"""validators/visual.py — V4 节奏与画面密度、V5 视觉约束（设计文档第 11 节）。

返回 {"errors": [...], "warnings": [...]}。errors 阻止确认与渲染，warnings 仅提示。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..visual.config import FORBIDDEN_WORDS
from ..visual.director import (MAX_DIAGRAM_NODES, MAX_KEYPOINT_ITEMS, MAX_LIST_ITEMS,
                               MAX_TABLE_COLS, MAX_TABLE_ROWS, TEXT_LIMIT)
from ..visual.states import SCENE_TYPES, expected_state_count

MAX_RUN = 3          # 相邻三个 beat 不得同一场景类型
MAX_PROMPT_CHARS = 300

_STRUCT_TO_TYPES = {"chain": ("diagram",), "table": ("table",), "math": ("formula",),
                    "list": ("list", "timeline")}


def validate_storyboard(sb: Dict[str, Any], script: Dict[str, Any], *, allowance: int,
                        style_anchor: str, images_enabled: bool,
                        shots_per_image: int = 1) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    beats = script["beats"]
    scenes = sb.get("scenes") or []

    # 每个 beat 恰好一个场景，顺序一致
    if [s.get("beat") for s in scenes] != [b["id"] for b in beats]:
        errors.append("分镜场景与口播稿的段落不一一对应（缺少、多余或顺序不符）")

    for sc in scenes:
        sid = sc.get("id", "?")
        kind = sc.get("type")
        if kind not in SCENE_TYPES:
            errors.append(f"{sid}：未知场景类型 {kind!r}")
            continue
        items = [str(x) for x in sc.get("items") or []]
        if any(not x.strip() for x in items):
            errors.append(f"{sid}：存在空白条目")
        for x in items:
            if len(x) > TEXT_LIMIT:
                errors.append(f"{sid}：单条文字 {len(x)} 字，超过上限 {TEXT_LIMIT}（V4 密度）")
                break
        limit = {"keypoint": MAX_KEYPOINT_ITEMS, "illustration": MAX_KEYPOINT_ITEMS,
                 "list": MAX_LIST_ITEMS, "timeline": MAX_LIST_ITEMS,
                 "diagram": MAX_DIAGRAM_NODES, "table": MAX_TABLE_ROWS,
                 "quote": 1, "formula": MAX_KEYPOINT_ITEMS}[kind]
        if len(items) > limit:
            errors.append(f"{sid}：{kind} 条目 {len(items)} 个，超过上限 {limit}")
        if kind == "table":
            header = sc.get("header") or []
            if not header or len(header) > MAX_TABLE_COLS:
                errors.append(f"{sid}：表格表头为空或超过 {MAX_TABLE_COLS} 列")
            for r in sc.get("rows") or []:
                if len(r) != len(header):
                    errors.append(f"{sid}：表格行列数与表头不一致")
                    break
        if kind == "formula" and not str(sc.get("formula") or "").strip():
            errors.append(f"{sid}：公式场景缺少公式内容")
        if kind == "illustration":
            img = sc.get("image") or {}
            if img.get("leader", True):  # 只有主图需要画面主题（组内其余镜头复用主图）
                subject = str(img.get("subject") or "").strip()
                if not subject:
                    errors.append(f"{sid}：插图缺少画面主题")
                for w in FORBIDDEN_WORDS:
                    if w in subject:
                        errors.append(f"{sid}：插图主题含禁用词「{w}」（插图禁止文字类内容）")
                        break
                if len(style_anchor) + len(subject) > MAX_PROMPT_CHARS:
                    errors.append(f"{sid}：生图提示词超过 {MAX_PROMPT_CHARS} 字")
            if not images_enabled:
                warnings.append(f"{sid}：生图已关闭，插图将按卡片绘制")

        beat = next((b for b in beats if b["id"] == sc.get("beat")), None)
        if beat is not None:
            n_sent = len(beat["sentences"])
            reveal = sc.get("reveal") or []
            if len(reveal) != expected_state_count(sc):
                errors.append(f"{sid}：逐步出现的状态数与条目不一致，请保存以重算")
            prev = -1
            for r in reveal:
                k = r.get("at_sentence")
                if not isinstance(k, int) or k < 0 or k >= n_sent or k < prev:
                    errors.append(f"{sid}：逐步出现的句序号无效（需在本段句子范围内且不倒退）")
                    break
                prev = k

    # V4：连续三个同类型
    for i in range(MAX_RUN - 1, len(scenes)):
        window = [s.get("type") for s in scenes[i - MAX_RUN + 1:i + 1]]
        if len(set(window)) == 1:
            errors.append(f"{scenes[i].get('id')}：相邻 {MAX_RUN} 段为同一画面类型「{window[0]}」，请调整")
            break

    # V5：需要生成的图片数（按组计）不超过本章额度；每组镜头数不超过每图上限，且只有一张主图
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for s in scenes:
        if s.get("type") == "illustration":
            img = s.get("image") or {}
            groups.setdefault(img.get("group") or s.get("id"), []).append(s)
    if len(groups) > allowance:
        errors.append(f"需生成图片 {len(groups)} 张，超过本章额度 {allowance}（每分钟上限 × 章时长）")
    for gid, members in groups.items():
        if len(members) > shots_per_image:
            errors.append(f"图片组 {gid} 有 {len(members)} 个镜头，超过每图上限 {shots_per_image}")
        if len(members) > 1 and sum(1 for m in members if (m.get("image") or {}).get("leader")) != 1:
            errors.append(f"图片组 {gid} 必须恰好有一个主图")

    # V4：原文中的结构是否被画面表达（提示，不阻止）
    types_present = {s.get("type") for s in scenes}
    for kind in sb.get("source_structure") or []:
        wanted = _STRUCT_TO_TYPES.get(kind, ())
        if wanted and not any(t in types_present for t in wanted):
            warnings.append(f"原文有「{kind}」结构，本章画面中未体现（可在分镜页改为对应类型）")

    return {"errors": errors, "warnings": warnings}
