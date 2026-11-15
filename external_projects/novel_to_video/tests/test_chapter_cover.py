"""章节封面：默认关闭（与以前一致）；开启后每章专门生成背景图，程序叠字，作为章节视频开头静帧。"""
import json
from pathlib import Path

import pytest
from PIL import Image

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import project_store as ps
from novel_to_video import tool_api
from novel_to_video.media import ffmpeg as mf
from novel_to_video.media.cover import CoverError, render_chapter_cover
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.steps import chapter_cover as CC
from test_g6_full_auto import FakeLLM, _run_one, raw_proj  # noqa: F401  复用夹具与假 LLM

BUDGET = {"max_video_calls": 99, "max_image_calls": 99, "max_minutes": 99}


class CountingImage(StubImageProvider):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def generate(self, prompt, save_path, **kw):
        self.prompts.append(prompt)
        return super().generate(prompt, save_path, **kw)


def _prep_assets(proj):
    from novel_to_video import auto_prepare as ap
    from novel_to_video import batch
    tool_api.set_gear(str(proj), "G6", BUDGET)
    prepare = lambda no: ap.prepare_chapter_assets(proj, no, FakeLLM(), StubImageProvider())
    return batch, prepare


def test_default_is_off_and_old_projects_unaffected(raw_proj):
    assert CC.chapter_cover_settings(raw_proj)["enabled"] is False
    proj = ps.read_json(raw_proj / "project.json")
    proj.pop("chapter_cover", None)                                  # 旧项目没有该键
    ps.write_json_atomic(raw_proj / "project.json", proj)
    assert CC.chapter_cover_settings(raw_proj)["enabled"] is False
    assert CC.ensure_chapter_cover(raw_proj, 1, StubImageProvider()) is None
    assert not (raw_proj / "chapters" / "001" / "cover_bg.png").exists()


def test_render_text_is_drawn_by_program(tmp_path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (400, 700), (10, 80, 120)).save(bg)
    out = render_chapter_cover(bg, tmp_path / "c.png", "铁城纪事", 3, "清晨的铁城", "center")
    img = Image.open(out)
    assert img.size == (mf.W, mf.H)
    assert img.convert("L").getextrema()[1] == 255                    # 有白色文字
    with pytest.raises(CoverError):
        render_chapter_cover(bg, tmp_path / "x.png", "书", 1, "章", "nope")


def test_set_chapter_cover_validates(raw_proj):
    assert not tool_api.set_chapter_cover(str(raw_proj), True, layout="x")["ok"]
    assert not tool_api.set_chapter_cover(str(raw_proj), True, duration_sec=20)["ok"]
    r = tool_api.set_chapter_cover(str(raw_proj), True, book_title="  我的书 ", layout="bottom_bar", duration_sec=2)
    assert r["ok"] and r["data"]["book_title"] == "我的书"
    cfg = CC.chapter_cover_settings(raw_proj)
    assert cfg["enabled"] and cfg["layout"] == "bottom_bar" and cfg["duration_sec"] == 2.0


def test_cover_prepended_to_chapter_video_and_bg_reused(raw_proj):
    batch, prepare = _prep_assets(raw_proj)
    # 先不开封面，拿到基准时长
    base = batch.run_queue(raw_proj, [1], gear="G6", run_one=_run_one(raw_proj), prepare=prepare)
    assert base["results"][1]["status"] == "done"
    base_dur = mf.probe_duration(raw_proj / "chapters" / "001" / "chapter_001.mp4")
    assert not (raw_proj / "chapters" / "001" / "cover.png").exists()

    assert tool_api.set_chapter_cover(str(raw_proj), True, book_title="铁城纪事", duration_sec=2)["ok"]
    from novel_to_video.steps.chapter_compose import recompose_chapter
    # 重新合成没有生图接口：没有背景图时不加封面，只警告
    recompose_chapter(raw_proj, 1)
    assert mf.probe_duration(raw_proj / "chapters" / "001" / "chapter_001.mp4") == pytest.approx(base_dur, abs=0.3)

    # 走流水线（有生图接口）：生成背景图并加封面
    img = CountingImage()
    from novel_to_video import chapter_pipeline as cp
    from novel_to_video.providers.tts import SilenceTTS
    from novel_to_video.providers.video import StubVideoProvider
    cio.set_chapter_status(raw_proj, 1, "pending")
    state = cio.load_state(raw_proj, 1)
    state["stages"]["compose"] = {"status": "pending", "error": None}
    cio.save_state(raw_proj, 1, state)
    res = cp.run_chapter(raw_proj, 1, text_llm=FakeLLM(), image=img, video=StubVideoProvider(), tts=SilenceTTS())
    assert res["status"] == "done", res
    cdir = raw_proj / "chapters" / "001"
    assert (cdir / "cover_bg.png").is_file() and (cdir / "cover.png").is_file()
    assert len([p for p in img.prompts if "chapter of a story" in p]) == 1 and "no text" in img.prompts[-1]
    assert mf.probe_duration(cdir / "chapter_001.mp4") == pytest.approx(base_dur + 2, abs=0.6)

    # 再合成一次：背景图沿用，不再调用生图接口
    n = len(img.prompts)
    recompose_chapter(raw_proj, 1)
    assert len(img.prompts) == n
    assert mf.probe_duration(cdir / "chapter_001.mp4") == pytest.approx(base_dur + 2, abs=0.6)
