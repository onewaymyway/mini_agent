"""R13：配音是否使用系统代理（默认不使用）。"""
import pytest
from article_to_video.audio.config import normalize_tts
from article_to_video.providers import tts as T

pytest.importorskip("edge_tts")


def _direct(e):
    return ((e.proxy or "").strip().lower() == T.PROXY_DIRECT) or (not e.proxy and not e.use_system_proxy)


def test_default_off():
    cfg, _ = normalize_tts({})
    assert cfg["use_system_proxy"] is False
    assert _direct(T.EdgeTTS())


def test_opt_in_system_proxy():
    cfg, _ = normalize_tts({"use_system_proxy": True})
    assert cfg["use_system_proxy"] is True
    assert not _direct(T.EdgeTTS(use_system_proxy=True))


def test_explicit_proxy_wins():
    e = T.EdgeTTS(proxy="http://127.0.0.1:7890")
    assert not _direct(e)


def test_build_chain_passes_flag():
    chain = T.build_chain(["edge-tts"], use_system_proxy=True)
    assert chain.engines[0].use_system_proxy is True
