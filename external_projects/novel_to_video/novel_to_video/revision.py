"""revision.py — 修改意见分级回退（设计方案 10.6）与 stale 传播。

四类修改及其清理范围（只清理下游，不整章重跑）：
  clip_only      仅重生视频：删除该镜头 clip，保留提示词与音频
  prompt         改提示词：清 prompts 中该镜头、一致性结论、clip
  dialogue       改台词：清该镜头音频、提示词、clip，并重新校验 script.md
  shot_plan      改分镜：清该章 shots.yaml 中本镜头之后的全部镜头产物，回到 C-4

章节级 stale 传播：
  章内任何清理 → 该章 chapter_NNN.mp4 与 compose 阶段标记为 stale，需重新合成；
  章节视频一旦 stale，全书 exports/video.mp4 也标记为 stale。
其他章节的文件、状态、审核记录一律不动。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from . import chapter_io as cio
from .project_store import read_json, write_json_atomic

LEVELS = ("clip_only", "prompt", "dialogue", "shot_plan")


def _touch(path: Path) -> None:
    path.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _shot_index(shots: List[Dict[str, Any]], sid: str) -> int:
    for i, sh in enumerate(shots):
        if sh["id"] == sid:
            return i
    raise KeyError(f"镜头不存在：{sid}")


def invalidate(project_dir: Path, no: int, sid: str, level: str) -> Dict[str, Any]:
    """按层级清理下游产物。返回被清理的文件与被重置的镜头，便于日志与界面展示。"""
    if level not in LEVELS:
        raise ValueError(f"未知修改层级：{level}")
    cdir = cio.chapter_dir(project_dir, no)
    shots_doc = cio.load_shots(project_dir, no)
    shots = shots_doc.get("shots", [])
    idx = _shot_index(shots, sid)
    targets = [shots[idx]] if level != "shot_plan" else shots[idx:]
    removed: List[str] = []
    reset: List[str] = []

    for sh in targets:
        s = sh["id"]
        _touch(cdir / "clips" / f"{s}.mp4")
        removed.append(f"clips/{s}.mp4")
        sh["clip_key"] = None
        sh.pop("clip_error", None)
        if level in ("prompt", "dialogue", "shot_plan"):
            sh["prompt_en"] = None
            sh["consistency"] = None
        if level in ("dialogue", "shot_plan"):
            _touch(cdir / "audio" / f"{s}.wav")
            removed.append(f"audio/{s}.wav")
            for b in sh.get("blocks", []):
                b.pop("audio_key", None)
            sh["audio_key"] = None
            sh["duration_sec"] = None
        reset.append(s)

    if level == "shot_plan":
        # 分镜变化：本镜头之后的镜头编号与场次会失效，整段从 shots.yaml 中截断并交由 C-4 重规划
        shots_doc["shots"] = shots[:idx]
        shots_doc["invalidated_from"] = sid
        shots_doc["invalidated_at"] = _now()
        removed.append("shots.yaml(尾部)")
    cio.save_shots(project_dir, no, shots_doc)

    review_path = cdir / "review.json"
    if review_path.is_file():
        review = read_json(review_path)
        for s in reset:
            if s in review.get("items", {}):
                review["items"][s]["status"] = "stale"
        write_json_atomic(review_path, review)

    if level == "dialogue":
        # 台词变化必须在剧本中仍可逐字找到，否则标记剧本需人工处理
        script_path = cdir / "script.md"
        if script_path.is_file():
            cio.mark_stage(project_dir, no, "script", "stale", "台词已修改，建议重新校验剧本")

    stale_out = propagate_chapter_stale(project_dir, no, reason=f"{sid}:{level}")
    return {"shot": sid, "level": level, "removed": removed, "reset": reset, **stale_out}


def propagate_chapter_stale(project_dir: Path, no: int, reason: str) -> Dict[str, Any]:
    """章节视频与全书成片标记为 stale。只改状态，不删除文件。"""
    cdir = cio.chapter_dir(project_dir, no)
    marked_chapter = False
    if (cdir / f"chapter_{no:03d}.mp4").is_file():
        cio.mark_stage(project_dir, no, "compose", "stale", reason)
        marked_chapter = True
    cio.set_chapter_status(project_dir, no, "stale", f"章节内容已变更：{reason}")
    marked_final = False
    final = project_dir / "exports" / "video.mp4"
    if marked_chapter and final.is_file():
        mark_final_stale(project_dir, reason=f"第 {no} 章变更")
        marked_final = True
    return {"chapter_stale": marked_chapter, "final_stale": marked_final}


def mark_final_stale(project_dir: Path, reason: str) -> None:
    path = project_dir / "exports" / "stale.json"
    write_json_atomic(path, {"stale": True, "reason": reason, "at": _now()})


def clear_final_stale(project_dir: Path) -> None:
    (project_dir / "exports" / "stale.json").unlink(missing_ok=True)


def final_is_stale(project_dir: Path) -> bool:
    return (project_dir / "exports" / "stale.json").is_file()
