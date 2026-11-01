"""ffmpeg / ffprobe 统一查找：与 novel-video-studio 的优先级一致，找不到时列出尝试过的每一种方式。"""
import sys
import types
from pathlib import Path

import pytest

from novel_to_video.media import binaries
from novel_to_video.media import ffmpeg as mf


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    binaries.reset_cache()
    for v in ("FFMPEG_BIN", "FFPROBE_BIN", "NOVEL_FFMPEG_PATH", "NOVEL_FFPROBE_PATH", "CONDA_PREFIX"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PATH", "")                       # 不让真实系统的 ffmpeg 干扰
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)   # 默认视为未安装
    yield
    binaries.reset_cache()


def _exe(p: Path) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake")
    p.chmod(0o755)                       # shutil.which 需要可执行位
    return str(p)


def test_project_env_var_wins(monkeypatch, tmp_path):
    f = _exe(tmp_path / "my" / "ffmpeg.exe")
    monkeypatch.setenv("FFMPEG_BIN", f)
    assert binaries.find_ffmpeg() == f


def test_skill_env_var_is_accepted(monkeypatch, tmp_path):
    f = _exe(tmp_path / "ffprobe.exe")
    monkeypatch.setenv("NOVEL_FFPROBE_PATH", f)
    assert binaries.find_ffprobe() == f


def test_imageio_ffmpeg_is_used_when_no_env(monkeypatch, tmp_path):
    f = _exe(tmp_path / "imgio" / "ffmpeg-win64.exe")
    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: f
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert binaries.find_ffmpeg() == f


def test_ffprobe_guessed_next_to_imageio_ffmpeg(monkeypatch, tmp_path):
    base = _exe(tmp_path / "imgio" / "ffmpeg-x.exe")
    probe = _exe(tmp_path / "imgio" / "ffprobe-x.exe")
    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: base
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert binaries.find_ffprobe() == probe


def test_conda_active_env_is_probed(monkeypatch, tmp_path):
    prefix = tmp_path / "conda_env"
    bin_dir = prefix / ("Library/bin" if sys.platform == "win32" else "bin")
    f = _exe(bin_dir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"))
    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    assert binaries.find_ffmpeg() == f


def test_system_path_is_last(monkeypatch, tmp_path):
    f = _exe(tmp_path / "bin" / "ffmpeg")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert binaries.find_ffmpeg() == f


def test_not_found_lists_every_attempt(monkeypatch):
    with pytest.raises(binaries.BinaryNotFound) as e:
        binaries.find_ffmpeg()
    msg = str(e.value)
    for needle in ("FFMPEG_BIN", "NOVEL_FFMPEG_PATH", "imageio_ffmpeg", "conda", "系统 PATH", "pip install imageio-ffmpeg"):
        assert needle in msg, (needle, msg)


def test_media_layer_raises_media_error_with_the_same_message():
    with pytest.raises(mf.MediaError) as e:
        mf.ffmpeg()
    assert "FFMPEG_BIN" in str(e.value)


def test_result_is_cached(monkeypatch, tmp_path):
    f = _exe(tmp_path / "ffmpeg")
    monkeypatch.setenv("FFMPEG_BIN", f)
    assert binaries.find_ffmpeg() == f
    monkeypatch.delenv("FFMPEG_BIN")
    assert binaries.find_ffmpeg() == f          # 已缓存


def test_failure_is_not_cached(monkeypatch, tmp_path):
    with pytest.raises(binaries.BinaryNotFound):
        binaries.find_ffmpeg()
    f = _exe(tmp_path / "ffmpeg")
    monkeypatch.setenv("FFMPEG_BIN", f)
    assert binaries.find_ffmpeg() == f          # 装好之后下一次就能用
