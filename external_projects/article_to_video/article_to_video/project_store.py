"""project_store.py — 项目目录、project.json 读写（原子写入）、设置与署名/水印归一化。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
MODES = ("condensed", "faithful", "highlights")
PERSONAS = ("host", "author")
ORIENTATIONS = ("landscape", "portrait")
RESOLUTIONS = ("720P", "1080P")
WATERMARK_POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right", "center", "diagonal_tile")
WATERMARK_MODES = ("static", "drift")
MAX_AUTHOR_CHARS = 60
MAX_WATERMARK_CHARS = 60
DEFAULT_AI_TEXT = "本视频配音与部分插图由 AI 生成"


def now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


# ---------- 原子 IO ----------

def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(Path(path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def append_progress(project_dir: Path, text: str, now: Optional[datetime] = None) -> None:
    with (Path(project_dir) / "progress.log").open("a", encoding="utf-8") as fh:
        fh.write(f"[{now_iso(now)}] {text}\n")


# ---------- 归一化 ----------

def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _clean_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_chars]


def normalize_settings(raw: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    raw = dict(raw or {})
    warnings: List[str] = []
    # 画幅与分辨率存放在 video 子字典中（导入与设置页都如此写入）；顶层同名字段仅作兼容
    video_raw = raw.get("video") if isinstance(raw.get("video"), dict) else {}
    for key in ("orientation", "resolution"):
        if key in video_raw:
            raw[key] = video_raw[key]

    def pick(key: str, allowed: Tuple[str, ...], default: str) -> str:
        value = raw.get(key)
        if value is None:
            return default
        if value in allowed:
            return value
        warnings.append(f"{key}={value!r} 不合法，已改用 {default}")
        return default

    settings = {
        "mode": pick("mode", MODES, "condensed"),
        "persona": pick("persona", PERSONAS, "host"),
        "target_minutes": round(_clamp(_to_float(raw.get("target_minutes"), 10.0), 3.0, 60.0), 1),
        "episode_max_minutes": round(_clamp(_to_float(raw.get("episode_max_minutes"), 15.0), 3.0, 60.0), 1),
        "video": {
            "orientation": pick("orientation", ORIENTATIONS, "landscape"),
            "resolution": pick("resolution", RESOLUTIONS, "1080P"),
            "fps": 25,
        },
    }
    return settings, warnings


def normalize_branding(raw: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """署名与水印。水印未填文字时自动关闭；位置、模式不合法时取默认值。"""
    raw = dict(raw or {})
    warnings: List[str] = []

    credits_raw = raw.get("credits") if isinstance(raw.get("credits"), dict) else {}
    credits = {
        "title_card": bool(credits_raw.get("title_card", True)),
        "lower_third": bool(credits_raw.get("lower_third", True)),
        "end_card": bool(credits_raw.get("end_card", True)),
        "corner_tag": bool(credits_raw.get("corner_tag", False)),
    }

    wm_raw = raw.get("watermark") if isinstance(raw.get("watermark"), dict) else {}
    wm_text = _clean_text(wm_raw.get("text"), MAX_WATERMARK_CHARS)
    wm_image = _clean_text(wm_raw.get("image"), 200) or None  # 项目内相对路径，如 assets/logo.png
    wm_type = wm_raw.get("type") if wm_raw.get("type") in ("text", "image") else "text"
    if wm_type == "image" and not wm_image:
        wm_type = "text"
    wm_requested = bool(wm_raw.get("enabled", True))
    has_content = bool(wm_text) if wm_type == "text" else True
    if wm_requested and not has_content:
        if "enabled" in wm_raw:
            warnings.append("水印未填写文字，未启用水印")
        wm_enabled = False
    else:
        wm_enabled = wm_requested

    position = wm_raw.get("position") or "top_right"
    if position not in WATERMARK_POSITIONS:
        warnings.append(f"水印位置 {position!r} 不合法，已改用 top_right")
        position = "top_right"
    wm_mode = wm_raw.get("mode") or "static"
    if wm_mode not in WATERMARK_MODES:
        warnings.append(f"水印模式 {wm_mode!r} 不合法，已改用 static")
        wm_mode = "static"

    ai_raw = raw.get("ai_disclosure") if isinstance(raw.get("ai_disclosure"), dict) else {}

    branding = {
        "author": _clean_text(raw.get("author"), MAX_AUTHOR_CHARS),
        "author_handle": _clean_text(raw.get("author_handle"), MAX_AUTHOR_CHARS),
        "source_title": _clean_text(raw.get("source_title"), 120),
        "source_url": _clean_text(raw.get("source_url"), 300),
        "credits": credits,
        "watermark": {
            "enabled": wm_enabled,
            "type": wm_type,
            "text": wm_text,
            "image": wm_image,
            "position": position,
            "opacity": round(_clamp(_to_float(wm_raw.get("opacity"), 0.35), 0.0, 1.0), 2),
            "scale": round(_clamp(_to_float(wm_raw.get("scale"), 0.06), 0.01, 0.3), 3),
            "margin": round(_clamp(_to_float(wm_raw.get("margin"), 0.03), 0.0, 0.2), 3),
            "mode": wm_mode,
        },
        "ai_disclosure": {
            "enabled": bool(ai_raw.get("enabled", True)),
            "text": _clean_text(ai_raw.get("text"), 120) or DEFAULT_AI_TEXT,
        },
    }
    return branding, warnings


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def update_branding(project_dir: Path, raw: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """更新已有项目的署名与水印。只改 project.json 的覆盖值，不动源文章与块数据。

    合并而非整体替换：只提交的字段被修改，其余字段保持生效值（嵌套字典逐层合并）。
    """
    from .app_settings import effective_group, save_group  # 延迟导入，避免循环依赖
    project_dir = Path(project_dir)
    cur = effective_group(project_dir, "branding")
    warnings = save_group(project_dir, "branding", _deep_merge(cur, raw or {}), now=now)
    append_progress(project_dir, "更新署名与水印设置", now)
    return warnings


# ---------- 目录 ----------

def slugify(title: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip()).strip("._")
    return s[:60] or "article"


def unique_project_dir(root: Path, base: str) -> Path:
    """在 root 下创建一个不重名的项目目录（同名加 _2、_3…）。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    candidate = root / base
    n = 2
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            candidate = root / f"{base}_{n}"
            n += 1


def load_project(project_dir: Path) -> Dict[str, Any]:
    return read_json(Path(project_dir) / "project.json")


def list_projects(root: Path) -> List[Dict[str, Any]]:
    root = Path(root)
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        pj = d / "project.json"
        if not (d.is_dir() and pj.is_file()):
            continue
        try:
            data = read_json(pj)
        except (OSError, json.JSONDecodeError):
            out.append({"id": d.name, "error": "project.json 无法读取", "project_dir": str(d)})
            continue
        out.append({
            "id": d.name,
            "title": data.get("title", ""),
            "author": (data.get("branding") or {}).get("author", ""),
            "mode": (data.get("settings") or {}).get("mode", ""),
            "stage": data.get("stage", ""),
            "created_at": data.get("created_at", ""),
            "project_dir": str(d),
        })
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out
