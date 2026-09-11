"""check_macro_scenes.py — 校验 novel-macro-scene-planner 的产物。

用法：
    python check_macro_scenes.py <output_dir> [--novel-text-file <原文路径>]

依次读取 <output_dir> 下的 macro_scenes.yaml / global/characters.json /
global/locations.json，检查：

  1. 每条大场景的 char_count 是否 <= --max-chars（默认 1000）。
  2. 每条大场景的 estimated_duration_sec 是否 <= --max-duration-sec（默认 120）。
  3. char_count 是否与 len(raw_text) 一致（防止字段和实际内容不同步）。
  4. estimated_duration_sec 是否与 char_count / --chars-per-sec 大致一致
     （容忍误差，防止估算公式算错）。
  5. uses_characters / uses_locations 引用的 id 是否都能在全局角色/地点库
     里找到。
  6. （传了 --novel-text-file 时）所有 raw_text 拼接后的总字数，与原文
     总字数的比例是否在 --coverage-tolerance（默认 0.15，即 ±15%）区间内。

设计上不对文件做任何自动修复——本脚本只负责"发现问题"，"如何切"必须由
Agent 结合小说语义决定。

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


def check(
    output_dir: Path,
    novel_text_file: Path | None,
    max_chars: int,
    max_duration_sec: float,
    chars_per_sec: float,
    coverage_tolerance: float,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    macro_data = _load_yaml(output_dir / "macro_scenes.yaml")
    characters_data = _load_json(output_dir / "global" / "characters.json")
    locations_data = _load_json(output_dir / "global" / "locations.json")

    scenes = macro_data.get("macro_scenes", []) if isinstance(macro_data, dict) else []
    if not scenes:
        errors.append("macro_scenes.yaml 不存在或没有任何 macro_scenes")

    char_id_set = {c.get("id") for c in characters_data.get("characters", [])}
    loc_id_set = {l.get("id") for l in locations_data.get("locations", [])}

    # id 重复检查
    scene_ids = [s.get("id") for s in scenes]
    dup_ids = {sid for sid in scene_ids if scene_ids.count(sid) > 1}
    if dup_ids:
        errors.append(f"macro_scenes.yaml 存在重复 id：{sorted(dup_ids)}")

    total_raw_chars = 0
    for s in scenes:
        sid = s.get("id", "<无id>")
        raw_text = s.get("raw_text") or ""
        char_count = s.get("char_count")
        estimated_sec = s.get("estimated_duration_sec")

        if not raw_text.strip():
            errors.append(f"大场景 {sid} 的 raw_text 为空")
            continue

        total_raw_chars += len(raw_text)

        # 1. 字数硬约束
        if char_count is None:
            errors.append(f"大场景 {sid} 缺少 char_count 字段")
        elif char_count > max_chars:
            errors.append(f"大场景 {sid} 字数 {char_count} 超过硬约束 {max_chars} 字，需要继续细分")

        # 3. char_count 与实际 raw_text 长度一致性
        if char_count is not None and char_count != len(raw_text):
            errors.append(
                f"大场景 {sid} 的 char_count={char_count} 与 raw_text 实际长度 "
                f"{len(raw_text)} 不一致"
            )

        # 2. 时长硬约束
        if estimated_sec is None:
            errors.append(f"大场景 {sid} 缺少 estimated_duration_sec 字段")
        elif estimated_sec > max_duration_sec:
            errors.append(
                f"大场景 {sid} 预估时长 {estimated_sec} 秒超过硬约束 {max_duration_sec} 秒，需要继续细分"
            )

        # 4. 估算公式一致性（容忍 ±10% 或 ±2 秒，取较宽松者）
        if char_count is not None and estimated_sec is not None and chars_per_sec > 0:
            expected_sec = char_count / chars_per_sec
            tol = max(expected_sec * 0.1, 2.0)
            if abs(expected_sec - estimated_sec) > tol:
                warnings.append(
                    f"大场景 {sid} 的 estimated_duration_sec={estimated_sec} 与按 "
                    f"char_count/{chars_per_sec} 算出的 {expected_sec:.1f} 秒偏差较大，请复核"
                )

        # 5. 引用完整性
        for cid in s.get("uses_characters", []) or []:
            if cid not in char_id_set:
                errors.append(f"大场景 {sid} 引用了不存在的角色 id：{cid}（需先用 novel-entity-extractor 模式B补抽取）")
        for lid in s.get("uses_locations", []) or []:
            if lid not in loc_id_set:
                errors.append(f"大场景 {sid} 引用了不存在的地点 id：{lid}（需先用 novel-entity-extractor 模式B补抽取）")

    # 6. 原文覆盖率检查（可选）
    coverage_ratio = None
    if novel_text_file is not None:
        if not novel_text_file.exists():
            warnings.append(f"--novel-text-file 指定的文件不存在：{novel_text_file}，跳过覆盖率检查")
        else:
            novel_text = novel_text_file.read_text(encoding="utf-8")
            novel_chars = len(novel_text)
            if novel_chars > 0:
                coverage_ratio = total_raw_chars / novel_chars
                lower = 1 - coverage_tolerance
                upper = 1 + coverage_tolerance
                if not (lower <= coverage_ratio <= upper):
                    direction = "可能有大段原文被遗漏" if coverage_ratio < lower else "可能存在重复摘录或引入了原文之外的内容"
                    errors.append(
                        f"所有大场景 raw_text 总字数 {total_raw_chars}，原文总字数 {novel_chars}，"
                        f"比例 {coverage_ratio:.2f} 超出 [{lower:.2f}, {upper:.2f}] 区间，{direction}"
                    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "macro_scene_count": len(scenes),
            "total_raw_chars": total_raw_chars,
            "coverage_ratio": round(coverage_ratio, 3) if coverage_ratio is not None else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--novel-text-file", type=Path, default=None, help="原文文件路径，传了会额外做覆盖率检查")
    parser.add_argument("--max-chars", type=int, default=1000)
    parser.add_argument("--max-duration-sec", type=float, default=120.0)
    parser.add_argument("--chars-per-sec", type=float, default=4.5)
    parser.add_argument("--coverage-tolerance", type=float, default=0.15)
    args = parser.parse_args()

    result = check(
        args.output_dir,
        args.novel_text_file,
        args.max_chars,
        args.max_duration_sec,
        args.chars_per_sec,
        args.coverage_tolerance,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
