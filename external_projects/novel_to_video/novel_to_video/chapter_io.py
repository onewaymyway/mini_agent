"""chapter_io.py — 章节目录、哈希、状态与共享数据读写。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .project_store import read_json, read_yaml, write_json_atomic, write_yaml_atomic

MAX_BLOCK_CHARS = 40          # 单个口播块的字数上限（超出则按标点切分）
# 视频生成接口的硬性时长范围（与 novel-video-studio/scripts/common.py 的 MIN_SEC/MAX_SEC 一致）：
# 接口返回 "seconds must be in [4, 12]"，超出即 400 参数错误。镜头时长必须落在此范围内。
VIDEO_MIN_SEC = 4
VIDEO_MAX_SEC = 12
SHOT_MIN_SEC = VIDEO_MIN_SEC  # 规划参考：每镜头口播约 4–12 秒（建议值，差几秒可以接受，不做硬性拦截）
SHOT_MAX_SEC = VIDEO_MAX_SEC


def requested_video_seconds(speech_sec: float) -> int:
    """向视频接口请求的时长：取口播时长向上取整，并限制在接口范围内。
    实际画面会在合成时按配音的真实时长缩放（见 media.ffmpeg.fit_clip），所以请求时长只需大致对齐。"""
    import math
    return int(min(VIDEO_MAX_SEC, max(VIDEO_MIN_SEC, math.ceil(speech_sec))))
SHOT_PLAN_SEC = 10.0          # 规划提示词中的参考时长（建议值，不是硬限制）


def sha12(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def norm(text: str) -> str:
    """用于“逐字摘录”比对：去掉全部空白与常见标点差异。"""
    return re.sub(r"[\s，。！？；：、“”‘’「」『』\"'（）()—\-…]", "", text)


def chapter_dir(project_dir: Path, no: int) -> Path:
    return project_dir / "chapters" / f"{no:03d}"


def load_index(project_dir: Path) -> Dict[str, Any]:
    return read_yaml(project_dir / "chapters" / "index.yaml")


def set_chapter_status(project_dir: Path, no: int, status: str, message: str = "") -> None:
    idx = load_index(project_dir)
    for c in idx.get("chapters", []):
        if c["no"] == no:
            c["status"] = status
            c["message"] = message
            c["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_yaml_atomic(project_dir / "chapters" / "index.yaml", idx)


def chapter_row(project_dir: Path, no: int) -> Dict[str, Any]:
    for c in load_index(project_dir).get("chapters", []):
        if c["no"] == no:
            return c
    raise KeyError(f"章节不存在：{no}")


def source_text(project_dir: Path, no: int) -> str:
    """读取章节原文（去掉首个标题行），路径相对于 novel_writer 导出目录。"""
    from .project_store import working_source_dir
    src_dir = working_source_dir(project_dir)
    row = chapter_row(project_dir, no)
    text = (src_dir / row["source_file"]).read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def load_state(project_dir: Path, no: int) -> Dict[str, Any]:
    path = chapter_dir(project_dir, no) / "state.json"
    if not path.is_file():
        return {"stages": {}}
    return read_json(path)


def save_state(project_dir: Path, no: int, state: Dict[str, Any]) -> None:
    write_json_atomic(chapter_dir(project_dir, no) / "state.json", state)


def mark_stage(project_dir: Path, no: int, stage: str, status: str, error: Optional[str] = None) -> None:
    state = load_state(project_dir, no)
    state["stages"][stage] = {"status": status, "error": error,
                              "at": datetime.now().isoformat(timespec="seconds")}
    save_state(project_dir, no, state)


def load_shots(project_dir: Path, no: int) -> Dict[str, Any]:
    return read_yaml_file(chapter_dir(project_dir, no) / "shots.yaml") or {}


def save_shots(project_dir: Path, no: int, data: Dict[str, Any]) -> None:
    write_yaml_atomic(chapter_dir(project_dir, no) / "shots.yaml", data)


def load_entities(project_dir: Path) -> Dict[str, Any]:
    chars = read_json(project_dir / "global" / "characters.json")
    locs = read_json(project_dir / "global" / "locations.json")
    return {"characters": chars, "locations": locs}


def save_characters(project_dir: Path, data: Dict[str, Any]) -> None:
    write_json_atomic(project_dir / "global" / "characters.json", data)


def find_entity(entities: Dict[str, Any], kind: str, eid: str) -> Optional[Dict[str, Any]]:
    rows = entities["characters" if kind == "character" else "locations"]["characters" if kind == "character" else "locations"]
    return next((e for e in rows if e["id"] == eid), None)


def read_yaml_file(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
