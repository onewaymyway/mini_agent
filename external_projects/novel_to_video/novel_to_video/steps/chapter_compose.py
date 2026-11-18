"""steps/chapter_compose.py — C-9 章节合成：所有镜头都通过审核后，按顺序拼接为 chapter_NNN.mp4，并烧录字幕。

字幕（默认开启，项目选项 subtitles.enabled）：
  * 时间轴按台词块与镜头的真实时长生成，见 media/subtitles.py；
  * 对白前加说话人名，旁白原样；
  * 关闭字幕时直接输出拼接结果，与以前的行为一致。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .. import cancel, joblog
from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage, set_chapter_status
from .. import render_mode as RM
from ..media import ffmpeg as mf
from ..media import subtitles as sub
from ..project_store import read_json, write_json_atomic
from .. import shot_review as sr
from ..shot_review import shot_states
from ..video_format import load as load_format


def subtitle_settings(project_dir: Path) -> Dict[str, Any]:
    """读取字幕设置。旧项目没有 subtitles 键时按默认值（开启）处理。"""
    try:
        cfg = read_json(Path(project_dir) / "project.json").get("subtitles") or {}
    except (OSError, ValueError):
        cfg = {}
    size = cfg.get("font_size")
    return {"enabled": bool(cfg.get("enabled", True)),
            "font_size": int(size) if size else None}


def speaker_names(project_dir: Path) -> Dict[str, str]:
    """角色库中 id → 显示名（字幕中对白的说话人）。"""
    ents = load_entities(project_dir)
    out: Dict[str, str] = {}
    for c in ents["characters"]["characters"]:
        names = c.get("names") or []
        if c.get("id") and names:
            out[c["id"]] = str(names[0])
    return out


COMPOSE_KEY_FILE = "compose_key.json"


def compose_fingerprint(project_dir: Path, no: int) -> str:
    """章节合成的输入指纹：镜头素材键、剧本、格式、字幕、动态漫转场、章节封面设置。
    任一变化都会得到不同的指纹，从而触发重新合成；未变化则可以跳过 C-9。"""
    from ..video_format import load as _load_fmt
    from .. import render_mode as _RM
    from . import chapter_cover as _CC
    from ..chapter_io import sha12
    project_dir = Path(project_dir)
    meta = read_json(project_dir / "project.json")
    still = _RM.mode_of(meta) == _RM.STILL
    key_field = "still_key" if still else "clip_key"
    shots = load_shots(project_dir, no).get("shots", [])
    cdir = chapter_dir(project_dir, no)
    script = cdir / "script.md"
    script_hash = sha12(script.read_text(encoding="utf-8")) if script.is_file() else "no-script"
    parts = [script_hash, str(_load_fmt(project_dir).key)]
    parts += [f"{sh['id']}:{sh.get(key_field)}:{sh.get('duration_sec')}" for sh in shots]
    parts.append(repr(sorted(subtitle_settings(project_dir).items())))
    parts.append(repr(sorted((_RM.still_settings(meta) if still else {}).items())))
    parts.append(repr(sorted(_CC.chapter_cover_settings(project_dir).items())))
    return sha12(*parts)


def _compose_record(project_dir: Path, no: int) -> Dict[str, Any]:
    path = chapter_dir(project_dir, no) / COMPOSE_KEY_FILE
    return (read_json(path) or {}) if path.is_file() else {}


def _is_current(project_dir: Path, no: int, fingerprint: str) -> bool:
    """指纹一致，且章节封面（若已开启）确实已加入成片：没有背景图时封面可能未生成，不能当作已是最新。"""
    from . import chapter_cover as _CC
    rec = _compose_record(project_dir, no)
    if rec.get("key") != fingerprint:
        return False
    if _CC.chapter_cover_settings(project_dir).get("enabled") and not rec.get("cover_applied"):
        return False
    return True


def is_up_to_date(project_dir: Path, no: int) -> bool:
    """章节已合成、成片存在、所有镜头已通过审核，且输入指纹与上次合成时一致。
    队列用它跳过已完成且未修改的章节（不需要初始化引擎）。"""
    try:
        project_dir = Path(project_dir)
        out = chapter_dir(project_dir, no) / f"chapter_{no:03d}.mp4"
        if not out.is_file():
            return False
        states = shot_states(project_dir, no)
        if not states or any(st != "approved" for st in states.values()):
            return False
        return _is_current(project_dir, no, compose_fingerprint(project_dir, no))
    except (OSError, ValueError, KeyError):
        return False


def recompose_chapter(project_dir: Path, no: int) -> Dict[str, Any]:
    """重新合成本章的声音、画面与字幕，但不重新生成视频，也不调用配音接口。

    依据：每个镜头的原始视频 clips/raw 与现有配音 audio/*.wav（均已在前面的步骤生成）。
    流程：按当前配音时长重新拟合每个镜头的画面（fit_clip / mux_clip）→ 拼接 → 烧录字幕。
    因此修改了视频输出格式、字幕设置或拼接参数后，只需重新合成即可生效；审核状态不受影响，
    因为镜头的 clip_key（依据提示词、配音、时长、参考图、格式）没有改变。

    前提：所有小场景已通过审核，且每个镜头的原始视频都存在。缺少原始视频的镜头需要重新生成。
    """
    project_dir = Path(project_dir)
    shots = load_shots(project_dir, no)
    if not shots.get("shots"):
        raise RuntimeError("本章还没有镜头，请先运行本章流水线")
    from .. import render_mode as RM
    if RM.mode_of(read_json(project_dir / "project.json")) == RM.STILL:
        return _recompose_still(project_dir, no)
    # 门槛看审核记录与镜头内容是否一致（clip_key），不看 clips 里的文件是否存在：
    # 重新合成本来就会重建这些文件，所以它们缺失并不代表未通过审核。
    review = sr.load_shot_review(project_dir, no)["items"]
    not_ready = []
    for sh in shots["shots"]:
        item = review.get(sh["id"], {})
        ok = (sh.get("clip_key") and item.get("status") == "approved"
              and item.get("approved_key") == sh["clip_key"])
        if not ok:
            not_ready.append(sh["id"])
    if not_ready:
        raise RuntimeError(f"还有 {len(not_ready)} 个小场景未通过审核，不能重新合成：" + "、".join(not_ready))
    cdir = chapter_dir(project_dir, no)
    raw_dir, audio_dir = cdir / "clips" / "raw", cdir / "audio"     # 原始视频与 C-8 的目录一致：clips/raw
    missing_raw = [sh["id"] for sh in shots["shots"] if not (raw_dir / f"{sh['id']}.mp4").is_file()]
    if missing_raw:
        raise RuntimeError("缺少原始视频，无法重新合成（这些镜头需要重新生成）：" + "、".join(missing_raw))

    mf.use_project_format(project_dir)
    joblog.log(project_dir, "chapter",
               f"第 {no} 章 重新合成：不调用视频与配音接口，按现有素材重做 {len(shots['shots'])} 个镜头的声音与画面")
    for sh in shots["shots"]:
        cancel.check()
        sid = sh["id"]
        raw = raw_dir / f"{sid}.mp4"
        clip = cdir / "clips" / f"{sid}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        audio = audio_dir / f"{sid}.wav"
        if audio.is_file():
            scale = mf.fit_clip(raw, audio, clip)
            joblog.log(project_dir, "chapter",
                       f"第 {no} 章 镜头 {sid}：画面按配音时长重新拟合（x{scale:.2f}）")
        else:
            mf.mux_clip(raw, None, clip, float(sh.get("duration_sec") or 4))
            joblog.log(project_dir, "chapter", f"第 {no} 章 镜头 {sid}：无配音，按镜头时长重新合成画面")

    result = run_compose(project_dir, no, force=True)
    if result.get("output"):
        set_chapter_status(project_dir, no, "done", "章节视频已重新合成（未重新生成视频）")
        if (project_dir / "exports" / "video.mp4").is_file():
            from ..revision import mark_final_stale
            mark_final_stale(project_dir, reason=f"第 {no} 章已重新合成")
        joblog.log(project_dir, "chapter", f"第 {no} 章 重新合成完成：{result['output']}", level="ok")
    return result


def _join(clips, out: Path, *, still: bool, cfg: Dict[str, Any]) -> None:
    """拼接镜头片段。动态漫：淡化（逐段黑场，时长不变）或叠化（画面重叠）；否则硬切，与视频模式相同。
    失败时错误信息附上片段数量、首尾文件名与输出路径，便于定位是哪一批素材出的问题。"""
    try:
        if still and cfg.get("transition") == "fade":
            durations = [mf.probe_duration(c) for c in clips]
            mf.concat_fade(clips, durations, out, float(cfg["transition_sec"]))
        elif still and cfg.get("transition") == "dissolve":
            durations = [mf.probe_duration(c) for c in clips]
            mf.concat_dissolve(clips, durations, out, float(cfg["transition_sec"]))
        else:
            mf.concat(clips, out)
    except mf.MediaError as exc:
        first = clips[0].name if clips else "-"
        last = clips[-1].name if clips else "-"
        raise mf.MediaError(f"章节拼接失败：{len(clips)} 个片段（首 {first}，末 {last}）→ {out}：{exc}") from exc


def _timeline_durations(clips, *, still: bool, cfg: Dict[str, Any]):
    """字幕时间轴使用的每个镜头时长：叠化时每个镜头（除最后一个）让出重叠部分；其它情况与片段时长相同。"""
    durations = [mf.probe_duration(c) for c in clips]
    if still and cfg.get("transition") == "dissolve":
        return mf.dissolve_timeline(durations, float(cfg["transition_sec"]))
    return durations


def _recompose_still(project_dir: Path, no: int) -> Dict[str, Any]:
    """动态漫的重新合成：场景图与配音不变，按当前设置重建动效片段（不调用图像、视频或配音接口），再合成。"""
    from .chapter_segments import run_segments
    states = shot_states(project_dir, no)
    not_ready = [sid for sid, st in states.items() if st != "approved"]
    if not_ready:
        raise RuntimeError(f"还有 {len(not_ready)} 个场景图未通过审核，不能重新合成：" + "、".join(not_ready))
    mf.use_project_format(project_dir)
    joblog.log(project_dir, "chapter", f"第 {no} 章 重新合成：不调用图像、视频与配音接口，按当前设置重建动效片段")
    seg = run_segments(project_dir, no)
    if seg["failed"]:
        raise RuntimeError("动效片段合成失败：" + "、".join(seg["failed"]))
    result = run_compose(project_dir, no, force=True)
    if result.get("output"):
        set_chapter_status(project_dir, no, "done", "章节视频已重新合成（场景图未重新生成）")
        if (project_dir / "exports" / "video.mp4").is_file():
            from ..revision import mark_final_stale
            mark_final_stale(project_dir, reason=f"第 {no} 章已重新合成")
        joblog.log(project_dir, "chapter", f"第 {no} 章 重新合成完成：{result['output']}", level="ok")
    return result


def run_compose(project_dir: Path, no: int, image=None, *, force: bool = False) -> Dict[str, Any]:
    """image：生图接口，仅用于首次生成章节封面背景（设置里开启章节封面时）；不传则只用已有背景图。
    force：忽略指纹缓存，强制重新合成（手动“重新合成”使用）。未变化时直接跳过，不重做拼接与字幕。"""
    shots = load_shots(project_dir, no)
    states = shot_states(project_dir, no)
    not_ready = [sid for sid, st in states.items() if st != "approved"]
    if not_ready:
        mark_stage(project_dir, no, "compose", "waiting", "等待审核：" + "、".join(not_ready))
        return {"waiting": not_ready}
    cdir = chapter_dir(project_dir, no)
    fingerprint = compose_fingerprint(project_dir, no)
    out_existing = cdir / f"chapter_{no:03d}.mp4"
    if not force and out_existing.is_file() and _is_current(project_dir, no, fingerprint):
        mark_stage(project_dir, no, "compose", "done")
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-9 合成：输入未变化，沿用已有成片，跳过合成")
        return {"waiting": [], "output": str(out_existing), "duration": round(mf.probe_duration(out_existing), 2),
                "subtitles": {}, "skipped": True}
    clips = [cdir / "clips" / f"{sh['id']}.mp4" for sh in shots["shots"]]
    missing = [str(p.name) for p in clips if not p.is_file()]
    if missing:
        raise RuntimeError(f"缺少小场景视频：{', '.join(missing)}")
    out = cdir / f"chapter_{no:03d}.mp4"
    settings = subtitle_settings(project_dir)
    meta = read_json(project_dir / "project.json")
    still = RM.mode_of(meta) == RM.STILL
    tcfg = RM.still_settings(meta)
    if still:
        RM.validate_transition(tcfg)     # 动态漫的转场设置非法时直接报错，不产生半成品
    if not settings["enabled"]:
        _join(clips, out, still=still, cfg=tcfg)
        info: Dict[str, Any] = {"enabled": False}
    else:
        raw = cdir / "chapter_nosub.mp4"
        _join(clips, raw, still=still, cfg=tcfg)
        durations = _timeline_durations(clips, still=still, cfg=tcfg)
        # 动态漫：字幕按配音的真实时长排列（exact）；视频模式保持原来的估算方式
        cues, total = sub.build_cues(shots["shots"], durations, speaker_names(project_dir), exact=still)
        fmt = load_format(project_dir)
        info = {"enabled": True,
                **sub.burn(raw, out, cues, total, fmt.width, fmt.height, cdir / "subs", settings["font_size"])}
        raw.unlink(missing_ok=True)
        shutil.rmtree(cdir / "subs", ignore_errors=True)
    # 章节封面（设置里开启才有，默认关闭）：封面叠字每次合成都按当前设置重绘；字幕已烧录在正片里，加封面不影响字幕时间轴
    from . import chapter_cover as CC
    cover = CC.ensure_chapter_cover(project_dir, no, image)
    if cover is not None:
        CC.prepend_cover(cover, out, CC.chapter_cover_settings(project_dir)["duration_sec"])
        info["cover"] = True
    write_json_atomic(cdir / COMPOSE_KEY_FILE, {"key": fingerprint, "cover_applied": bool(info.get("cover"))})
    mark_stage(project_dir, no, "compose", "done")
    return {"waiting": [], "output": str(out), "duration": round(mf.probe_duration(out), 2),
            "subtitles": info}
