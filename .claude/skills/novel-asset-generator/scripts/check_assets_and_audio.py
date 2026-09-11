"""check_assets_and_audio.py — 校验 novel-asset-generator 的产物。

用法：
    python check_assets_and_audio.py <output_dir>

合并了 mv-generator 里 check_assets.py 的职责（定妆图完整性）+ 新增音频
检查（方案文档第 2 节）。

校验项：
  1. characters.json / locations.json 每一项是否都已回填 asset_path，
     文件存在且非空。
  2. scene_plan.yaml 每个 scene 的 uses_characters / uses_locations 引用
     的 asset_path 是否都能找到（间接检查，通过角色/地点文件解析）。
  3. scene_plan.yaml 每个 scene 的 narration_audio 是否存在、非空，且
     duration_sec 是否在 [4, 12] 秒范围内。
  4. 全部 scene 的 duration_sec 之和是否落在 novel_project.json 的
     target_duration_sec 的 ±30% 区间内。

退出码：
  0 = 全部通过，可以进入 novel-scene-video-generator
  1 = 存在问题，stdout 打印结构化的问题清单

设计上不做任何自动修复——发现问题后，缺图回 Step "定妆图生成"补生成，
音频问题回 synthesize_narration.py（配合 --segment-id 只重跑受影响场景）。
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

MIN_DURATION = 4.0
MAX_DURATION = 12.0
TOLERANCE = 0.3


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


def _check_asset_file(output_dir: Path, asset_path: str | None, label: str, errors: list[str]) -> None:
    if not asset_path:
        errors.append(f"{label} 尚未回填 asset_path")
        return
    p = Path(asset_path)
    if not p.is_absolute():
        p = output_dir / p
    if not p.exists():
        errors.append(f"{label} 的 asset_path 指向的文件不存在：{asset_path}")
    elif p.stat().st_size == 0:
        errors.append(f"{label} 的 asset_path 文件为空：{asset_path}")


def check(output_dir: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    project = _load_json(output_dir / "novel_project.json")
    characters_data = _load_json(output_dir / "characters.json")
    locations_data = _load_json(output_dir / "locations.json")
    scene_plan = _load_yaml(output_dir / "scene_plan.yaml")

    characters = characters_data.get("characters", [])
    locations = locations_data.get("locations", [])
    scenes = scene_plan.get("scenes", [])

    char_by_id = {c.get("id"): c for c in characters}
    loc_by_id = {l.get("id"): l for l in locations}

    # 1. 定妆图完整性
    for c in characters:
        _check_asset_file(output_dir, c.get("asset_path"), f"角色 {c.get('id')}", errors)
    for l in locations:
        _check_asset_file(output_dir, l.get("asset_path"), f"地点 {l.get('id')}", errors)

    if not scenes:
        errors.append("scene_plan.yaml 里没有任何 scenes（请先运行 synthesize_narration.py）")

    total_duration = 0.0
    for sc in scenes:
        sc_id = sc.get("id", sc.get("segment_id", "<无id>"))

        # 2. 引用完整性（间接：引用的角色/地点是否存在于 characters/locations.json，
        # 存在则其素材完整性已在上面第 1 步检查过）
        for cid in sc.get("uses_characters", []) or []:
            if cid not in char_by_id:
                errors.append(f"scene {sc_id} 引用了不存在的角色 id：{cid}")
        for lid in sc.get("uses_locations", []) or []:
            if lid not in loc_by_id:
                errors.append(f"scene {sc_id} 引用了不存在的地点 id：{lid}")

        # 3. 音频完整性
        audio_rel = sc.get("narration_audio")
        if not audio_rel:
            errors.append(f"scene {sc_id} 没有 narration_audio 字段")
        else:
            audio_path = output_dir / audio_rel
            if not audio_path.exists():
                errors.append(f"scene {sc_id} 的 narration_audio 文件不存在：{audio_rel}")
            elif audio_path.stat().st_size == 0:
                errors.append(f"scene {sc_id} 的 narration_audio 文件为空：{audio_rel}")

        duration = sc.get("duration_sec")
        if duration is None:
            errors.append(f"scene {sc_id} 没有 duration_sec 字段")
        else:
            total_duration += duration
            if duration > MAX_DURATION:
                errors.append(f"scene {sc_id} 时长 {duration}s 超过 {MAX_DURATION}s 上限，需要拆分")
            elif duration < MIN_DURATION:
                errors.append(f"scene {sc_id} 时长 {duration}s 低于 {MIN_DURATION}s 下限，建议合并相邻场景")

    # 4. 总时长预算
    target = project.get("target_duration_sec")
    if target:
        lower, upper = target * (1 - TOLERANCE), target * (1 + TOLERANCE)
        if not (lower <= total_duration <= upper):
            warnings.append(
                f"scenes 总时长 {total_duration:.1f}s 超出目标 {target}s 的 "
                f"±{int(TOLERANCE * 100)}% 区间（[{lower:.1f}, {upper:.1f}]）"
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "character_count": len(characters),
            "location_count": len(locations),
            "scene_count": len(scenes),
            "total_duration_sec": round(total_duration, 1),
            "target_duration_sec": target,
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
