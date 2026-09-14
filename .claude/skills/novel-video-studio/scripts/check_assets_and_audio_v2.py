"""check_assets_and_audio_v2.py — 校验 novel-asset-generator v2（按
content_blocks 配音）的产物。

用法：
    python check_assets_and_audio_v2.py <output_dir> [--macro-id macro_01 ...]

检查：
  1. 所有在 macro_scene_*/scene_detail.yaml 里被引用的角色/地点，在
     global/characters.json / global/locations.json 里是否都已有非空
     asset_path。若某个 micro_scene 通过 character_variant_overrides/
     location_variant_overrides 指定使用了某个外观变体，还会额外检查
     该变体自己的 asset_path 是否已生成（而不只是检查顶层条目的
     asset_path）——变体定妆图缺失会导致该镜头 reference 模式实际用的
     还是默认外观的参考图，画面与变体描述脱节，属于本项硬性 error，不
     是等阶段5生成视频时才发现。
  2. 每个 micro_scene 的每个非空 content_block 是否都有对应的音频文件
     （narration_seg_<mid>_<i>.wav 或 dialogue_<speaker>_<mid>_<i>.wav，
     统一 .wav 后缀，与 compose_macro_scene.py 的读取约定一致），
     文件存在且非空。
  3. 每个 micro_scene 的 duration_sec 是否已回填（非 null）且落在
     4–12 秒范围内——超出范围说明该小场景需要回
     novel-scene-detail-planner 拆分/合并 content_blocks，重新配音。
  4. 【时长合理性自查，warning，不依赖问题1是否已修好】用 03 文档同款的
     粗估语速（4.5字/秒）算出每个 micro_scene 全部 content_blocks 的理论
     时长，与实际 duration_sec 做比值，比值明显偏离（实际不足理论值40%
     或超过理论值250%）时报 warning，附带具体数字——这类偏离通常意味着
     `duration_sec` 是假数据/串号，即使数值本身落在 4-12 秒硬范围内、
     不会被 3 拦下，也值得人工核实。
  5. 【环境自检，warning】探测 `ffprobe` 是否能正常调用（复用
     `common.py::resolve_ffprobe()`，认 `NOVEL_FFPROBE_PATH` 环境变量/
     imageio_ffmpeg/conda 环境自动探测，不是只查系统 PATH），探测不到时
     提示 `duration_sec` 的真实性无法通过时长本身验证，建议先修好
     ffprobe，并在 warning 里附上具体尝试过哪些查找方式。

`--macro-id`（可传多个，不传则查全部，参数风格对齐 check_clips_v2.py）
支持只校验某一个/几个大场景，方便在 SKILL.md §2.3 的逐场景循环中定向
校验当前正在处理的大场景，不必等所有大场景都配完音才能跑校验。

退出码：
  0 = 全部通过
  1 = 存在问题
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import macro_scene_dir_name, ROUGH_CHARS_PER_SEC, resolve_ffprobe

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(1)

# 与 check_scene_detail.py 的粗估语速保持一致（统一定义在 common.py 的
# ROUGH_CHARS_PER_SEC），用于问题4的"时长合理性"自查——这里的估算目的和
# check_scene_detail.py 不同：check_scene_detail.py 是规划阶段的提前
# 预警，这里是配完音之后的兜底复核，独立发现"时长和文本长度对不上"这种
# 此前会被静默接受的数据异常（不依赖问题1本身是否已修好）。
_ROUGH_CHARS_PER_SEC = ROUGH_CHARS_PER_SEC
_REASONABLE_RATIO_LOW = 0.4   # 实际值低于理论值 40% 时报 warning
_REASONABLE_RATIO_HIGH = 2.5  # 实际值高于理论值 250% 时报 warning


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


def _ffprobe_available() -> tuple[bool, str | None]:
    """探测 ffprobe 是否可用。复用 common.py 的 `resolve_ffprobe()`——它
    按 环境变量 NOVEL_FFPROBE_PATH > imageio_ffmpeg > conda 环境自动探测
    > 系统 PATH 的优先级查找，和 `compose_macro_scene.py`/
    `compose_final_video_v2.py`/`tts_engine.py` 等脚本用的是同一套逻辑。

    此前这里是自己重新实现了一遍、只查系统 PATH（`shutil.which("ffprobe")`），
    没读 `NOVEL_FFPROBE_PATH` 环境变量，也不认 imageio_ffmpeg/conda 环境
    里装的 ffprobe——如果 ffprobe 实际是通过这几种方式之一装的、只是不在
    系统 PATH 里，这里会误报"不可用"，即使其它脚本（比如实际配音时用到
    的 `tts_engine.py`）明明用同一个环境变量就能找到。现在统一改用
    `common.resolve_ffprobe()`，找不到时把它抛出的详细"已尝试哪些方式"
    原因带回去，让 warning 里能看到具体是哪几种查找方式都没命中，而不是
    一句"不可用"。

    返回 (是否可用, 找不到时的详细原因或 None)。
    """
    try:
        resolve_ffprobe()
        return True, None
    except RuntimeError as e:
        return False, str(e)


def _variant_asset_path(entity: dict, variant_id: str) -> str | None:
    """从实体（角色/地点）的 appearance_variants 里找到指定 variant_id，
    返回其 asset_path（找不到该 variant 或字段为空时返回 None，调用方
    区分对待——找不到 variant 本身是 check_scene_detail.py 已经拦过的
    引用完整性问题，这里假定引用合法，只关心 asset_path 是否已生成）。"""
    for v in (entity.get("appearance_variants") or []):
        if v.get("variant_id") == variant_id:
            return (v.get("asset_path") or "").strip() or None
    return None


def check(output_dir: Path, min_sec: float, max_sec: float, macro_ids: list | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    ffprobe_ok, ffprobe_reason = _ffprobe_available()
    if not ffprobe_ok:
        warnings.append(
            "当前环境 ffprobe 不可用，duration_sec 的真实性无法通过时长本身验证，"
            "建议先修好 ffprobe 再信任已有数据（另见下面的时长合理性 warning，"
            "可作为 ffprobe 不可用时的部分兜底，但不能完全替代）。"
            f"具体探测过程：{ffprobe_reason}"
        )

    characters_data = _load_json(output_dir / "global" / "characters.json")
    locations_data = _load_json(output_dir / "global" / "locations.json")
    char_by_id = {c.get("id"): c for c in characters_data.get("characters", [])}
    loc_by_id = {l.get("id"): l for l in locations_data.get("locations", [])}

    if macro_ids:
        detail_files = [output_dir / macro_scene_dir_name(m) / "scene_detail.yaml" for m in macro_ids]
        missing = [str(f) for f in detail_files if not f.exists()]
        for f in missing:
            errors.append(f"{f} 不存在")
        detail_files = [f for f in detail_files if f.exists()]
    else:
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

            # 外观变体定妆图完整性：character_variant_overrides/
            # location_variant_overrides 指定用了某个变体时，该变体自己
            # 的 asset_path 也必须已生成，否则 reference 模式实际会退回
            # 默认外观图，画面与变体描述（比如换装/受伤/地下室化身）脱节。
            char_overrides = ms.get("character_variant_overrides") or {}
            for cid, variant_id in char_overrides.items():
                c = char_by_id.get(cid)
                if c is None:
                    continue  # 引用是否合法由 check_scene_detail.py 负责，这里只管 asset_path
                if _variant_asset_path(c, variant_id) is None:
                    errors.append(
                        f"小场景 {mid} 指定角色 {cid} 使用外观变体 {variant_id!r}，"
                        f"但该变体缺少 asset_path（尚未生成变体定妆图），需先用该变体的 "
                        f"visual_override_en 生成一张定妆图并回填到该变体的 asset_path，"
                        f"否则这个镜头 reference 模式实际会退回使用角色默认外观的参考图"
                    )
            loc_overrides = ms.get("location_variant_overrides") or {}
            for lid, variant_id in loc_overrides.items():
                l = loc_by_id.get(lid)
                if l is None:
                    continue
                if _variant_asset_path(l, variant_id) is None:
                    errors.append(
                        f"小场景 {mid} 指定地点 {lid} 使用外观变体 {variant_id!r}，"
                        f"但该变体缺少 asset_path（尚未生成变体定妆图），需先用该变体的 "
                        f"visual_override_en 生成一张定妆图并回填到该变体的 asset_path，"
                        f"否则这个镜头 reference 模式实际会退回使用地点默认外观的参考图"
                    )

            content_blocks = ms.get("content_blocks", []) or []
            for i, block in enumerate(content_blocks):
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                btype = block.get("type", "narration")
                prefix = "narration_seg" if btype == "narration" else f"dialogue_{block.get('speaker', 'unknown')}"
                audio_path = audio_dir / f"{prefix}_{mid}_{i:02d}.wav"
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

                # 4. 时长合理性自查：与文本量粗估的理论时长做比值，明显偏离
                # 时报 warning，独立发现"时长和文本对不上"这类数据异常，
                # 不依赖问题1（tts_engine.py 时长估算 bug）是否已经修好。
                total_chars = sum(len((b.get("text") or "").strip()) for b in content_blocks)
                if total_chars > 0:
                    theoretical_sec = total_chars / _ROUGH_CHARS_PER_SEC
                    ratio = duration_sec / theoretical_sec if theoretical_sec > 0 else None
                    if ratio is not None and (ratio < _REASONABLE_RATIO_LOW or ratio > _REASONABLE_RATIO_HIGH):
                        warnings.append(
                            f"小场景 {mid} 实际 duration_sec={duration_sec}s，按文本共{total_chars}字、"
                            f"粗估语速{_ROUGH_CHARS_PER_SEC}字/秒算出的理论时长约{theoretical_sec:.1f}s，"
                            f"比值={ratio:.2f}（合理区间约{_REASONABLE_RATIO_LOW}~{_REASONABLE_RATIO_HIGH}），"
                            f"这条时长和文本长度明显对不上，大概率是假数据或串号，建议人工核实/重新配音"
                        )

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
    parser.add_argument("--macro-id", nargs="*", default=None, help="只校验指定的大场景，不传则校验全部")
    args = parser.parse_args()

    result = check(args.output_dir, args.min_sec, args.max_sec, args.macro_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
