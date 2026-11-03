"""steps/chapter_scenes.py — C-3a 大场景划分，以及大场景与镜头、共用素材的关系。

层级：一章小说 → 多个大场景 → 每个大场景多个镜头。
  * 大场景 = 同一地点、连续发生的一段剧情（剧本中的若干“场次”合并而成）。
  * 场次（剧本标题“### 场次 N｜…”）是剧本的切分，比大场景细；大场景只是把连续、同地点的场次归并起来。
  * 同一大场景内的素材（地点与定妆图、出镜角色、道具）可以共用，由代码汇总到大场景上，镜头继承。

划分由模型完成（按剧情内容判断切换点），代码负责校验：必须从 1 开始连续覆盖全部场次、不重不漏。
模型输出不合格时，按场次标题中的地点自动归并作为兜底，并在结果中记录 source="fallback"。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import joblog, llm_trace, prompts
from ..chapter_io import chapter_dir, read_yaml_file
from ..project_store import write_yaml_atomic
from ..providers.text import TextLLM

PLAN_FILE = "big_scenes.yaml"
PREVIEW_CHARS = 90
MAX_BIG_SCENES_HINT = 8

_HEADER = re.compile(r"^###\s*场次\s*(\d+)\s*[｜|]\s*(.*)$")


def parse_script_scenes(script: str) -> List[Dict[str, Any]]:
    """把剧本按“### 场次 N｜…”切成场次：[{scene_no, header, place, preview}]。"""
    scenes: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    body: List[str] = []

    def close():
        if cur is not None:
            text = " ".join(x.strip() for x in body if x.strip())
            cur["preview"] = text[:PREVIEW_CHARS]
            scenes.append(cur)

    for line in script.splitlines():
        m = _HEADER.match(line.strip())
        if m:
            close()
            header = m.group(2).strip()
            cur = {"scene_no": int(m.group(1)), "header": header, "place": _place_key(header)}
            body = []
        elif cur is not None:
            body.append(line)
    close()
    return scenes


def _place_key(header: str) -> str:
    """场次标题中的地点（去掉时间与内外景标注）：'铁城·东三街·清晨（外景）' → '铁城·东三街'。"""
    head = re.split(r"[（(]", header, maxsplit=1)[0]
    parts = [p.strip() for p in head.split("·") if p.strip()]
    return "·".join(parts[:-1]) if len(parts) >= 2 else (parts[0] if parts else "未知")


def validate_groups(groups: List[Dict[str, Any]], total_scenes: int) -> List[str]:
    """校验大场景是否从 1 开始、连续、不重不漏地覆盖全部场次。返回问题列表（空即通过）。"""
    issues: List[str] = []
    if not groups:
        return ["没有大场景"]
    expect = 1
    for i, g in enumerate(groups, start=1):
        a, b = g.get("from_scene"), g.get("to_scene")
        if not isinstance(a, int) or not isinstance(b, int):
            issues.append(f"大场景 {i} 的场次范围不是整数")
            continue
        if a != expect:
            issues.append(f"大场景 {i} 从场次 {a} 开始，应为 {expect}（场次必须连续覆盖）")
        if b < a:
            issues.append(f"大场景 {i} 结束场次 {b} 小于开始场次 {a}")
        expect = b + 1
    if expect - 1 != total_scenes:
        issues.append(f"大场景覆盖到场次 {expect - 1}，剧本共 {total_scenes} 场")
    return issues


def fallback_groups(scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """兜底：连续的、地点相同的场次归为一个大场景。"""
    groups: List[Dict[str, Any]] = []
    for s in scenes:
        if groups and groups[-1]["place_key"] == s["place"]:
            groups[-1]["to_scene"] = s["scene_no"]
        else:
            groups.append({"from_scene": s["scene_no"], "to_scene": s["scene_no"], "place_key": s["place"]})
    for i, g in enumerate(groups, start=1):
        g["big_no"] = i
        g["title_zh"] = g.pop("place_key").replace("·", " · ") or f"大场景 {i}"
        g["place_zh"] = g["title_zh"]
        g["time_zh"] = ""
        g["summary_zh"] = ""
    return groups


def _normalize(raw_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, g in enumerate(raw_groups, start=1):
        out.append({"big_no": i,
                    "from_scene": g.get("from_scene"), "to_scene": g.get("to_scene"),
                    "title_zh": str(g.get("title_zh") or f"大场景 {i}").strip(),
                    "place_zh": str(g.get("place_zh") or "").strip(),
                    "time_zh": str(g.get("time_zh") or "").strip(),
                    "summary_zh": str(g.get("summary_zh") or "").strip()})
    return out


def _script_key(script: str) -> str:
    return hashlib.sha1(script.encode("utf-8")).hexdigest()[:12]


def plan_big_scenes(project_dir: Path, no: int, script: str, text_llm: TextLLM, *,
                    force: bool = False) -> Dict[str, Any]:
    """C-3a：得到本章的大场景划分（缓存于 chapters/NNN/big_scenes.yaml，剧本不变则不重算）。"""
    cdir = chapter_dir(project_dir, no)
    cached = read_yaml_file(cdir / PLAN_FILE) or {}
    key = _script_key(script)
    if not force and cached.get("script_key") == key and cached.get("big_scenes"):
        return cached

    scenes = parse_script_scenes(script)
    if not scenes:
        raise ValueError("剧本中没有“### 场次 N｜…”场次标题，无法划分大场景")
    info = {"task": "big_scenes", "total_scenes": len(scenes),
            "scenes": [{"scene_no": s["scene_no"], "header": s["header"], "preview": s["preview"]} for s in scenes],
            "max_hint": MAX_BIG_SCENES_HINT}
    issues: List[str] = []
    groups: List[Dict[str, Any]] = []
    source = "model"
    for attempt in range(2):
        label = f"第 {no} 章 C-3a 划分大场景" + (f"（第 {attempt} 次修正）" if attempt else f"（{len(scenes)} 场）")
        with joblog.timed(project_dir, "chapter", label):
            raw = llm_trace.ask_traced(project_dir, text_llm,
                                       prompts.big_scenes_prompt(info, issues), system=prompts.SYSTEM_VISUAL,
                                       scope="chapter", label=label, step="C-3a", chapter=no)
        try:
            obj = _parse_json(raw)
            cand = _normalize(obj.get("big_scenes") or [])
        except ValueError as exc:
            issues = [f"输出不是合法 JSON：{exc}"]
            continue
        issues = validate_groups(cand, len(scenes))
        if not issues:
            groups = cand
            break
    if not groups:
        source = "fallback"
        groups = fallback_groups(scenes)
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-3a 大场景划分未通过校验，已按地点自动归并：{'；'.join(issues)}",
                   level="warn")
    for g in groups:
        g["scene_nos"] = list(range(g["from_scene"], g["to_scene"] + 1))
    plan = {"script_key": key, "source": source, "total_scenes": len(scenes), "big_scenes": groups}
    write_yaml_atomic(cdir / PLAN_FILE, plan)
    return plan


def _parse_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("找不到 JSON 对象")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc


def big_scene_of(plan: Dict[str, Any], scene_no: int) -> Optional[int]:
    for g in plan.get("big_scenes") or []:
        if g["from_scene"] <= scene_no <= g["to_scene"]:
            return g["big_no"]
    return None


def attach(shots_doc: Dict[str, Any], plan: Dict[str, Any], locations: List[Dict[str, Any]],
           characters: List[Dict[str, Any]]) -> None:
    """把大场景写进镜头：每个镜头标上 big_scene_no；每个大场景汇总共用素材（地点、出镜角色、道具）。"""
    shots = shots_doc.get("shots") or []
    char_names = {}
    for c in characters:
        for n in c.get("names") or []:
            char_names.setdefault(n, c["id"])
    loc_ids = {l["id"] for l in locations}
    big_list = []
    for g in plan.get("big_scenes") or []:
        mine = [s for s in shots if big_scene_of(plan, s.get("scene_no")) == g["big_no"]]
        for s in mine:
            s["big_scene_no"] = g["big_no"]
        loc_count: Dict[str, int] = {}
        chars: List[str] = []
        props: List[str] = []
        for s in mine:
            if s.get("location_id") in loc_ids:
                loc_count[s["location_id"]] = loc_count.get(s["location_id"], 0) + 1
            for cid in s.get("character_ids") or []:
                if cid not in chars:
                    chars.append(cid)
            for p in s.get("prop_names") or []:
                if p not in props:
                    props.append(p)
        big_list.append({
            "big_no": g["big_no"], "title_zh": g["title_zh"], "place_zh": g.get("place_zh", ""),
            "time_zh": g.get("time_zh", ""), "summary_zh": g.get("summary_zh", ""),
            "from_scene": g["from_scene"], "to_scene": g["to_scene"],
            "shot_ids": [s["id"] for s in mine],
            "location_id": max(loc_count, key=loc_count.get) if loc_count else None,
            "character_ids": chars, "prop_names": props,
        })
    shots_doc["big_scenes"] = big_list
