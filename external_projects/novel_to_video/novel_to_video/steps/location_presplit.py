"""地点细化 L4（可选）：区域预拆解。

对一个区域级地点，让模型根据区域描述与已有剧本中出现该区域的场次，列出常用场景点候选；
用户确认后才写入全局地点库（level=spot、parent_id、source=auto_added），并起草视觉锚点。
候选阶段不写任何文件。项目选项 `location_presplit_enabled` 默认开启，可在全书素材页关闭。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .. import autogate, joblog, llm_trace, prompts
from ..chapter_io import chapter_dir
from ..project_store import read_json, write_json_atomic
from ..providers.text import TextLLM
from ..review import entity_key, get_item, load_review, save_review
from .global_visual import StepError, parse_json_object
from .chapter_assets import _next_loc_id

MAX_CANDIDATES = 8
MAX_CONTEXT_LINES = 40
KIND_MAP = {"interior": "interior", "exterior": "exterior", "内景": "interior", "外景": "exterior"}


def presplit_enabled(project_dir: Path) -> bool:
    try:
        return bool(read_json(Path(project_dir) / "project.json").get("location_presplit_enabled", True))   # 旧项目未写该键时按默认开启处理
    except (OSError, ValueError):
        return False


def _locations(project_dir: Path) -> Dict[str, Any]:
    return read_json(project_dir / "global" / "locations.json")


def _area_context(project_dir: Path, area: Dict[str, Any]) -> List[str]:
    """已有剧本中，场次标题或台词里出现该区域名的行（最多 MAX_CONTEXT_LINES 行）。"""
    names = [area["name"]] + list(area.get("aliases") or [])
    out: List[str] = []
    for script in sorted((project_dir / "chapters").glob("*/script.md")):
        for line in script.read_text(encoding="utf-8").splitlines():
            if line.strip() and any(n and n in line for n in names):
                out.append(line.strip())
                if len(out) >= MAX_CONTEXT_LINES:
                    return out
    return out


def propose_spots(project_dir: Path, area_id: str, text_llm: TextLLM) -> List[Dict[str, Any]]:
    """返回候选场景点（不写文件）：[{name, spot_kind, reason}]。"""
    if not presplit_enabled(project_dir):
        raise StepError("区域预拆解已关闭（全书素材页可重新开启，项目选项 location_presplit_enabled）")
    locs = _locations(project_dir)["locations"]
    area = next((l for l in locs if l["id"] == area_id), None)
    if area is None:
        raise StepError(f"找不到地点 {area_id}")
    if (area.get("level") or "area") != "area":
        raise StepError(f"{area_id} 不是区域级地点，不能预拆解")
    existing = {n for l in locs for n in [l.get("name")] + list(l.get("aliases") or []) if n}
    context = _area_context(project_dir, area)
    info = {"task": "presplit", "area": area["name"], "description_zh": (area.get("description_zh") or "")[:800],
            "existing": sorted(existing), "max": MAX_CANDIDATES, "context": context}
    prompt = (
        f"TASK=presplit\n为区域「{area['name']}」列出短剧里常用的具体场景点候选（办公室、街道、住所、仓库……），"
        f"最多 {MAX_CANDIDATES} 个。只列在剧本上下文中实际出现或明显需要的场所，不要列无关紧要的地方；"
        "不要与 existing 中已有的名称重复；每个给出 spot_kind（interior 室内 / exterior 室外）和一句理由。\n"
        "只输出 JSON：{\"spots\": [{\"name\": str, \"spot_kind\": \"interior|exterior\", \"reason\": str}]}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )
    label = f"L4 区域预拆解：为「{area['name']}」提议场景点"
    with joblog.timed(project_dir, "global", label):
        raw = llm_trace.ask_traced(project_dir, text_llm, prompt, system=prompts.SYSTEM_VISUAL,
                                   scope="global", label=label, step="L4")
    obj = parse_json_object(raw)
    out: List[Dict[str, Any]] = []
    seen = set(existing)
    for s in obj.get("spots") or []:
        name = str(s.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "spot_kind": KIND_MAP.get(str(s.get("spot_kind") or "").strip().lower()),
                    "reason": str(s.get("reason") or "").strip()})
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def confirm_spots(project_dir: Path, area_id: str, spots: List[Dict[str, Any]], text_llm: TextLLM) -> List[str]:
    """用户确认后的写入：为每个场景点起草视觉锚点，写入地点库并登记审核项。返回新增的 id。"""
    if not presplit_enabled(project_dir):
        raise StepError("区域预拆解已关闭（全书素材页可重新开启，项目选项 location_presplit_enabled）")
    doc = _locations(project_dir)
    locs = doc["locations"]
    area = next((l for l in locs if l["id"] == area_id), None)
    if area is None or (area.get("level") or "area") != "area":
        raise StepError(f"{area_id} 不是可用的区域级地点")
    existing = {n for l in locs for n in [l.get("name")] + list(l.get("aliases") or []) if n}
    review = load_review(project_dir)
    added: List[str] = []
    for s in spots:
        name = str(s.get("name") or "").strip()
        if not name or name in existing:
            continue
        existing.add(name)
        eid = _next_loc_id(locs)
        ent = {"id": eid, "name": name, "aliases": [], "location_type": None, "era_setting": None,
               "first_appear": None, "description_zh": str(s.get("reason") or "").strip() or f"{area['name']}内的场所",
               "visual_anchor_en": None, "visual_anchor_source": None, "asset_path": None,
               "appearance_variants": [], "source": "auto_added", "auto_added_chapter": None,
               "level": "spot", "parent_id": area["id"], "spot_kind": KIND_MAP.get(s.get("spot_kind")) or None,
               "spot_reason": str(s.get("reason") or "").strip() or None}
        label = f"L4 区域预拆解：起草场景点「{name}」视觉锚点"
        with joblog.timed(project_dir, "global", label):
            raw = llm_trace.ask_traced(project_dir, text_llm, prompts.location_prompt(ent, area),
                                       system=prompts.SYSTEM_VISUAL, scope="global", label=label, step="L4")
        anchor = str(parse_json_object(raw).get("visual_anchor_en") or "").strip()
        if not anchor:
            raise StepError(f"场景点「{name}」的草稿缺少 visual_anchor_en")
        ent["visual_anchor_en"] = anchor
        ent["visual_anchor_source"] = text_llm.name
        locs.append(ent)
        get_item(review, entity_key("location", eid)).update(
            text_status="approved" if autogate.is_auto(project_dir, "asset_text") else "pending",
            portrait_status="missing")
        added.append(eid)
    if added:
        write_json_atomic(project_dir / "global" / "locations.json", doc)
        save_review(project_dir, review)
        joblog.log(project_dir, "global", f"L4 区域预拆解：「{area['name']}」新增 {len(added)} 个场景点：" +
                   "、".join(l["name"] for l in locs if l["id"] in added))
    return added
