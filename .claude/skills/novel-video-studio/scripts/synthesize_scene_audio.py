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

**`duration_sec` 只能来自实际测得的音频时长，不允许使用任何估算值**：
本脚本调用 `tts_engine.synthesize()` 时不传 `allow_estimated_duration`
（即固定使用其默认值 `False`），也没有提供任何命令行开关去打开它。

**读不出真实时长时（`DurationReadError`），本脚本立即整体终止，不会把
这一个 block 标记失败后继续处理别的场景**：这类失败（`ffprobe`/
`mutagen` 都不可用）几乎总是环境问题，不是某一条文本偶发出错——环境
坏了，后面几十上百个 block 大概率会遇到一模一样的失败，硬跑完只会
浪费时间（包括白白消耗掉那些能正常合成、只是读不出时长的 TTS 调用
次数），还会让人误以为是很多个独立小问题。终止时会在 stdout 打印
清楚的 `error`/`action_required`，说明是哪个文件、哪个环节读取失败，
交给 Agent 先去修好环境（检查/安装 `ffmpeg`、或确认 `mutagen` 可用）
再重新跑（用 `--force` 重新生成断点续跑跳过的那些片段）。

其它类型的配音失败（`TTSError`，比如内容审核拦截、网络超时、单条
CosyVoice 推理失败）仍按原来的方式处理：只把这一个 block 记入
`errors`、其余场景继续跑完，因为这类失败通常是单条文本/单次请求的
偶发问题，不代表环境本身坏了，没有理由为了一条失败的台词中断整批
已经在正常进行的配音工作。**这类失败会先自动重试**（默认最多
`DEFAULT_MAX_RETRIES`=3 次，每次重试之间做简单的线性退避
`DEFAULT_RETRY_BACKOFF_SEC`=2s、4s...，可用 `--max-retries`/
`--retry-backoff-sec` 调整），因为这类失败里有相当一部分（网络抖动、
服务端偶发限流/超时）重试一次就能过，不需要每次都要人工介入重跑整条
命令；重试次数用尽仍失败，才最终记入这一个 block 失败。

**失败原因会打印完整细节，不只是一句 `str(exception)`**：每次重试
失败都会把这次尝试的完整 traceback（`traceback.format_exc()`，包含
`tts_engine.py` 里 `raise ... from e` 保留下来的原始异常链——比如
CosyVoice 本地推理的原始报错、edge-tts 网络请求的原始异常、或者服务端
返回的错误信息，只要底层异常对象里带了这些信息就都在链上）通过 `_log`
实时打印到 stderr，多次重试之间不会互相覆盖看不清哪次失败在哪；最终
放弃这个 block 后，会把最后一次尝试的完整 traceback 一并写进返回结果的
`failed_blocks` 字段（而不只是 `errors` 里那一行摘要），方便不方便看
stderr 完整历史时也能从最终 JSON 结果里拿到足够定位问题的信息。

断点续跑：已经存在且非空的音频文件默认直接复用（读取真实时长回填），
不重新合成，`--force` 才会重新生成全部已存在的音频。这意味着重跑一次
失败很多的命令时，之前已经成功生成的音频不会被重复消耗合成配额，只有
真正失败/缺失的那些会被处理——**这条逻辑本来就有（见下方
`out_path.exists()` 判断），本次修改没有改变它，只是把它和新增的重试
机制放在一起说明**：重试解决的是"单次请求失败"，断点续跑解决的是"上次
命令整体中断/部分失败后重跑不用从头来"，两者分工不同、配合使用。

不处理定妆图生成（那部分仍由 Agent 直接调用 gen_image_with_text 完成，
见 SKILL.md Step 1）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(1)

from tts_engine import (
    DEFAULT_COSYVOICE_TIMEOUT_SEC,
    DEFAULT_EDGE_TTS_TIMEOUT_SEC,
    DurationReadError,
    TTSError,
    synthesize,
)
from voice_mapping import resolve_cosyvoice_speaker, resolve_edge_tts_voice
from common import macro_scene_dir_name

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SEC = 2.0


def _log(msg: str) -> None:
    """过程日志统一走 stderr 并显式 flush，保证被外部工具捕获/重定向时也
    能实时看到，不会攒在缓冲区里最后一次性冒出来（见
    next_doc/novel_video_studio_fix_plan_v1.md 问题4）。"""
    print(msg, file=sys.stderr, flush=True)


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


class _FatalDurationStop(Exception):
    """内部信号类，不代表某个 block 失败，代表"环境本身读不出真实时长，
    应该立即整体终止脚本"。在 `run()` 的最外层统一捕获并转成结构化结果，
    不让它冒泡成未处理异常的 traceback（那样 Agent 看到的是一堆 Python
    调用栈，不如这里直接给出的 `error`/`action_required` 清楚）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def run(
    output_dir: Path,
    macro_id: str | None,
    force: bool,
    engine_pref: str,
    *,
    edge_tts_timeout_sec: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC,
    cosyvoice_timeout_sec: float = DEFAULT_COSYVOICE_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_sec: float = DEFAULT_RETRY_BACKOFF_SEC,
) -> dict:
    project = _load_json(output_dir / "novel_project.json")
    characters_data = _load_json(output_dir / "global" / "characters.json")
    char_by_id = {c.get("id"): c for c in characters_data.get("characters", [])}

    tts_cfg = project.get("tts", {}) if isinstance(project, dict) else {}
    # 若配置为 cosyvoice 但本地未装，直接降级到 edge-tts
    raw_pref = engine_pref or tts_cfg.get("engine", "cosyvoice")
    if raw_pref == "cosyvoice":
        try:
            import torchaudio  # noqa: F401
            engine_pref = "cosyvoice"
        except ImportError:
            engine_pref = "edge-tts"
    else:
        engine_pref = raw_pref
    fallback = tts_cfg.get("fallback", "edge-tts")
    if engine_pref == "cosyvoice" and fallback == "edge-tts":
        # cosyvoice 不可用时直接走 edge-tts
        engine_pref = "edge-tts"

    if macro_id:
        detail_files = [output_dir / macro_scene_dir_name(macro_id) / "scene_detail.yaml"]
    else:
        detail_files = sorted(output_dir.glob("macro_scene_*/scene_detail.yaml"))

    engine_usage: dict[str, int] = {}
    errors: list[str] = []
    failed_blocks: list[dict] = []
    processed_micro_scenes = 0
    run_started = time.monotonic()

    try:
        for detail_file in detail_files:
            if not detail_file.exists():
                errors.append(f"{detail_file} 不存在")
                continue
            data = _load_yaml(detail_file)
            micro_scenes = data.get("micro_scenes", []) if isinstance(data, dict) else []
            audio_dir = detail_file.parent / "audio"
            macro_label = detail_file.parent.name

            _log(f"[{macro_label}] 开始配音，共 {len(micro_scenes)} 个 micro_scene")

            for ms in micro_scenes:
                mid = ms.get("id", "unknown")
                content_blocks = ms.get("content_blocks", []) or []
                total_duration = 0.0
                block_failed = False
                n_blocks = len(content_blocks)

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
                            _log(
                                f"[{macro_label}][{mid}][block {i}/{n_blocks}] "
                                f"复用已存在音频 时长={total_duration:.2f}s（累计）"
                            )
                        except DurationReadError as e:
                            raise _FatalDurationStop(
                                f"读取已存在音频 {out_path} 的真实时长失败：{e}"
                            ) from e
                        except Exception as e:  # noqa: BLE001 -- 非"读不出时长"类的意外错误，按单 block 失败处理
                            tb = traceback.format_exc()
                            errors.append(f"小场景 {mid} 第{i}块已存在音频但读取失败：{e}")
                            failed_blocks.append({
                                "macro": macro_label,
                                "micro_id": mid,
                                "block_index": i,
                                "label": "existing_audio_read",
                                "attempts": 1,
                                "error": str(e),
                                "traceback": tb,
                            })
                            block_failed = True
                            _log(f"[{macro_label}][{mid}][block {i}/{n_blocks}] 已存在音频但读取失败：{e}\n{tb}")
                        continue

                    voice, label = _voice_for_block(block, char_by_id, engine_pref)
                    block_started = time.monotonic()
                    result = None
                    last_error: str | None = None
                    last_tb: str | None = None
                    attempts_used = 0

                    for attempt in range(1, max_retries + 1):
                        attempts_used = attempt
                        try:
                            result = synthesize(
                                text, out_path,
                                engine_pref=engine_pref, fallback=fallback, voice=voice,
                                edge_tts_timeout_sec=edge_tts_timeout_sec,
                                cosyvoice_timeout_sec=cosyvoice_timeout_sec,
                                # 不传 allow_estimated_duration：固定使用 synthesize()
                                # 的默认值 False，本脚本不提供任何打开估算值的入口。
                            )
                            break
                        except DurationReadError as e:
                            # 合成本身已经成功落盘（音频文件是好的），只是读不出
                            # 真实时长——这是环境问题，不是这一条文本的问题，不
                            # 走重试（重试大概率会遇到一模一样的环境问题，纯粹
                            # 浪费合成次数），直接整体终止。
                            raise _FatalDurationStop(
                                f"小场景 {mid} 第{i}块（{label}）音频已合成到 {out_path}，"
                                f"但读取真实时长失败：{e}"
                            ) from e
                        except TTSError as e:
                            last_error = str(e)
                            last_tb = traceback.format_exc()
                            _log(
                                f"[{macro_label}][{mid}][block {i}/{n_blocks}][{label}] "
                                f"第{attempt}/{max_retries}次尝试失败 原因={last_error}\n{last_tb}"
                            )
                            if attempt < max_retries:
                                time.sleep(retry_backoff_sec * attempt)
                            continue

                    if result is None:
                        errors.append(
                            f"小场景 {mid} 第{i}块（{label}）配音失败"
                            f"（已重试{attempts_used}/{max_retries}次）：{last_error}"
                        )
                        failed_blocks.append({
                            "macro": macro_label,
                            "micro_id": mid,
                            "block_index": i,
                            "label": label,
                            "attempts": attempts_used,
                            "error": last_error,
                            "traceback": last_tb,
                        })
                        block_failed = True
                        _log(
                            f"[{macro_label}][{mid}][block {i}/{n_blocks}][{label}] "
                            f"最终失败（已重试{attempts_used}/{max_retries}次），用时="
                            f"{time.monotonic() - block_started:.1f}s"
                        )
                        continue

                    elapsed = time.monotonic() - block_started
                    engine_usage[result["engine_used"]] = engine_usage.get(result["engine_used"], 0) + 1
                    total_duration += result["duration_sec"]
                    _log(
                        f"[{macro_label}][{mid}][block {i}/{n_blocks}][{label}] "
                        f"引擎={result['engine_used']} 用时={elapsed:.1f}s "
                        f"时长={result['duration_sec']:.2f}s（实测）"
                    )

                if not block_failed:
                    ms["duration_sec"] = round(total_duration, 2)
                    processed_micro_scenes += 1
                    _log(f"[{macro_label}][{mid}] 完成，duration_sec={ms['duration_sec']}")
                else:
                    _log(f"[{macro_label}][{mid}] 存在失败的 content_block，本小场景 duration_sec 不回填")

            _write_yaml(detail_file, data)
    except _FatalDurationStop as e:
        _log(f"[致命错误，脚本终止] {e.message}")
        try:
            # 把这次运行中已经成功拿到真实时长的部分先落盘，不要因为
            # 后面环境坏了，连这次运行里已经做对的部分也一起丢掉——
            # 下次修好环境重跑时，断点续跑能从这里继续，而不是从头来过。
            _write_yaml(detail_file, data)
        except Exception:  # noqa: BLE001 -- 落盘失败不能掩盖真正的致命原因，忽略即可
            pass
        return {
            "ok": False,
            "fatal": True,
            "error": e.message,
            "action_required": (
                "读不出真实音频时长通常是环境问题（ffprobe/ffmpeg 未安装或"
                "不可执行、mutagen 未装），已立即终止，不再继续处理后面的场景。"
                "请先检查/修复本地 ffmpeg（确保命令行能跑通 `ffprobe -version`），"
                "或确认 `pip install mutagen` 已装好，任选其一能正常读出任意 "
                "wav 文件的时长后，再重新执行本命令；已成功生成的音频会因为"
                "断点续跑机制被跳过，不需要 --force 也不会被重复消耗合成次数，"
                "只有真正没有产出真实时长的片段会被重新处理。"
            ),
            "errors": errors,
            "failed_blocks": failed_blocks,
            "engine_usage": engine_usage,
            "processed_micro_scenes": processed_micro_scenes,
        }

    total_elapsed = time.monotonic() - run_started
    _log(
        f"[汇总] 处理完成，用时={total_elapsed:.1f}s，成功 micro_scene={processed_micro_scenes}，"
        f"engine_usage={engine_usage}，失败数={len(errors)}"
    )

    return {
        "ok": not errors,
        "fatal": False,
        "errors": errors,
        "failed_blocks": failed_blocks,
        "engine_usage": engine_usage,
        "processed_micro_scenes": processed_micro_scenes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--macro-id", default=None, help="只处理指定大场景，不传则处理全部 macro_scene_*")
    parser.add_argument("--force", action="store_true", help="强制重新生成已存在的音频")
    parser.add_argument("--engine", default=None, choices=["cosyvoice", "edge-tts"], help="覆盖 novel_project.json 里的 tts.engine")
    parser.add_argument(
        "--edge-tts-timeout", type=float, default=DEFAULT_EDGE_TTS_TIMEOUT_SEC,
        help=f"edge-tts 在线请求超时秒数（默认 {DEFAULT_EDGE_TTS_TIMEOUT_SEC}）",
    )
    parser.add_argument(
        "--cosyvoice-timeout", type=float, default=DEFAULT_COSYVOICE_TIMEOUT_SEC,
        help=f"CosyVoice 本地推理超时秒数（默认 {DEFAULT_COSYVOICE_TIMEOUT_SEC}）",
    )
    parser.add_argument(
        "--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
        help=f"单个 content_block 配音失败后的最大重试次数（默认 {DEFAULT_MAX_RETRIES}，"
             f"不含首次尝试之外的重试次数即为该值，比如默认值3代表最多尝试3次）",
    )
    parser.add_argument(
        "--retry-backoff-sec", type=float, default=DEFAULT_RETRY_BACKOFF_SEC,
        help=f"重试之间的等待秒数，按尝试次数线性递增（默认 {DEFAULT_RETRY_BACKOFF_SEC}，"
             f"即第1次重试前等这个值，第2次重试前等它的2倍，以此类推）",
    )
    args = parser.parse_args()

    result = run(
        args.output_dir, args.macro_id, args.force, args.engine,
        edge_tts_timeout_sec=args.edge_tts_timeout,
        cosyvoice_timeout_sec=args.cosyvoice_timeout,
        max_retries=args.max_retries,
        retry_backoff_sec=args.retry_backoff_sec,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
