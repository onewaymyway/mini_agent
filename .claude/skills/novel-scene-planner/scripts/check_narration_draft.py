"""check_narration_draft.py — 校验 novel-scene-planner 的产物。

用法：
    python check_narration_draft.py <output_dir>

依次读取 <output_dir> 下的 novel_project.json / characters.json /
locations.json / narration_script.yaml，检查：

  1. characters.json / locations.json 里的 id 是否有重复。
  2. narration_script.yaml 每个 segment 的 uses_characters / uses_locations
     引用的 id 是否都能在 characters.json / locations.json 里找到。
  3. 全部 segments[*].text 的总字数，按 --chars-per-sec（默认 4.5 字/秒）
     换算成预估朗读时长，是否落在 novel_project.json.target_duration_sec
     的 ±--tolerance（默认 0.3，即 ±30%）区间内。

设计上不对文件做任何自动修复——本脚本只负责"发现问题"，"如何改"（重新
取舍浓缩 / 合并重复角色等）必须由 Agent 结合小说语义决定。

退出码：
  0 = 全部通过
  1 = 存在问题，stdout 会打印结构化的错误清单
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


def check(output_dir: Path, chars_per_sec: float, tolerance: float) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    project = _load_json(output_dir / "novel_project.json")
    characters_data = _load_json(output_dir / "characters.json")
    locations_data = _load_json(output_dir / "locations.json")
    narration = _load_yaml(output_dir / "narration_script.yaml")

    if not project:
        errors.append("novel_project.json 不存在或为空")
    if not narration:
        errors.append("narration_script.yaml 不存在或为空")

    characters = characters_data.get("characters", [])
    locations = locations_data.get("locations", [])
    segments = narration.get("segments", []) if isinstance(narration, dict) else []

    # 1. id 重复检查
    char_ids = [c.get("id") for c in characters]
    loc_ids = [l.get("id") for l in locations]
    dup_chars = {cid for cid in char_ids if char_ids.count(cid) > 1}
    dup_locs = {lid for lid in loc_ids if loc_ids.count(lid) > 1}
    if dup_chars:
        errors.append(f"characters.json 存在重复 id：{sorted(dup_chars)}")
    if dup_locs:
        errors.append(f"locations.json 存在重复 id：{sorted(dup_locs)}")

    char_id_set = set(char_ids)
    loc_id_set = set(loc_ids)

    # 2. 引用完整性检查
    if not segments:
        errors.append("narration_script.yaml 里没有任何 segments")
    for seg in segments:
        seg_id = seg.get("id", "<无id>")
        for cid in seg.get("uses_characters", []) or []:
            if cid not in char_id_set:
                errors.append(f"segment {seg_id} 引用了不存在的角色 id：{cid}")
        for lid in seg.get("uses_locations", []) or []:
            if lid not in loc_id_set:
                errors.append(f"segment {seg_id} 引用了不存在的地点 id：{lid}")
        if not (seg.get("text") or "").strip():
            errors.append(f"segment {seg_id} 的 text 为空")

    # 3. 篇幅预估检查
    target_sec = project.get("target_duration_sec")
    total_chars = sum(len((seg.get("text") or "").strip()) for seg in segments)
    estimated_sec = total_chars / chars_per_sec if chars_per_sec > 0 else 0

    if target_sec:
        lower = target_sec * (1 - tolerance)
        upper = target_sec * (1 + tolerance)
        if not (lower <= estimated_sec <= upper):
            direction = "过长，需要重新取舍浓缩" if estimated_sec > upper else "过短，可考虑补充内容"
            errors.append(
                f"预估旁白时长 {estimated_sec:.1f} 秒，超出目标时长 {target_sec} 秒 "
                f"的 ±{int(tolerance * 100)}% 区间（[{lower:.1f}, {upper:.1f}]），{direction}"
            )
    else:
        warnings.append("novel_project.json 未设置 target_duration_sec，跳过篇幅预估检查")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "character_count": len(characters),
            "location_count": len(locations),
            "segment_count": len(segments),
            "total_narration_chars": total_chars,
            "estimated_narration_sec": round(estimated_sec, 1),
            "target_duration_sec": target_sec,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--chars-per-sec", type=float, default=4.5,
        help="中文口播语速估算（字/秒），默认 4.5",
    )
    parser.add_argument(
        "--tolerance", type=float, default=0.3,
        help="篇幅预估允许偏差比例，默认 0.3（±30%%）",
    )
    args = parser.parse_args()

    result = check(args.output_dir, args.chars_per_sec, args.tolerance)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
