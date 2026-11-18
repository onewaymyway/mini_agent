"""visual/storyboard.py — 分镜的生成、保存编辑、确认（C2）与过期判断。

文件：parts/<章>/storyboard.json。输入指纹 input_sha 覆盖：已确认口播稿、本章时长、
源结构块、视觉配置（风格锚、插图开关与上限、主题）与导演版本。口播稿或视觉配置变化后，
旧分镜标记为 stale（过期）；生成新分镜会覆盖旧文件（界面需提示）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..planning.errors import PlanError
from ..planning.runner import load_spine
from ..project_store import append_progress, now_iso, read_json, write_json_atomic
from ..script.writer import load_script
from ..validators.visual import validate_storyboard
from .config import image_allowance, load_visual_config
from .director import DIRECTOR_VERSION, direct_part
from .normalize import normalize_scene


def _sb_path(pdir: Path, part_id: str) -> Path:
    return Path(pdir) / "parts" / part_id / "storyboard.json"


def _blocks(pdir: Path) -> list:
    return read_json(Path(pdir) / "source" / "blocks.json")["blocks"]


def _part_and_index(spine: Dict[str, Any], part_id: str):
    for i, p in enumerate(spine["parts"]):
        if p["id"] == part_id:
            return p, i
    raise PlanError(f"大纲中没有章 {part_id}")


def _input_sha(pdir: Path, part_id: str, script: Dict[str, Any], part: Dict[str, Any],
               cfg: Dict[str, Any]) -> str:
    payload = {
        "director": DIRECTOR_VERSION,
        "script": script,
        "budget_minutes": part["budget_minutes"],
        "visual": cfg,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_storyboard(pdir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = _sb_path(Path(pdir), part_id)
    return read_json(p) if p.is_file() else None


def _verdict(pdir: Path, part_id: str, sb: Dict[str, Any], script: Dict[str, Any],
             cfg: Dict[str, Any], part: Dict[str, Any]) -> Dict[str, Any]:
    allowance = image_allowance(part["budget_minutes"], cfg["images"]["max_per_minute"],
                                cfg["images"]["enabled"])
    return validate_storyboard(sb, script, allowance=allowance,
                               style_anchor=cfg["bible"]["style_anchor"],
                               images_enabled=cfg["images"]["enabled"])


def storyboard_status(pdir: Path, part_id: str) -> Dict[str, Any]:
    pdir = Path(pdir)
    spine = load_spine(pdir)
    script = load_script(pdir, part_id)
    sb = load_storyboard(pdir, part_id)
    status: Dict[str, Any] = {"exists": sb is not None, "confirmed": False, "stale": False,
                              "errors": [], "warnings": [], "images": 0, "allowance": 0}
    if spine is None or script is None:
        return status
    part, _ = _part_and_index(spine, part_id)
    cfg = load_visual_config(pdir)
    status["allowance"] = image_allowance(part["budget_minutes"], cfg["images"]["max_per_minute"],
                                          cfg["images"]["enabled"])
    if sb is None:
        return status
    current = _input_sha(pdir, part_id, script, part, cfg)
    status["stale"] = sb.get("input_sha") != current
    status["confirmed"] = bool(sb.get("confirmed")) and not status["stale"]
    status["images"] = sum(1 for s in sb["scenes"] if s["type"] == "illustration")
    v = _verdict(pdir, part_id, sb, script, cfg, part)
    status["errors"], status["warnings"] = v["errors"], list(sb.get("warnings", [])) + v["warnings"]
    return status


def plan_storyboard(pdir: Path, part_id: str, *, force: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    pdir = Path(pdir)
    spine = load_spine(pdir)
    if spine is None:
        raise PlanError("还没有大纲，请先在「🗺️ 大纲」生成并确认")
    if not spine.get("confirmed"):
        raise PlanError("请先确认大纲（C0）")
    part, idx = _part_and_index(spine, part_id)
    script = load_script(pdir, part_id)
    if script is None or not script.get("confirmed"):
        raise PlanError(f"请先确认 {part_id} 的口播稿（C1），再生成分镜")
    cfg = load_visual_config(pdir)
    sha = _input_sha(pdir, part_id, script, part, cfg)
    existing = load_storyboard(pdir, part_id)
    if existing and existing.get("input_sha") == sha and not force:
        return existing
    chapter = {"number": f"第 {idx + 1} 章", "title": part["title"], "one_line": part.get("one_line", "")}
    allowance = image_allowance(part["budget_minutes"], cfg["images"]["max_per_minute"],
                                cfg["images"]["enabled"])
    sb = direct_part(script, _blocks(pdir), chapter=chapter, allowance=allowance)
    sb["input_sha"] = sha
    sb["confirmed"] = False
    sb["confirmed_at"] = None
    sb["generated_at"] = now_iso(now)
    v = _verdict(pdir, part_id, sb, script, cfg, part)
    sb["errors"] = v["errors"]
    sb["warnings"] = sb["warnings"] + v["warnings"]
    write_json_atomic(_sb_path(pdir, part_id), sb)
    append_progress(pdir, f"生成分镜（规则版）{part_id}：{len(sb['scenes'])} 段，插图 {sb['images_planned']}/{allowance}", now)
    return sb


def save_storyboard(pdir: Path, part_id: str, raw: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """保存人工编辑。有错误则不写入（与口播稿保存一致）；保存后本章分镜变为未确认。"""
    pdir = Path(pdir)
    spine = load_spine(pdir)
    script = load_script(pdir, part_id)
    if spine is None or script is None:
        raise PlanError("缺少大纲或口播稿")
    part, _ = _part_and_index(spine, part_id)
    old = load_storyboard(pdir, part_id)
    if old is None:
        raise PlanError("还没有分镜，请先生成")
    by_beat = {b["id"]: b for b in script["beats"]}
    scenes = []
    for item in raw.get("scenes") or []:
        beat = by_beat.get(item.get("beat") or item.get("id"))
        if beat is None:
            raise PlanError(f"分镜中的段落 {item.get('beat')!r} 不在口播稿中")
        scenes.append(normalize_scene(item, beat["sentences"]))
    sb = dict(old)
    sb["scenes"] = scenes
    sb["confirmed"] = False
    sb["confirmed_at"] = None
    cfg = load_visual_config(pdir)
    v = _verdict(pdir, part_id, sb, script, cfg, part)
    if v["errors"]:
        raise PlanError("分镜不合格：" + "；".join(v["errors"][:8]))
    sb["errors"] = []
    sb["images_planned"] = sum(1 for s in scenes if s["type"] == "illustration")
    write_json_atomic(_sb_path(pdir, part_id), sb)
    append_progress(pdir, f"保存分镜编辑 {part_id}（未确认）", now)
    return sb


def confirm_storyboard(pdir: Path, part_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """C2：分镜无错误且未过期时确认。"""
    pdir = Path(pdir)
    st = storyboard_status(pdir, part_id)
    if not st["exists"]:
        raise PlanError(f"{part_id} 还没有分镜")
    if st["stale"]:
        raise PlanError("口播稿或视觉设置已变更，分镜已过期，请先重新生成")
    if st["errors"]:
        raise PlanError("分镜不合格，不能确认：" + "；".join(st["errors"][:6]))
    sb = load_storyboard(pdir, part_id)
    sb["confirmed"] = True
    sb["confirmed_at"] = now_iso(now)
    write_json_atomic(_sb_path(pdir, part_id), sb)
    append_progress(pdir, f"确认分镜（C2）{part_id}", now)
    return sb
