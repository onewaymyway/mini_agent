#!/usr/bin/env python3
"""compose_final_video_v2.py — 小说转视频 v2 六段流程的最后一步：把所有
`status: done` 的 `macro_scene_XX/macro_scene_XX.mp4` 按 `macro_scenes.yaml`
顺序拼接成最终 `video.mp4`。

改造自 v1 `compose_novel_video.py`，核心差异（方案文档
`next_doc/novel_video_generator_plan_v2.md` 第 7 节）：
  - 输入单位从 v1 的 scene clip 换成 v2 的"已完整合成好音轨+字幕"的大场景
    视频，本脚本只做"大场景之间"的拼接，不再处理逐场景音轨/字幕（那些
    在 `novel-scene-video-generator` 的 `compose_macro_scene.py` 里已经
    做完）；
  - 新增 `transition_mode`：`cut`（默认，硬切，行为等价于 v1）/ `fade`
    （每两个大场景之间插入一段可配置时长的黑场淡入淡出，会增加总时长）；
  - 封面效果（v7 改造，见 `next_doc/novel_video_studio_fix_plan_v7.md`）：
    不再是 v1/v6 的"挤压/替换第一个大场景视频前几秒、不改变总时长"，
    改为在最前面**新增**一段独立封面片段（静音轨），会增加总时长；
    仅当 `novel_project.json.cover.enabled == true` 时触发，新功能
    默认关闭；
  - 仍不接 BGM（`bgm_enabled` 恒 false，理由同 v1/v2 方案）。

用法：
    python compose_final_video_v2.py <output_dir> \
        [--output video.mp4] [--transition-mode cut|fade] \
        [--transition-duration 0.5] [--cover-duration 3] [--no-cover] \
        [--font-path <字体路径>] [--allow-missing-macro-scenes]

产物：<output_dir>/video.mp4（或 --output 指定路径）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from common import macro_scene_dir_name, resolve_ffmpeg, resolve_ffprobe

# ffmpeg/ffprobe 查找统一改用 common.py 的自动探测（`resolve_ffmpeg()`/
# `resolve_ffprobe()`），不再在这里硬编码任何 Windows 专属路径。
#
# [BUGFIX] 此前这里自己维护一份 `_FFMPEG_CANDIDATES`/`_FFPROBE_CANDIDATES`
# 硬编码列表，第一项是某台开发机上的具体用户名路径
# （`C:\Users\onewa\.conda\envs\mv_env\...`），完全不读
# `NOVEL_FFMPEG_PATH`/`NOVEL_FFPROBE_PATH` 环境变量，也没有 conda 环境
# 自动探测；且 `_resolve_bin()` 找不到时会静默 fallback 成
# `candidates[-1]`（同样是一条写死、大概率不存在的路径），而不是明确报错——
# 换一台电脑（或者同一台电脑但用户名不叫 onewa）大概率直接在后面某次
# `subprocess.run` 时报出一个语焉不详的"文件不存在"，而不是在启动时就
# 说清楚"找不到 ffmpeg，已尝试以下方式"。现在改为与 `compose_macro_scene.py`
# 相同的模式：FFMPEG/FFPROBE 在 `main()` 里用 `resolve_ffmpeg()`/
# `resolve_ffprobe()` 解析，找不到时直接在启动时报清楚原因并退出，不再
# 硬编码任何本机专属路径，也不允许静默用一条不存在的路径往下跑。
FFMPEG = None
FFPROBE = None

ASPECT_TO_SIZE = {
    "16:9": "1280:720",
    "9:16": "720:1280",
}


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败:\n{' '.join(cmd)}\nstderr:\n{result.stderr[-3000:]}")
    return result


def get_dur(path):
    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(path)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return float(json.loads(r.stdout)["format"]["duration"])
    raise RuntimeError(f"无法获取媒体时长: {path}")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_target_size(aspect_ratio: str) -> str:
    if aspect_ratio in ASPECT_TO_SIZE:
        return ASPECT_TO_SIZE[aspect_ratio]
    try:
        wr, hr = [float(x) for x in aspect_ratio.split(":")]
        base_h = 720
        w = int(round(base_h * wr / hr / 2) * 2)
        return f"{w}:{base_h}"
    except Exception:
        return "1280:720"


def load_macro_scenes(output_dir: Path):
    data = _load_yaml(output_dir / "macro_scenes.yaml")
    raw = data.get("macro_scenes", [])
    if not raw:
        raise RuntimeError("macro_scenes.yaml 里没有任何 macro_scenes，无法合成")

    scenes = []
    for m in raw:
        mid = m["id"]
        macro_dir = output_dir / macro_scene_dir_name(mid)
        video_path = macro_dir / f"{macro_scene_dir_name(mid)}.mp4"
        scenes.append({
            "id": mid,
            "title": m.get("title"),
            "video": video_path if video_path.exists() else None,
            "status": m.get("status"),
        })
    return scenes


def normalize_clip(src: Path, dst: Path, target_size: str, fps: int, preset: str) -> None:
    """统一分辨率/帧率/像素格式/音频采样率，避免不同大场景视频（可能由
    不同批次跑出来）参数不完全一致导致后续 filter_complex concat 报错。"""
    W, H = [int(x) for x in target_size.split(":")]
    _run([
        FFMPEG, "-y", "-i", str(src),
        "-vf", (f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"),
        "-ar", "44100", "-ac", "2",
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])


def make_black_silence(dst: Path, target_size: str, duration: float, fps: int, preset: str) -> None:
    W, H = [int(x) for x in target_size.split(":")]
    _run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}:d={duration}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(dst),
    ])


def apply_fade(src: Path, dst: Path, fade_in: bool, fade_out: bool, fade_dur: float, preset: str) -> None:
    dur = get_dur(src)
    vf_parts = []
    af_parts = []
    if fade_in:
        vf_parts.append(f"fade=t=in:st=0:d={fade_dur:.3f}")
        af_parts.append(f"afade=t=in:st=0:d={fade_dur:.3f}")
    if fade_out:
        st = max(0.0, dur - fade_dur)
        vf_parts.append(f"fade=t=out:st={st:.3f}:d={fade_dur:.3f}")
        af_parts.append(f"afade=t=out:st={st:.3f}:d={fade_dur:.3f}")
    if not vf_parts:
        shutil.copyfile(src, dst)
        return
    _run([
        FFMPEG, "-y", "-i", str(src),
        "-vf", ",".join(vf_parts),
        "-af", ",".join(af_parts),
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])


def build_intro_clip(cover_image: Path, cover_duration: float, target_size: str,
                      target_fps: int, preset_scale: str, workdir: Path) -> Path:
    """构造一段独立的封面片段（画面用封面图做缓慢缩放的 ken-burns 效果，
    音轨用静音），产出的文件之后会被**前置拼接**在第一个大场景视频前面
    ——这是新增片段，不是像 v6 及更早版本那样"替换第一个大场景视频的
    前几秒"，所以总时长会相应增加，不再对 cover_duration 做"clamp 到
    大场景时长比例以内"的限制，直接用调用方传入的时长。"""
    cover_dur = max(0.3, cover_duration)
    W, H = [int(x) for x in target_size.split(":")]
    frame_count = max(1, int(round(cover_dur * target_fps)))

    intro = workdir / "cover_intro.mp4"
    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='min(zoom+0.0008,1.15)':d={frame_count}:s={W}x{H}:fps={target_fps},"
        f"format=yuv420p"
    )
    _run([
        FFMPEG, "-y",
        "-loop", "1", "-i", str(cover_image),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", vf,
        "-t", f"{cover_dur:.3f}",
        "-c:v", "libx264", "-preset", preset_scale, "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(intro),
    ])
    print(f"  Cover intro built: {cover_image} ({cover_dur:.2f}s), total will increase by this amount")
    return intro


def main():
    parser = argparse.ArgumentParser(description="小说转视频 v2 最终合成（大场景拼接+转场）")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--output", default=None, help="默认 <output_dir>/video.mp4")
    parser.add_argument("--transition-mode", default=None, choices=["cut", "fade"],
                         help="不传则读取 novel_project.json.transition_mode，默认 cut")
    parser.add_argument("--transition-duration", type=float, default=None,
                         help="fade 模式下每次转场的黑场时长（秒），不传则读取 "
                              "novel_project.json.transition_duration_sec，默认 0.5")
    parser.add_argument("--target-fps", type=int, default=24)
    parser.add_argument("--preset-scale", default="veryfast")
    parser.add_argument("--cover-duration", type=float, default=None,
                         help="封面片段时长（秒），不传则读取 "
                              "novel_project.json.cover.duration_sec，默认 3.0")
    parser.add_argument("--no-cover", action="store_true",
                         help="临时关闭封面效果（即使 novel_project.json.cover.enabled 为 true）")
    parser.add_argument("--allow-missing-macro-scenes", action="store_true",
                         help="[默认关闭] 允许跳过还没有合成出 macro_scene_XX.mp4 的大场景，"
                              "正常流程应确保所有 status:done 的大场景都已生成视频")
    parser.add_argument("--font-path", default=None, help="保留参数位（本脚本不渲染字幕），兼容调用方传入")
    args = parser.parse_args()

    if not HAS_YAML:
        print("缺少 pyyaml，请先 pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    global FFMPEG, FFPROBE
    try:
        FFMPEG = resolve_ffmpeg()
        FFPROBE = resolve_ffprobe()
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(2)
    print(f"[env] ffmpeg={FFMPEG} ffprobe={FFPROBE}", file=sys.stderr)

    output_dir: Path = args.output_dir
    project = _load_json(output_dir / "novel_project.json")
    aspect_ratio = project.get("aspect_ratio", "16:9")
    target_size = resolve_target_size(aspect_ratio)
    transition_mode = args.transition_mode or project.get("transition_mode", "cut")
    transition_dur = args.transition_duration
    if transition_dur is None:
        transition_dur = project.get("transition_duration_sec", 0.5)
    output = Path(args.output) if args.output else (output_dir / "video.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)

    macro_scenes = load_macro_scenes(output_dir)
    print(f"Loaded {len(macro_scenes)} macro scenes, target_size={target_size}, "
          f"transition_mode={transition_mode}"
          + (f", transition_duration={transition_dur}s" if transition_mode == "fade" else ""))

    missing = [m["id"] for m in macro_scenes if m["video"] is None]
    if missing and not args.allow_missing_macro_scenes:
        print(f"❌ 缺少已合成视频的大场景（共 {len(missing)} 个）：{missing}", file=sys.stderr)
        print("请先用 novel-scene-video-generator 的 compose_macro_scene.py 逐个大场景合成，"
              "确认 macro_scenes.yaml 里对应 status 为 done 后再执行本命令。", file=sys.stderr)
        sys.exit(1)
    ready_scenes = [m for m in macro_scenes if m["video"] is not None]
    if not ready_scenes:
        raise RuntimeError("没有任何已合成的大场景视频，无法合成最终视频")
    if missing:
        print(f"  [警告] 跳过 {len(missing)} 个尚未合成的大场景：{missing}", file=sys.stderr)

    not_done = [m["id"] for m in ready_scenes if m["status"] != "done"]
    if not_done:
        print(f"  [警告] 以下大场景视频文件已存在但 status 不是 done：{not_done}（不影响合成，"
              f"建议检查 compose_macro_scene.py 是否正常回写了状态）", file=sys.stderr)

    workdir = Path(tempfile.mkdtemp(prefix="novel_final_compose_"))
    print(f"Work dir: {workdir}")

    # ── 1. 统一规格 ──────────────────────────────────────────────────
    norm_dir = workdir / "normalized"
    norm_dir.mkdir()
    normalized_paths = []
    macro_durations = []
    for m in ready_scenes:
        dst = norm_dir / f"{m['id']}.mp4"
        normalize_clip(m["video"], dst, target_size, args.target_fps, args.preset_scale)
        normalized_paths.append(dst)
        macro_durations.append(get_dur(dst))
    macro_dur_sum = sum(macro_durations)
    print(f"Normalized {len(normalized_paths)} macro videos, total {macro_dur_sum:.2f}s")

    # ── 2. 封面：新增一段独立片段，前置拼接在第一个大场景之前 ─────────
    # 触发条件是 novel_project.json.cover.enabled == true（新功能默认
    # 关闭，缺省字段视为未开启）且 cover.png 存在；--no-cover 可临时
    # 覆盖关闭。这段片段是"新增"，不是像更早版本那样"替换第一个大场景
    # 开头几秒"，所以后面总时长的预期计算要把它加进去。
    cover_cfg = project.get("cover", {}) or {}
    cover_enabled = bool(cover_cfg.get("enabled", False)) and not args.no_cover
    cover_image = output_dir / "global" / "assets" / "cover.png"
    cover_dur = 0.0
    intro_clip = None
    if cover_enabled and not cover_image.exists():
        print("  [警告] novel_project.json.cover.enabled=true 但未找到 "
              "global/assets/cover.png，跳过封面效果（见 "
              "references/revision_and_rollback.md §事后补建封面 补生成）", file=sys.stderr)
    elif cover_enabled:
        cover_dur = args.cover_duration if args.cover_duration is not None \
            else float(cover_cfg.get("duration_sec", 3.0))
        intro_clip = build_intro_clip(cover_image, cover_dur, target_size,
                                       args.target_fps, args.preset_scale, workdir)

    # ── 3. 拼接（cut 直接拼，fade 插入黑场转场 + 首尾淡入淡出） ────────
    segments = []
    if transition_mode == "fade" and len(normalized_paths) > 1:
        half = transition_dur / 2.0
        for i, p in enumerate(normalized_paths):
            faded = workdir / f"faded_{i:02d}.mp4"
            apply_fade(p, faded, fade_in=(i > 0), fade_out=(i < len(normalized_paths) - 1),
                       fade_dur=half, preset=args.preset_scale)
            segments.append(faded)
            if i < len(normalized_paths) - 1:
                black = workdir / f"black_{i:02d}.mp4"
                make_black_silence(black, target_size, transition_dur, args.target_fps, args.preset_scale)
                segments.append(black)
    else:
        segments = normalized_paths

    if intro_clip is not None:
        # 封面片段前置拼接，不参与 fade 转场逻辑（转场只发生在大场景
        # 之间），直接拼在最前面。
        segments = [intro_clip] + segments

    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"file '{s.as_posix()}'\n")
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "18",
          "-c:a", "aac", "-b:a", "192k", str(output)])

    expected_dur = macro_dur_sum
    if transition_mode == "fade" and len(normalized_paths) > 1:
        expected_dur += transition_dur * (len(normalized_paths) - 1)
    if intro_clip is not None:
        expected_dur += cover_dur

    report = validate_output(output, target_size, expected_dur)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nFinal video: {output}")
    if not report["ok"]:
        sys.exit(1)


def validate_output(output: Path, target_size: str, expected_dur: float) -> dict:
    errors = []
    warnings = []
    W, H = [int(x) for x in target_size.split(":")]

    final_dur = get_dur(output)
    if abs(final_dur - expected_dur) > 2.0:
        errors.append(f"总时长 {final_dur:.1f}s 与预期 {expected_dur:.1f}s（各大场景时长之和"
                      f"[+转场时长][+封面时长，若启用]）相差过大")

    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams", str(output)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    bitrate = None
    width = height = None
    if r.returncode == 0 and r.stdout.strip():
        streams = json.loads(r.stdout).get("streams", [])
        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if vstream:
            width = vstream.get("width")
            height = vstream.get("height")
            fmt = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                                   "-show_format", str(output)], capture_output=True, text=True)
            bitrate = vstream.get("bit_rate") or json.loads(fmt.stdout or "{}").get("format", {}).get("bit_rate")
    if bitrate is not None:
        try:
            if int(bitrate) < 1_000_000:
                warnings.append(f"视频比特率偏低（{int(bitrate)/1000:.0f} kbps < 1 Mbps）")
        except (TypeError, ValueError):
            pass
    else:
        warnings.append("未能读取到视频比特率信息")
    if width and height and (int(width) != W or int(height) != H):
        errors.append(f"最终分辨率 {width}x{height} 与目标 {W}x{H} 不一致")

    file_size_mb = output.stat().st_size / 1024 / 1024
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "duration_sec": round(final_dur, 2),
            "expected_duration_sec": round(expected_dur, 2),
            "bitrate_bps": bitrate,
            "resolution": f"{width}x{height}" if width and height else None,
            "file_size_mb": round(file_size_mb, 1),
        },
    }


if __name__ == "__main__":
    main()
