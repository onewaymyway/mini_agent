"""视频接口时长范围 [4, 12]：超出时在发请求之前拦住；镜头与配音上限与之一致。"""
from pathlib import Path

import pytest

from novel_to_video import chapter_io as cio
from novel_to_video.providers import video as vp
from novel_to_video.steps import chapter_audio as ca


@pytest.mark.parametrize("sec", [3, 13, 15])
def test_out_of_range_seconds_never_reach_the_api(sec, tmp_path, monkeypatch):
    called = {"n": 0}

    class _Client:
        def post(self, *a, **k):
            called["n"] += 1
            raise AssertionError("不应发送请求")
    prov = vp.AgnesVideoProvider.__new__(vp.AgnesVideoProvider)  # 只测前置校验，不触网
    prov.__dict__.update(model="m", timeout=5, session=_Client())
    res = prov.generate("x", tmp_path / "a.mp4", seconds=sec, reference_images=[tmp_path / "r.png"])
    assert not res.ok and res.non_retryable
    assert "[4, 12]" in res.error
    assert called["n"] == 0


@pytest.mark.parametrize("sec", [4, 12])
def test_boundary_seconds_are_accepted_by_the_check(sec):
    assert vp._seconds_error(sec) == ""


def test_shot_and_speech_limits_match_the_api():
    assert cio.SHOT_MIN_SEC == 4 and cio.SHOT_MAX_SEC == 12
    assert ca.MAX_SPEECH_SEC <= cio.SHOT_MAX_SEC      # 配音时长不会超过镜头上限
