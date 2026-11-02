"""providers/tts.py — 配音 provider。

  * SilenceTTS（stub）：按字数估算时长写静音 wav，离线可用，只用于流程验证，不进入正式降级链；
  * CosyVoiceTTS：本地 CosyVoice（需 cosyvoice 环境与模型权重）；
  * EdgeTTS：edge-tts 在线语音（需 pip install -U edge-tts 且网络可达）；
  * ChainTTS：按顺序尝试引擎（默认 cosyvoice → edge-tts，与 novel-video-studio 的 tts_engine.synthesize 一致）。
    任一引擎失败就降级到下一个；记录实际使用的引擎（last_engine）与各引擎的失败原因（last_errors）。
返回实际时长（秒）。
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path

from .. import cancel
from ..media import ffmpeg as mf

CHARS_PER_SEC = 4.2  # 中文口播的粗略语速，仅用于 stub 与规划估算

VOICE_BY_GENDER = {
    ("narration", None): "zh-CN-YunjianNeural",
    ("male", None): "zh-CN-YunxiNeural",
    ("female", None): "zh-CN-XiaoxiaoNeural",
    ("unknown", None): "zh-CN-XiaoyiNeural",
}


def voice_for(role: str, gender: str | None) -> str:
    """role: narration | dialogue；gender 决定对白音色。"""
    if role == "narration":
        return VOICE_BY_GENDER[("narration", None)]
    return VOICE_BY_GENDER.get((gender or "unknown", None), VOICE_BY_GENDER[("unknown", None)])


def estimate_seconds(text: str) -> float:
    return max(0.6, len(re.sub(r"\s", "", text)) / CHARS_PER_SEC)


class TTSProvider(ABC):
    name = "base"

    @abstractmethod
    def synth(self, text: str, out_wav: Path, voice: str) -> float: ...


class SilenceTTS(TTSProvider):
    name = "stub"
    last_engine = "stub"

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        return mf.write_silence_wav(out_wav, estimate_seconds(text))


def edge_tts_version() -> str:
    try:
        import importlib.metadata as md
        return md.version("edge-tts")
    except Exception:  # noqa: BLE001
        return "未知"


def edge_tts_error_hint(exc: Exception) -> str:
    """把 edge-tts 的底层错误翻译成用户能操作的说明。原始信息保留在末尾，便于排查。"""
    raw = f"{type(exc).__name__}: {exc}"
    text = str(exc)
    ver = edge_tts_version()
    if "403" in text or "Invalid response status" in text:
        return (f"edge-tts 服务拒绝连接（403，当前 edge-tts 版本 {ver}）。按顺序排查："
                "①执行 pip install -U edge-tts 升级到最新版；"
                "②在挡位页“配音网络”选择“强制直连”（或在 tts.edge_tts_proxy 设为 direct），绕过系统代理；"
                "③若系统代理是必需的，把 tts.edge_tts_proxy 设为可用的代理地址；"
                "④以上都无效时，服务端可能已拒绝该客户端，需要改用其他配音引擎。"
                f"原始错误：{raw}")
    if "Name or service not known" in text or "ClientConnectorDNSError" in text or "ClientConnectorError" in text:
        return (f"无法连接 edge-tts 服务（DNS/网络不通，当前版本 {ver}）。请检查网络或代理设置。原始错误：{raw}")
    return f"edge-tts 配音失败（当前版本 {ver}）：{raw}"


# 与 novel-video-studio/scripts/tts_engine.py 保持一致的默认值
DEFAULT_COSYVOICE_MODEL_DIR = "pretrained_models/CosyVoice-300M"   # 可用环境变量 COSYVOICE_MODEL_DIR 覆盖
DEFAULT_COSYVOICE_TIMEOUT_SEC = 60.0
DEFAULT_EDGE_TTS_TIMEOUT_SEC = 45.0
DEFAULT_EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"

# CosyVoice 没有 edge-tts 的音色名，按性别映射到它的说话人
COSY_SPEAKER_BY_EDGE_VOICE = {
    "zh-CN-YunjianNeural": "中文男",
    "zh-CN-YunxiNeural": "中文男",
    "zh-CN-XiaoxiaoNeural": "中文女",
    "zh-CN-XiaoyiNeural": "中文女",
}
_COSY_MODELS: dict = {}   # 模型目录 -> 已加载的 CosyVoice 实例（同一进程只加载一次）


def _cosy_model(model_dir: str):
    if model_dir not in _COSY_MODELS:
        from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore
        _COSY_MODELS[model_dir] = CosyVoice(model_dir)
    return _COSY_MODELS[model_dir]


class CosyVoiceTTS(TTSProvider):
    """本地 CosyVoice。任何失败（未安装、模型缺失、推理异常、超时）都抛 RuntimeError，由 ChainTTS 决定降级。"""
    name = "cosyvoice"

    def __init__(self, model_dir: str = DEFAULT_COSYVOICE_MODEL_DIR, timeout: float = DEFAULT_COSYVOICE_TIMEOUT_SEC):
        import os
        self.model_dir = os.environ.get("COSYVOICE_MODEL_DIR") or model_dir
        self.timeout = float(timeout)

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        import concurrent.futures
        speaker = COSY_SPEAKER_BY_EDGE_VOICE.get(voice, "中文女")

        def _do_inference() -> None:
            import torch  # type: ignore
            import torchaudio  # type: ignore
            model = _cosy_model(self.model_dir)
            results = list(model.inference_sft(text, speaker))
            if not results:
                raise RuntimeError("CosyVoice inference_sft 未返回任何音频片段")
            speech = torch.cat([r["tts_speech"] for r in results], dim=1)
            sample_rate = getattr(model, "sample_rate", 22050)
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            torchaudio.save(str(out_wav), speech, sample_rate)

        # 推理是同步阻塞调用，用线程做超时控制（跨平台）；超时后不再等待，判定为失败
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_do_inference)
            try:
                fut.result(timeout=self.timeout)
            except concurrent.futures.TimeoutError as exc:
                raise RuntimeError(f"本地推理超时（>{self.timeout}s），可在项目配置中调整 cosyvoice_timeout_sec") from exc
        return mf.probe_duration(out_wav)


class UnavailableTTS(TTSProvider):
    """链上某个引擎无法构造（例如 edge-tts 未安装）：合成时直接报出原因，供降级链记录。"""

    def __init__(self, name: str, reason: str):
        self.name = name
        self.reason = reason

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        raise RuntimeError(self.reason)


class ChainTTS(TTSProvider):
    """按顺序尝试引擎：前一个失败就用下一个。全部失败时，错误信息里列出每一个引擎的原因。"""

    def __init__(self, engines: list):
        if not engines:
            raise ValueError("ChainTTS 至少需要一个引擎")
        self.engines = engines
        self.name = "+".join(e.name for e in engines)   # 链变化时缓存键随之变化
        self.last_engine = None
        self.last_errors: dict = {}

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        self.last_errors = {}
        for eng in self.engines:
            try:
                dur = cancel.call(eng.synth, text, out_wav, voice)   # 停止时不再等待引擎返回
                self.last_engine = eng.name
                return dur
            except Exception as exc:  # noqa: BLE001 - 任何失败都归为“不可用”，走下一个引擎
                self.last_errors[eng.name] = f"{type(exc).__name__}: {exc}"
        self.last_engine = None
        parts = [f"{n} 失败（{err}）" for n, err in self.last_errors.items()]
        raise RuntimeError("；".join(parts))


PROXY_SYSTEM = None       # 默认：沿用系统/环境变量里的代理设置
PROXY_DIRECT = "direct"   # 强制直连：忽略环境变量与系统代理


class _no_system_proxy:
    """在一次 edge-tts 调用期间，让 aiohttp 不再读取系统代理（含环境变量与 Windows 注册表）。
    aiohttp 通过 aiohttp.client.get_env_proxy_for_url 查找代理；这里临时替换它，结束后恢复。
    注意：这是进程内的临时替换，适合单线程的配音流程；并发调用时请勿同时使用其他依赖系统代理的请求。"""

    def __enter__(self):
        import aiohttp.client as _client

        def _direct(url):
            raise LookupError("强制直连：不使用系统代理")
        self._mod = _client
        self._orig = _client.get_env_proxy_for_url
        _client.get_env_proxy_for_url = _direct
        return self

    def __exit__(self, *exc):
        self._mod.get_env_proxy_for_url = self._orig
        return False


class EdgeTTS(TTSProvider):
    name = "edge-tts"

    def __init__(self, timeout: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC, proxy: str | None = PROXY_SYSTEM):
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("未安装 edge-tts：pip install edge-tts") from exc
        self.timeout = timeout
        # proxy: None = 系统代理；"direct" = 强制直连；其他字符串 = 指定代理地址（如 http://127.0.0.1:7890）
        self.proxy = proxy

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        import edge_tts
        mp3 = out_wav.with_suffix(".mp3")
        mp3.parent.mkdir(parents=True, exist_ok=True)

        direct = (self.proxy or "").strip().lower() == PROXY_DIRECT
        explicit = None if (not self.proxy or direct) else self.proxy

        async def _run():
            comm = edge_tts.Communicate(text, voice, proxy=explicit)
            await asyncio.wait_for(comm.save(str(mp3)), self.timeout)

        try:
            if direct:
                with _no_system_proxy():
                    asyncio.run(_run())
            else:
                asyncio.run(_run())
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"edge-tts 在线请求超时（>{self.timeout}s）：网络不通、被墙或服务端无响应。"
                               "可检查网络或代理设置后重试。") from exc
        except Exception as exc:  # noqa: BLE001 - 把服务端拒绝翻译成可操作的说明
            raise RuntimeError(edge_tts_error_hint(exc)) from exc
        mf.to_wav(mp3, out_wav)
        mp3.unlink(missing_ok=True)
        return mf.probe_duration(out_wav)
