"""R12：ffmpeg/ffprobe 自动查找（环境变量 → imageio_ffmpeg → conda → PATH），找不到时列出尝试过的方式。"""

import pytest

from article_to_video.media import binaries, ffmpeg as mf


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    binaries.reset_cache()
    for v in ("FFMPEG_BIN", "FFPROBE_BIN", "NOVEL_FFMPEG_PATH", "NOVEL_FFPROBE_PATH", "CONDA_PREFIX"):
        monkeypatch.delenv(v, raising=False)
    yield
    binaries.reset_cache()


def _isolate(monkeypatch):
    """屏蔽 imageio_ffmpeg 与 PATH、conda，只测指定来源。"""
    import sys
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    monkeypatch.setattr(binaries.shutil, "which", lambda *_a, **_k: None)


def test_env_var_wins(tmp_path, monkeypatch):
    exe = tmp_path / "myffmpeg.exe"
    exe.write_text("x")
    monkeypatch.setenv("FFMPEG_BIN", str(exe))
    assert mf.find_ffmpeg() == str(exe)


def test_skill_env_var_also_accepted(tmp_path, monkeypatch):
    exe = tmp_path / "ffprobe"
    exe.write_text("x")
    monkeypatch.setenv("NOVEL_FFPROBE_PATH", str(exe))
    assert mf.find_ffprobe() == str(exe)


def test_conda_env_autodetect(tmp_path, monkeypatch):
    _isolate(monkeypatch)
    bindir = binaries._conda_bin_dir(str(tmp_path))
    bindir.mkdir(parents=True)
    name = "ffmpeg.exe" if binaries.os.name == "nt" else "ffmpeg"
    (bindir / name).write_text("x")
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path))
    assert mf.find_ffmpeg() == str(bindir / name)


def test_not_found_is_media_error_listing_attempts(monkeypatch):
    _isolate(monkeypatch)
    with pytest.raises(mf.MediaError) as e:
        mf.find_ffmpeg()
    msg = str(e.value)
    assert "未找到 ffmpeg" in msg and "FFMPEG_BIN" in msg and "系统 PATH" in msg


def test_not_cached_when_missing(tmp_path, monkeypatch):
    _isolate(monkeypatch)
    with pytest.raises(mf.MediaError):
        mf.find_ffmpeg()
    exe = tmp_path / "ffmpeg"
    exe.write_text("x")
    monkeypatch.setenv("FFMPEG_BIN", str(exe))
    assert mf.find_ffmpeg() == str(exe)
