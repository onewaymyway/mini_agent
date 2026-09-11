"""check_clips_v2.py — 校验 v2 流程里 micro_scene clip 的完整性，以及
（若已合成）大场景视频时长是否与所有小场景时长之和一致。

用法：
    python check_clips_v2.py <output_dir> [--macro-id macro_01 ...]

校验项：
  1. 每个 macro_scene_*/scene_detail.yaml 里每个 micro_scene 的
     clips/<id>.mp4 是否存在且非空；
  2. status 字段与磁盘状态是否一致（同 v1：status=done 但文件缺失/为空
     是错误；文件存在但 status 不是 done 只是 warning）；
  3. 若 <macro_dir>/<macro_dir_name>.mp4 已存在，校验其时长是否约等于
     该大场景所有 micro_scene.duration_sec 之和（容差 2 秒），用于确认
     `compose_macro_scene.py` 跑出来的结果没有跑偏。

退出码：
  0 = 全部通过（可以进入 novel-video-composer，前提是所有大场景都已
      合成为 macro_scene_XX.mp4 且 macro_scenes.yaml 里 status 全为 done）
  1 = 存在问题，stdout 打印结构化问题清单
"""

from __future__ import annotations

import argparse
import json
import subprocess
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(2)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _ffprobe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
                        capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        try:
            return float(json.loads(r.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return None
    return None


def _macro_dir_name(macro_id: str) -> str:
    if macro_id.startswith("macro_"):
        return f"macro_scene_{macro_id[len('macro_'):]}"
    return f"macro_scene_{macro_id}"


def check(output_dir: Path, macro_ids: list | None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if macro_ids:
        detail_files = [output_dir / _macro_dir_name(m) / "scene_detail.yaml" for m in macro_ids]
    else:
        detail_files = sorted(output_dir.glob("macro_scene_*/scene_detail.yaml"))

    if not detail_files:
        errors.append("没有找到任何 macro_scene_*/scene_detail.yaml")
        return {"ok": False, "errors": errors, "warnings": warnings, "summary": {}}

    total_micro = 0
    total_ready = 0
    missing_ids: list[str] = []
    empty_ids: list[str] = []
    macro_summaries = []

    for detail_file in detail_files:
        macro_dir = detail_file.parent
        if not detail_file.exists():
            errors.append(f"{detail_file} 不存在")
            continue
        data = _load_yaml(detail_file)
        micro_scenes = data.get("micro_scenes", []) if isinstance(data, dict) else []
        clips_dir = macro_dir / "clips"

        macro_missing = []
        macro_empty = []
        duration_sum = 0.0
        for ms in micro_scenes:
            mid = ms.get("id")
            total_micro += 1
            duration_sum += float(ms.get("duration_sec") or 0.0)
            clip_path = clips_dir / f"{mid}.mp4"
            if not clip_path.exists():
                macro_missing.append(mid)
                if ms.get("status") == "done":
                    errors.append(f"[{macro_dir.name}] micro_scene {mid} status=done 但 clip 文件不存在: {clip_path}")
            elif clip_path.stat().st_size == 0:
                macro_empty.append(mid)
                if ms.get("status") == "done":
                    errors.append(f"[{macro_dir.name}] micro_scene {mid} status=done 但 clip 文件为空: {clip_path}")
            else:
                total_ready += 1
                if ms.get("status") != "done":
                    warnings.append(f"[{macro_dir.name}] micro_scene {mid} clip 已存在，但 status 是 "
                                     f"{ms.get('status')!r} 不是 done")

        missing_ids += [f"{macro_dir.name}/{m}" for m in macro_missing]
        empty_ids += [f"{macro_dir.name}/{m}" for m in macro_empty]

        macro_video = macro_dir / f"{macro_dir.name}.mp4"
        macro_video_dur = None
        if macro_video.exists():
            macro_video_dur = _ffprobe_duration(macro_video)
            if macro_video_dur is None:
                warnings.append(f"[{macro_dir.name}] 无法读取 {macro_video.name} 的时长（未找到 ffprobe 或探测失败）")
            elif abs(macro_video_dur - duration_sum) > 2.0:
                errors.append(f"[{macro_dir.name}] {macro_video.name} 时长 {macro_video_dur:.1f}s 与所有 "
                              f"micro_scene.duration_sec 之和 {duration_sum:.1f}s 相差过大")

        macro_summaries.append({
            "macro_dir": macro_dir.name,
            "micro_scene_count": len(micro_scenes),
            "missing": macro_missing,
            "empty": macro_empty,
            "duration_sum": round(duration_sum, 2),
            "macro_video_exists": macro_video.exists(),
            "macro_video_duration": round(macro_video_dur, 2) if macro_video_dur else None,
        })

    if missing_ids:
        errors.append(f"缺少 clip 的小场景（共 {len(missing_ids)} 个）：{missing_ids}")
    if empty_ids:
        errors.append(f"clip 文件为空的小场景（共 {len(empty_ids)} 个）：{empty_ids}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "missing_micro_scene_ids": missing_ids,
        "empty_micro_scene_ids": empty_ids,
        "summary": {
            "total_micro_scenes": total_micro,
            "ready_micro_scenes": total_ready,
            "macro_scenes": macro_summaries,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--macro-id", nargs="*", default=None, help="只校验指定的大场景，不传则校验全部")
    args = parser.parse_args()

    result = check(args.output_dir, args.macro_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
