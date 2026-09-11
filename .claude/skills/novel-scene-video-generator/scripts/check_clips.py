"""check_clips.py — 校验 scene_plan.yaml 里的每个场景是否都已生成对应的
clips/<scene_id>.mp4，改造自 mv-generator 的同名脚本。

用法：
    python check_clips.py <output_dir>

校验项：
  1. scene_plan.yaml 里每个 scene 的 clips/<id>.mp4 是否存在且非空；
  2. 对照 scene 自身的 `status` 字段是否与磁盘状态一致（status=done 但
     文件缺失/为空，或者 status 不是 done 但文件其实已存在——后一种不算
     错误，只是 warning，提醒 Agent 状态字段没跟上，不影响是否能进入
     novel-video-composer，只要文件本身存在即可）。

退出码：
  0 = 全部场景都有非空 clip，可以进入 novel-video-composer
  1 = 存在缺失，stdout 打印结构化问题清单，Agent 需要用
      generate_scene_videos.py（配合 --scene-id 只补缺失场景）补齐，
      重新跑本脚本直到通过
"""

from __future__ import annotations

import argparse
import json
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


def check(output_dir: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    scene_plan = _load_yaml(output_dir / "scene_plan.yaml")
    scenes = scene_plan.get("scenes", [])
    clips_dir = output_dir / "clips"

    if not scenes:
        errors.append("scene_plan.yaml 里没有任何 scenes")

    missing_ids: list[str] = []
    empty_ids: list[str] = []

    for sc in scenes:
        sc_id = sc.get("id")
        clip_path = clips_dir / f"{sc_id}.mp4"
        if not clip_path.exists():
            missing_ids.append(sc_id)
            if sc.get("status") == "done":
                errors.append(f"scene {sc_id} status=done 但 clip 文件不存在：{clip_path}")
        elif clip_path.stat().st_size == 0:
            empty_ids.append(sc_id)
            if sc.get("status") == "done":
                errors.append(f"scene {sc_id} status=done 但 clip 文件为空：{clip_path}")
        else:
            if sc.get("status") != "done":
                warnings.append(f"scene {sc_id} 的 clip 文件已存在，但 status 字段是 "
                                 f"{sc.get('status')!r} 不是 done（不影响交付，建议下次跑 "
                                 f"generate_scene_videos.py 时顺带刷新一下）")

    if missing_ids:
        errors.append(f"缺少 clip 的场景（共 {len(missing_ids)} 个）：{missing_ids}")
    if empty_ids:
        errors.append(f"clip 文件为空的场景（共 {len(empty_ids)} 个）：{empty_ids}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "missing_scene_ids": missing_ids,
        "empty_scene_ids": empty_ids,
        "summary": {
            "total_scenes": len(scenes),
            "ready_scenes": len(scenes) - len(missing_ids) - len(empty_ids),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    result = check(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
