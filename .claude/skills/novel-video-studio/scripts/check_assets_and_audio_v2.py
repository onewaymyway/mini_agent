"""check_assets_and_audio_v2.py — 校验 novel-asset-generator v2（按
content_blocks 配音）的产物。

用法：
    python check_assets_and_audio_v2.py <output_dir>

检查：
  1. 所有在 macro_scene_*/scene_detail.yaml 里被引用的角色/地点，在
     global/characters.json / global/locations.json 里是否都已有非空
     asset_path。
  2. 每个 micro_scene 的每个非空 content_block 是否都有对应的音频文件
     （narration_seg_<mid>_<i>.wav 或 dialogue_<speaker>_<mid>_<i>.wav），
     文件存在且非空。
  3. 每个 micro_scene 的 duration_sec 是否已回填（非 null）且落在
     4–12 秒范围内——超出范围说明该小场景需要回
     novel-scene-detail-planner 拆分/合并 content_blocks，重新配音。

退出码：
  0 = 全部通过
  1 = 存在问题
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
    sys.exit(1)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check(output_dir: Path, min_sec: float, max_sec: float) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    characters_data = _load_json(output_dir / "global" / "characters.json")
    locations_data = _load_json(output_dir / "global" / "locations.json")
    char_by_id = {c.get("id"): c for c in characters_data.get("characters", [])}
    loc_by_id = {l.get("id"): l for l in locations_data.get("locations", [])}

    detail_files = sorted(output_dir.glob("macro_scene_*/scene_detail.yaml"))
    if not detail_files:
        errors.append("没有找到任何 macro_scene_*/scene_detail.yaml，检查是否已跑完 novel-scene-detail-planner")

    total_micro_scenes = 0
    total_duration = 0.0

    for detail_file in detail_files:
        data = _load_yaml(detail_file)
        micro_scenes = data.get("micro_scenes", []) if isinstance(data, dict) else []
        audio_dir = detail_file.parent / "audio"

        for ms in micro_scenes:
            mid = ms.get("id", "<无id>")
            total_micro_scenes += 1

            for cid in ms.get("uses_characters", []) or []:
                c = char_by_id.get(cid)
                if c is None or not (c.get("asset_path") or "").strip():
                    errors.append(f"小场景 {mid} 引用的角色 {cid} 缺少 asset_path")
            for lid in ms.get("uses_locations", []) or []:
                l = loc_by_id.get(lid)
                if l is None or not (l.get("asset_path") or "").strip():
                    errors.append(f"小场景 {mid} 引用的地点 {lid} 缺少 asset_path")

            content_blocks = ms.get("content_blocks", []) or []
            for i, block in enumerate(content_blocks):
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                btype = block.get("type", "narration")
                prefix = "narration_seg" if btype == "narration" else f"dialogue_{block.get('speaker', 'unknown')}"
                audio_path = audio_dir / f"{prefix}_{mid}_{i:02d}.mp3"
                if not audio_path.exists() or audio_path.stat().st_size == 0:
                    errors.append(f"小场景 {mid} 第{i}块（{btype}）缺少配音文件：{audio_path}")

            duration_sec = ms.get("duration_sec")
            if duration_sec is None:
                errors.append(f"小场景 {mid} 的 duration_sec 未回填")
            else:
                total_duration += duration_sec
                if duration_sec < min_sec:
                    warnings.append(f"小场景 {mid} 时长 {duration_sec}s 低于下限 {min_sec}s，建议与相邻场景合并")
                elif duration_sec > max_sec:
                    errors.append(f"小场景 {mid} 时长 {duration_sec}s 超过上限 {max_sec}s，需要拆分 content_blocks 重新配音")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "macro_scene_files": len(detail_files),
            "micro_scene_count": total_micro_scenes,
            "total_duration_sec": round(total_duration, 1),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--min-sec", type=float, default=4.0)
    parser.add_argument("--max-sec", type=float, default=12.0)
    args = parser.parse_args()

    result = check(args.output_dir, args.min_sec, args.max_sec)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
