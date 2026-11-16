"""steps/chapter_cover.py — 章节封面（可选，默认关闭）。

开启后，每章合成时在章节视频最前面加一段静帧封面：
  * 背景图 chapters/NNN/cover_bg.png：专门为本章生成（生图接口，要求无文字）。已存在则沿用，不重复调用接口；
  * 叠字 chapters/NNN/cover.png：程序绘制「小说名 / 第 N 章 / 章节标题」，免费、秒级，每次合成都会按当前设置重绘；
  * 封面时长 duration_sec（默认 3 秒）。
设置存于 project.json 的 chapter_cover；旧项目没有该键，等同于关闭，章节合成与以前完全一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .. import cancel, joblog
from ..call_log import log_call
from ..chapter_io import chapter_dir, chapter_row, source_text
from ..media import ffmpeg as mf
from ..media.cover import LAYOUTS, CoverError, render_chapter_cover
from ..project_store import read_json
from .global_visual import StepError

DEFAULTS: Dict[str, Any] = {"enabled": False, "book_title": None, "layout": "center", "duration_sec": 3.0}


def chapter_cover_settings(project_dir: Path) -> Dict[str, Any]:
    """读取设置，缺省值补齐。book_title 为空时用项目书名。"""
    try:
        proj = read_json(Path(project_dir) / "project.json")
    except (OSError, ValueError):
        proj = {}
    cfg = {**DEFAULTS, **(proj.get("chapter_cover") or {})}
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["book_title"] = (cfg.get("book_title") or proj.get("title") or "").strip()
    if cfg.get("layout") not in LAYOUTS:
        cfg["layout"] = DEFAULTS["layout"]
    try:
        cfg["duration_sec"] = min(8.0, max(1.0, float(cfg.get("duration_sec") or 3.0)))
    except (TypeError, ValueError):
        cfg["duration_sec"] = 3.0
    return cfg


def _paths(project_dir: Path, no: int):
    cdir = chapter_dir(project_dir, no)
    return cdir / "cover_bg.png", cdir / "cover.png"


def _bg_prompt(project_dir: Path, no: int, title: str) -> str:
    from .. import video_format
    meta = read_json(project_dir / "project.json")
    fmt = video_format.load(project_dir)
    orient = "vertical" if fmt.is_portrait else "horizontal"
    try:
        excerpt = " ".join(source_text(project_dir, no).split())[:160]
    except (OSError, KeyError, ValueError):
        excerpt = ""
    return (f"Cinematic {orient} poster background for one chapter of a story, no text, no letters, "
            f"no typography, no watermark, leave calm space in the middle for a title. "
            f"Mood and palette: {meta.get('art_style') or ''}. Chapter theme: {title}. "
            f"Story excerpt (for atmosphere only): {excerpt}")


def ensure_chapter_cover(project_dir: Path, no: int, image=None) -> Optional[Path]:
    """返回叠好字的 cover.png 路径；未开启返回 None。
    背景图缺失且有 image provider 时生成；没有 provider 时（如重新合成）无法生成，记警告并返回 None。"""
    project_dir = Path(project_dir)
    cfg = chapter_cover_settings(project_dir)
    if not cfg["enabled"]:
        return None
    mf.use_project_format(project_dir)
    bg, cover = _paths(project_dir, no)
    row = chapter_row(project_dir, no)
    title = str(row.get("title") or "").strip()
    if not bg.is_file():
        if image is None:
            joblog.log(project_dir, "chapter",
                       f"第 {no} 章 章节封面：缺少背景图且当前没有生图接口，本次合成不加封面（重新运行本章会生成）",
                       level="warn")
            return None
        cancel.check()
        joblog.log(project_dir, "chapter", f"第 {no} 章 章节封面：生成背景图…")
        from .. import video_format
        fmt = video_format.load(project_dir)
        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 章节封面背景图"):
            res = cancel.call(image.generate, _bg_prompt(project_dir, no, title), bg, ratio=fmt.aspect_ratio)
        log_call(project_dir, {"kind": "image", "provider": getattr(image, "name", "?"), "entity": f"chapter_cover_{no}",
                               "stage": "chapter_cover", "ok": res.ok, "error": (res.error or "")[:300] or None})
        if not res.ok:
            raise StepError(f"章节封面背景生成失败：{res.error}")
    try:
        render_chapter_cover(bg, cover, cfg["book_title"], int(no), title, cfg["layout"])
    except CoverError as exc:
        raise StepError(str(exc)) from exc
    joblog.log(project_dir, "chapter", f"第 {no} 章 章节封面叠字完成（布局 {cfg['layout']}）", level="ok")
    return cover


def prepend_cover(cover_png: Path, video: Path, seconds: float) -> None:
    """把封面静帧接到章节视频最前面，结果覆盖 video。"""
    work = video.parent / ".cover_work"
    work.mkdir(exist_ok=True)
    try:
        intro = mf.still_clip(cover_png, work / "intro.mp4", seconds)
        merged = work / "merged.mp4"
        mf.concat([intro, video], merged)
        merged.replace(video)
    finally:
        for p in work.glob("*"):
            p.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass
