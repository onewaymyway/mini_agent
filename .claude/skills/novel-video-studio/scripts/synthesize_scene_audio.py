"""synthesize_scene_audio.py — v2 批量配音：按 content_blocks（旁白+角色
对话）给每个大场景下的所有小场景配音，回填 scene_detail.yaml 的
duration_sec。

用法：
    python synthesize_scene_audio.py <output_dir> [--macro-id macro_01] \
        [--force] [--engine cosyvoice|edge-tts]

方案文档：next_doc/novel_video_generator_plan_v2.md 第 4 点/第 5 节。

与 v1 synthesize_narration.py 的区别：
  - v1 是"整段旁白配一次音"，本脚本是"每个 content_block（旁白/角色
    对话）分别配音"，因为要支持角色差异化音色；
  - 产物路径不再是全局 audio/，而是每个大场景自己的
    macro_scene_<后缀>/audio/ 下，文件名区分 narration_/dialogue_<角色id>_
    前缀；
  - 配音全部完成后，本脚本会把同一个 micro_scene 内所有 content_block
    的真实时长相加，回填该 micro_scene 的 duration_sec（多段音频按顺序
    首尾相接播放，不叠加，所以直接求和）。

不处理定妆图生成（那部分仍由 Agent 直接调用 gen_image_with_text 完成，
见 SKILL.md Step 1）。
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

from tts_engine import TTSError, synthesize
from voice_mapping import resolve_cosyvoice_speaker, resolve_edge_tts_voice


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


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _macro_scene_dir_name(macro_id: str) -> str:
    if macro_id.startswith("macro_"):
        return f"macro_scene_{macro_id[len('macro_'):]}"
    return f"macro_scene_{macro_id}"


def _voice_for_block(
    block: dict,
    char_by_id: dict,
    engine_pref: str,
) -> tuple[str | None, str]:
    """返回 (voice_for_synthesize, log_label)。

    engine_pref == "edge-tts" 时返回 edge-tts 音色名；
    engine_pref == "cosyvoice" 时返回 CosyVoice 说话人名（可能为 None，
    交给 tts_engine 用其默认说话人）——注意两种引擎的 voice 参数含义
    不同，tts_engine.synthesize() 内部会按 engine_pref 自己决定怎么用
    这个 voice 值，这里只按 engine_pref 选对应体系的值。
    """
    if block.get("type") == "dialogue":
        speaker = block.get("speaker")
        profile = (char_by_id.get(speaker) or {}).get("voice_profile")
        label = f"dialogue/{speaker}"
    else:
        profile = None  # 旁白用项目默认音色，不查角色库
        label = "narration"

    if engine_pref == "cosyvoice":
        return resolve_cosyvoice_speaker(profile), label
    return resolve_edge_tts_voice(profile), label


def run(output_dir: Path, macro_id: str | None, force: bool, engine_pref: str) -> dict:
    project = _load_json(output_dir / "novel_project.json")
    characters_data = _load_json(output_dir / "global" / "characters.json")
    char_by_id = {c.get("id"): c for c in characters_data.get("characters", [])}

    tts_cfg = project.get("tts", {}) if isinstance(project, dict) else {}
    engine_pref = engine_pref or tts_cfg.get("engine", "cosyvoice")
    fallback = tts_cfg.get("fallback", "edge-tts")

    if macro_id:
        detail_files = [output_dir / _macro_scene_dir_name(macro_id) / "scene_detail.yaml"]
    else:
        detail_files = sorted(output_dir.glob("macro_scene_*/scene_detail.yaml"))

    engine_usage: dict[str, int] = {}
    errors: list[str] = []
    processed_micro_scenes = 0

    for detail_file in detail_files:
        if not detail_file.exists():
            errors.append(f"{detail_file} 不存在")
            continue
        data = _load_yaml(detail_file)
        micro_scenes = data.get("micro_scenes", []) if isinstance(data, dict) else []
        audio_dir = detail_file.parent / "audio"

        for ms in micro_scenes:
            mid = ms.get("id", "unknown")
            content_blocks = ms.get("content_blocks", []) or []
            total_duration = 0.0
            block_failed = False

            for i, block in enumerate(content_blocks):
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                btype = block.get("type", "narration")
                prefix = "narration_seg" if btype == "narration" else f"dialogue_{block.get('speaker', 'unknown')}"
                out_path = audio_dir / f"{prefix}_{mid}_{i:02d}.wav"

                if out_path.exists() and out_path.stat().st_size > 0 and not force:
                    # 断点续跑：已存在的音频直接复用其时长，不重新合成
                    try:
                        from tts_engine import _ffprobe_duration  # 内部工具函数复用
                        total_duration += _ffprobe_duration(out_path)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"小场景 {mid} 第{i}块已存在音频但读取时长失败：{e}")
                        block_failed = True
                    continue

                voice, label = _voice_for_block(block, char_by_id, engine_pref)
                try:
                    result = synthesize(
                        text, out_path,
                        engine_pref=engine_pref, fallback=fallback, voice=voice,
                    )
                except TTSError as e:
                    errors.append(f"小场景 {mid} 第{i}块（{label}）配音失败：{e}")
                    block_failed = True
                    continue

                engine_usage[result["engine_used"]] = engine_usage.get(result["engine_used"], 0) + 1
                total_duration += result["duration_sec"]

            if not block_failed:
                ms["duration_sec"] = round(total_duration, 2)
                processed_micro_scenes += 1

        _write_yaml(detail_file, data)

    return {
        "ok": not errors,
        "errors": errors,
        "engine_usage": engine_usage,
        "processed_micro_scenes": processed_micro_scenes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--macro-id", default=None, help="只处理指定大场景，不传则处理全部 macro_scene_*")
    parser.add_argument("--force", action="store_true", help="强制重新生成已存在的音频")
    parser.add_argument("--engine", default=None, choices=["cosyvoice", "edge-tts"], help="覆盖 novel_project.json 里的 tts.engine")
    args = parser.parse_args()

    result = run(args.output_dir, args.macro_id, args.force, args.engine)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
