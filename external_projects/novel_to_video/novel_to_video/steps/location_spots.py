"""地点细化（场景点）：镜头规划时识别「现有地点不能直接使用」的新场景，并规划成新的场景点。

背景：全局地点库来自小说设定，常常只有「铁城」这样的区域级地点。镜头规划如果只能「从库里选」，
所有发生在铁城里的戏都会挂在这一个地点上，视频只能生成城市全景，没有具体场所（办公室、街道、卧室）。

做法（项目选项 `location_spots_enabled`，默认 False，关闭时提示词、镜头与流水线与之前完全一致）：
  * 提示词让模型逐镜头判断「所选地点是否就是戏真正发生的场所」；不是就规划新场景点：
    location_id 置 null，location_name 写具体场所，parent_location_id 指向所属区域，spot_kind 标内/外景；
  * 代码用剧本场次标题（`### 场次 N｜地点·时间（内景/外景）`）做**确定性核对**：标题里的具体场所
    与所选地点对不上时，把问题反馈给模型修正一次（自动修正次数用尽则只记为警告，不让整章失败）；
  * 新场景点记入镜头文件的 `new_location_specs`（名称、所属区域、内外景、理由、场次），
    C-5 据此起草并写入全局库（level=spot、parent_id、继承区域风格）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..project_store import read_json

SCENE_HEAD_RE = re.compile(r"^###\s*场次\s*(\d+)\s*[｜|](.*)$")
KIND_RE = re.compile(r"[（(]\s*(内景|外景)\s*[）)]")
UNKNOWN_PLACES = {"", "未知", "不详", "无", "不明"}
KIND_MAP = {"内景": "interior", "外景": "exterior", "interior": "interior", "exterior": "exterior"}
MAX_NEW_SPOTS_PER_CHAPTER = 4      # 超过时在章节页警告（模型可能把场所拆得过细）


def spots_enabled(project_dir: Path) -> bool:
    try:
        return bool(read_json(Path(project_dir) / "project.json").get("location_spots_enabled", False))
    except (OSError, ValueError):
        return False


def scene_places(script: str) -> Dict[int, Dict[str, Any]]:
    """解析剧本场次标题里的地点：{场次号: {place, specific, area, kind}}。

    标题 `地点·时间（内景/外景）`：去掉末尾的（内景/外景）后按「·」切分，最后一段是时间，其余是地点；
    地点可以是 `区域·场所`（specific=场所，area=区域）或单个名字（两者相同）。只有一段时无法区分时间，
    整段当作地点。地点为「未知」等时 place 为空串。
    """
    out: Dict[int, Dict[str, Any]] = {}
    for raw in script.splitlines():
        m = SCENE_HEAD_RE.match(raw.strip())
        if not m:
            continue
        head = m.group(2).strip()
        km = KIND_RE.search(head)
        kind = KIND_MAP.get(km.group(1)) if km else None
        head = KIND_RE.sub("", head).strip()
        tokens = [t.strip() for t in re.split(r"[·・･]", head) if t.strip()]
        place_tokens = tokens[:-1] if len(tokens) >= 2 else tokens
        place = "·".join(place_tokens)
        if place in UNKNOWN_PLACES:
            place_tokens, place = [], ""
        out[int(m.group(1))] = {"place": place,
                                "specific": place_tokens[-1] if place_tokens else "",
                                "area": place_tokens[0] if place_tokens else "",
                                "kind": kind}
    return out


def _names(loc: Dict[str, Any]) -> List[str]:
    return [str(n).strip() for n in [loc.get("name")] + list(loc.get("aliases") or []) if n and str(n).strip()]


def loc_fits(specific: str, loc: Dict[str, Any]) -> bool:
    """标题里的具体场所与所选地点是否对得上：名称相等或互相包含即视为对得上（宁可放过，避免误报反复修正）。"""
    if not specific:
        return True
    return any(n == specific or n in specific or specific in n for n in _names(loc))


def find_by_name(locations: List[Dict[str, Any]], name: Optional[str]) -> Optional[Dict[str, Any]]:
    name = str(name or "").strip()
    if not name:
        return None
    for l in locations:
        if name in _names(l):
            return l
    return None


def entries_for_prompt(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给模型看的地点清单：带层级与类型，模型才能判断「现有地点是否能直接用」。"""
    out = []
    for l in locations:
        e = {"id": l["id"], "name": l["name"], "level": l.get("level") or "area"}
        if l.get("parent_id"):
            e["parent_id"] = l["parent_id"]
        if l.get("location_type"):
            e["type"] = l["location_type"]
        out.append(e)
    return out


def granularity_issues(shots: List[Dict[str, Any]], script: str,
                       locations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """已构建的镜头与剧本场次标题的核对：标题写到了具体场所，镜头却挂在对不上的已有地点上。"""
    places = scene_places(script)
    by_id = {l["id"]: l for l in locations}
    issues: List[Dict[str, str]] = []
    seen = set()
    for sh in shots:
        lid = sh.get("location_id")
        info = places.get(int(sh.get("scene_no") or 0))
        if not lid or lid not in by_id or not info or not info["specific"]:
            continue
        loc = by_id[lid]
        if loc_fits(info["specific"], loc):
            continue
        key = (sh.get("scene_no"), lid)
        if key in seen:
            continue
        seen.add(key)
        parent_hint = ""
        area_loc = find_by_name(locations, info["area"]) if info["area"] != info["specific"] else None
        if area_loc:
            parent_hint = f"，parent_location_id 填 {area_loc['id']}（{area_loc['name']}）"
        elif loc.get("level", "area") != "spot":
            parent_hint = f"，parent_location_id 填 {lid}（{loc['name']}，如果「{info['specific']}」在它里面）"
        issues.append({"message": (
            f"场次 {sh.get('scene_no')} 的标题地点是「{info['place']}」，但镜头 {sh.get('id') or ''} 选用了地点"
            f"「{loc['name']}」（{lid}），它不能直接用于该场景。请把发生在「{info['specific']}」的镜头的 location_id 置 null，"
            f"location_name 写「{info['specific']}」{parent_hint}，spot_kind 填 interior 或 exterior；"
            f"只有纯粹的城市/区域全景镜头才使用区域级地点")})
    return issues


def build_specs(raw_shots: List[Dict[str, Any]], names: List[str], script: str,
                locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为 new_locations 里的每个名字整理出新场景点规格（所属区域、内外景、理由、场次）。"""
    ids = {l["id"] for l in locations}
    places = scene_places(script)
    specs: Dict[str, Dict[str, Any]] = {}
    for rs in raw_shots:
        loc_id, nm = rs.get("location_id"), str(rs.get("location_name") or "").strip()
        name = nm or (str(loc_id).strip() if loc_id else "")
        if name not in names or name in specs:
            continue
        scene_no = int(rs.get("scene_no") or 1)
        info = places.get(scene_no) or {}
        parent = rs.get("parent_location_id")
        if parent not in ids:
            parent = None
        if parent is None and info.get("area") and info.get("area") != info.get("specific"):
            hit = find_by_name(locations, info["area"])
            parent = hit["id"] if hit else None
        kind = KIND_MAP.get(str(rs.get("spot_kind") or "").strip().lower()) or info.get("kind")
        specs[name] = {"name": name, "parent_id": parent, "spot_kind": kind,
                       "reason": str(rs.get("location_reason") or "").strip(), "scene_no": scene_no}
    for n in names:      # 名字有但模型没给出可用字段的：仍给出最小规格
        specs.setdefault(n, {"name": n, "parent_id": None, "spot_kind": None, "reason": "", "scene_no": None})
    return [specs[n] for n in names]
