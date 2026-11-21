"""R34：新方案页面背景插图——按节共用、缓存复用、无 key 不生图、失败回退渐变、默认开启。"""
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from article_to_video.app_settings import effective_group, set_field
from article_to_video.deck import background as bg
from article_to_video.deck.compose import RENDER_VERSION, compose_deck_part, page_state_images
from article_to_video.deck.store import deck_root
from article_to_video.providers.image import ImageResult, StubImageProvider
from article_to_video.render.themes import DEFAULT_THEME
from article_to_video.visual.config import normalize_visual
from article_to_video.visual.prompting import check_illustration
from article_to_video.project_store import read_json
from test_deck_phase4 import small_project  # noqa: F401  (fixture)

CANVAS = (1920, 1080)


class FakeLLM:
    def __init__(self, answers=None, fail=False):
        self.answers = list(answers or [])
        self.fail = fail
        self.calls = 0

    def ask(self, prompt, system=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.answers.pop(0) if self.answers else "一座海边的旧灯塔"


class CountingProvider(StubImageProvider):
    """离线生图（沿用 stub 的写图逻辑，calls 记录每次调用）。"""


class RejectingProvider:
    """模拟鉴权失败：第一次调用即返回不可重试错误。"""
    name = "reject"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt, save_path, **kw):
        self.calls += 1
        return ImageResult(False, error="参数/鉴权错误（HTTP 401）", non_retryable=True)


def _sections(n=3):
    return [{"key": f"s{i:02d}", "title": f"小节{i}", "context": f"内容{i}"} for i in range(n)]


# ---------------- 分节与取景 ----------------

def test_pages_are_grouped_by_their_first_section():
    pages = [{"id": "P01.S01"}, {"id": "P01.S02"}, {"id": "P01.S03"}]
    slides = {
        "P01.S01": {"title": "甲", "section_ids": ["s01"], "elements": []},
        "P01.S02": {"title": "乙", "section_ids": ["s01", "s02"], "elements": []},
        "P01.S03": {"title": "丙", "section_ids": ["s02"], "elements": []},
    }
    secs = bg.plan_sections(pages, slides)
    assert [s["key"] for s in secs] == ["s01", "s02"]
    assert [len(s["pages"]) for s in secs] == [2, 1]
    assert secs[0]["title"] == "甲"


def test_page_variation_stays_in_range_and_differs_within_a_section():
    params = [bg.page_background_params(j, 0.55) for j in range(6)]
    assert all(0.0 <= f <= 1.0 and bg.DARKEN_MIN <= d <= bg.DARKEN_MAX for f, d in params)
    assert len({f for f, _ in params}) > 1                        # 同一节内取景不同
    assert bg.page_background_params(0, 0.55)[1] == pytest.approx(0.55)


# ---------------- 主体与提示词 ----------------

def test_subject_rejects_abstract_text_and_overlong_answers():
    assert bg.clean_subject("一座海边的旧灯塔") == "一座海边的旧灯塔"
    assert bg.clean_subject("画面主体：一扇打开的木门") == "一扇打开的木门"
    assert bg.clean_subject("「效率」") is None                    # 抽象词
    assert bg.clean_subject("一张写着标语的海报") is None           # 含文字类词
    assert bg.clean_subject("很长" * 30) is None
    assert bg.clean_subject("") is None


def test_fallback_subject_is_always_valid():
    assert check_illustration({"subject": bg.FALLBACK_SUBJECT, "scene": "", "composition": ""}) == []


def test_build_subject_falls_back_without_llm_or_on_failure():
    assert bg.build_subject("标题", "内容", None) == bg.FALLBACK_SUBJECT
    assert bg.build_subject("标题", "内容", FakeLLM(fail=True)) == bg.FALLBACK_SUBJECT
    assert bg.build_subject("标题", "内容", FakeLLM(answers=["效率"])) == bg.FALLBACK_SUBJECT
    assert bg.build_subject("标题", "内容", FakeLLM(answers=["一台老式收音机"])) == "一台老式收音机"


# ---------------- 生成与缓存 ----------------

def test_one_background_per_section_then_cache_reuse(small_project):
    pdir = small_project
    prov = CountingProvider()
    llm = FakeLLM()
    kw = dict(provider=prov, style_anchor="扁平插画", orientation="landscape", size="1024x1024", llm=llm)
    first = bg.ensure_section_backgrounds(pdir, "P01", _sections(3), **kw)
    assert len(prov.calls) == 3 and llm.calls == 3
    assert all(p and p.is_file() for p in first.values())
    again = bg.ensure_section_backgrounds(pdir, "P01", _sections(3), **kw)
    assert len(prov.calls) == 3 and llm.calls == 3                  # 输入未变：不生图、不问模型
    assert again == first


def test_changed_section_regenerates_only_that_section(small_project):
    pdir = small_project
    prov = CountingProvider()
    kw = dict(provider=prov, style_anchor="扁平插画", orientation="landscape", size="1024x1024", llm=None)
    bg.ensure_section_backgrounds(pdir, "P01", _sections(2), **kw)
    secs = _sections(2)
    secs[1]["context"] = "改过的内容"
    out = bg.ensure_section_backgrounds(pdir, "P01", secs, **kw)
    assert len(prov.calls) == 3
    assert out["s01"].name.endswith("_v002.png")                    # 旧版本保留，新版本递增


def test_no_provider_means_no_images_and_no_network(small_project):
    out = bg.ensure_section_backgrounds(small_project, "P01", _sections(2), provider=None,
                                        style_anchor="a", orientation="landscape", size="1024x1024")
    assert out == {"s00": None, "s01": None}
    assert not (deck_root(small_project, "P01") / "backgrounds" / "index.json").exists()


def test_rejected_request_stops_later_sections_in_the_chapter(small_project):
    prov = RejectingProvider()
    out = bg.ensure_section_backgrounds(small_project, "P01", _sections(4), provider=prov,
                                        style_anchor="a", orientation="landscape", size="1024x1024")
    assert prov.calls == 1                                          # 第一次即配置错误，后续不再尝试
    assert all(v is None for v in out.values())


def test_failed_section_falls_back_without_stopping_others(small_project):
    class Flaky(StubImageProvider):
        def generate(self, prompt, save_path, **kw):
            if len(self.calls) == 0:
                self.calls.append({"prompt": prompt})
                return ImageResult(False, error="HTTP 500")
            return super().generate(prompt, save_path, **kw)

    out = bg.ensure_section_backgrounds(small_project, "P01", _sections(3), provider=Flaky(),
                                        style_anchor="a", orientation="landscape", size="1024x1024")
    assert out["s00"] is None and out["s01"] and out["s02"]


# ---------------- 渲染 ----------------

def _white_png(path: Path) -> Path:
    Image.new("RGB", (1024, 1024), (255, 255, 255)).save(path)
    return path


def test_background_image_is_darkened_for_readability(tmp_path):
    slide = {"title": "标题", "elements": [{"kind": "keypoint", "items": ["要点一", "要点二"]}]}
    page = {"id": "P01.S01"}
    src = _white_png(tmp_path / "w.png")
    img = page_state_images(page, slide, CANVAS, "landscape", DEFAULT_THEME,
                            background=src, bg_focus=0.5, bg_darken=0.55)[0]
    corner = img.convert("RGB").getpixel((5, CANVAS[1] - 5))
    assert abs(corner[0] - 255 * 0.45) < 10                          # 压暗到 45% 亮度
    plain = page_state_images(page, slide, CANVAS, "landscape", DEFAULT_THEME)[0]
    assert ImageStat.Stat(img.convert("L")).mean[0] > ImageStat.Stat(plain.convert("L")).mean[0]


# ---------------- 设置 ----------------

def test_background_defaults_on_and_is_clamped():
    cfg, _ = normalize_visual({})
    assert cfg["deck_background"] == {"enabled": True, "darken": 0.55}
    cfg, warns = normalize_visual({"deck_background": {"enabled": False, "darken": 2}})
    assert cfg["deck_background"] == {"enabled": False, "darken": 0.8} and warns


def test_settings_page_exposes_background_fields():
    src = (Path(__file__).resolve().parent.parent / "ui" / "settings_page.py").read_text(encoding="utf-8")
    assert "deck_background.enabled" in src and "deck_background.darken" in src


# ---------------- 端到端：合成时按节生成并复用 ----------------

def test_compose_generates_per_section_and_reuses_on_second_run(small_project):
    pdir = small_project
    prov = CountingProvider()
    info = compose_deck_part(pdir, "P01", image=prov, llm=FakeLLM())
    n_sec = len(info["backgrounds"])
    assert n_sec >= 1 and all(v for v in info["backgrounds"].values())
    assert len(prov.calls) == n_sec                                 # 每节一张，不是每页一张
    assert info["render_version"] == RENDER_VERSION
    before = len(prov.calls)
    compose_deck_part(pdir, "P01", image=prov, llm=FakeLLM())
    assert len(prov.calls) == before                                # 第二次全部复用


def test_compose_without_key_uses_gradient(small_project):
    info = compose_deck_part(small_project, "P01", image=None, llm=None)
    assert info["backgrounds"] and all(v is None for v in info["backgrounds"].values())


def test_compose_respects_switch_off(small_project):
    set_field(small_project, "visual", "deck_background.enabled", False)
    prov = CountingProvider()
    info = compose_deck_part(small_project, "P01", image=prov, llm=None)
    assert prov.calls == [] and info["backgrounds"] == {}
