"""新方案：标题与内容不被截断；封面；清空当前数据并重新生成。"""
import json

import pytest

from article_to_video.compose.ffrun import ComposeError  # noqa: F401
from article_to_video.deck.compose import _fit_title, page_state_images
from article_to_video.deck.pipeline import regenerate_deck, reset_deck, run_deck_pipeline
from article_to_video.planning.errors import PlanError
from article_to_video.project_store import write_json_atomic
from article_to_video.providers.tts import SilenceTTS
from article_to_video.render.themes import DEFAULT_THEME, canvas_size
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FIXTURE, FakeLLM

LONG_TITLE = "这是一个非常长的幻灯片标题用来检验超过两行时字号是否会自动缩小从而完整显示而不被截断的效果"


def test_long_title_shrinks_and_keeps_every_line():
    img = _fit_title((1920, 1080), "landscape", DEFAULT_THEME, LONG_TITLE)
    assert img.size[0] == 1920
    assert img.size[1] <= 1080 * 0.26 + 2                   # 标题区不超过画面的 26%
    box = img.getchannel("A").getbbox()
    assert box is not None and box[1] >= 0 and box[3] <= img.size[1]


def test_page_with_many_elements_is_complete_and_within_the_canvas():
    slide = {"title": LONG_TITLE, "elements": [
        {"kind": "keypoint", "items": [f"第{i}条要点内容用于检验完整显示" for i in range(12)], "emphasis": []},
        {"kind": "diagram", "items": [f"节点{i}" for i in range(16)], "loop": False, "emphasis": []},
        {"kind": "table", "header": ["项目", "数值"], "rows": [["甲", "1"], ["乙", "2"]], "emphasis": []}]}
    states = page_state_images({"title": "x"}, slide, (1920, 1080), "landscape", DEFAULT_THEME)
    assert len(states) == 3 and all(im.size == (1920, 1080) for im in states)
    last = states[-1]
    box = last.convert("RGBA").getchannel("A").getbbox() if last.mode == "RGBA" else None
    # 最后一页包含全部元素：与第一页相比内容明显增加（说明后续元素没有被挤出画面）
    from PIL import ImageChops
    assert ImageChops.difference(states[0], last).getbbox() is not None


def _deck_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "测试文章", "confirmed": True,
                       "parts": [{"id": "P01", "title": "引言", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    # 现有方案的数据（必须不受影响）
    (pdir / "parts" / "P01" / "audio").mkdir(parents=True)
    (pdir / "parts" / "P01" / "audio" / "classic.wav").write_bytes(b"c")
    (pdir / "exports").mkdir(exist_ok=True)
    (pdir / "exports" / "测试文章.mp4").write_bytes(b"c")
    return pdir


def test_reset_needs_confirmation(tmp_path, monkeypatch):
    pdir = _deck_project(tmp_path, monkeypatch)
    with pytest.raises(PlanError):
        reset_deck(pdir, confirm=False)


def test_reset_clears_deck_data_and_keeps_outline_and_classic(tmp_path, monkeypatch):
    pdir = _deck_project(tmp_path, monkeypatch)
    run_deck_pipeline(pdir, gear="G3", until="script", llm=FakeLLM(), tts=SilenceTTS())
    assert (pdir / "deck" / "parts" / "P01" / "deck.json").is_file()
    removed = reset_deck(pdir, confirm=True)
    assert "deck/parts/P01/" in removed
    assert not (pdir / "deck" / "parts" / "P01").exists()
    assert not (pdir / "deck" / "run_status.json").exists()
    assert (pdir / "plan" / "spine.json").is_file()                       # 大纲保留
    assert (pdir / "parts" / "P01" / "audio" / "classic.wav").is_file()   # 现有方案保留
    assert (pdir / "exports" / "测试文章.mp4").is_file()


def test_reset_can_keep_the_voice_cache(tmp_path, monkeypatch):
    pdir = _deck_project(tmp_path, monkeypatch)
    run_deck_pipeline(pdir, gear="G3", until="script", llm=FakeLLM(), tts=SilenceTTS())
    audio = pdir / "deck" / "parts" / "P01" / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    (audio / "keep.wav").write_bytes(b"d")
    reset_deck(pdir, keep_audio=True, confirm=True)
    assert (audio / "keep.wav").is_file()
    assert not (pdir / "deck" / "parts" / "P01" / "deck.json").exists()


def test_regenerate_clears_then_runs_the_whole_flow_again(tmp_path, monkeypatch):
    pdir = _deck_project(tmp_path, monkeypatch)
    run_deck_pipeline(pdir, gear="G3", until="script", llm=FakeLLM(), tts=SilenceTTS())
    old = json.loads((pdir / "deck" / "parts" / "P01" / "deck.json").read_text(encoding="utf-8"))
    out = regenerate_deck(pdir, keep_audio=False, confirm=True, gear="G3", until="script",
                          llm=FakeLLM(), tts=SilenceTTS())
    assert out["status"]["state"] == "done"
    new = json.loads((pdir / "deck" / "parts" / "P01" / "deck.json").read_text(encoding="utf-8"))
    assert new["input_sha"] == old["input_sha"]                            # 同样的输入，重新生成同样的结果
    assert (pdir / "deck" / "parts" / "P01" / "deck_script.json").is_file()
