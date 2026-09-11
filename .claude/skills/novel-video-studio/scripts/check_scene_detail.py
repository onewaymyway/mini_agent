"""check_scene_detail.py — 校验 novel-scene-detail-planner 单个大场景的产物。

用法：
    python check_scene_detail.py <output_dir> <macro_id>

读取 <output_dir>/macro_scenes.yaml 里 id==<macro_id> 的记录（取其
raw_text 用于对话真实性校验），以及
<output_dir>/macro_scene_<后缀>/scene_detail.yaml，检查：

  1. 每个 micro_scene 的 uses_characters / uses_locations 是否都能在全局
     角色/地点库里找到，且对应条目的 asset_path 非空。
  2. 每个 dialogue block 的 speaker 是否在该 micro_scene 的
     uses_characters 列表里。
  3. 每个 dialogue.text（去除空白/标点后）是否能在该大场景 raw_text
     （同样去除空白/标点后）里找到子串匹配——找不到判定"疑似臆造对话"。
  4. 每个 micro_scene 至少有一个 content_blocks，且不能全是空文本。
  5. micro_scene 的 id 在整个项目范围内（扫描所有 macro_scene_*/
     scene_detail.yaml）不重复。

设计上不对文件做任何自动修复。

退出码：
  0 = 全部通过
  1 = 存在问题，stdout 会打印结构化的错误清单
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(1)


_STRIP_RE = re.compile(r"[\s，。！？、；：“”‘’\"'.,!?;:()（）\-—…]")


def _normalize(text: str) -> str:
    return _STRIP_RE.sub("", text or "")


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


def _macro_scene_dir_name(macro_id: str) -> str:
    if macro_id.startswith("macro_"):
        return f"macro_scene_{macro_id[len('macro_'):]}"
    return f"macro_scene_{macro_id}"


def check(output_dir: Path, macro_id: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    macro_data = _load_yaml(output_dir / "macro_scenes.yaml")
    macro_scenes = macro_data.get("macro_scenes", []) if isinstance(macro_data, dict) else []
    macro_record = next((s for s in macro_scenes if s.get("id") == macro_id), None)
    if macro_record is None:
        errors.append(f"macro_scenes.yaml 里找不到 id={macro_id} 的大场景")
        return {"ok": False, "errors": errors, "warnings": warnings, "summary": {}}

    raw_text_normalized = _normalize(macro_record.get("raw_text") or "")

    scene_dir = output_dir / _macro_scene_dir_name(macro_id)
    detail_data = _load_yaml(scene_dir / "scene_detail.yaml")
    micro_scenes = detail_data.get("micro_scenes", []) if isinstance(detail_data, dict) else []
    if not micro_scenes:
        errors.append(f"{scene_dir}/scene_detail.yaml 不存在或没有任何 micro_scenes")

    characters_data = _load_json(output_dir / "global" / "characters.json")
    locations_data = _load_json(output_dir / "global" / "locations.json")
    char_by_id = {c.get("id"): c for c in characters_data.get("characters", [])}
    loc_by_id = {l.get("id"): l for l in locations_data.get("locations", [])}

    for ms in micro_scenes:
        mid = ms.get("id", "<无id>")
        uses_characters = ms.get("uses_characters", []) or []
        uses_locations = ms.get("uses_locations", []) or []

        # 1. 引用完整性 + asset_path 非空
        for cid in uses_characters:
            c = char_by_id.get(cid)
            if c is None:
                errors.append(f"小场景 {mid} 引用了不存在的角色 id：{cid}")
            elif not (c.get("asset_path") or "").strip():
                errors.append(f"小场景 {mid} 引用的角色 {cid} 还没有 asset_path（需先补生成素材）")
        for lid in uses_locations:
            l = loc_by_id.get(lid)
            if l is None:
                errors.append(f"小场景 {mid} 引用了不存在的地点 id：{lid}")
            elif not (l.get("asset_path") or "").strip():
                errors.append(f"小场景 {mid} 引用的地点 {lid} 还没有 asset_path（需先补生成素材）")

        content_blocks = ms.get("content_blocks", []) or []
        if not content_blocks:
            errors.append(f"小场景 {mid} 没有任何 content_blocks")
        has_non_empty = False
        for i, block in enumerate(content_blocks):
            text = (block.get("text") or "").strip()
            btype = block.get("type")
            if text:
                has_non_empty = True
            if btype == "dialogue":
                speaker = block.get("speaker")
                if not speaker:
                    errors.append(f"小场景 {mid} 第{i+1}个 dialogue block 缺少 speaker")
                elif speaker not in uses_characters:
                    errors.append(
                        f"小场景 {mid} 第{i+1}个 dialogue block 的 speaker={speaker} "
                        f"不在该小场景的 uses_characters {uses_characters} 里"
                    )
                if text and _normalize(text) not in raw_text_normalized:
                    errors.append(
                        f"小场景 {mid} 第{i+1}个 dialogue block 的台词疑似臆造（在大场景 "
                        f"{macro_id} 原文里找不到对应子串）：{text!r}"
                    )
            elif btype == "narration":
                if block.get("speaker") not in (None, "null"):
                    warnings.append(f"小场景 {mid} 第{i+1}个 narration block 的 speaker 应为 null")
            else:
                errors.append(f"小场景 {mid} 第{i+1}个 content block 的 type 非法：{btype!r}（应为 narration/dialogue）")

        if not has_non_empty:
            errors.append(f"小场景 {mid} 的所有 content_blocks 文本均为空")

    # 5. 全局 micro_scene id 唯一性
    all_ids: list[str] = []
    for detail_file in sorted(output_dir.glob("macro_scene_*/scene_detail.yaml")):
        data = _load_yaml(detail_file)
        for ms in (data.get("micro_scenes", []) if isinstance(data, dict) else []):
            if ms.get("id"):
                all_ids.append(ms["id"])
    dup_ids = {i for i in all_ids if all_ids.count(i) > 1}
    if dup_ids:
        errors.append(f"micro_scene id 在项目范围内存在重复：{sorted(dup_ids)}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "macro_id": macro_id,
            "micro_scene_count": len(micro_scenes),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("macro_id")
    args = parser.parse_args()

    result = check(args.output_dir, args.macro_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
