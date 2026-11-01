"""steps/final_compose.py — 全书收尾 F-1 至 F-4。

  F-1 章节检查：所有选中章节 status == done，且章节成片未 stale；否则拒绝。
  F-2 封面（可选）：cover_bg.png 生成/沿用，cover.png 叠字；两者分离。
  F-3 全书拼接：[封面] + 各章 chapter_NNN.mp4，章节间可选淡化转场。
  F-4 成片校验：时长与期望一致（容差 0.2 秒），分辨率/帧率/音轨存在。

人工确认（任何挡位都不可跳过）：
  合成前必须有 confirm 记录（exports/confirm.json，含确认时间与成片版本号）。
  成片产生后 confirm 失效；章节再次变化会使成片 stale 并要求重新确认。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import chapter_io as cio
from .. import gates
from .. import project_store as ps
from ..media import concat_final as cf
from ..media import ffmpeg as mf
from ..media.cover import LAYOUTS, CoverError, render_cover
from ..revision import clear_final_stale, final_is_stale
from ..project_store import read_json, write_json_atomic
from .. import joblog

DURATION_TOL_SEC = 0.2
EXPORTS = "exports"


class FinalError(RuntimeError):
    pass


def _exports(project_dir: Path) -> Path:
    return project_dir / EXPORTS


def check_chapters_ready(project_dir: Path) -> Dict[str, Any]:
    """F-1：返回 {ready, chapters:[no...], problems:[...]}。"""
    rows = ps.load_project(project_dir)["chapters"]
    selected = [r for r in rows if r.get("selected")]
    problems: List[str] = []
    if not selected:
        problems.append("没有选中的章节")
    for r in selected:
        no = r["no"]
        if r.get("status") != "done":
            problems.append(f"第 {no} 章未完成（状态：{r.get('status')}）")
            continue
        cdir = cio.chapter_dir(project_dir, no)
        if not (cdir / f"chapter_{no:03d}.mp4").is_file():
            problems.append(f"第 {no} 章缺少章节成片")
        st = cio.load_state(project_dir, no).get("stages", {}).get("compose", {})
        if st.get("status") == "stale":
            problems.append(f"第 {no} 章成片已过期，需重新合成")
    return {"ready": not problems, "chapters": [r["no"] for r in selected], "problems": problems}


def cover_status(project_dir: Path) -> Dict[str, Any]:
    proj = read_json(project_dir / "project.json")
    cov = proj.get("cover") or {}
    assets = project_dir / "global" / "assets"
    return {"enabled": bool(cov.get("enabled")), "title": cov.get("title"),
            "layout": cov.get("layout", "center"), "duration_sec": cov.get("duration_sec", 3.0),
            "bg_exists": (assets / "cover_bg.png").is_file(),
            "cover_exists": (assets / "cover.png").is_file()}


def set_cover(project_dir: Path, *, enabled: bool, title: Optional[str] = None,
              layout: Optional[str] = None, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    """写入封面设置。只改配置，不生成图片；叠字与背景图由 render_cover_assets 负责。"""
    if layout is not None and layout not in LAYOUTS:
        raise FinalError(f"未知封面布局：{layout}")
    if duration_sec is not None and not (1.0 <= float(duration_sec) <= 8.0):
        raise FinalError("封面时长应在 1–8 秒之间")
    proj = read_json(project_dir / "project.json")
    cov = {**(proj.get("cover") or {})}
    cov["enabled"] = bool(enabled)
    if title is not None:
        cov["title"] = title.strip()
    if layout is not None:
        cov["layout"] = layout
    if duration_sec is not None:
        cov["duration_sec"] = float(duration_sec)
    cov.setdefault("duration_sec", 3.0)
    cov.setdefault("layout", "center")
    proj["cover"] = cov
    write_json_atomic(project_dir / "project.json", proj)
    return cov


def render_cover_assets(project_dir: Path, *, bg_provider=None) -> Dict[str, Any]:
    """F-2：若缺少 cover_bg.png 则生成（需要 image provider），再叠字得到 cover.png。"""
    cov = read_json(project_dir / "project.json").get("cover") or {}
    if not cov.get("enabled"):
        raise FinalError("封面未启用")
    title = cov.get("title") or ""
    assets = project_dir / "global" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    bg = assets / "cover_bg.png"
    if not bg.is_file():
        if bg_provider is None:
            raise FinalError("缺少 cover_bg.png，请先生成背景图")
        art = read_json(project_dir / "project.json").get("art_style") or ""
        prompt = (f"Cinematic vertical poster background, no text, no letters, no typography, no watermark. "
                  f"Mood and palette: {art}. Symbolic composition of the whole story, not a single scene.")
        joblog.log(project_dir, "final", "生成封面背景图…")
        res = bg_provider.generate(prompt, bg, ratio="9:16")
        if not res.ok:
            raise FinalError(f"封面背景生成失败：{res.error}")
    joblog.log(project_dir, "final", f"封面叠字：布局 {cov.get('layout', 'center')}")
    try:
        render_cover(bg, assets / "cover.png", title, cov.get("layout", "center"))
    except CoverError as exc:
        raise FinalError(str(exc)) from exc
    return {"bg": str(bg.relative_to(project_dir)), "cover": str((assets / "cover.png").relative_to(project_dir))}


def confirm_state(project_dir: Path) -> Dict[str, Any]:
    p = _exports(project_dir) / "confirm.json"
    return read_json(p) if p.is_file() else {"confirmed": False}


def confirm_final(project_dir: Path, *, note: str = "") -> Dict[str, Any]:
    """人工确认：对当前全部章节的快照做确认。章节再变化后确认失效。"""
    fr = check_chapters_ready(project_dir)
    if not fr["ready"]:
        raise FinalError("章节未全部就绪，不能确认：" + "；".join(fr["problems"]))
    snap = _chapter_snapshot(project_dir, fr["chapters"])
    data = {"confirmed": True, "at": datetime.now().isoformat(timespec="seconds"),
            "snapshot": snap, "note": note.strip()}
    _exports(project_dir).mkdir(parents=True, exist_ok=True)
    write_json_atomic(_exports(project_dir) / "confirm.json", data)
    ps.append_progress(project_dir, f"全书合成前人工确认（章节 {fr['chapters']}）{'：' + note.strip() if note.strip() else ''}")
    return data


def _chapter_snapshot(project_dir: Path, nos: List[int]) -> Dict[str, str]:
    out = {}
    for no in nos:
        p = cio.chapter_dir(project_dir, no) / f"chapter_{no:03d}.mp4"
        out[str(no)] = cio.sha12(str(p.stat().st_size), str(p.stat().st_mtime_ns)) if p.is_file() else "missing"
    return out


def compose_book(project_dir: Path, *, gear: str, transition: str = "cut",
                 use_cover: Optional[bool] = None) -> Dict[str, Any]:
    """F-3 + F-4。必须先通过 F-1 与人工确认；任何挡位都不跳过确认。"""
    gear = gates.normalize_gear(gear)
    if gates.is_auto(gear, "final_compose"):  # 防御：策略表保证永远为 False
        raise FinalError("内部错误：全书合成不允许自动执行")
    fr = check_chapters_ready(project_dir)
    if not fr["ready"]:
        raise FinalError("章节未全部就绪：" + "；".join(fr["problems"]))
    joblog.log(project_dir, "final", f"F-1 检查通过：{len(fr['chapters'])} 章均已完成")
    conf = confirm_state(project_dir)
    if not conf.get("confirmed"):
        raise FinalError("请先在全书合成页完成人工确认")
    if conf.get("snapshot") != _chapter_snapshot(project_dir, fr["chapters"]):
        raise FinalError("确认之后章节成片已变化，请重新确认")

    cov = read_json(project_dir / "project.json").get("cover") or {}
    enable_cover = bool(cov.get("enabled")) if use_cover is None else bool(use_cover)
    cover_png = project_dir / "global" / "assets" / "cover.png"
    if enable_cover and not cover_png.is_file():
        raise FinalError("已启用封面但缺少 cover.png，请先渲染封面")

    chapter_files = [cio.chapter_dir(project_dir, n) / f"chapter_{n:03d}.mp4" for n in fr["chapters"]]
    out = _exports(project_dir) / "video.mp4"
    joblog.log(project_dir, "final", f"F-3 开始拼接：{len(chapter_files)} 章" + ("，含封面" if enable_cover else "")
               + f"，转场 {transition}")
    res = cf.compose_final(chapter_files, out,
                           cover_png=cover_png if enable_cover else None,
                           cover_sec=float(cov.get("duration_sec", 3.0)), transition=transition)
    verdict = verify_final(out, res["expected"], expect_audio=True)
    joblog.log(project_dir, "final", f"F-4 成片校验：时长 {res['duration']} 秒（期望 {res['expected']}），"
               + ("通过" if verdict["ok"] else "未通过：" + "；".join(verdict["problems"])),
               level="ok" if verdict["ok"] else "error")
    if not verdict["ok"]:
        raise FinalError("成片校验未通过：" + "；".join(verdict["problems"]))
    clear_final_stale(project_dir)
    (_exports(project_dir) / "confirm.json").unlink(missing_ok=True)  # 成片产生后确认失效
    ps.append_progress(project_dir, f"全书合成：{res['segments']} 段，时长 {res['duration']} 秒（期望 {res['expected']}），转场 {transition}")
    return {**res, "verify": verdict}


def verify_final(path: Path, expected: float, *, expect_audio: bool = True) -> Dict[str, Any]:
    """F-4 机械校验。"""
    problems: List[str] = []
    if not path.is_file():
        return {"ok": False, "problems": ["成片不存在"], "duration": 0}
    dur = mf.probe_duration(path)
    if abs(dur - expected) > DURATION_TOL_SEC:
        problems.append(f"时长 {dur:.2f} 秒与期望 {expected:.2f} 秒相差超过 {DURATION_TOL_SEC} 秒")
    info = _probe_streams(path)
    if info.get("width") != mf.W or info.get("height") != mf.H:
        problems.append(f"分辨率 {info.get('width')}x{info.get('height')} 不是 {mf.W}x{mf.H}")
    if info.get("fps") not in (mf.FPS, round(mf.FPS, 2)):
        problems.append(f"帧率 {info.get('fps')} 不是 {mf.FPS}")
    if expect_audio and not info.get("has_audio"):
        problems.append("缺少音轨")
    return {"ok": not problems, "problems": problems, "duration": round(dur, 2), **info}


def _probe_streams(path: Path) -> Dict[str, Any]:
    import subprocess
    out = subprocess.run([mf.ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except json.JSONDecodeError:
        return {}
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps_raw = v.get("r_frame_rate", "0/1")
    try:
        n, d = fps_raw.split("/")
        fps = round(float(n) / float(d), 2) if float(d) else 0
    except ValueError:
        fps = 0
    return {"width": v.get("width"), "height": v.get("height"), "fps": fps, "has_audio": a is not None}
