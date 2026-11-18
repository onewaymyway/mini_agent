"""分镜预览：只有一个状态的场景不能再创建 min==max 的滑块（渲染成功后页面报错）。"""
import base64
import json

from conftest import PROJECT_DIR
from article_to_video.visual import storyboard as sbm
from helpers_p4 import scripted_project
from helpers_ui_nav import go_step


_PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _write_manifest(pdir, pid, scenes):
    d = pdir / "parts" / pid / "visuals"
    d.mkdir(parents=True, exist_ok=True)
    for sc in scenes:                                   # 页面会读取每个状态的图片文件
        for f in sc["frames"]:
            (pdir / "parts" / pid / f["file"]).write_bytes(_PNG_1PX)
        (pdir / "parts" / pid / sc["background"]).write_bytes(_PNG_1PX)
    (d / "manifest.json").write_text(json.dumps({"counts": {}, "canvas": "1080x1920", "scenes": scenes,
                                                 "warnings": []}), encoding="utf-8")


def test_single_frame_scene_renders_without_slider_error(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    sbm.plan_storyboard(pdir, "P01")          # 页面只有存在分镜时才显示预览
    frame = {"file": "visuals/x.png", "start": 0.0}
    _write_manifest(pdir, "P01", [{"id": "P01.b01", "frames": [frame], "background": "visuals/x.png"}])
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🎞️ 分镜")
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "画面预览" for h in at.subheader)     # 确实走到了预览
    assert not any(sl.label == "状态" for sl in at.slider)


def test_multi_frame_scene_keeps_slider_per_scene(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = scripted_project(tmp_path)
    sbm.plan_storyboard(pdir, "P01")
    frames = [{"file": f"visuals/{i}.png", "start": float(i)} for i in range(3)]
    _write_manifest(pdir, "P01", [{"id": "P01.b01", "frames": frames, "background": "visuals/0.png"},
                                  {"id": "P01.b02", "frames": frames[:1], "background": "visuals/0.png"}])
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🎞️ 分镜")
    assert not at.exception, [e.value for e in at.exception]
    sl = next(s for s in at.slider if s.label == "状态")
    assert sl.min == 0 and sl.max == 2
