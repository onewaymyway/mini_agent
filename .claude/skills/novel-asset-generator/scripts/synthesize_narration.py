"""synthesize_narration.py — 批量给 narration_script.yaml 的每段旁白配音，
并据此生成正式版 scene_plan.yaml（回填每个 scene 的精确时长）。

用法：
    python synthesize_narration.py <output_dir> [--segment-id seg_01 seg_02 ...] [--force]

行为：
  1. 读取 <output_dir>/narration_script.yaml 和 <output_dir>/novel_project.json
     （取 tts.engine / tts.fallback / tts.voice 配置）。
  2. 对每个 segment（或 --segment-id 指定的子集）调用 tts_engine.synthesize()，
     产出 <output_dir>/audio/segment_<id>.wav。
     - 已存在且非空的音频默认跳过（断点续跑），--force 强制重新生成。
  3. 用每段真实音频时长，把 narration_script.yaml 的 segments 转换成正式版
     <output_dir>/scene_plan.yaml（scene id 沿用 segment id 改前缀
     `scene_` + 序号，字段见下方"产出格式"）。
  4. 对时长超出 [4, 12] 秒的 segment，**只报告不自动处理**——是否拆分/
     合并、如何改写文案，必须由 Agent 结合语义决定；改完 narration_script.yaml
     后对该 segment 重新跑一次本脚本（用 --segment-id 只重新生成受影响的
     场景，不需要全量重跑）。

产出格式（scene_plan.yaml）：
    scenes:
      - id: scene_01
        segment_id: seg_01
        narration_audio: audio/segment_seg_01.wav
        duration_sec: 6.8
        uses_characters: [char_01]
        uses_locations: [loc_01]
        text: "旁白口播文案"
        visual_hint: "..."
        prompt_en: null          # novel-scene-video-generator 生成前需要
                                   # Agent 补充，本脚本不负责写 prompt
        video_mode: null          # 同上，由 Agent 结合 uses_assets 是否
                                   # 就绪来决定 reference/keyframe/text
        status: pending

退出码：
  0 = 全部成功且无时长越界
  1 = 存在时长越界或生成失败的 segment，stdout 打印结构化问题清单
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from tts_engine import synthesize, TTSError  # noqa: E402

MIN_DURATION = 4.0
MAX_DURATION = 12.0


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_yaml(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--segment-id", nargs="*", default=None, help="只处理指定的 segment id，不传则处理全部")
    parser.add_argument("--force", action="store_true", help="强制重新生成，即使音频文件已存在")
    args = parser.parse_args()

    output_dir: Path = args.output_dir
    narration_path = output_dir / "narration_script.yaml"
    project_path = output_dir / "novel_project.json"
    scene_plan_path = output_dir / "scene_plan.yaml"
    audio_dir = output_dir / "audio"

    if not narration_path.exists():
        print(json.dumps({"ok": False, "errors": [f"{narration_path} 不存在"]}, ensure_ascii=False, indent=2))
        sys.exit(1)
    if not project_path.exists():
        print(json.dumps({"ok": False, "errors": [f"{project_path} 不存在"]}, ensure_ascii=False, indent=2))
        sys.exit(1)

    narration = _load_yaml(narration_path)
    project = _load_json(project_path)
    tts_cfg = project.get("tts", {})
    engine_pref = tts_cfg.get("engine", "cosyvoice")
    fallback = tts_cfg.get("fallback", "edge-tts")
    voice = tts_cfg.get("voice")

    segments = narration.get("segments", [])
    target_ids = set(args.segment_id) if args.segment_id else None

    # 若 scene_plan.yaml 已存在，读出来复用（增量更新，不丢弃其它字段的既有内容，
    # 比如 Agent 已经手写过的 prompt_en/video_mode）
    existing_scenes = {}
    if scene_plan_path.exists():
        existing = _load_yaml(scene_plan_path)
        for sc in existing.get("scenes", []):
            existing_scenes[sc.get("segment_id")] = sc

    errors = []
    warnings = []
    scenes = []

    for idx, seg in enumerate(segments, start=1):
        seg_id = seg.get("id", f"seg_{idx:02d}")
        if target_ids is not None and seg_id not in target_ids:
            # 不在本次处理范围内：沿用已有 scene_plan.yaml 里的记录（如果有）
            if seg_id in existing_scenes:
                scenes.append(existing_scenes[seg_id])
            continue

        audio_path = audio_dir / f"segment_{seg_id}.wav"
        text = (seg.get("text") or "").strip()
        prior = existing_scenes.get(seg_id, {})

        def _keep_prior_or_skip(reason: str) -> None:
            """生成/读取失败时：如果之前已经有一版成功的记录，保留旧记录
            （不因为这次重跑失败就把已有数据丢了），只在从没成功过时才
            彻底跳过。"""
            errors.append(reason)
            if prior:
                scenes.append(prior)

        if not text:
            _keep_prior_or_skip(f"segment {seg_id} 的 text 为空，跳过配音")
            continue

        need_generate = args.force or not audio_path.exists() or audio_path.stat().st_size == 0
        if need_generate:
            try:
                result = synthesize(
                    text, audio_path,
                    engine_pref=engine_pref, fallback=fallback, voice=voice,
                )
                print(
                    f"[{idx}/{len(segments)}] segment {seg_id} 配音完成，"
                    f"引擎={result['engine_used']}，时长={result['duration_sec']:.1f}s"
                    + (f"（CosyVoice 不可用已降级：{result['cosyvoice_error']}）" if result.get("cosyvoice_error") else "")
                )
                duration_sec = result["duration_sec"]
            except TTSError as e:
                _keep_prior_or_skip(f"segment {seg_id} 配音失败：{e}（已保留上一次的记录，未更新）" if prior else f"segment {seg_id} 配音失败：{e}")
                continue
        else:
            from tts_engine import _ffprobe_duration  # noqa: WPS433
            try:
                duration_sec = _ffprobe_duration(audio_path)
                print(f"[{idx}/{len(segments)}] segment {seg_id} 已有音频，跳过（--force 可强制重新生成）")
            except TTSError as e:
                _keep_prior_or_skip(f"segment {seg_id} 已有音频但读取时长失败：{e}")
                continue

        if duration_sec > MAX_DURATION:
            warnings.append(
                f"segment {seg_id} 音频时长 {duration_sec:.1f}s 超过 {MAX_DURATION}s 上限，"
                f"需要 Agent 回 narration_script.yaml 拆分该段文案，改完用 "
                f"--segment-id {seg_id} 重新跑本脚本"
            )
        elif duration_sec < MIN_DURATION:
            warnings.append(
                f"segment {seg_id} 音频时长 {duration_sec:.1f}s 低于 {MIN_DURATION}s 下限，"
                f"建议 Agent 考虑与相邻场景合并"
            )

        scenes.append({
            "id": prior.get("id", f"scene_{idx:02d}"),
            "segment_id": seg_id,
            "narration_audio": f"audio/segment_{seg_id}.wav",
            "duration_sec": round(duration_sec, 2),
            "uses_characters": seg.get("uses_characters", []),
            "uses_locations": seg.get("uses_locations", []),
            "text": text,
            "visual_hint": seg.get("visual_hint", ""),
            "prompt_en": prior.get("prompt_en"),
            "video_mode": prior.get("video_mode"),
            "status": prior.get("status", "pending"),
        })

    _dump_yaml(scene_plan_path, {"scenes": scenes})

    total_duration = sum(s["duration_sec"] for s in scenes)
    result = {
        "ok": not errors and not warnings,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "scene_count": len(scenes),
            "total_duration_sec": round(total_duration, 1),
            "target_duration_sec": project.get("target_duration_sec"),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if (not errors and not warnings) else 1)


if __name__ == "__main__":
    main()
