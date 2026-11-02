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


def test_requested_seconds_rounds_up_and_stays_in_api_range():
    assert cio.requested_video_seconds(2.3) == 4      # 接口最小 4 秒
    assert cio.requested_video_seconds(7.1) == 8
    assert cio.requested_video_seconds(11.2) == 12
    assert cio.requested_video_seconds(20.0) == 12    # 超过接口上限时取上限，画面会在合成时缩放


def test_fit_clip_stretches_video_to_narration(tmp_path):
    """画面按配音真实时长快放/慢放，输出时长 ≈ 配音时长（音频保持原样）。"""
    from novel_to_video.media import ffmpeg as mf
    raw = tmp_path / "raw.mp4"
    # 生成一段 4 秒的画面与一段 6 秒的配音
    _make_still(tmp_path / "ref.png")
    mf.still_clip(tmp_path / "ref.png", raw, 4.0)
    wav = tmp_path / "speech.wav"
    mf.write_silence_wav(wav, 6.0)
    out = tmp_path / "fit.mp4"
    scale = mf.fit_clip(raw, wav, out)
    assert abs(scale - 1.5) < 0.05                        # 4s -> 6s：慢放
    assert abs(mf.probe_duration(out) - 6.0) < 0.2


def _make_still(path):
    import subprocess
    from novel_to_video.media import ffmpeg as mf
    subprocess.run([mf.ffmpeg(), "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x480", "-frames:v", "1", str(path)],
                   check=True, capture_output=True)
