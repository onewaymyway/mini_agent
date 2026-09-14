"""check_entities.py — 校验 novel-entity-extractor 的产物。

用法：
    python check_entities.py <output_dir>

依次读取 <output_dir> 下的 global/characters.json / global/locations.json，
检查：

  1. characters.json / locations.json 里的 id 是否有重复。
  2. 每个角色是否都有非空 voice_profile / description_zh / description_en /
     visual_anchor_en / age_range / gender / nationality_or_ethnicity，
     且 age_range 取值属于 child/teen/youth/middle_aged/elderly 五档之一。
  3. 每个地点是否都有非空 description_zh / description_en / visual_anchor_en。
  4. （警告级，不阻断）角色 names 列表之间是否存在明显重叠/高度相似的
     字符串，提示可能有同名角色未合并成一个 id。
  5. 角色/地点的 `appearance_variants`（可选字段，只有登记了外观变体
     才会非空）：每条记录的 `variant_id`/`trigger_zh`/`visual_override_en`
     是否非空，`variant_id` 在同一实体内部是否重复。本脚本只检查这些
     字段"存在"，不检查 `applies_scope` 指向的 macro_id/micro_id 是否
     真实存在——那些 id 此时可能还没被阶段2/3 创建，交给阶段3
     `check_scene_detail.py` 在实际引用时校验（见该文档校验项8）。

`visual_anchor_en`/`age_range`/`gender` 是阶段5 Agent 逐条语义核查
（角色/地点/情节一致性，结论写入 `consistency_report.yaml`，见
`05_scene_video_generation.md`）能否有依据可查的前提字段，本脚本只
保证它们"存在"，不保证它们和 description_zh/en 里的描述互相印证
（那部分依赖 Agent 在 01_entity_extraction.md Step 2.5 自查）。

设计上不对文件做任何自动修复——本脚本只负责"发现问题"，"如何改"（补充
voice_profile、合并重复角色等）必须由 Agent 结合小说语义决定。

退出码：
  0 = 全部通过
  1 = 存在问题，stdout 会打印结构化的错误清单
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _names_overlap(names_a: list[str], names_b: list[str]) -> bool:
    set_a = {n.strip() for n in names_a if n and n.strip()}
    set_b = {n.strip() for n in names_b if n and n.strip()}
    return bool(set_a & set_b)


_VALID_AGE_RANGES = {"child", "teen", "youth", "middle_aged", "elderly"}


def check(output_dir: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    global_dir = output_dir / "global"
    characters_data = _load_json(global_dir / "characters.json")
    locations_data = _load_json(global_dir / "locations.json")

    if not characters_data:
        warnings.append("global/characters.json 不存在或为空（若小说确实没有可视化角色，属正常情况）")
    if not locations_data:
        warnings.append("global/locations.json 不存在或为空（若小说没有反复出现的地点，属正常情况）")

    # Handle both list and dict formats
    if isinstance(characters_data, list):
        characters = characters_data
    else:
        characters = characters_data.get("characters", [])
    if isinstance(locations_data, list):
        locations = locations_data
    else:
        locations = locations_data.get("locations", [])

    # 1. id 重复检查
    char_ids = [c.get("id") for c in characters]
    loc_ids = [l.get("id") for l in locations]
    dup_chars = {cid for cid in char_ids if char_ids.count(cid) > 1}
    dup_locs = {lid for lid in loc_ids if loc_ids.count(lid) > 1}
    if dup_chars:
        errors.append(f"global/characters.json 存在重复 id：{sorted(dup_chars)}")
    if dup_locs:
        errors.append(f"global/locations.json 存在重复 id：{sorted(dup_locs)}")

    # 2. 角色字段完整性
    for c in characters:
        cid = c.get("id", "<无id>")
        for field in ("voice_profile", "description_zh", "description_en",
                      "visual_anchor_en", "age_range", "gender", "nationality_or_ethnicity"):
            if not (c.get(field) or "").strip():
                errors.append(f"角色 {cid} 缺少非空字段：{field}")
        age_range = (c.get("age_range") or "").strip()
        if age_range and age_range not in _VALID_AGE_RANGES:
            errors.append(
                f"角色 {cid} 的 age_range={age_range!r} 不是合法取值，"
                f"必须是 {sorted(_VALID_AGE_RANGES)} 之一"
            )
        if not c.get("names"):
            errors.append(f"角色 {cid} 的 names 列表为空")

    # 3. 地点字段完整性
    for l in locations:
        lid = l.get("id", "<无id>")
        for field in ("description_zh", "description_en", "visual_anchor_en"):
            if not (l.get(field) or "").strip():
                errors.append(f"地点 {lid} 缺少非空字段：{field}")
        if not (l.get("name") or "").strip():
            errors.append(f"地点 {lid} 的 name 为空")

    # 4. 同名未合并启发式检查（警告级）
    for i, ca in enumerate(characters):
        for cb in characters[i + 1:]:
            if _names_overlap(ca.get("names", []), cb.get("names", [])):
                warnings.append(
                    f"角色 {ca.get('id')} 与 {cb.get('id')} 的 names 有重叠，"
                    f"可能是同一角色未合并，请人工核对"
                )

    # 5. appearance_variants 字段完整性（可选字段，非空时才检查）
    for entity_kind, entities in (("角色", characters), ("地点", locations)):
        for e in entities:
            eid = e.get("id", "<无id>")
            variants = e.get("appearance_variants") or []
            seen_variant_ids: set[str] = set()
            for v in variants:
                vid = (v.get("variant_id") or "").strip()
                if not vid:
                    errors.append(f"{entity_kind} {eid} 的 appearance_variants 存在一条缺少 variant_id 的记录")
                elif vid in seen_variant_ids:
                    errors.append(f"{entity_kind} {eid} 的 appearance_variants 存在重复的 variant_id：{vid}")
                else:
                    seen_variant_ids.add(vid)
                for field in ("trigger_zh", "visual_override_en"):
                    if not (v.get(field) or "").strip():
                        errors.append(
                            f"{entity_kind} {eid} 的外观变体 {vid or '<无id>'} 缺少非空字段 {field}"
                            f"（{field} 是判断这个变体该不该生效/供阶段5核查的必需依据）"
                        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "character_count": len(characters),
            "location_count": len(locations),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    result = check(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
