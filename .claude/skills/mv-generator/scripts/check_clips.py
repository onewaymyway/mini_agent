"""check_clips.py — 校验 scene_plan.yaml 里规划的所有场景视频片段是否都已生成落盘。

用法：
    python check_clips.py <scene_plan.yaml> --clips-dir <output_dir>/clips

校验项：
  1. 每个 scene 是否都能在 `--clips-dir` 下匹配到至少一个
     `<scene_id>*.mp4` 文件（与 `compose_mv.py`/`generate_scene_videos.py`
     的匹配/命名规则保持一致）。
  2. 匹配到的文件是否非空（生成失败但留下空文件/占位文件的情况也要抓出来）。
  3. 如果 `scene_plan.yaml` 里定义了 `cover`（封面图，Step 4 产物）且
     `mv_config.json` 里 `cover_enabled=true`，会额外检查
     `assets/cover.png`（合成时用作封面短片素材）是否存在非空——这个
     不属于 clips，但同样是 Step 6 合成前必须就绪的产物，一并检查
     可以少跑一次 `check_assets.py`。传 `--output-dir` 才会做这项检查，
     不传则跳过（不当作错误）。

设计上不做任何自动生成/自动修复——本脚本只负责"发现问题"，补生成
缺失的视频片段仍然要走 Step 5 的 `generate_scene_videos.py`。

退出码：
  0 = 全部通过，可以进入 Step 6 最终合成
  1 = 存在缺失，stdout 打印结构化的问题清单，Agent 需要据此重新运行
      `generate_scene_videos.py`（断点续跑，只会补齐缺失的场景），
      再重新运行本脚本，直到通过为止再进入 Step 6。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print(
        json.dumps(
            {"ok": False, "fatal": "缺少 pyyaml 依赖，请先执行: pip install pyyaml"},
            ensure_ascii=False,
            indent=2,
        )
    )
    sys.exit(2)


def load_scene_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_cover(plan: dict, output_dir: Optional[Path]) -> Optional[dict]:
    """检查封面图是否就绪。返回 None 表示不需要检查（没开封面/没传 output_dir）。"""
    cover = plan.get("cover")
    if not isinstance(cover, dict):
        return None

    cover_enabled = None
    if output_dir is not None:
        config_path = output_dir / "mv_config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                cover_enabled = bool(config.get("cover_enabled"))
            except Exception:
                cover_enabled = None

    # cover_enabled 明确为 False 时跳过；未知（没传 output_dir 或读不到配置）
    # 时按 scene_plan.yaml 里定义了 cover 为准，保守起见仍然检查。
    if cover_enabled is False:
        return None

    cover_path_str = cover.get("asset_path")
    if not cover_path_str:
        return {
            "type": "missing_cover_asset_path",
            "message": "scene_plan.yaml 中定义了 cover 但尚未回填 asset_path 字段（Step 4 生成封面图后应立即回填），Step 6 --cover-image 无法使用",
        }

    p = Path(cover_path_str)
    if not p.is_absolute() and output_dir is not None:
        p = output_dir / p
    if not p.exists():
        return {
            "type": "cover_file_not_found",
            "asset_path": cover_path_str,
            "resolved_path": str(p),
            "message": f"cover.asset_path={cover_path_str} 指向的文件不存在（解析后路径: {p}），需要重新执行 Step 4 生成封面图",
        }
    if p.stat().st_size == 0:
        return {
            "type": "cover_file_empty",
            "asset_path": cover_path_str,
            "resolved_path": str(p),
            "message": "cover 对应文件存在但大小为 0（大概率是生成失败但留下了空文件），需要重新生成",
        }
    return None


def check(plan: dict, clips_dir: Path, output_dir: Optional[Path]) -> dict:
    errors = []
    warnings = []

    scenes = plan.get("scenes") or []
    if not scenes:
        return {
            "ok": False,
            "scenes_count": 0,
            "ready_count": 0,
            "errors": [{"type": "no_scenes", "message": "scene_plan.yaml 中没有任何 scenes"}],
            "warnings": [],
        }

    if not clips_dir.exists():
        errors.append({
            "type": "clips_dir_not_found",
            "clips_dir": str(clips_dir),
            "message": f"--clips-dir 指向的目录不存在: {clips_dir}，说明 Step 5 还没跑过或路径传错了",
        })
        return {
            "ok": False,
            "scenes_count": len(scenes),
            "ready_count": 0,
            "errors": errors,
            "warnings": warnings,
        }

    missing = []
    empty_file = []
    ready_ids = []

    for sc in scenes:
        scene_id = sc.get("id", "<missing id>")
        # 匹配规则需与 compose_mv.py 的 parse_scene_plan()（glob(scene_id + "*.mp4")）
        # 及 generate_scene_videos.py 的命名（<scene_id>.mp4）保持一致。
        matched = sorted(clips_dir.glob(scene_id + "*.mp4"))
        if not matched:
            missing.append({
                "type": "clip_not_found",
                "scene_id": scene_id,
                "message": f"场景 {scene_id} 在 {clips_dir} 下没有匹配到任何 {scene_id}*.mp4 文件，需要执行/重新执行 Step 5 生成该场景视频",
            })
            continue
        non_empty = [m for m in matched if m.stat().st_size > 0]
        if not non_empty:
            empty_file.append({
                "type": "clip_file_empty",
                "scene_id": scene_id,
                "files": [str(m) for m in matched],
                "message": f"场景 {scene_id} 匹配到的文件大小均为 0（大概率是生成失败但留下了空文件），需要重新执行 Step 5 生成该场景视频",
            })
            continue
        ready_ids.append(scene_id)

    errors.extend(missing)
    errors.extend(empty_file)

    cover_error = check_cover(plan, output_dir)
    cover_ready = None
    if cover_error is not None:
        errors.append(cover_error)
        cover_ready = False
    elif isinstance(plan.get("cover"), dict):
        cover_ready = True

    ok = len(errors) == 0
    return {
        "ok": ok,
        "scenes_count": len(scenes),
        "ready_count": len(ready_ids),
        "missing_scene_ids": [m["scene_id"] for m in missing],
        "empty_scene_ids": [e["scene_id"] for e in empty_file],
        "cover_ready": cover_ready,
        "errors": errors,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="校验 scene_plan.yaml 里的所有场景视频片段是否都已生成落盘")
    parser.add_argument("scene_plan", help="scene_plan.yaml 文件路径")
    parser.add_argument("--clips-dir", required=True, help="分场景视频片段所在目录（通常是 <output_dir>/clips）")
    parser.add_argument("--output-dir", help="MV 输出目录，用来定位 mv_config.json（判断 cover_enabled）及解析 cover 的相对路径；不提供则跳过封面检查")
    args = parser.parse_args()

    plan_path = Path(args.scene_plan)
    if not plan_path.exists():
        print(json.dumps({"ok": False, "fatal": f"文件不存在: {args.scene_plan}"}, ensure_ascii=False, indent=2))
        sys.exit(2)

    plan = load_scene_plan(str(plan_path))
    clips_dir = Path(args.clips_dir)
    output_dir = Path(args.output_dir) if args.output_dir else None

    result = check(plan, clips_dir, output_dir)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"]:
        print(f"\n✅ 视频片段校验通过：{result['ready_count']}/{result['scenes_count']} 个场景均已生成落盘，可以进入 Step 6 最终合成。",
              file=sys.stderr)
        sys.exit(0)
    else:
        missing_ids = result.get("missing_scene_ids") or []
        empty_ids = result.get("empty_scene_ids") or []
        need_regen = sorted(set(missing_ids) | set(empty_ids))
        print(f"\n❌ 视频片段校验未通过，{result.get('ready_count', 0)}/{result.get('scenes_count', '?')} "
              f"个场景就绪，发现 {len(result['errors'])} 个问题。", file=sys.stderr)
        if need_regen:
            print(f"需要重新运行 generate_scene_videos.py 补齐以下场景（断点续跑，已成功的场景会自动跳过，"
                  f"不会重复生成）: {', '.join(need_regen)}", file=sys.stderr)
        print("在校验通过之前，不能进入 Step 6 最终合成。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
