"""动态漫模式 S5：平移（左/右/上/下）与晃动（drift）、动效强度对幅度的影响。

golden 测试锁定滤镜字符串；另用数值求值检查每一帧的取景位置是否在画面余量之内、起止是否正确。
"""
import math
import re
from pathlib import Path

import pytest
from PIL import Image

from novel_to_video.media import ffmpeg as mf
from novel_to_video.media import motion as MO

W, H, FPS = 720, 1280, 24
FRAMES = 48
IW, IH = W * MO.UPSCALE, H * MO.UPSCALE          # 放大后的输入尺寸


def _expr(filter_str: str, axis: str) -> str:
    m = re.search(rf"{axis}='([^']*)'", filter_str)
    assert m, filter_str
    return m.group(1)


def _eval(expr: str, *, zoom: float, on: int) -> float:
    e = expr.replace("sin(", "math.sin(").replace("cos(", "math.cos(")
    return float(eval(e, {"math": math}, {"iw": IW, "ih": IH, "zoom": zoom, "on": on}))


def _track(filter_str: str, strength: str = "slow"):
    zmax = 1 + MO.STRENGTHS[strength]
    xs = [_eval(_expr(filter_str, "x"), zoom=zmax, on=k) for k in range(FRAMES)]
    ys = [_eval(_expr(filter_str, "y"), zoom=zmax, on=k) for k in range(FRAMES)]
    slack_x = IW - IW / zmax
    slack_y = IH - IH / zmax
    return xs, ys, slack_x, slack_y


# ---------- 选择与规则

def test_new_motion_names_are_accepted_and_pass_through():
    for name in ("pan_left", "pan_right", "pan_up", "pan_down", "drift"):
        assert MO.resolve_motion(name, 3) == name


def test_auto_alternate_still_only_uses_push_and_pull():
    assert {MO.resolve_motion("auto_alternate", i) for i in range(10)} == {"push_in", "pull_out"}


# ---------- golden：滤镜字符串

def test_golden_pan_left_slow():
    assert MO.zoom_filter("pan_left", frames=FRAMES, w=W, h=H, fps=FPS, strength="slow") == (
        "scale=1440:2560:force_original_aspect_ratio=increase,crop=1440:2560,"
        "zoompan=z='1.0800':x='(iw-iw/zoom)*(1-on/47)':y='ih/2-(ih/zoom/2)':"
        "d=1:s=720x1280:fps=24,setsar=1")


def test_golden_pan_right_slow():
    assert "x='(iw-iw/zoom)*on/47':y='ih/2-(ih/zoom/2)'" in MO.zoom_filter(
        "pan_right", frames=FRAMES, w=W, h=H, fps=FPS)


def test_golden_pan_up_and_down_slow():
    up = MO.zoom_filter("pan_up", frames=FRAMES, w=W, h=H, fps=FPS)
    down = MO.zoom_filter("pan_down", frames=FRAMES, w=W, h=H, fps=FPS)
    assert "x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-on/47)'" in up
    assert "x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*on/47'" in down


def test_golden_drift_slow():
    assert MO.zoom_filter("drift", frames=FRAMES, w=W, h=H, fps=FPS) == (
        "scale=1440:2560:force_original_aspect_ratio=increase,crop=1440:2560,"
        "zoompan=z='1.0800':x='(iw-iw/zoom)/2*(1+0.6*sin(on*0.12))':"
        "y='(ih-ih/zoom)/2*(1+0.6*cos(on*0.09))':d=1:s=720x1280:fps=24,setsar=1")


def test_golden_medium_strength_changes_zoom_and_slack():
    med = MO.zoom_filter("pan_right", frames=FRAMES, w=W, h=H, fps=FPS, strength="medium")
    assert "z='1.1500'" in med


# ---------- 数值检查：位置在余量之内，起止正确

@pytest.mark.parametrize("motion,start,end", [
    ("pan_left", "x_max", "x_min"),
    ("pan_right", "x_min", "x_max"),
    ("pan_up", "y_max", "y_min"),
    ("pan_down", "y_min", "y_max"),
])
def test_pan_moves_across_full_slack_in_the_right_direction(motion, start, end):
    xs, ys, sx, sy = _track(MO.zoom_filter(motion, frames=FRAMES, w=W, h=H, fps=FPS))
    track, slack = (xs, sx) if motion.startswith("pan_l") or motion.startswith("pan_r") else (ys, sy)
    assert all(-1e-6 <= v <= slack + 1e-6 for v in track)
    first, last = track[0], track[-1]
    if start.endswith("_max"):
        assert first == pytest.approx(slack, abs=1e-6) and last == pytest.approx(0, abs=1e-6)
    else:
        assert first == pytest.approx(0, abs=1e-6) and last == pytest.approx(slack, abs=1e-6)
    # 单调：每一帧都朝同一方向移动
    diffs = [b - a for a, b in zip(track, track[1:])]
    assert all(d <= 1e-9 for d in diffs) or all(d >= -1e-9 for d in diffs)


def test_pan_keeps_the_other_axis_centred():
    xs, ys, sx, sy = _track(MO.zoom_filter("pan_left", frames=FRAMES, w=W, h=H, fps=FPS))
    assert all(v == pytest.approx(sy / 2, abs=1e-6) for v in ys)


def test_drift_stays_inside_the_slack_and_actually_moves():
    xs, ys, sx, sy = _track(MO.zoom_filter("drift", frames=FRAMES, w=W, h=H, fps=FPS))
    assert all(0 <= v <= sx for v in xs) and all(0 <= v <= sy for v in ys)
    assert max(xs) - min(xs) > 0.2 * sx                  # 确实在晃动
    assert max(ys) - min(ys) > 0.2 * sy


def test_medium_strength_gives_larger_pan_range():
    _, _, sx_slow, _ = _track(MO.zoom_filter("pan_right", frames=FRAMES, w=W, h=H, fps=FPS, strength="slow"))
    _, _, sx_med, _ = _track(MO.zoom_filter("pan_right", frames=FRAMES, w=W, h=H, fps=FPS, strength="medium"),
                             strength="medium")
    assert sx_med > sx_slow * 1.5


def test_unknown_motion_and_strength_still_rejected():
    with pytest.raises(ValueError):
        MO.zoom_filter("spiral", frames=10, w=W, h=H, fps=FPS)
    with pytest.raises(ValueError):
        MO.zoom_filter("drift", frames=10, w=W, h=H, fps=FPS, strength="wild")


# ---------- 真实 ffmpeg 渲染

def _png(path: Path):
    Image.new("RGB", (360, 640), (90, 120, 160)).save(path, format="PNG")
    return path


@pytest.mark.parametrize("motion", ["pan_left", "pan_up", "drift"])
def test_real_render_of_new_motions(tmp_path, motion):
    out = tmp_path / f"{motion}.mp4"
    MO.render_segment(_png(tmp_path / "s.png"), None, out, seconds=2.0, motion=motion,
                      strength="slow", w=W, h=H, fps=FPS)
    assert out.is_file()
    assert abs(mf.probe_duration(out) - 2.0) < 0.15
