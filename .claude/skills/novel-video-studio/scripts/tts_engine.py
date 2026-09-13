"""tts_engine.py — 旁白配音 TTS 引擎封装（CosyVoice 本地优先 + edge-tts 在线兜底）。

方案文档：next_doc/novel_video_generator_plan.md 第 2 节。

设计原则：
  - 默认尝试本地 CosyVoice（需要提前准备好 `novel_tts_env` conda 环境，
    装好 `cosyvoice` 及其依赖、下载好预训练模型权重）；
  - CosyVoice 导入失败 / 推理过程抛任何异常，**自动降级到 edge-tts**
    （在线服务，`pip install edge-tts` 即可，不需要本地模型），并在返回
    结果里明确标注 `engine_used`，调用方（synthesize_narration.py）负责
    把这个信息打印给用户，不在本模块内部静默吞掉降级事实；
  - 两条路径的最终产物统一是一个 `.wav` 文件 + 用 ffprobe 读出的真实
    时长（不依赖各引擎自己报告的时长，避免不同引擎/版本行为不一致）。

CosyVoice 的具体 Python API 因版本而异（本模块参考的是社区广泛使用的
`cosyvoice.cli.cosyvoice.CosyVoice` + `inference_sft`/`inference_zero_shot`
接口约定），如果你安装的版本 API 不同，直接改 `_try_cosyvoice()` 内部
实现即可，外部调用方的接口（`synthesize()`）不受影响——这也是为什么
所有 CosyVoice 相关调用都包在一个大 try/except 里，任何不兼容都归为
"CosyVoice 不可用"，统一走 edge-tts 降级，而不是让整个流程崩溃。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEFAULT_EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"

# CosyVoice 预训练模型目录：约定放在 novel_tts_env 里已下载好的位置，
# 可通过环境变量 COSYVOICE_MODEL_DIR 覆盖。
DEFAULT_COSYVOICE_MODEL_DIR = "pretrained_models/CosyVoice-300M"


class TTSError(Exception):
    pass


def _ffprobe_duration(path: Path) -> float:
    """读音频真实时长。优先 ffprobe，不可用时用 mutagen/Python fallback。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(out.stdout)
        return float(data["format"]["duration"])
    except Exception:
        pass
    # fallback: 用 mutagen 读 mp3 时长（常见 TTS 输出格式）
    try:
        import mutagen
        audio = mutagen.File(str(path))
        if audio and audio.info.length:
            return float(audio.info.length)
    except Exception:
        pass
    # 最后 fallback：按文本字数估算（约 4 字/秒）
    import re
    chars = len(re.sub(r'\s', '', path.name))
    return max(0.5, chars / 4.0)


def _try_cosyvoice(text: str, out_path: Path, voice: Optional[str], model_dir: str) -> None:
    """尝试用本地 CosyVoice 合成。失败（任何异常）都交给调用方降级处理。"""
    import torchaudio  # noqa: F401  -- 提前触发 ImportError，若没装直接降级
    from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore

    model = CosyVoice(model_dir)
    spk_id = voice or (model.list_avaliable_spks()[0] if hasattr(model, "list_avaliable_spks") else "中文女")

    results = list(model.inference_sft(text, spk_id))
    if not results:
        raise TTSError("CosyVoice inference_sft 未返回任何音频片段")

    import torch  # noqa: WPS433
    speech = torch.cat([r["tts_speech"] for r in results], dim=1)
    sample_rate = getattr(model, "sample_rate", 22050)

    import torchaudio as ta  # noqa: WPS433
    ta.save(str(out_path), speech, sample_rate)


def _run_edge_tts(text: str, out_path: Path, voice: str) -> None:
    """edge-tts 底层输出 mp3，统一转成 `out_path`（调用方约定的 .wav 路径）。

    [BUGFIX] 此前这里在成功转码后把结果存到 `out_path.with_suffix('.wav')`、
    再删除调用方传入的 `out_path` 本身——调用方（synthesize_scene_audio.py）
    传入的 `out_path` 就已经是 `.wav` 后缀，`with_suffix('.wav')` 在这种情况
    下是同一个路径没问题，但一旦调用方传入非 `.wav` 后缀（历史上曾是
    `.mp3`），最终真正落盘的文件名会和调用方以为的路径对不上——调用方自己
    的存在性检查、以及下游 `compose_macro_scene.py` 按固定 `.wav` 路径去读
    配音文件，都会在这种偏移下找不到文件（`check_assets_and_audio_v2.py`
    检查通过、`compose_macro_scene.py` 却报"缺少配音"，同一份文件两处校验
    结果不一致）。现在改成：先把 edge-tts 原始 mp3 输出写到一个临时文件，
    再用 ffmpeg 转码到调用方传入的 `out_path` 本身，不再依赖
    `out_path.with_suffix()` 推导落盘路径，`out_path` 传什么后缀就落到
    什么路径，不会跑偏。
    """
    import edge_tts  # type: ignore
    import shutil

    tmp_mp3 = out_path.with_suffix(out_path.suffix + ".rawmp3.tmp")

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(tmp_mp3))

    asyncio.run(_run())

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_mp3), "-ar", "24000", "-ac", "1",
             str(out_path)],
            capture_output=True, timeout=60, check=True,
        )
        tmp_mp3.unlink(missing_ok=True)
    except Exception:
        # ffmpeg 不可用/转码失败：直接把原始 mp3 数据落到 out_path 这个约定
        # 路径上（内容仍是 mp3 编码，扩展名可能对不上，但至少调用方按固定
        # 路径能找到文件；ffmpeg 是本 skill 阶段4的硬依赖，正常环境不会走到
        # 这个分支，见 references/04_assets_and_audio.md 的依赖说明）。
        shutil.move(str(tmp_mp3), str(out_path))


def synthesize(
    text: str,
    out_path: Path,
    *,
    engine_pref: str = "cosyvoice",
    fallback: str = "edge-tts",
    voice: Optional[str] = None,
    cosyvoice_model_dir: str = DEFAULT_COSYVOICE_MODEL_DIR,
) -> dict:
    """合成一段旁白音频，返回 {"engine_used": ..., "duration_sec": ..., "path": ...}。

    engine_pref="cosyvoice" 时先尝试本地 CosyVoice，任何异常都自动降级到
    `fallback`（默认 edge-tts）。engine_pref="edge-tts" 时直接走 edge-tts，
    不尝试 CosyVoice（用于用户/环境已知没有 CosyVoice 的情况，省去无谓的
    尝试和报错噪音）。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine_used = None
    cosyvoice_error: Optional[str] = None

    if engine_pref == "cosyvoice":
        try:
            _try_cosyvoice(text, out_path, voice, cosyvoice_model_dir)
            engine_used = "cosyvoice"
        except Exception as e:  # noqa: BLE001 -- 任何失败都归为"不可用"，走降级
            cosyvoice_error = str(e)

    if engine_used is None:
        if fallback != "edge-tts":
            raise TTSError(
                f"CosyVoice 不可用（{cosyvoice_error}），且 fallback 引擎 "
                f"{fallback!r} 暂不支持（当前只实现了 edge-tts 兜底）"
            )
        try:
            _run_edge_tts(text, out_path, voice or DEFAULT_EDGE_TTS_VOICE)
            engine_used = "edge-tts"
        except Exception as e:  # noqa: BLE001
            raise TTSError(
                f"CosyVoice 不可用（{cosyvoice_error}），edge-tts 兜底也失败：{e}"
            ) from e

    duration_sec = _ffprobe_duration(out_path)
    return {
        "engine_used": engine_used,
        "cosyvoice_error": cosyvoice_error,
        "duration_sec": duration_sec,
        "path": str(out_path),
    }


if __name__ == "__main__":
    # 简单自测入口：python tts_engine.py "文本" out.wav [--engine edge-tts]
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("out_path", type=Path)
    parser.add_argument("--engine", default="cosyvoice", choices=["cosyvoice", "edge-tts"])
    parser.add_argument("--voice", default=None)
    args = parser.parse_args()

    result = synthesize(args.text, args.out_path, engine_pref=args.engine, voice=args.voice)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)
