"""场景设定图（L5）：一个场景点配一组参考图，供镜头按视角选用。

一个场景点的设定图（sheets）：
  * exterior  外观全景：从外面看的全景（室外场所本身，或室内场所所在的建筑外观）；
  * interior  内部总览：室内场所的整体视图（室外场所不生成）；
  * room_NN   主要房间：只有正文或设定里确有多个房间时才生成，最多 MAX_ROOMS 个；
特殊标志物（markers）：正文中反复出现或对剧情有意义的物件（如蒸汽电话、墙上的巨钟），
写入标志物列表，并附在它出现的设定图提示词里，保证视频里能看到它。

依据：模型提议时以原始正文段落为首选依据（外观、陈设、房间、标志物的原文描写必须逐项保留）；
正文没写的部分才按所属区域风格补充。每个场景点记录 `sheets_basis`：novel_text / inferred，
界面上据此标注“依据正文”或“依据推断”。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import autogate, joblog, llm_trace, prompts
from .. import runtime as _rt
from ..call_log import log_call
from ..project_store import read_json, write_json_atomic
from ..providers.text import TextLLM
from .global_visual import StepError, parse_json_object
from . import location_context

MAX_ROOMS = 4
MAX_MARKERS = 6
SHEET_DIR = "global/assets/sheets"


# ---------------------------------------------------------------- 查询

def sheet_for_shot(loc: Dict[str, Any], shot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """镜头应使用的设定图。镜头指定了合法的 sheet_id 就用它；否则室内场所用内部总览、室外用外观全景；
    都没有时取第一张。地点没有设定图时返回 None（沿用地点本身的定妆图）。"""
    sheets = loc.get("sheets") or []
    if not sheets:
        return None
    sid = shot.get("sheet_id")
    if sid:
        for s in sheets:
            if s.get("id") == sid:
                return s
    want = {"interior": "interior", "exterior": "exterior"}.get(loc.get("spot_kind") or "")
    for s in sheets:
        if want and s.get("kind") == want:
            return s
    return sheets[0]


def markers_for(loc: Dict[str, Any], sheet: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """设定图中应出现的标志物（没有设定图时返回地点全部标志物）。"""
    markers = loc.get("markers") or []
    if sheet is None:
        return list(markers)
    names = set(sheet.get("marker_names") or [])
    return [m for m in markers if m.get("name") in names]


def has_sheets(loc: Dict[str, Any]) -> bool:
    return bool(loc.get("sheets"))


# ---------------------------------------------------------------- 规范化（纯函数，便于测试）

def _clean_str(v: Any) -> str:
    return str(v or "").strip()


def _markers(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        name = _clean_str(m.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        views = [_clean_str(v) for v in (m.get("views") or []) if _clean_str(v)]
        out.append({"name": name, "description_zh": _clean_str(m.get("description_zh")),
                    "visual_anchor_en": _clean_str(m.get("visual_anchor_en")) or name,
                    "views": views})
        if len(out) >= MAX_MARKERS:
            break
    return out


def _sheet(sid: str, kind: str, label: str, spec: Dict[str, Any], fallback_anchor: str, basis: str) -> Dict[str, Any]:
    anchor = _clean_str(spec.get("visual_anchor_en")) or fallback_anchor
    return {"id": sid, "kind": kind, "label": label, "description_zh": _clean_str(spec.get("description_zh")),
            "visual_anchor_en": anchor, "marker_names": [], "asset_path": None, "basis": basis}


def build_sheets(spot: Dict[str, Any], obj: Dict[str, Any], *, basis: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把模型的提议整理成设定图与标志物。返回 (sheets, markers)。

    规则：spot_kind == exterior → 只有外观全景；其它（室内或未标注）→ 外观全景 + 内部总览 + 主要房间。
    锚点缺失时回退为场景点的视觉锚点，保证每张设定图都有可用的提示词。
    """
    name = _clean_str(spot.get("name")) or "该场所"
    base = _clean_str(spot.get("visual_anchor_en"))
    markers = _markers(obj.get("markers"))
    ext_spec = obj.get("exterior") if isinstance(obj.get("exterior"), dict) else {}
    sheets = [_sheet("exterior", "exterior", "外观全景", ext_spec, base or f"exterior of {name}", basis)]
    if spot.get("spot_kind") != "exterior":
        int_spec = obj.get("interior") if isinstance(obj.get("interior"), dict) else {}
        sheets.append(_sheet("interior", "interior", "内部总览", int_spec, base or f"interior of {name}", basis))
        seen = set()
        n = 0
        for room in obj.get("rooms") or []:
            if not isinstance(room, dict):
                continue
            rname = _clean_str(room.get("name"))
            if not rname or rname in seen:
                continue
            seen.add(rname)
            n += 1
            sheets.append(_sheet(f"room_{n:02d}", "room", f"房间：{rname}", room, base or f"room in {name}", basis))
            if n >= MAX_ROOMS:
                break
    for sh in sheets:
        sh["marker_names"] = [m["name"] for m in markers
                              if not m["views"] or sh["id"] in m["views"] or sh["kind"] in m["views"]]
    return sheets, markers


# ---------------------------------------------------------------- 提议（调用模型）

def propose(project_dir: Path, spot: Dict[str, Any], parent: Optional[Dict[str, Any]], text_llm: TextLLM) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """为场景点提议设定图与标志物（不写文件）。返回 (sheets, markers, basis)。"""
    names = location_context.names_of(spot)
    excerpts = location_context.novel_paragraphs(project_dir, names, max_paras=location_context.SHEET_PARAS,
                                                 max_chars=location_context.SHEET_CHARS)
    basis = "novel_text" if excerpts else "inferred"
    info = {
        "task": "sheets",
        "spot": {"name": spot.get("name"), "spot_kind": spot.get("spot_kind"), "description_zh": (spot.get("description_zh") or "")[:600],
                 "visual_anchor_en": spot.get("visual_anchor_en")},
        "parent": {"name": (parent or {}).get("name"), "visual_anchor_en": (parent or {}).get("visual_anchor_en")},
        "novel_paragraphs": excerpts,
        "max_rooms": MAX_ROOMS, "max_markers": MAX_MARKERS,
    }
    prompt = (
        f"TASK=sheets\n为场景点「{spot.get('name')}」规划参考设定图。\n"
        "依据：novel_paragraphs 是原始正文中提到该场所的段落，是首选依据。\n"
        "1. exterior（外观全景）：从外面看的全景，写建筑/场所的外观、体量、周边环境；室内场所写它所在建筑的外观。\n"
        "2. interior（内部总览）：室内场所的整体视图，写主要空间、家具陈设、光源；室外场所不需要。\n"
        f"3. rooms（主要房间）：只有正文或设定中确实提到多个房间时才列出，最多 {MAX_ROOMS} 个；没有就给空数组。\n"
        f"4. markers（特殊标志物）：正文中反复出现或对剧情有意义的物件（如特殊装置、标志性陈设），最多 {MAX_MARKERS} 个；"
        "views 填它应当出现的视角（exterior / interior / 房间名），不确定就留空数组表示所有视角都出现。\n"
        "正文中有明确的外观、陈设、房间、物件描写时，必须逐项保留到对应字段，不得省略或换成泛泛的描述；"
        "正文没写的部分才可以按所属区域的风格补充。visual_anchor_en 为英文一句话（不超过 45 词），只描述环境，不写人物。\n"
        "只输出 JSON：{\"exterior\": {\"description_zh\": str, \"visual_anchor_en\": str}, "
        "\"interior\": {\"description_zh\": str, \"visual_anchor_en\": str} 或 null, "
        "\"rooms\": [{\"name\": str, \"description_zh\": str, \"visual_anchor_en\": str}], "
        "\"markers\": [{\"name\": str, \"description_zh\": str, \"visual_anchor_en\": str, \"views\": [str]}]}\n\n"
        f"RULE_INPUT={json.dumps(info, ensure_ascii=False)}"
    )
    label = f"L5 设定图：为「{spot.get('name')}」提议视角与标志物（依据：{'正文段落 %d 条' % len(excerpts) if excerpts else '推断'}）"
    with joblog.timed(project_dir, "global", label):
        raw = llm_trace.ask_traced(project_dir, text_llm, prompt, system=prompts.SYSTEM_VISUAL,
                                   scope="global", label=label, step="L5")
    obj = parse_json_object(raw)
    sheets, markers = build_sheets(spot, obj, basis=basis)
    return sheets, markers, basis


# ---------------------------------------------------------------- 落盘

def _load(project_dir: Path) -> Dict[str, Any]:
    return read_json(Path(project_dir) / "global" / "locations.json")


def save_sheets(project_dir: Path, spot_id: str, sheets: List[Dict[str, Any]], markers: List[Dict[str, Any]],
                basis: str) -> None:
    doc = _load(project_dir)
    spot = next((l for l in doc["locations"] if l["id"] == spot_id), None)
    if spot is None:
        raise StepError(f"找不到场景点 {spot_id}")
    spot["sheets"] = sheets
    spot["markers"] = markers
    spot["sheets_basis"] = basis
    write_json_atomic(Path(project_dir) / "global" / "locations.json", doc)


def sheet_rel(spot_id: str, sheet_id: str, version: int) -> str:
    suffix = "" if version <= 1 else f"_v{version}"
    return f"{SHEET_DIR}/{spot_id}_{sheet_id}{suffix}.png"


def _file_ok(project_dir: Path, rel: Optional[str]) -> bool:
    return bool(rel) and (Path(project_dir) / rel).is_file()


def sheet_prompt(art_style: str, spot: Dict[str, Any], sheet: Dict[str, Any], markers: List[Dict[str, Any]]) -> str:
    name = spot.get("name") or "the place"
    view = {
        "exterior": f"Exterior panoramic view of {name}, seen from the outside, showing the whole facade and its surroundings",
        "interior": f"Interior overall view of {name}, wide shot showing the main space and its furnishings",
        "room": f"Interior view of the room \"{sheet.get('label', '')}\" inside {name}, wide shot showing the whole room",
    }.get(sheet.get("kind"), f"View of {name}")
    parts = [art_style, view, sheet.get("visual_anchor_en") or ""]
    if markers:
        parts.append("Key objects that must be clearly visible: " +
                     "; ".join(m.get("visual_anchor_en") or m["name"] for m in markers))
    parts.append("No people, no text, no letters, no watermark")
    return ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip()) + "."


def generate_spot_sheets(project_dir: Path, spot: Dict[str, Any], image, art_style: str, *,
                         version: int, ratio: str, only_missing: bool = True) -> Dict[str, Any]:
    """为场景点的每张设定图生成图片（失败隔离；参数/鉴权错误停止）。

    只生成缺失的（only_missing 且文件存在则跳过）；修改后的新版本使用新文件名，避免缓存误用旧图。
    返回 {done, failed, skipped, stopped}；成功的设定图的 asset_path 被写回 spot（调用方负责保存）。
    """
    summary: Dict[str, Any] = {"done": [], "failed": [], "skipped": [], "stopped": None}
    for sh in spot.get("sheets") or []:
        if _rt.stop_requested(project_dir):
            summary["stopped"] = "用户请求停止"
            return summary
        tag = f"{spot.get('name')}·{sh.get('label')}"
        if only_missing and _file_ok(project_dir, sh.get("asset_path")):
            summary["skipped"].append(sh["id"])
            continue
        rel = sheet_rel(spot["id"], sh["id"], version)
        marks = markers_for(spot, sh)
        joblog.log(project_dir, "assets", f"设定图生成：{tag}（标志物 {len(marks)} 个）")
        result = image.generate(sheet_prompt(art_style, spot, sh, marks), Path(project_dir) / rel, ratio=ratio)
        log_call(project_dir, {"kind": "image", "provider": image.name, "entity": f"{spot['id']}:{sh['id']}",
                               "ok": result.ok, "error": (result.error or "")[:300] or None})
        if result.ok:
            sh["asset_path"] = rel
            summary["done"].append(sh["id"])
            joblog.log(project_dir, "assets", f"设定图完成：{tag} → {rel}", level="ok")
        else:
            joblog.log(project_dir, "assets", f"设定图失败：{tag}：{result.error}", level="error")
            summary["failed"].append({"id": sh["id"], "error": result.error})
            if result.non_retryable:
                summary["stopped"] = f"{spot['id']}:{sh['id']}：{result.error}（参数/鉴权错误，已停止）"
                return summary
    return summary
