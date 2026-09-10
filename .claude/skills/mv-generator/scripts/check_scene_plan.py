"""check_scene_plan.py — 校验 scene_plan.yaml 是否符合规范。

用法：
    python check_scene_plan.py <scene_plan.yaml> [--audio <mp3路径>] [--audio-duration <秒数>]

校验项：
  1. 每个 scene 的时长（end - start）必须在 [4, 12] 秒范围内
     （gen_video_with_text 单 clip 硬限制）。
  2. 所有 scene 按 start 排序后检查首尾衔接情况：
     - scene[0].start ≈ 0，scene[-1].end ≈ 音频总时长（见第 3 项）；
     - 重叠（前一个 end 晚于后一个 start）一律报错，需要调整边界——重叠
       是真实的素材冲突，没法靠慢放/快放解决；
     - 缝隙（前一个 end 早于后一个 start）：compose_mv.py 现在**无条件**
       把前一个场景强制慢放延长来填补缝隙，不设任何"幅度是否合理"的
       阈值，所以任何缝隙都只报 warning（幅度较大时会额外提示画面质量
       风险），不再报 error、不会阻断合成，不需要为了通过校验去改
       scene_plan.yaml。
  3. 场景覆盖的总时长是否等于（或接近）mp3 的实际时长——由于
     compose_mv.py 现在会把最后一个场景强制顶到/收到 audio_dur、并在
     合成末尾再做一次整体强制对齐兜底，总时长偏差同样只报 warning，
     不阻断合成。音频时长优先从 --audio（用 ffprobe 读取）或
     --audio-duration 获取，缺省时退回 scene_plan.yaml 里的
     song_meta.duration 字段。
  4. video_mode 与实际可用素材是否匹配：video_mode=reference 的场景必须有
     uses_assets 命中 recurring_assets 且带 asset_path；video_mode=keyframe
     的场景必须有 first_frame 或 last_frame。否则调用 gen_video_with_text
     时会直接 400（reference/keyframe mode 缺少对应素材）。

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
GAP_TOLERANCE = 0.5  # 秒，视为"完全衔接"的容差，小于这个值不报告
# [GAP-FIX][SYNC-FIX] compose_mv.py 现在对"空隙/超长是否在合理范围内"
# 不设任何阈值——不管是场景之间的空隙、开头空隙、结尾空隙/超长，还是
# scene_plan.yaml 规划总时长和 mp3 实际时长对不上，一律无条件用
# setpts 强制慢放/快放顶满或收紧（Step 6 场景独立缩放 + 一道"整体强制
# 对齐 audio_dur"的兜底），所以本脚本这里不会再因为空隙/总时长偏差
# 而把校验判成"fatal error 阻断继续"——那样会让 Agent 误以为必须先
# 手改 scene_plan.yaml 才能合成，但实际上 compose_mv.py 已经能兜底对齐。
# 这两个常量仅用于当空隙/偏差超出下面给出的参考线时，额外打一条更醒目
# 的 warning 提示 Agent："这里可以合成、时长一定会对齐，但缩放幅度较大，
# 画面可能出现明显的慢动作拖影/卡顿感，建议优化 scene_plan.yaml 以获得
# 更好的画面质量"——纯建议性质，不影响退出码。
AUTO_FILL_GAP_RATIO = 0.5
AUTO_FILL_GAP_ABS_MAX = 3.0
TOTAL_TOLERANCE = 2.0  # 秒，超过这个差值只是额外提示画面质量风险，不再报错阻断


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
            # [GAP-FIX][SYNC-FIX] compose_mv.py 现在无条件把 cur 场景强制
            # 慢放延长到顶满这个空隙（fill_dur = nxt.start - cur.start），
            # 不设"拉伸幅度太大就报错阻断"的阈值，所以这里统一降级为
            # warning——用 AUTO_FILL_GAP_RATIO/ABS_MAX 仅仅是为了区分提示
            # 语气：幅度较大时额外提醒可能有肉眼可见的慢动作拖影，供 Agent
            # 决定要不要顺手优化 scene_plan.yaml，但从不阻断合成流程。
            allowed_gap = min(cur["duration"] * AUTO_FILL_GAP_RATIO, AUTO_FILL_GAP_ABS_MAX)
            fill_dur = cur["duration"] + gap
            extra_ratio = round(gap / cur["duration"], 3) if cur["duration"] else None
            large_stretch = gap > allowed_gap
            warnings.append({
                "type": "timeline_gap_auto_fillable",
                "between": [cur["id"], nxt["id"]],
                "gap_seconds": gap,
                "fill_dur": round(fill_dur, 3),
                "extra_slowdown_ratio": extra_ratio,
                "large_stretch": large_stretch,
                "message": (
                    f"场景 {cur['id']}(end={cur['end']}s) 与 {nxt['id']}(start={nxt['start']}s) "
                    f"之间有 {gap}s 空隙——compose_mv.py 合成时会无条件把 {cur['id']} 强制慢放"
                    f"（延长到 {round(fill_dur, 3)}s，约多拉伸 "
                    f"{round(extra_ratio * 100, 1) if extra_ratio is not None else '?'}%）来顶满这段空隙，"
                    f"{nxt['id']} 仍会严格从 {nxt['start']}s 开始，时长一定会对齐，不需要手动修改 "
                    "scene_plan.yaml"
                    + ("；不过拉伸幅度明显偏大（超过参考线 "
                       f"{round(allowed_gap, 3)}s），画面可能出现肉眼可见的慢动作拖影/卡顿感，"
                       f"如果不满意可以考虑缩短这段空隙或插入新场景来改善画面质量（非必须）"
                       if large_stretch else ""),
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
            # [SYNC-FIX] compose_mv.py 现在会把最后一个场景强制顶到/收到
            # 真实 audio_dur（parse_scene_plan 的 window_end 逻辑），加上
            # Step 3.5 的整体强制对齐兜底，所以场景规划总时长和音频时长
            # 不一致不再是必须先改 yaml 才能合成的 fatal error，降级为
            # warning：仍然提示偏差，供 Agent 判断是否要顺手优化规划，
            # 但不阻断合成——合成出来的总时长一定会等于音频时长。
            if diff < -TOTAL_TOLERANCE:
                warnings.append({
                    "type": "total_duration_too_short",
                    "plan_end": plan_total_end,
                    "audio_duration": reference_duration,
                    "missing_seconds": round(-diff, 3),
                    "message": (
                        f"scene_plan.yaml 里最后一个场景结束于 {plan_total_end}s，"
                        f"但音频总时长为 {reference_duration}s，"
                        f"有 {round(-diff, 3)}s 没有场景覆盖——compose_mv.py 合成时会自动把"
                        "最后一个场景强制慢放顶满到音频结尾，总时长一定会对齐，"
                        "但拉伸幅度较大时画面可能有明显慢动作感，"
                        "如果不满意可以考虑在末尾补充场景或延长最后几个场景（非必须）"
                    ),
                })
            elif diff > TOTAL_TOLERANCE:
                warnings.append({
                    "type": "total_duration_too_long",
                    "plan_end": plan_total_end,
                    "audio_duration": reference_duration,
                    "excess_seconds": round(diff, 3),
                    "message": (
                        f"scene_plan.yaml 里最后一个场景结束于 {plan_total_end}s，"
                        f"超出音频总时长 {reference_duration}s 达 {round(diff, 3)}s——"
                        "compose_mv.py 合成时会自动把最后一个场景强制快放压缩到音频结尾，"
                        "总时长一定会对齐，但压缩幅度较大时画面切换会明显变快，"
                        "如果不满意可以考虑裁剪掉多余的场景规划（非必须）"
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

    # --- 5. video_mode 与实际可用素材是否匹配 ---
    # Agnes 接口：mode=reference 时 images/audios/videos 三者必须至少有一项
    # 非空，否则直接 400 invalid_request；mode=keyframe 至少需要 first_frame
    # 或 last_frame 之一。这里在规划阶段就拦下来，避免 Step 5 批量生成时
    # 才发现某个场景（比如没有定妆图的开场空镜/过渡镜头）被错误规划成
    # reference/keyframe 却没有可用素材。
    assets_by_id = {a.get("id"): a for a in (plan.get("recurring_assets") or []) if a.get("id")}
    for sc in scenes:
        sid = sc.get("id", "<missing id>")
        video_mode = sc.get("video_mode", "reference")
        if video_mode == "reference":
            uses_assets = sc.get("uses_assets") or []
            resolved = [aid for aid in uses_assets
                        if assets_by_id.get(aid, {}).get("asset_path")]
            if not resolved:
                errors.append({
                    "type": "reference_mode_without_assets",
                    "scene_id": sid,
                    "message": (
                        f"场景 {sid} 的 video_mode=reference，但 uses_assets 为空，"
                        f"或引用的定妆图在 recurring_assets 中缺少 asset_path，"
                        f"没有可用的参考图片。Agnes 接口 mode=reference 要求 "
                        f"images/audios/videos 至少一项非空，否则会 400 报错。"
                        f"该场景没有对应定妆图（比如开场空镜/过渡镜头），"
                        f"应把 video_mode 改成 text，或者补充一个定妆图并加入 uses_assets"
                    ),
                })
        elif video_mode == "keyframe":
            if not sc.get("first_frame") and not sc.get("last_frame"):
                errors.append({
                    "type": "keyframe_mode_without_frames",
                    "scene_id": sid,
                    "message": (
                        f"场景 {sid} 的 video_mode=keyframe，但既没有 first_frame 也没有 "
                        f"last_frame，应把 video_mode 改成 text，或补充首/尾帧图片"
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
