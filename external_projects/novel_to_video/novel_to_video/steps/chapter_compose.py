"""steps/chapter_compose.py — C-9 章节合成：所有镜头都通过审核后，按顺序拼接为 chapter_NNN.mp4，并烧录字幕。

字幕（默认开启，项目选项 subtitles.enabled）：
  * 时间轴按台词块与镜头的真实时长生成，见 media/subtitles.py；
  * 对白前加说话人名，旁白原样；
  * 关闭字幕时直接输出拼接结果，与以前的行为一致。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from .. import cancel, joblog
from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage, set_chapter_status
from ..media import ffmpeg as mf
from ..media import subtitles as sub
from ..project_store import read_json
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

    result = run_compose(project_dir, no)
    if result.get("output"):
        set_chapter_status(project_dir, no, "done", "章节视频已重新合成（未重新生成视频）")
        if (project_dir / "exports" / "video.mp4").is_file():
            from ..revision import mark_final_stale
            mark_final_stale(project_dir, reason=f"第 {no} 章已重新合成")
        joblog.log(project_dir, "chapter", f"第 {no} 章 重新合成完成：{result['output']}", level="ok")
    return result


def run_compose(project_dir: Path, no: int) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    states = shot_states(project_dir, no)
    not_ready = [sid for sid, st in states.items() if st != "approved"]
    if not_ready:
        mark_stage(project_dir, no, "compose", "waiting", "等待审核：" + "、".join(not_ready))
        return {"waiting": not_ready}
    cdir = chapter_dir(project_dir, no)
    clips = [cdir / "clips" / f"{sh['id']}.mp4" for sh in shots["shots"]]
    missing = [str(p.name) for p in clips if not p.is_file()]
    if missing:
        raise RuntimeError(f"缺少小场景视频：{', '.join(missing)}")
    out = cdir / f"chapter_{no:03d}.mp4"
    settings = subtitle_settings(project_dir)
    if not settings["enabled"]:
        mf.concat(clips, out)
        info: Dict[str, Any] = {"enabled": False}
    else:
        raw = cdir / "chapter_nosub.mp4"
        mf.concat(clips, raw)
        durations = [mf.probe_duration(c) for c in clips]
        cues, total = sub.build_cues(shots["shots"], durations, speaker_names(project_dir))
        fmt = load_format(project_dir)
        info = {"enabled": True,
                **sub.burn(raw, out, cues, total, fmt.width, fmt.height, cdir / "subs", settings["font_size"])}
        raw.unlink(missing_ok=True)
        shutil.rmtree(cdir / "subs", ignore_errors=True)
    mark_stage(project_dir, no, "compose", "done")
    return {"waiting": [], "output": str(out), "duration": round(mf.probe_duration(out), 2),
            "subtitles": info}
