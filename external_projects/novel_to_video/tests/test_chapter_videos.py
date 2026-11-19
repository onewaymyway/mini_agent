"""「章节视频」页：全章完成情况（tool_api.chapter_videos）与连续播放（ui/video_page）。"""
import shutil
import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from ui import video_page as vp

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _mk_video(path: Path, seconds: int = 1) -> None:
    """生成真实的小视频（需要 ffmpeg）；没有 ffmpeg 时写入占位字节（时长读不到，页面仍应正常）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ff = shutil.which("ffmpeg")
    if ff:
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
    else:
        path.write_bytes(b"\x00" * 2048)


@pytest.fixture
def proj(tmp_path):
    src = build_export(tmp_path / "src", chapters=(1, 2, 3))
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="书")
    assert res["ok"], res
    return Path(res["data"]["project_dir"])


def _chapters(res):
    assert res["ok"], res
    return {c["no"]: c for c in res["data"]["chapters"]}


def test_lists_every_chapter_with_status_and_no_video(proj):
    cio.set_chapter_status(proj, 2, "failed", "镜头规划校验未通过：模拟")
    res = tool_api.chapter_videos(str(proj))
    chs = _chapters(res)
    assert sorted(chs) == [1, 2, 3]
    assert chs[2]["status"] == "failed" and "镜头规划校验未通过" in chs[2]["message"]
    assert all(c["video"] is None and c["duration_sec"] is None for c in chs.values())
    sm = res["data"]["summary"]
    assert sm["total"] == 3 and sm["done"] == 0 and sm["failed"] == 1 and sm["total_duration_sec"] == 0


def test_chapter_with_video_is_reported_and_stale_without_review(proj):
    """有成片文件但镜头没有全部审核通过（与当前素材不一致）→ 标为过期，不计入已完成。"""
    _mk_video(cio.chapter_dir(proj, 1) / "chapter_001.mp4", seconds=2)
    res = tool_api.chapter_videos(str(proj))
    c1 = _chapters(res)[1]
    assert c1["video"] == str(Path("chapters") / "001" / "chapter_001.mp4")
    assert c1["size_mb"] is not None and c1["stale"] is True
    assert res["data"]["summary"]["stale"] == 1 and res["data"]["summary"]["done"] == 0
    if shutil.which("ffprobe"):
        assert 1.5 <= c1["duration_sec"] <= 2.6


def test_up_to_date_chapter_counts_as_done(proj, monkeypatch):
    from novel_to_video.steps import chapter_compose as cc
    _mk_video(cio.chapter_dir(proj, 1) / "chapter_001.mp4")
    _mk_video(cio.chapter_dir(proj, 3) / "chapter_003.mp4")
    monkeypatch.setattr(cc, "is_up_to_date", lambda p, no: no == 1)
    res = tool_api.chapter_videos(str(proj))
    chs, sm = _chapters(res), res["data"]["summary"]
    assert chs[1]["stale"] is False and chs[3]["stale"] is True
    assert sm["done"] == 1 and sm["stale"] == 1


def test_missing_project_returns_error(tmp_path):
    res = tool_api.chapter_videos(str(tmp_path / "nope"))
    assert res["ok"] is False and "读取项目失败" in res["error"]["message"]


def test_playlist_order_and_stale_filter():
    items = [{"no": 3, "video": "c3", "stale": False}, {"no": 1, "video": "c1", "stale": False},
             {"no": 2, "video": "c2", "stale": True}, {"no": 4, "video": None, "stale": False}]
    assert [i["no"] for i in vp.playlist_of(items)] == [1, 3]
    assert [i["no"] for i in vp.playlist_of(items, include_stale=True)] == [1, 2, 3]


def test_labels_and_duration_format():
    assert vp.fmt_duration(None) == "—" and vp.fmt_duration(65) == "1:05" and vp.fmt_duration(3725) == "1:02:05"
    assert vp.chapter_state_label({"video": "x", "stale": False, "status": "done"}) == "✅ 已完成"
    assert vp.chapter_state_label({"video": "x", "stale": True, "status": "done"}) == "🟡 成片已过期"
    assert vp.chapter_state_label({"video": None, "stale": False, "status": "failed"}) == "❌ 失败"


def test_autoplay_script_wires_ended_to_next_button():
    on, off = vp.build_autoplay_script(True), vp.build_autoplay_script(False)
    assert "var AUTO = true;" in on and "var AUTO = false;" in off
    assert "v.onended" in on and f".st-key-{vp.NEXT_BUTTON_KEY}" in on and "下一章" in on


def _open_video_page(out_root: Path) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("🧭 驾驶舱").run()
    at.text_input(key="ck_root").input(str(out_root)).run()
    at.radio(key="ck_sub").set_value("📺 章节视频").run()
    return at


def test_page_renders_overview_without_playable_videos(proj):
    at = _open_video_page(proj.parent)
    assert not at.exception
    assert any("章节视频" in h.value for h in at.header)
    assert len(at.dataframe) == 1
    assert any("还没有可播放的章节视频" in i.value for i in at.info)


def test_next_prev_buttons_walk_the_playlist(proj, monkeypatch):
    from novel_to_video.steps import chapter_compose as cc
    for n in (1, 2, 3):
        _mk_video(cio.chapter_dir(proj, n) / f"chapter_{n:03d}.mp4")
    monkeypatch.setattr(cc, "is_up_to_date", lambda p, no: True)
    at = _open_video_page(proj.parent)
    assert not at.exception
    cur_key = f"vid_cur:{proj}"
    assert at.session_state[cur_key] == 1
    at.button(key="vid_next").click().run()
    assert at.session_state[cur_key] == 2
    at.button(key="vid_next").click().run()
    assert at.session_state[cur_key] == 3
    assert at.button(key="vid_next").disabled                       # 最后一章：下一章不可点
    at.button(key="vid_prev").click().run()
    assert at.session_state[cur_key] == 2
    assert not at.exception
