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
import concurrent.futures
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from common import resolve_ffprobe

DEFAULT_EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"

# CosyVoice 预训练模型目录：约定放在 novel_tts_env 里已下载好的位置，
# 可通过环境变量 COSYVOICE_MODEL_DIR 覆盖。
DEFAULT_COSYVOICE_MODEL_DIR = "pretrained_models/CosyVoice-300M"

# edge-tts 在线请求 / CosyVoice 本地推理的默认超时（秒），可通过
# synthesize() 的参数覆盖。见 next_doc/novel_video_studio_fix_plan_v1.md
# 问题4：这两处调用此前完全没有超时保护，网络不通/模型很慢时会无限期
# 挂起，且外部无法区分"卡死"和"正常运行中"。
DEFAULT_EDGE_TTS_TIMEOUT_SEC = 45.0
DEFAULT_COSYVOICE_TIMEOUT_SEC = 60.0

# 按文本真实字数估算时长的粗略语速（字/秒），仅在显式打开
# allow_estimated_duration 时使用，且只用于"未合成前无法拿到真实文件"
# 或者"ffprobe/mutagen 都读不出时长"的兜底场景。
_ESTIMATE_CHARS_PER_SEC = 4.0


class TTSError(Exception):
    pass


class DurationReadError(TTSError):
    """专指"合成/文件都拿到了，但读不出真实时长（ffprobe 和 mutagen 都
    失败）"这一类错误——通常意味着当前环境本身有问题（`ffprobe`/
    `ffmpeg` 未安装或不可执行、`mutagen` 未装），不是某一条文本/某一次
    网络请求的偶发失败：接下来处理的每一个 block 大概率会遇到同样的
    问题。调用方（`synthesize_scene_audio.py`）据此和"这条文本合成失败"
    这类可能只是单条偶发问题的 `TTSError` 区分开，遇到这个类型直接整体
    终止脚本、把环境问题报给人处理，而不是把每个 block 各记一条错误、
    继续硬跑完剩下几十上百个 block（既没有意义，也会让人误以为是很多
    个独立小问题而不是一个环境配置问题）。
    """


def _estimate_duration_by_text(text: str) -> float:
    """按**文本真实字数**（不是文件名）粗略估算时长，约
    `_ESTIMATE_CHARS_PER_SEC` 字/秒。只应在 `allow_estimated_duration=True`
    且 ffprobe/mutagen 都失败时调用，返回值必须让调用方标记
    `duration_estimated: True`，不能和真实测得的时长混在一起看不出区别。
    """
    chars = len(re.sub(r"\s", "", text or ""))
    return max(0.5, chars / _ESTIMATE_CHARS_PER_SEC)


def _ffprobe_duration(path: Path) -> float:
    """读音频**真实**时长。优先 ffprobe，不可用时用 mutagen。

    [BUGFIX，见 next_doc/novel_video_studio_fix_plan_v1.md 问题1] 此前
    这里在 ffprobe/mutagen 都失败时，还有第三级"fallback"按 `path.name`
    （音频文件名字符串，形如 `narration_seg_micro_16_00.wav`）估算时长——
    这是一次传参错误（应该传真正要合成的文本，却传了文件名），不是"简陋
    但方向对"的近似，而且这个 fallback 不报任何错，产出的假数据会被当成
    真实数据一路写进 `scene_detail.yaml` 并通过下游所有校验。现在两级都
    失败时抛 `DurationReadError`（`TTSError` 的子类），调用方
    `synthesize_scene_audio.py` 会把这类错误当成环境问题**立即整体终止
    脚本**并说明原因，不会静默接受、也不会继续硬跑完剩下的场景。如果
    确实需要在读不到真实时长时退回估算（不推荐，仅用于临时验证流程本身
    跑不跑得通，不能用于产出正式交付内容），调用方应显式使用
    `synthesize()` 的 `allow_estimated_duration=True`，并按**文本**
    （而不是文件名）估算。

    [一致性修复] 此前这里只有一行 `os.environ.get("NOVEL_FFPROBE_PATH")
    or "ffprobe"`，能读到环境变量，但没有 `common.resolve_ffprobe()` 的
    imageio_ffmpeg 猜测路径/conda 环境自动探测这两级兜底，和本 skill 其它
    脚本（`compose_macro_scene.py`/`compose_final_video_v2.py`/
    `check_assets_and_audio_v2.py`）用的不是同一套逻辑，容易出现"同一台
    机器上，一个脚本能找到 ffprobe、另一个找不到"的不一致。现在统一改用
    `common.resolve_ffprobe()`——这里只是拿它当"探测"用，找不到时不重新
    抛错，直接往下走 mutagen 兜底（或者最终的 `DurationReadError`），
    行为和此前一致，只是换了更完整的探测逻辑。
    """
    try:
        _ffprobe_cmd = resolve_ffprobe()
    except RuntimeError:
        _ffprobe_cmd = None

    if _ffprobe_cmd:
        try:
            out = subprocess.run(
                [_ffprobe_cmd, "-v", "quiet", "-print_format", "json",
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
    raise DurationReadError(
        f"无法读取 {path} 的真实音频时长（ffprobe 和 mutagen 都失败），"
        f"当前环境的 ffprobe 可能不可用，请先检查/修复 ffprobe（或安装/修复 "
        f"mutagen 作为备选），确认修好后再重新运行，不建议退回估算值"
    )


def _try_cosyvoice(
    text: str,
    out_path: Path,
    voice: Optional[str],
    model_dir: str,
    timeout_sec: float = DEFAULT_COSYVOICE_TIMEOUT_SEC,
) -> None:
    """尝试用本地 CosyVoice 合成。失败（任何异常，包括超时）都交给调用方
    降级处理。

    CosyVoice 推理是同步阻塞调用，`asyncio.wait_for` 不适用；用
    `ThreadPoolExecutor` 包一层来做超时控制（跨平台，不依赖 Unix-only 的
    `signal.alarm`）。注意 Python 线程无法被强制中断，超时只是不再等待
    结果、把调用判定为失败并走降级，底层线程可能仍在后台跑一段时间，这
    是一个已知的权衡（见 references/05_assets_and_audio.md 说明）。
    """

    def _do_inference() -> None:
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_inference)
        try:
            future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError as e:
            raise TTSError(
                f"CosyVoice 本地推理超时（>{timeout_sec}s），模型文件可能较大或跑在"
                f"CPU 上较慢，可通过 cosyvoice_timeout_sec 调整超时阈值"
            ) from e


def _run_edge_tts(
    text: str,
    out_path: Path,
    voice: str,
    timeout_sec: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC,
) -> None:
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

    [BUGFIX，见 next_doc/novel_video_studio_fix_plan_v1.md 问题4] 此前
    `edge_tts.Communicate(...).save(...)` 这次在线网络请求全程没有设置
    任何超时——网络不通/被墙/DNS解析慢/服务端无响应时会无限期挂起，且
    没有任何机制能打断它。现在用 `asyncio.wait_for` 包一层，超时后抛
    `TTSError`，交给调用方按"该 block 配音失败"处理。
    """
    import edge_tts  # type: ignore
    import shutil

    tmp_mp3 = out_path.with_suffix(out_path.suffix + ".rawmp3.tmp")

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(tmp_mp3))

    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=timeout_sec))
    except asyncio.TimeoutError as e:
        raise TTSError(
            f"edge-tts 在线请求超时（>{timeout_sec}s），可能是网络不通/被墙/"
            f"DNS解析慢/服务端无响应，可通过 edge_tts_timeout_sec 调整超时阈值"
        ) from e

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
        # 路径能找到文件；ffmpeg 是本 skill 阶段6的硬依赖，正常环境不会走到
        # 这个分支，见 references/05_assets_and_audio.md 的依赖说明）。
        shutil.move(str(tmp_mp3), str(out_path))


def synthesize(
    text: str,
    out_path: Path,
    *,
    engine_pref: str = "cosyvoice",
    fallback: str = "edge-tts",
    voice: Optional[str] = None,
    cosyvoice_model_dir: str = DEFAULT_COSYVOICE_MODEL_DIR,
    edge_tts_timeout_sec: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC,
    cosyvoice_timeout_sec: float = DEFAULT_COSYVOICE_TIMEOUT_SEC,
    allow_estimated_duration: bool = False,
) -> dict:
    """合成一段旁白音频，返回
    {"engine_used": ..., "duration_sec": ..., "duration_estimated": ..., "path": ...}。

    engine_pref="cosyvoice" 时先尝试本地 CosyVoice，任何异常（含超时）都
    自动降级到 `fallback`（默认 edge-tts）。engine_pref="edge-tts" 时直接
    走 edge-tts，不尝试 CosyVoice（用于用户/环境已知没有 CosyVoice 的情况，
    省去无谓的尝试和报错噪音）。

    `edge_tts_timeout_sec`/`cosyvoice_timeout_sec` 控制两条合成路径各自
    的超时阈值，超时会被当成该引擎失败处理（cosyvoice 超时走 edge-tts
    降级，edge-tts 超时直接抛 TTSError）。

    `allow_estimated_duration`（默认 False）：合成成功后仍读不出真实时长
    （ffprobe/mutagen 都失败）时，是否允许退回"按**文本**真实字数"估算
    （不是按文件名，见 `_ffprobe_duration` 的说明）。默认关闭，读不到真实
    时长直接抛 TTSError，避免假数据被静默当成真实数据使用；显式打开时，
    返回结果里的 `duration_estimated` 会标记为 True，调用方必须把这个
    信息汇总展示给用户，不能和真实测得的时长混在一起看不出区别。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine_used = None
    cosyvoice_error: Optional[str] = None

    if engine_pref == "cosyvoice":
        try:
            _try_cosyvoice(text, out_path, voice, cosyvoice_model_dir, timeout_sec=cosyvoice_timeout_sec)
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
            _run_edge_tts(text, out_path, voice or DEFAULT_EDGE_TTS_VOICE, timeout_sec=edge_tts_timeout_sec)
            engine_used = "edge-tts"
        except Exception as e:  # noqa: BLE001
            raise TTSError(
                f"CosyVoice 不可用（{cosyvoice_error}），edge-tts 兜底也失败：{e}"
            ) from e

    duration_estimated = False
    try:
        duration_sec = _ffprobe_duration(out_path)
    except TTSError:
        if not allow_estimated_duration:
            raise
        duration_sec = _estimate_duration_by_text(text)
        duration_estimated = True

    return {
        "engine_used": engine_used,
        "cosyvoice_error": cosyvoice_error,
        "duration_sec": duration_sec,
        "duration_estimated": duration_estimated,
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
    parser.add_argument("--edge-tts-timeout", type=float, default=DEFAULT_EDGE_TTS_TIMEOUT_SEC)
    parser.add_argument("--cosyvoice-timeout", type=float, default=DEFAULT_COSYVOICE_TIMEOUT_SEC)
    parser.add_argument(
        "--allow-estimated-duration", action="store_true",
        help="真实时长读取失败时，允许退回按文本字数估算（默认关闭，直接报错）",
    )
    args = parser.parse_args()

    result = synthesize(
        args.text, args.out_path, engine_pref=args.engine, voice=args.voice,
        edge_tts_timeout_sec=args.edge_tts_timeout,
        cosyvoice_timeout_sec=args.cosyvoice_timeout,
        allow_estimated_duration=args.allow_estimated_duration,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)
