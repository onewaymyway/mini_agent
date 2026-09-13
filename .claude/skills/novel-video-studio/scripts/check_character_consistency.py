"""check_character_consistency.py — 校验 micro_scene.prompt_en 是否与
全局角色/地点档案（global/characters.json、global/locations.json）里
锁定的视觉属性一致。

背景：`novel-scene-video-generator`（阶段5）要求 Agent 为每个
micro_scene 手写 `prompt_en`，如果每次都凭当下这一段的画面感觉重新
描述角色/地点长什么样，同一个角色在不同大场景里的年龄/发型/穿着很容易
逐场景漂移——这正是"生成出来的视频片段里的角色/场景和抽取出的素材不
一样"这个问题的根源。本脚本在跑 `generate_scene_videos_v2.py` 之前，
检查每个引用了角色/地点的 `prompt_en` 是否带上了对应的
`visual_anchor_en`（锁定视觉锚点，见 01_entity_extraction.md），
并做启发式的属性冲突检测（如角色 `age_range=child` 但 prompt_en 里
出现了 "elderly"/"old man" 这类矛盾词）。

用法：
    python check_character_consistency.py <output_dir> [--macro-id macro_01 ...]

校验项：
  1.（error）`prompt_en` 非空的 micro_scene，其引用的每个角色/地点，若
     对应 `visual_anchor_en` 字段本身缺失/为空 —— 说明阶段1的档案还没
     补全到能支撑一致性检查的程度，必须先回阶段1补，不能指望阶段5的
     prompt_en 自己保持一致；
  2.（error）`prompt_en` 命中了与该角色锁定的 `age_range`/`gender` 明确
     矛盾的关键词（见 _AGE_CONFLICTS/_GENDER_CONFLICTS），比如角色被
     锁定为 `age_range: child` 但 prompt_en 里出现 "elderly woman"；
  3.（warning）`prompt_en` 里一个能对应上 `visual_anchor_en`
     关键词的锚点都没找到——大概率是这一条 prompt 完全脱离了角色档案
     现场发挥，需要 Agent 人工确认是否有意为之（比如特写镜头确实不需要
     体现全身特征），不是机械判死。

本脚本做的是关键词级别的启发式检测，不理解语义，查不出"用了同义词但
其实没矛盾"和"关键词都对但整体描述其实文不对题"这两类情况——这一步
永远不能替代 Agent 自己在写 prompt_en 时对照角色档案的判断，只是给一个
兜底的机械复核，避免最粗暴的"忘了带年龄/性别设定"或"写反了"这类错误
漏到视频生成之后才发现。

退出码：
  0 = 全部通过（errors 为空，可以进入 generate_scene_videos_v2.py）
  1 = 存在问题，stdout 打印结构化问题清单
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
    sys.exit(2)


# 年龄段互斥关键词组：同一 prompt_en 里如果角色被锁定为某一组，却出现了
# 另一组的关键词，判定为矛盾。只覆盖"一望而知互斥"的粗粒度分组，不细分
# 到具体岁数（细分容易漏检/误报，粗分反而更可靠）。
_AGE_GROUPS: dict[str, list[str]] = {
    "child": ["child", "kid", "little boy", "little girl", "young child", "toddler"],
    "teen": ["teenager", "teenage", "adolescent"],
    "youth": ["young adult", "young man", "young woman", "in his twenties", "in her twenties"],
    "middle_aged": ["middle-aged", "middle aged"],
    "elderly": ["elderly", "old man", "old woman", "aged", "senior citizen", "gray-haired", "grey-haired"],
}

_GENDER_GROUPS: dict[str, list[str]] = {
    "male": [" he ", " his ", " him ", "male", "man ", " boy "],
    "female": [" she ", " her ", "female", "woman ", " girl "],
}

# 用于排除的 stop words，避免把 "a man standing nearby" 误判为性别关键词
_EXCLUDE_SUFFIXES = [
    "nearby", "behind", "background", "background figure",
    "silhouette", "shadow", "reflected in", "through",
]


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


def _norm(text: str) -> str:
    return f" {text.lower()} "


def _hit_groups(text_norm: str, groups: dict[str, list[str]]) -> set[str]:
    hits = set()
    for group, keywords in groups.items():
        for kw in keywords:
            if kw.lower() in text_norm:
                hits.add(group)
                break
    return hits


def _anchor_hit(text_norm: str, visual_anchor_en: str) -> bool:
    """粗粒度判断 prompt_en 是否包含了 visual_anchor_en 里的关键信息：
    只要 visual_anchor_en 拆词后（去掉停用词/标点）有任意一个长度 >= 4
    的实词出现在 prompt_en 里，就算命中，不要求逐字匹配（不同场景的
    prompt_en 措辞必然不同，只检查"提到过这个人/地方的核心特征"）。
    """
    if not visual_anchor_en.strip():
        return True  # 没有锚点可比对时不误报，交给上面的"缺锚点"检查处理
    words = re.findall(r"[a-zA-Z]{4,}", visual_anchor_en.lower())
    stop = {"with", "wearing", "long", "short", "very", "some", "this", "that", "have", "hair"}
    words = [w for w in words if w not in stop]
    return any(w in text_norm for w in words[:12])  # 只看前12个实词，够用且避免过长锚点稀释判断


def check(output_dir: Path, macro_ids: list[str] | None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    characters = {c.get("id"): c for c in _load_json(output_dir / "global" / "characters.json").get("characters", [])}
    locations = {l.get("id"): l for l in _load_json(output_dir / "global" / "locations.json").get("locations", [])}

    if macro_ids:
        detail_files = [output_dir / f"macro_scene_{mid.replace('macro_', '')}" / "scene_detail.yaml" for mid in macro_ids]
    else:
        detail_files = sorted(output_dir.glob("macro_scene_*/scene_detail.yaml"))

    checked = 0
    for detail_file in detail_files:
        if not detail_file.exists():
            continue
        data = _load_yaml(detail_file)
        for ms in data.get("micro_scenes", []) or []:
            prompt_en = (ms.get("prompt_en") or "").strip()
            if not prompt_en:
                continue  # 还没写 prompt_en 的场景不在本脚本校验范围（阶段5前置步骤会单独拦）
            checked += 1
            mid = ms.get("id", "<无id>")
            text_norm = _norm(prompt_en)

            for cid in ms.get("uses_characters", []) or []:
                c = characters.get(cid)
                if c is None:
                    continue  # 引用不存在的角色是阶段3的校验范围，这里不重复报
                anchor = (c.get("visual_anchor_en") or "").strip()
                if not anchor:
                    errors.append(
                        f"角色 {cid} 缺少 visual_anchor_en（锁定视觉锚点），"
                        f"小场景 {mid} 的 prompt_en 无法做一致性校验，需回阶段1补全该角色档案"
                    )
                    continue

                age_range = (c.get("age_range") or "").strip()
                if age_range in _AGE_GROUPS:
                    hit = _hit_groups(text_norm, _AGE_GROUPS)
                    conflict = hit - {age_range}
                    if conflict:
                        errors.append(
                            f"小场景 {mid} 的 prompt_en 提到角色 {cid} 时使用了与其锁定 "
                            f"age_range={age_range!r} 矛盾的年龄描述（命中分组：{sorted(conflict)}），"
                            f"需要按角色档案的 age_range/visual_anchor_en 改写这部分描述"
                        )

                gender = (c.get("gender") or "").strip().lower()
                if gender in _GENDER_GROUPS:
                    hit = _hit_groups(text_norm, _GENDER_GROUPS)
                    # 多角色场景：prompt_en 同时包含男女关键词属正常，跳过冲突检查
                    if len(hit) > 1:
                        pass  # multi-character scene, expect both genders
                    else:
                        conflict = hit - {gender}
                        if conflict:
                            errors.append(
                                f"小场景 {mid} 的 prompt_en 提到角色 {cid} 时使用了与其锁定 "
                                f"gender={gender!r} 矛盾的性别代词/称谓（命中分组：{sorted(conflict)}），"
                                f"需要核对是不是写混了不同角色的描述"
                            )

                if not _anchor_hit(text_norm, anchor):
                    warnings.append(
                        f"小场景 {mid} 的 prompt_en 没有出现角色 {cid} 的 visual_anchor_en "
                        f"（{anchor!r}）里任何核心特征词，确认是否遗漏了角色外观描述"
                        f"（特写/远景等确实不需要体现全部特征的场景可以放行）"
                    )

            for lid in ms.get("uses_locations", []) or []:
                l = locations.get(lid)
                if l is None:
                    continue
                anchor = (l.get("visual_anchor_en") or "").strip()
                if not anchor:
                    errors.append(
                        f"地点 {lid} 缺少 visual_anchor_en（锁定视觉锚点），"
                        f"小场景 {mid} 的 prompt_en 无法做一致性校验，需回阶段1补全该地点档案"
                    )
                    continue
                if not _anchor_hit(text_norm, anchor):
                    warnings.append(
                        f"小场景 {mid} 的 prompt_en 没有出现地点 {lid} 的 visual_anchor_en "
                        f"（{anchor!r}）里任何核心特征词，确认是否遗漏了场景环境描述"
                    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {"prompt_en_checked": checked},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--macro-id", nargs="*", default=None, help="只校验指定大场景，不传则校验全部已存在的 scene_detail.yaml")
    args = parser.parse_args()

    result = check(args.output_dir, args.macro_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
