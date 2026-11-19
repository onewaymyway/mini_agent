"""R21：封面不淡入——片头卡、章封面、片尾卡从第一帧直接显示；只保留它们结尾的淡出。"""
import json

from article_to_video.compose import export as ex
from article_to_video.compose import post


def _capture(monkeypatch):
    calls = []

    def fake_ffmpeg(args):
        calls.append(list(args))

    monkeypatch.setattr(post, "ffmpeg", fake_ffmpeg)
    return calls


def _vf(call):
    return call[call.index("-vf") + 1]


def _af(call):
    return call[call.index("-af") + 1]


def test_cover_segments_do_not_fade_in_but_still_fade_out(tmp_path, monkeypatch):
    calls = _capture(monkeypatch)
    seq = [(tmp_path / "intro.mp4", 4.0), (tmp_path / "p1.mp4", 20.0), (tmp_path / "p2.mp4", 15.0)]
    post.join(seq, "fade", 0.5, tmp_path / "out.mp4", tmp_path / "concat.txt", [], holds=[4.0, 3.5, 0.0])
    per_seg = [c for c in calls if "-vf" in c]
    assert len(per_seg) == 3
    # 片头卡与第一章封面：没有淡入（第一帧就是封面），只有淡出
    for i in (0, 1):
        assert "fade=t=in" not in _vf(per_seg[i]) and "fade=t=out" in _vf(per_seg[i])
        assert "afade=t=in" not in _af(per_seg[i])
    # 第二章没有封面开头（hold=0）：保持原来的淡入淡出
    assert "fade=t=in" in _vf(per_seg[2]) and "fade=t=out" in _vf(per_seg[2])


def test_without_holds_behaviour_is_unchanged(tmp_path, monkeypatch):
    calls = _capture(monkeypatch)
    post.join([(tmp_path / "a.mp4", 10.0), (tmp_path / "b.mp4", 10.0)], "fade", 0.5,
              tmp_path / "o.mp4", tmp_path / "c.txt", [])
    per_seg = [c for c in calls if "-vf" in c]
    assert all("fade=t=in" in _vf(c) for c in per_seg)


def test_cut_mode_is_untouched_by_holds(tmp_path, monkeypatch):
    calls = _capture(monkeypatch)
    post.join([(tmp_path / "a.mp4", 5.0), (tmp_path / "b.mp4", 5.0)], "cut", 0.0,
              tmp_path / "o.mp4", tmp_path / "c.txt", [], holds=[3.0, 3.0])
    assert all("-vf" not in c for c in calls)


def test_chapter_hold_comes_from_the_timeline(tmp_path):
    part = tmp_path / "parts" / "P01"
    part.mkdir(parents=True)
    (part / "timeline.json").write_text(json.dumps({"lead_in_sec": 3.5, "total_sec": 60}), encoding="utf-8")
    assert ex._cover_hold(tmp_path, "P01") == 3.5
    assert ex._cover_hold(tmp_path, "P02") == 0.0      # 没有时间轴：不当作封面
