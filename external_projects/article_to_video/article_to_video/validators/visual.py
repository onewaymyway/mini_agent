"""validators/visual.py — V4 节奏与画面密度、V5 视觉约束（设计文档第 11 节）。

返回 {"errors": [...], "warnings": [...]}。errors 阻止确认与渲染，warnings 仅提示。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..visual.prompting import check_illustration, compose_prompt
from ..visual.director import (MAX_DIAGRAM_NODES, MAX_KEYPOINT_ITEMS, MAX_LIST_ITEMS,
                               MAX_TABLE_COLS, MAX_TABLE_ROWS, TEXT_LIMIT)
from ..visual.shots import reveal_in_range, shot_range
from ..visual.states import SCENE_TYPES

MAX_RUN = 3          # 同一画面类型连续超过此数量给出警告
MAX_PROMPT_CHARS = 400

_STRUCT_TO_TYPES = {"chain": ("diagram", "stack"), "table": ("table",), "math": ("formula",),
                    "list": ("list", "timeline")}


def _partition_errors(scenes: List[Dict[str, Any]], beats: List[Dict[str, Any]]) -> List[str]:
    """镜头必须按段落顺序、在每段内连续覆盖全部句子（不遗漏、不重叠）。

    没有 sentences 字段的场景（规则版）覆盖整段，因此每段只能有一个这样的场景。
    """
    out: List[str] = []
    known = {b["id"] for b in beats}
    by_beat: Dict[str, List[Dict[str, Any]]] = {}
    for s in scenes:
        by_beat.setdefault(s.get("beat"), []).append(s)
    for bid in by_beat:
        if bid not in known:
            out.append(f"分镜中的段落 {bid} 不在口播稿中")
    for b in beats:
        bid = b["id"]
        n = len(b["sentences"])
        group = by_beat.get(bid, [])
        if not group:
            out.append(f"{bid}：没有镜头覆盖本段")
            continue
        if len(group) > 1 and any(not s.get("sentences") for s in group):
            out.append(f"{bid}：一段内有多个镜头，但镜头没有标明句子范围")
            continue
        pos = 0
        for s in group:
            a, c = shot_range(s, n)
            if a != pos:
                out.append(f"{bid}：镜头 {s.get('id')} 从第 {a + 1} 句开始，应从第 {pos + 1} 句开始（遗漏或重叠）")
                break
            if c < a:
                out.append(f"{bid}：镜头 {s.get('id')} 的句子范围倒置")
                break
            pos = c + 1
        else:
            if pos != n:
                out.append(f"{bid}：镜头只覆盖到第 {pos} 句，本段共 {n} 句（末尾句子未被覆盖）")
    expected = [b["id"] for b in beats for _ in by_beat.get(b["id"], [])]
    if [s.get("beat") for s in scenes] != expected:
        out.append("分镜镜头的顺序与口播稿段落顺序不一致")
    return out


def validate_storyboard(sb: Dict[str, Any], script: Dict[str, Any], *, allowance: int,
                        style_anchor: str, images_enabled: bool,
                        shots_per_image: int = 1) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    beats = script["beats"]
    scenes = sb.get("scenes") or []

    # 镜头覆盖：每段内连续覆盖全部句子，段落顺序一致
    errors += _partition_errors(scenes, beats)

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
                 "diagram": MAX_DIAGRAM_NODES, "stack": MAX_DIAGRAM_NODES, "table": MAX_TABLE_ROWS,
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
                # 第三期：主体须具体、不得含文字类请求；场景与构图同样不得含文字类请求
                errors += [f"{sid}：{e}" for e in check_illustration(img)]
                prompt_len = len(compose_prompt(img, style_anchor, "landscape"))
                if prompt_len > MAX_PROMPT_CHARS:
                    errors.append(f"{sid}：生图提示词超过 {MAX_PROMPT_CHARS} 字")
            if not images_enabled:
                warnings.append(f"{sid}：生图已关闭，插图将按卡片绘制")

        beat = next((b for b in beats if b["id"] == sc.get("beat")), None)
        if beat is not None:
            a, c = shot_range(sc, len(beat["sentences"]))
            if not reveal_in_range(sc, a, c):
                errors.append(f"{sid}：逐步出现的状态数、句序号或镜头句子范围不一致（需在镜头句子范围内且不倒退），请保存以重算")

    # V4：同一画面类型连续超过 MAX_RUN 个镜头为警告（计划 4.5）
    run = 1
    for i in range(1, len(scenes)):
        run = run + 1 if scenes[i].get("type") == scenes[i - 1].get("type") else 1
        if run == MAX_RUN + 1:
            warnings.append(f"{scenes[i].get('id')}：连续 {run} 个镜头为同一画面类型「{scenes[i].get('type')}」，建议调整")

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
