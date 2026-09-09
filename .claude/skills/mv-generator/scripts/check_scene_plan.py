"""check_scene_plan.py — 校验 scene_plan.yaml 是否符合规范。

用法：
    python check_scene_plan.py <scene_plan.yaml> [--audio <mp3路径>] [--audio-duration <秒数>]

校验项：
  1. 每个 scene 的时长（end - start）必须在 [4, 12] 秒范围内
     （gen_video_with_text 单 clip 硬限制）。
  2. 所有 scene 按 start 排序后必须首尾相接、无重叠、无缺口（覆盖整首歌），
     即 scene[0].start ≈ 0，scene[i].end ≈ scene[i+1].start，
     scene[-1].end ≈ 音频总时长。
  3. 场景覆盖的总时长是否等于（或接近）mp3 的实际时长——不够长/超出太多
     都会报错，音频时长优先从 --audio（用 ffprobe 读取）或 --audio-duration
     获取，缺省时退回 scene_plan.yaml 里的 song_meta.duration 字段。

退出码：
  0 = 全部通过
  1 = 存在问题，stdout 会打印结构化的错误清单，供 Agent 据此修改
      scene_plan.yaml 后重新运行本脚本，直到通过为止再进入 Step 4。

设计上不对 yaml 做任何自动修复——本脚本只负责"发现问题"，
"如何改"必须由 Agent 结合歌词语义决定（比如是拆分还是合并场景）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print(
        json.dumps(
            {
                "ok": False,
                "fatal": "缺少 pyyaml 依赖，请先执行: pip install pyyaml",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    sys.exit(2)


MIN_SEC = 4.0
MAX_SEC = 12.0
GAP_TOLERANCE = 0.5  # 秒，允许的场景间缝隙/重叠容差
TOTAL_TOLERANCE = 2.0  # 秒，允许的总时长与音频时长差异容差


# 尝试使用 imageio_ffmpeg 内置的 ffprobe
try:
    import imageio_ffmpeg
    _IMGIO_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    _FFPROBE_PATH = _IMGIO_FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
except ImportError:
    _FFPROBE_PATH = "ffprobe"

def probe_audio_duration(audio_path: str) -> Optional[float]:
    """用 ffprobe 读取 mp3 实际时长，失败返回 None（不阻断校验，只是少一项检查）。"""
    try:
        out = subprocess.run(
            [
                _FFPROBE_PATH,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(out.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return None


def load_scene_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check(plan: dict, audio_duration: Optional[float]) -> dict:
    errors = []
    warnings = []

    scenes = plan.get("scenes") or []
    if not scenes:
        errors.append({"type": "no_scenes", "message": "scene_plan.yaml 中没有任何 scenes，无法校验"})
        return {"ok": False, "errors": errors, "warnings": warnings, "scene_count": 0}

    # --- 1. 单场景时长范围 ---
    duration_issues = []
    parsed_scenes = []
    for sc in scenes:
        sid = sc.get("id", "<missing id>")
        start = sc.get("start")
        end = sc.get("end")
        if start is None or end is None:
            errors.append({
                "type": "missing_start_end",
                "scene_id": sid,
                "message": f"场景 {sid} 缺少 start 或 end 字段",
            })
            continue
        dur = round(float(end) - float(start), 3)
        parsed_scenes.append({"id": sid, "start": float(start), "end": float(end), "duration": dur})
        if dur < MIN_SEC or dur > MAX_SEC:
            duration_issues.append({
                "type": "duration_out_of_range",
                "scene_id": sid,
                "start": start,
                "end": end,
                "duration": dur,
                "message": (
                    f"场景 {sid} 时长 {dur}s 超出 [{MIN_SEC}, {MAX_SEC}] 秒范围，"
                    + ("需要拆分成多个场景" if dur > MAX_SEC else "需要与相邻场景合并或延长")
                ),
            })
    errors.extend(duration_issues)

    # --- 2. 场景时间轴连续性（排序后检查缝隙/重叠） ---
    parsed_scenes.sort(key=lambda s: s["start"])

    if parsed_scenes and abs(parsed_scenes[0]["start"] - 0.0) > GAP_TOLERANCE:
        errors.append({
            "type": "gap_at_start",
            "scene_id": parsed_scenes[0]["id"],
            "message": (
                f"第一个场景 {parsed_scenes[0]['id']} 的 start={parsed_scenes[0]['start']}s，"
                f"未从 0 秒开始，开头 {parsed_scenes[0]['start']}s 没有画面覆盖"
            ),
        })

    for i in range(len(parsed_scenes) - 1):
        cur = parsed_scenes[i]
        nxt = parsed_scenes[i + 1]
        gap = round(nxt["start"] - cur["end"], 3)
        if gap > GAP_TOLERANCE:
            errors.append({
                "type": "timeline_gap",
                "between": [cur["id"], nxt["id"]],
                "gap_seconds": gap,
                "message": (
                    f"场景 {cur['id']}(end={cur['end']}s) 与 {nxt['id']}(start={nxt['start']}s) "
                    f"之间有 {gap}s 的空隙没有场景覆盖，这段音频将没有画面"
                ),
            })
        elif gap < -GAP_TOLERANCE:
            errors.append({
                "type": "timeline_overlap",
                "between": [cur["id"], nxt["id"]],
                "overlap_seconds": -gap,
                "message": (
                    f"场景 {cur['id']}(end={cur['end']}s) 与 {nxt['id']}(start={nxt['start']}s) "
                    f"时间上重叠了 {-gap}s，需要调整边界"
                ),
            })

    # --- 3. 总时长是否覆盖音频时长 ---
    declared_duration = None
    song_meta = plan.get("song_meta") or {}
    if isinstance(song_meta, dict):
        declared_duration = song_meta.get("duration")

    reference_duration = audio_duration if audio_duration is not None else declared_duration

    if parsed_scenes:
        plan_total_end = parsed_scenes[-1]["end"]
        if reference_duration is not None:
            diff = round(plan_total_end - float(reference_duration), 3)
            if diff < -TOTAL_TOLERANCE:
                errors.append({
                    "type": "total_duration_too_short",
                    "plan_end": plan_total_end,
                    "audio_duration": reference_duration,
                    "missing_seconds": round(-diff, 3),
                    "message": (
                        f"scene_plan.yaml 里最后一个场景结束于 {plan_total_end}s，"
                        f"但音频总时长为 {reference_duration}s，"
                        f"缺少 {round(-diff, 3)}s 的场景没有规划到，"
                        f"需要在末尾补充场景或延长最后几个场景"
                    ),
                })
            elif diff > TOTAL_TOLERANCE:
                errors.append({
                    "type": "total_duration_too_long",
                    "plan_end": plan_total_end,
                    "audio_duration": reference_duration,
                    "excess_seconds": round(diff, 3),
                    "message": (
                        f"scene_plan.yaml 里最后一个场景结束于 {plan_total_end}s，"
                        f"超出音频总时长 {reference_duration}s 达 {round(diff, 3)}s，"
                        f"需要裁剪掉多余的场景规划"
                    ),
                })
        else:
            warnings.append({
                "type": "no_reference_duration",
                "message": (
                    "未提供 --audio 或 --audio-duration，且 scene_plan.yaml 的 "
                    "song_meta.duration 也为空，无法校验场景总时长是否覆盖音频，"
                    "建议补充其中一项后重新校验"
                ),
            })

    # --- 4. 封面（cover）时长合理性 ---
    # cover 不是新插入的片段，而是"挤压/替换"排在时间轴最前面的场景的
    # 前 N 秒（compose_mv.py 的实现），所以这里必须保证 cover.duration_sec
    # 不超过第一个场景时长的一半，否则该场景剩余可用内容会被挤压得过短，
    # 甚至出现负值这种彻底不合法的情况。
    cover = plan.get("cover")
    if isinstance(cover, dict):
        cover_dur = cover.get("duration_sec")
        if cover_dur is None:
            warnings.append({
                "type": "cover_duration_missing",
                "message": "scene_plan.yaml 里定义了 cover 但未填写 duration_sec，compose_mv.py 侧会用 --cover-duration 命令行参数的默认值，建议在这里显式填写以便提前校验",
            })
        elif parsed_scenes:
            first_scene = parsed_scenes[0]
            max_allowed = round(first_scene["duration"] * 0.5, 3)
            if float(cover_dur) <= 0:
                errors.append({
                    "type": "cover_duration_invalid",
                    "message": f"cover.duration_sec={cover_dur} 不合法，必须 > 0",
                })
            elif float(cover_dur) > max_allowed:
                errors.append({
                    "type": "cover_duration_too_long",
                    "first_scene_id": first_scene["id"],
                    "first_scene_duration": first_scene["duration"],
                    "cover_duration": cover_dur,
                    "max_allowed": max_allowed,
                    "message": (
                        f"cover.duration_sec={cover_dur}s 超过第一个场景 "
                        f"{first_scene['id']}（时长 {first_scene['duration']}s）的 50%（{max_allowed}s）。"
                        f"封面是用'挤压/替换'第一个场景的前 N 秒实现的（不改变总时长），"
                        f"过长会让第一个场景剩余可用画面过短，请调小 duration_sec，"
                        f"或者如果这首歌开头有较长纯音乐前奏，考虑把第一个场景本身规划得更长一些"
                    ),
                })

    ok = len(errors) == 0
    return {
        "ok": ok,
        "scene_count": len(scenes),
        "reference_duration": reference_duration,
        "plan_end": parsed_scenes[-1]["end"] if parsed_scenes else None,
        "errors": errors,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="校验 scene_plan.yaml 是否符合 mv-generator 规范")
    parser.add_argument("scene_plan", help="scene_plan.yaml 文件路径")
    parser.add_argument("--audio", help="mp3 音频文件路径，用 ffprobe 读取实际时长做校验（优先级最高）")
    parser.add_argument("--audio-duration", type=float, help="直接指定音频时长（秒），优先于 song_meta.duration，但低于 --audio")
    args = parser.parse_args()

    plan_path = Path(args.scene_plan)
    if not plan_path.exists():
        print(json.dumps({"ok": False, "fatal": f"文件不存在: {args.scene_plan}"}, ensure_ascii=False, indent=2))
        sys.exit(2)

    plan = load_scene_plan(str(plan_path))

    audio_duration = None
    if args.audio:
        audio_duration = probe_audio_duration(args.audio)
        if audio_duration is None:
            print(f"[警告] ffprobe 读取 {args.audio} 时长失败，改用 --audio-duration / song_meta.duration", file=sys.stderr)
    if audio_duration is None and args.audio_duration is not None:
        audio_duration = args.audio_duration

    result = check(plan, audio_duration)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"]:
        print(f"\n✅ scene_plan.yaml 校验通过：共 {result['scene_count']} 个场景，"
              f"覆盖到 {result['plan_end']}s（参考音频时长 {result['reference_duration']}s）。", file=sys.stderr)
        sys.exit(0)
    else:
        print(f"\n❌ scene_plan.yaml 校验未通过，发现 {len(result['errors'])} 个问题，"
              f"请根据上面 errors 列表逐条修改后重新运行本脚本，直到通过为止。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
