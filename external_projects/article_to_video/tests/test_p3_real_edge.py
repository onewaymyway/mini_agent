"""真实 edge-tts 冒烟：默认跳过。设置环境变量 A2V_REAL_TTS=1 后运行（需要网络）。"""
import os

import pytest

from article_to_video.providers.tts import EdgeTTS

pytestmark = pytest.mark.skipif(os.environ.get("A2V_REAL_TTS") != "1", reason="需要网络：设置 A2V_REAL_TTS=1")


def test_edge_tts_real_synthesis(tmp_path):
    out = tmp_path / "hello.wav"
    dur = EdgeTTS(timeout=30).synth("这是一句真实配音的冒烟测试。", out, "zh-CN-YunjianNeural", "+0%")
    assert out.is_file() and dur > 0.5
