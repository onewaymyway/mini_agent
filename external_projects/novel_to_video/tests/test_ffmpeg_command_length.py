"""回归：章节拼接片段多时，ffmpeg 命令行不能超过 Windows 32767 字符上限（WinError 206）。

不真正运行 ffmpeg：替换 cancel.run_process，捕获命令行与滤镜脚本内容进行检查。
"""
import subprocess
from pathlib import Path

import pytest

from novel_to_video import cancel
from novel_to_video.media import ffmpeg as mf


def _long_clip_paths(root: Path, n: int):
    """模拟深层、中文路径下的大量片段。"""
    d = root / "很长的中文目录名" / "项目数据" / "第一章节合成素材" / "clips"
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = d / f"s{i:03d}.mp4"
        p.write_bytes(b"")
        paths.append(p)
    return paths


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def fake_run(args, cwd=None):
        script = None
        if "-filter_complex_script" in args:
            script = Path(args[args.index("-filter_complex_script") + 1])
            script_text = script.read_text(encoding="utf-8") if script.is_file() else ""
        else:
            script_text = ""
        calls.append({"args": list(args), "cwd": cwd, "script_text": script_text})
        return 0, "", ""

    monkeypatch.setattr(cancel, "run_process", fake_run)
    monkeypatch.setattr(mf, "ffmpeg", lambda: "ffmpeg")
    return calls


def _cmd_len(args):
    return len(subprocess.list2cmdline([str(a) for a in args]))


@pytest.mark.parametrize("n", [5, 60, 300])
def test_concat_fade_command_stays_short(tmp_path, capture, n):
    clips = _long_clip_paths(tmp_path, n)
    durations = [4.0] * n
    out = tmp_path / "out" / "chapter_001_nosub.mp4"
    mf.concat_fade(clips, durations, out, 0.5)
    call = capture[-1]
    assert _cmd_len(call["args"]) < 8000, _cmd_len(call["args"])
    assert "-filter_complex" not in call["args"]
    assert call["script_text"].count("[outa]") == 1
    assert f"concat=n={n}:v=1:a=1" in call["script_text"]
    # 输入使用相对路径，并以公共目录为工作目录
    assert call["cwd"] is not None
    inputs = [call["args"][i + 1] for i, a in enumerate(call["args"]) if a == "-i"]
    assert len(inputs) == n
    assert not any(Path(a).is_absolute() for a in inputs)


@pytest.mark.parametrize("n", [5, 300])
def test_concat_dissolve_command_stays_short(tmp_path, capture, n):
    clips = _long_clip_paths(tmp_path, n)
    durations = [4.0] * n
    out = tmp_path / "out" / "chapter_001_nosub.mp4"
    mf.concat_dissolve(clips, durations, out, 0.5)
    call = capture[-1]
    assert _cmd_len(call["args"]) < 8000
    assert "xfade" in call["script_text"]
    assert f"concat=n={n}:v=0:a=1[outa]" in call["script_text"]


def test_filter_script_is_removed_after_run(tmp_path, capture):
    clips = _long_clip_paths(tmp_path, 3)
    out = tmp_path / "out" / "x.mp4"
    mf.concat_fade(clips, [3.0, 3.0, 3.0], out, 0.3)
    script = Path(capture[-1]["args"][capture[-1]["args"].index("-filter_complex_script") + 1])
    assert not script.exists()


def test_launch_failure_reports_context(tmp_path, monkeypatch):
    """启动失败（如 WinError 206）时，错误信息应包含命令行长度与输出路径，而不是只剩“文件名太长”。"""
    def boom(args, cwd=None):
        err = OSError("[WinError 206] 文件名或扩展名太长。")
        err.winerror = 206
        raise err

    monkeypatch.setattr(cancel, "run_process", boom)
    monkeypatch.setattr(mf, "ffmpeg", lambda: "ffmpeg")
    with pytest.raises(mf.MediaError) as info:
        mf._run(["ffmpeg", "-y", "-i", "a.mp4", "C:/out/chapter.mp4"], cwd="C:/work")
    msg = str(info.value)
    assert "无法启动" in msg
    assert "chapter.mp4" in msg
    assert "C:/work" in msg
    assert "32767" in msg
