"""providers/tts.py — 配音 provider（P3）。

  * SilenceTTS：只用于流程验证的静音（stub），不进入默认降级链；结果会标记 engine=stub。
  * CosyVoiceTTS：本地 CosyVoice（需 cosyvoice 环境与模型权重）。
  * EdgeTTS：edge-tts 在线语音（需 pip install edge-tts 且网络可达）。支持每句语速。
  * ChainTTS：按顺序尝试引擎，前一个失败用下一个；全部失败抛错，不悄悄降级为静音。

与 novel_to_video 的 providers/tts.py 同一套降级与错误说明；本项目的差异：每句可设语速（rate），
且 ChainTTS 的等待经 cancel.call，停止时立即返回。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .. import cancel
from ..media import ffmpeg as mf

DEFAULT_COSYVOICE_MODEL_DIR = "pretrained_models/CosyVoice-300M"
DEFAULT_COSYVOICE_TIMEOUT_SEC = 60.0
DEFAULT_EDGE_TTS_TIMEOUT_SEC = 45.0
NO_AUDIO_LOCAL_RETRIES = 2
PROXY_DIRECT = "direct"

COSY_SPEAKER_BY_EDGE_VOICE = {
    "zh-CN-YunjianNeural": "中文男",
    "zh-CN-YunxiNeural": "中文男",
    "zh-CN-XiaoxiaoNeural": "中文女",
    "zh-CN-XiaoyiNeural": "中文女",
}

_INVISIBLE_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\ufeff]")
_SPEAKABLE_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]")


def sanitize_tts_text(text: str) -> str:
    return re.sub(r"\s+", " ", _INVISIBLE_RE.sub("", text or "")).strip()


def has_speakable(text: str) -> bool:
    return bool(_SPEAKABLE_RE.search(text or ""))


def speakable_text(text: str) -> str:
    """清理后的可朗读文本；没有汉字/字母/数字时返回空字符串（空白、纯标点、不可见字符都算空）。"""
    s = sanitize_tts_text(text)
    return s if has_speakable(s) else ""


def describe_input(text: str, limit: int = 120) -> str:
    """用于日志与错误信息的输入文本摘要：压缩空白、超长截断，并标明字数。"""
    raw = "" if text is None else str(text)
    shown = re.sub(r"\s+", " ", raw).strip()
    if not shown:
        return "（空字符串）"
    if len(shown) > limit:
        shown = shown[:limit] + "…"
    return f"「{shown}」（原文 {len(raw)} 字）"


def edge_tts_version() -> str:
    try:
        import importlib.metadata as md
        return md.version("edge-tts")
    except Exception:  # noqa: BLE001
        return "未知"


def edge_tts_error_hint(exc: Exception) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    text = str(exc)
    ver = edge_tts_version()
    if "403" in text or "Invalid response status" in text:
        return (f"edge-tts 服务拒绝连接（403，版本 {ver}）。排查：①pip install -U edge-tts；"
                "②在设置里确认「使用系统代理」处于关闭（默认关闭，即直连）；③若必须走代理，填写 edge-tts 代理地址或打开「使用系统代理」；"
                f"④仍无效时服务端可能已拒绝该客户端，改用其它引擎。原始错误：{raw}")
    if "Name or service not known" in text or "ClientConnector" in text:
        return f"无法连接 edge-tts 服务（网络或 DNS 不通，版本 {ver}）。原始错误：{raw}"
    if "NoAudioReceived" in raw or "No audio was received" in text:
        return (f"edge-tts 没有返回音频（版本 {ver}）。常见原因：文本没有可朗读字符、含服务端不接受的字符，"
                f"或服务端瞬时异常。原始错误：{raw}")
    return f"edge-tts 配音失败（版本 {ver}）：{raw}"


class TTSProvider(ABC):
    name = "base"

    @abstractmethod
    def synth(self, text: str, out_wav: Path, voice: str, rate: str = "+0%") -> float: ...


class SilenceTTS(TTSProvider):
    """仅用于流程验证：按字数估算时长写静音。不在默认降级链中。"""
    name = "stub"

    def synth(self, text: str, out_wav: Path, voice: str, rate: str = "+0%") -> float:
        secs = max(0.6, len(re.sub(r"\s", "", text)) / 4.2)
        return mf.write_silence_wav(out_wav, secs)


class CosyVoiceTTS(TTSProvider):
    name = "cosyvoice"
    _MODELS: dict = {}

    def __init__(self, model_dir: str = DEFAULT_COSYVOICE_MODEL_DIR,
                 timeout: float = DEFAULT_COSYVOICE_TIMEOUT_SEC):
        self.model_dir = os.environ.get("COSYVOICE_MODEL_DIR") or model_dir
        self.timeout = float(timeout)

    def _model(self):
        if self.model_dir not in self._MODELS:
            from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore
            self._MODELS[self.model_dir] = CosyVoice(self.model_dir)
        return self._MODELS[self.model_dir]

    def synth(self, text: str, out_wav: Path, voice: str, rate: str = "+0%") -> float:
        speaker = COSY_SPEAKER_BY_EDGE_VOICE.get(voice, "中文女")

        def _do() -> None:
            import torch  # type: ignore
            import torchaudio  # type: ignore
            model = self._model()
            results = list(model.inference_sft(text, speaker))
            if not results:
                raise RuntimeError("CosyVoice inference_sft 未返回任何音频片段")
            speech = torch.cat([r["tts_speech"] for r in results], dim=1)
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            torchaudio.save(str(out_wav), speech, getattr(model, "sample_rate", 22050))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_do)
            try:
                fut.result(timeout=self.timeout)
            except concurrent.futures.TimeoutError as exc:
                raise RuntimeError(f"本地推理超时（>{self.timeout}s）") from exc
        return mf.probe_duration(out_wav)


class _no_system_proxy:
    """一次 edge-tts 调用期间不读取系统代理（进程内临时替换，适合单线程配音）。"""

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

    def __init__(self, timeout: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC, proxy: "str | None" = None,
                 use_system_proxy: bool = False):
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("未安装 edge-tts：pip install edge-tts") from exc
        self.timeout = timeout
        self.proxy = proxy  # 代理地址；"direct"=直连；None=按 use_system_proxy 决定
        self.use_system_proxy = bool(use_system_proxy)  # 默认 False：不读取系统代理

    def synth(self, text: str, out_wav: Path, voice: str, rate: str = "+0%") -> float:
        import edge_tts
        mp3 = out_wav.with_suffix(".mp3")
        mp3.parent.mkdir(parents=True, exist_ok=True)
        direct = ((self.proxy or "").strip().lower() == PROXY_DIRECT
                  or (not self.proxy and not self.use_system_proxy))
        explicit = None if (not self.proxy or direct) else self.proxy
        sent = sanitize_tts_text(text)
        if not has_speakable(sent):
            raise RuntimeError("edge-tts 未发送请求：文本里没有可朗读的字符（汉字/字母/数字）")

        async def _run():
            comm = edge_tts.Communicate(sent, voice, rate=rate, proxy=explicit)
            await asyncio.wait_for(comm.save(str(mp3)), self.timeout)

        last: "Exception | None" = None
        for attempt in range(1 + NO_AUDIO_LOCAL_RETRIES):
            try:
                if direct:
                    with _no_system_proxy():
                        asyncio.run(_run())
                else:
                    asyncio.run(_run())
                last = None
                break
            except asyncio.TimeoutError as exc:
                raise RuntimeError(f"edge-tts 在线请求超时（>{self.timeout}s）") from exc
            except Exception as exc:  # noqa: BLE001
                last = exc
                if type(exc).__name__ == "NoAudioReceived" and attempt < NO_AUDIO_LOCAL_RETRIES:
                    time.sleep(1.0 + attempt)
                    continue
                raise RuntimeError(edge_tts_error_hint(exc)) from exc
        if last is not None:
            raise RuntimeError(edge_tts_error_hint(last))
        mf.to_wav(mp3, out_wav)
        mp3.unlink(missing_ok=True)
        return mf.probe_duration(out_wav)


class UnavailableTTS(TTSProvider):
    def __init__(self, name: str, reason: str):
        self.name = name
        self.reason = reason

    def synth(self, text: str, out_wav: Path, voice: str, rate: str = "+0%") -> float:
        raise RuntimeError(self.reason)


class ChainTTS(TTSProvider):
    """按顺序尝试引擎；全部失败抛 RuntimeError，列出每个引擎的原因（不降级为静音）。"""

    def __init__(self, engines: list):
        if not engines:
            raise ValueError("ChainTTS 至少需要一个引擎")
        self.engines = engines
        self.name = "+".join(e.name for e in engines)
        self.last_engine = None
        self.last_errors: dict = {}

    def synth(self, text: str, out_wav: Path, voice: str, rate: str = "+0%") -> float:
        self.last_errors = {}
        for eng in self.engines:
            try:
                dur = cancel.call(eng.synth, text, out_wav, voice, rate)
                self.last_engine = eng.name
                return dur
            except cancel.Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — 任何失败归为不可用，走下一个
                self.last_errors[eng.name] = f"{type(exc).__name__}: {exc}"
        self.last_engine = None
        raise RuntimeError("；".join(f"{n} 失败（{e}）" for n, e in self.last_errors.items()))


def build_chain(names, *, edge_proxy=None, use_system_proxy=False) -> ChainTTS:
    """按名称构造降级链。无法构造的引擎包成 UnavailableTTS，合成时才报出原因。"""
    engines = []
    for n in names:
        if n == "cosyvoice":
            try:
                engines.append(CosyVoiceTTS())
            except Exception as exc:  # noqa: BLE001
                engines.append(UnavailableTTS("cosyvoice", str(exc)))
        elif n == "edge-tts":
            try:
                engines.append(EdgeTTS(proxy=edge_proxy, use_system_proxy=use_system_proxy))
            except Exception as exc:  # noqa: BLE001
                engines.append(UnavailableTTS("edge-tts", str(exc)))
        else:
            raise ValueError(f"未知配音引擎：{n}")
    return ChainTTS(engines)
