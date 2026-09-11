#!/usr/bin/env python3
"""compose_macro_scene.py — v2 流程第五步的第二段：在单个大场景内，把
所有已生成的 micro_scene clip 顺序硬切拼接，混合其 content_blocks 配音
（旁白+对话按顺序首尾相接），按 micro_scene 逐条渲染字幕，产出
`macro_scene_<id后缀>/macro_scene_<id后缀>.mp4`。

用法：
    python compose_macro_scene.py <output_dir> <macro_id> \
        [--font-path <字体路径>] [--allow-missing-clips]

改造自 novel-video-composer 的 v1 `compose_mv.py`/`compose_novel_video.py`
同款拼接/缩放/字幕手法，缩小到"单大场景"粒度，核心差异：
  - 时间轴单位从 v1 的 scene 换成 v2 的 micro_scene；
  - 每个 micro_scene 的音轨不是单个 wav，而是它 content_blocks 对应的
    若干段 wav（旁白+对话交替）按顺序拼接；
  - 不做封面处理（封面效果留到 novel-video-composer 的最终合成阶段，
    只作用于整支视频的开头，不需要在每个大场景里重复判断）；
  - 大场景内部小场景之间固定硬切（转场选项是大场景*之间*的效果，作用在
    novel-video-composer 阶段，不在本脚本处理范围内）；
  - 合成成功后，把 macro_scenes.yaml 里对应大场景的 status 从 `planned`
    回写为 `done`（失败/校验不通过时不回写，保持 `planned` 以便重跑）。

产物：<output_dir>/macro_scene_<后缀>/macro_scene_<后缀>.mp4
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

_FFMPEG_CANDIDATES = [
    r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
]
_FFPROBE_CANDIDATES = [
    r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffprobe.exe",
    r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
    r"C:\ffmpeg\bin\ffprobe.exe",
]
try:
    import imageio_ffmpeg
    _IMGIO_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    _FFMPEG_CANDIDATES.insert(0, _IMGIO_FFMPEG)
    _imgio_dir = Path(_IMGIO_FFMPEG).parent
    _imgio_name = Path(_IMGIO_FFMPEG).name.replace("ffmpeg", "ffprobe")
    _FFPROBE_CANDIDATES.insert(0, str(_imgio_dir / _imgio_name))
except ImportError:
    pass
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

ASPECT_TO_SIZE = {
    "16:9": "1280:720",
    "9:16": "720:1280",
}


def _resolve_bin(candidates, path_name):
    for c in candidates:
        if Path(c).exists():
            return c
    found = shutil.which(path_name)
    if found:
        return found
    return candidates[-1]


FFMPEG = _resolve_bin(_FFMPEG_CANDIDATES, "ffmpeg")
FFPROBE = _resolve_bin(_FFPROBE_CANDIDATES, "ffprobe")


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败:\n{' '.join(cmd)}\nstderr:\n{result.stderr[-3000:]}")
    return result


def get_dur(path):
    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
                 "-show_format", str(path)]
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


def _dump_yaml(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


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


def _macro_dir_name(macro_id: str) -> str:
    if macro_id.startswith("macro_"):
        return f"macro_scene_{macro_id[len('macro_'):]}"
    return f"macro_scene_{macro_id}"


def block_audio_path(audio_dir: Path, micro_id: str, block: dict, idx: int) -> Path:
    btype = block.get("type", "narration")
    prefix = "narration_seg" if btype == "narration" else f"dialogue_{block.get('speaker', 'unknown')}"
    return audio_dir / f"{prefix}_{micro_id}_{idx:02d}.wav"


def build_subtitle_text(content_blocks: list) -> str:
    """把一个 micro_scene 的 content_blocks 拼成一条字幕文案：旁白原样，
    对话用「」包裹，多块之间用一个空格分隔（同一屏幕展示整个小场景的
    全部台词，不做逐字逐句时间轴——novel 场景的字幕颗粒度是"小场景"而
    不是"单句"，同 v1 一个 scene 一张字幕图的设计）。"""
    parts = []
    for block in content_blocks or []:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        if block.get("type") == "dialogue":
            parts.append(f"「{text}」")
        else:
            parts.append(text)
    return " ".join(parts)


def _render_text_png(text, W, H, font, font_size, overlay_y_offset, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        draw = ImageDraw.Draw(img)
        max_w = int(W * 0.9)
        lines = []
        cur = ""
        for ch in text:
            trial = cur + ch
            tw = draw.textlength(trial, font=font) if hasattr(draw, "textlength") \
                else sum(font.getlength(c) for c in trial)
            if tw > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            lines.append(cur)

        line_h = font_size + 10
        block_h = line_h * len(lines) + 12
        y0 = H - overlay_y_offset - block_h
        for i, line in enumerate(lines):
            tw = draw.textlength(line, font=font) if hasattr(draw, "textlength") \
                else sum(font.getlength(c) for c in line)
            bw = 12
            bg_w = int(tw) + bw * 2
            x = (W - bg_w) // 2
            y = y0 + i * line_h
            draw.rectangle([x, y, x + bg_w, y + line_h], fill=(0, 0, 0, 180))
            draw.text((x + bw, y + 5), line, font=font, fill=(255, 255, 255, 255))
    img.save(path, "PNG")


def load_micro_scenes(macro_dir: Path):
    detail = _load_yaml(macro_dir / "scene_detail.yaml")
    raw = detail.get("micro_scenes", [])
    if not raw:
        raise RuntimeError(f"{macro_dir}/scene_detail.yaml 没有任何 micro_scenes")

    audio_dir = macro_dir / "audio"
    clips_dir = macro_dir / "clips"
    scenes = []
    for ms in raw:
        mid = ms["id"]
        clip_path = clips_dir / f"{mid}.mp4"
        content_blocks = ms.get("content_blocks", []) or []
        block_audio_paths = []
        for i, block in enumerate(content_blocks):
            if not (block.get("text") or "").strip():
                continue
            p = block_audio_path(audio_dir, mid, block, i)
            block_audio_paths.append(p if p.exists() else None)
        scenes.append({
            "id": mid,
            "duration_sec": float(ms.get("duration_sec") or 0.0),
            "text": build_subtitle_text(content_blocks),
            "clip": clip_path if clip_path.exists() else None,
            "block_audios": block_audio_paths,
            "status": ms.get("status"),
        })
    return scenes


def main():
    parser = argparse.ArgumentParser(description="单个大场景内合成：micro clip 拼接 + 配音混合 + 字幕")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("macro_id", help="如 macro_01")
    parser.add_argument("--target-fps", type=int, default=24)
    parser.add_argument("--font-size", type=int, default=28)
    parser.add_argument("--overlay-y-offset", type=int, default=80)
    parser.add_argument("--preset-scale", default="veryfast")
    parser.add_argument("--preset-final", default="medium")
    parser.add_argument("--allow-missing-clips", action="store_true",
                         help="[默认关闭] 允许缺 clip 的小场景借用相邻小场景画面强制拉伸填补，"
                              "正常流程应先用 check_clips_v2.py 校验通过、补齐缺失场景")
    parser.add_argument("--font-path", default=None,
                         help="中文字幕字体文件路径，非 Windows 环境需要显式指定")
    args = parser.parse_args()

    global FONT_PATH
    if args.font_path:
        FONT_PATH = args.font_path

    if not HAS_YAML:
        print("缺少 pyyaml，请先 pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    output_dir: Path = args.output_dir
    macro_id = args.macro_id
    macro_dir = output_dir / _macro_dir_name(macro_id)
    if not macro_dir.exists():
        print(f"找不到大场景工作目录: {macro_dir}", file=sys.stderr)
        sys.exit(2)

    project = _load_json(output_dir / "novel_project.json")
    aspect_ratio = project.get("aspect_ratio", "16:9")
    target_size = resolve_target_size(aspect_ratio)
    output = macro_dir / f"{macro_dir.name}.mp4"

    scenes = load_micro_scenes(macro_dir)
    print(f"[{macro_id}] Loaded {len(scenes)} micro_scenes, target_size={target_size}")

    missing_clip_ids = [s["id"] for s in scenes if s["clip"] is None]
    missing_audio_ids = [
        s["id"] for s in scenes
        if not s["block_audios"] or any(p is None for p in s["block_audios"])
    ]
    if missing_clip_ids and not args.allow_missing_clips:
        print(f"❌ 缺少 clip 文件的小场景（共 {len(missing_clip_ids)} 个）：{missing_clip_ids}", file=sys.stderr)
        print("请先运行 generate_scene_videos_v2.py 补齐（--micro-id 定向重跑），"
              "用 check_clips_v2.py 校验通过后再执行本命令。", file=sys.stderr)
        sys.exit(1)
    if missing_audio_ids:
        print(f"❌ 缺少配音的小场景（共 {len(set(missing_audio_ids))} 个）：{sorted(set(missing_audio_ids))}",
              file=sys.stderr)
        print("请先回 novel-asset-generator 用 synthesize_scene_audio.py 补齐配音。", file=sys.stderr)
        sys.exit(1)

    workdir = Path(tempfile.mkdtemp(prefix=f"macro_compose_{macro_id}_"))
    print(f"Work dir: {workdir}")

    # ── 1. 拼接每个小场景的 content_blocks 配音 -> 单个 wav；
    #      再把所有小场景的 wav 顺序拼成整个大场景的旁白/对话音轨 ──────
    per_scene_audio = {}
    for s in scenes:
        valid = [p for p in s["block_audios"] if p is not None]
        if len(valid) == 1:
            per_scene_audio[s["id"]] = valid[0]
            continue
        seg_out = workdir / f"{s['id']}_audio.wav"
        inputs = []
        filt = []
        for i, p in enumerate(valid):
            inputs += ["-i", str(p)]
            filt.append(f"[{i}:a]")
        concat_filter = "".join(filt) + f"concat=n={len(valid)}:v=0:a=1[aout]"
        _run([FFMPEG, "-y", *inputs, "-filter_complex", concat_filter, "-map", "[aout]", str(seg_out)])
        per_scene_audio[s["id"]] = seg_out

    narration_wav = workdir / "macro_audio.wav"
    audio_inputs = []
    filter_inputs = []
    for i, s in enumerate(scenes):
        audio_inputs += ["-i", str(per_scene_audio[s["id"]])]
        filter_inputs.append(f"[{i}:a]")
    concat_filter = "".join(filter_inputs) + f"concat=n={len(scenes)}:v=0:a=1[aout]"
    _run([FFMPEG, "-y", *audio_inputs, "-filter_complex", concat_filter, "-map", "[aout]", str(narration_wav)])
    audio_dur = get_dur(narration_wav)
    print(f"[{macro_id}] Narration/dialogue concatenated: {len(scenes)} segments, total {audio_dur:.2f}s")

    # ── 2. 逐小场景独立缩放到 duration_sec ────────────────────────────
    scaled_dir = workdir / "scaled"
    scaled_dir.mkdir()
    last_available_clip = None
    for s in scenes:
        clip = s["clip"]
        borrowed = False
        if clip is None:
            clip = last_available_clip
            borrowed = True
            if clip is None:
                for later in scenes:
                    if later["clip"] is not None:
                        clip = later["clip"]
                        break
        if clip is None:
            raise RuntimeError("所有小场景都没有匹配到任何 clip 文件，无法合成")
        if borrowed:
            print(f"  [强制填补] {s['id']} 没有 clip，借用 {clip.name} 强制拉伸到 "
                  f"{s['duration_sec']:.2f}s", file=sys.stderr)

        clip_dur = get_dur(clip)
        target_dur = max(s["duration_sec"], 0.1)
        scale = target_dur / clip_dur
        out_path = scaled_dir / f"{s['id']}.mp4"
        _run([
            FFMPEG, "-y",
            "-i", str(clip),
            "-vf", (f"fps={args.target_fps},setpts={scale}*PTS,"
                    f"scale={target_size}:force_original_aspect_ratio=decrease,"
                    f"pad={target_size}:(ow-iw)/2:(oh-ih)/2"),
            "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20",
            "-an",
            str(out_path),
        ])
        if not borrowed:
            last_available_clip = clip

    # ── 3. 拼接所有缩放后的 clip ───────────────────────────────────────
    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"file '{(scaled_dir / (s['id'] + '.mp4')).as_posix()}'\n")
    joined = workdir / "joined.mp4"
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20", "-an", str(joined)])
    joined_dur = get_dur(joined)
    print(f"[{macro_id}] Joined: {joined_dur:.2f}s (audio: {audio_dur:.2f}s)")

    align_scale = 1.0
    ALIGN_EPS = 0.02
    if abs(joined_dur - audio_dur) > ALIGN_EPS:
        align_scale = audio_dur / joined_dur
        print(f"  [强制对齐] joined={joined_dur:.3f}s 与配音 {audio_dur:.3f}s 不一致"
              f"（差 {joined_dur - audio_dur:+.3f}s），强制整体 setpts={align_scale:.5f} 对齐")
        aligned = workdir / "joined_aligned.mp4"
        _run([FFMPEG, "-y", "-i", str(joined),
              "-vf", f"setpts={align_scale}*PTS,fps={args.target_fps}",
              "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20", "-an", str(aligned)])
        joined = aligned
        joined_dur = get_dur(joined)

    # ── 4. 按 micro_scene 拼好的字幕文案渲染字幕 PNG ──────────────────
    from PIL import ImageFont
    W, H = [int(x) for x in target_size.split(":")]
    font = ImageFont.truetype(FONT_PATH, args.font_size)
    subs_dir = workdir / "subs"
    subs_dir.mkdir(exist_ok=True)

    text_to_png = {}
    sub_list = workdir / "subs_list.txt"
    with open(sub_list, "w", encoding="utf-8") as f:
        for s in scenes:
            text = s["text"] or ""
            if text not in text_to_png:
                png_path = subs_dir / f"cue_{len(text_to_png):04d}.png"
                _render_text_png(text, W, H, font, args.font_size, args.overlay_y_offset, png_path)
                text_to_png[text] = png_path
            dur = round(s["duration_sec"] * align_scale, 6)
            f.write(f"file '{text_to_png[text].as_posix()}'\n")
            f.write(f"duration {dur}\n")
        last_text = scenes[-1]["text"] or ""
        f.write(f"file '{text_to_png[last_text].as_posix()}'\n")
    print(f"[{macro_id}] Rendering {len(scenes)} subtitle cues ({len(text_to_png)} unique PNGs)")

    video_with_subs = workdir / "video_with_subs.mp4"
    _run([FFMPEG, "-y", "-i", str(joined), "-f", "concat", "-safe", "0", "-i", str(sub_list),
          "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[vout]",
          "-map", "[vout]", "-t", f"{joined_dur:.3f}",
          "-c:v", "libx264", "-preset", args.preset_final, "-crf", "16", "-an", str(video_with_subs)])

    print(f"[{macro_id}] Mixing narration/dialogue audio track")
    _run([FFMPEG, "-y", "-i", str(video_with_subs), "-i", str(narration_wav),
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{audio_dur:.3f}", str(output)])

    report = validate_output(output, target_size, audio_dur)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    shutil.rmtree(workdir, ignore_errors=True)
    print(f"\n[{macro_id}] Macro scene video: {output}")

    if report["ok"]:
        macro_scenes_path = output_dir / "macro_scenes.yaml"
        macro_data = _load_yaml(macro_scenes_path)
        macro_list = macro_data.get("macro_scenes", [])
        for m in macro_list:
            if m.get("id") == macro_id:
                m["status"] = "done"
        _dump_yaml(macro_scenes_path, {"macro_scenes": macro_list})
        print(f"[{macro_id}] macro_scenes.yaml 已回写 status=done")
    else:
        print(f"[{macro_id}] 校验未通过，不回写 macro_scenes.yaml 的 status（保持 planned）", file=sys.stderr)
        sys.exit(1)


def validate_output(output: Path, target_size: str, expected_dur: float) -> dict:
    errors = []
    warnings = []
    W, H = [int(x) for x in target_size.split(":")]

    final_dur = get_dur(output)
    if abs(final_dur - expected_dur) > 2.0:
        errors.append(f"总时长 {final_dur:.1f}s 与配音总时长 {expected_dur:.1f}s 相差过大")

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
        errors.append(f"分辨率 {width}x{height} 与目标 {W}x{H} 不一致")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "duration_sec": round(final_dur, 2),
            "expected_duration_sec": round(expected_dur, 2),
            "bitrate_bps": bitrate,
            "resolution": f"{width}x{height}" if width and height else None,
        },
    }


if __name__ == "__main__":
    main()
