"""P4 纯函数：切分、状态、校验（V4/V5）、插图提示词与额度、配置归一化、字体。"""
import pytest

from article_to_video.visual.director import split_chunks, TEXT_LIMIT
from article_to_video.visual.states import visible_counts, uniform_reveal, expected_state_count
from article_to_video.visual.config import normalize_visual, DEFAULT_STYLE_ANCHOR, image_allowance, aspect_ratio
from article_to_video.visual.images import build_prompt, image_sha
from article_to_video.validators.visual import validate_storyboard
from article_to_video.render.themes import canvas_size
from article_to_video.render.fonts import find_cjk_font, FontError, FONT_ENV


def _sc(t, n_items=2, **kw):
    d = {"id": "P01.b01", "beat": "P01.b01", "type": t, "items": [f"条目{i}" for i in range(n_items)],
         "emphasis": [], "header": [], "rows": [], "loop": False, "formula": "", "image": None, "refs": []}
    d.update(kw)
    d["reveal"] = uniform_reveal(d, 3)
    return d


def test_split_chunks_keeps_short_text_and_limits_length():
    assert split_chunks("这是一句短话。") == ["这是一句短话"]
    chunks = split_chunks("很长的句子，" * 30)
    assert chunks and all(0 < len(c) <= TEXT_LIMIT for c in chunks)
    assert not any(c.endswith(("，", "。")) for c in chunks)


def test_split_chunks_hard_splits_clause_without_punctuation():
    chunks = split_chunks("字" * 150)
    assert [len(c) for c in chunks] == [60, 60, 30]


def test_visible_counts_and_reveal_length_agree():
    assert visible_counts(_sc("keypoint", 3)) == [1, 2, 3]
    assert visible_counts(_sc("formula", 2)) == [0, 1, 2]     # 公式先出现，再逐条解释
    assert visible_counts(_sc("list", 0)) == [0]              # 空场景仍有一个状态
    for t in ("keypoint", "list", "diagram", "table", "formula"):
        sc = _sc(t, 4)
        assert len(uniform_reveal(sc, 2)) == expected_state_count(sc)


def test_uniform_reveal_stays_within_sentences_and_non_decreasing():
    sc = _sc("diagram", 6)
    rev = uniform_reveal(sc, 2)
    ats = [r["at_sentence"] for r in rev]
    assert ats == sorted(ats) and max(ats) <= 1


def _sb(scenes):
    return {"scenes": scenes, "source_structure": []}


def _script(n_beats, sentences=2):
    return {"beats": [{"id": f"P01.b{i + 1:02d}", "role": "claim",
                       "sentences": [{"text": "句", "quote": False, "emphasis": []}] * sentences}
                      for i in range(n_beats)]}


def _good_scene(i, t="keypoint"):
    sc = _sc(t, 2, id=f"P01.b{i:02d}", beat=f"P01.b{i:02d}")
    sc["reveal"] = [{"step": 0, "at_sentence": 0}, {"step": 1, "at_sentence": 1}]
    return sc


def _verdict(scenes, n_beats, **kw):
    kw.setdefault("allowance", 4)
    kw.setdefault("style_anchor", DEFAULT_STYLE_ANCHOR)
    kw.setdefault("images_enabled", True)
    return validate_storyboard(_sb(scenes), _script(n_beats), **kw)


def test_v4_allows_three_same_types_in_a_row_and_warns_beyond_that():
    """计划 4.5：同一类型连续超过 3 个为警告（不阻止确认）。"""
    three = _verdict([_good_scene(i, "keypoint") for i in (1, 2, 3)], 3)
    assert three["errors"] == [] and not any("连续" in w for w in three["warnings"])
    four = _verdict([_good_scene(i, "keypoint") for i in (1, 2, 3, 4)], 4)
    assert four["errors"] == [] and any("连续 4 个" in w for w in four["warnings"])


def test_v4_rejects_overlong_item_and_too_many_items():
    sc = _good_scene(1)
    sc["items"] = ["字" * (TEXT_LIMIT + 1), "短"]
    sc["reveal"] = [{"step": 0, "at_sentence": 0}, {"step": 1, "at_sentence": 1}]
    assert any("密度" in e for e in _verdict([sc], 1)["errors"])
    sc2 = _good_scene(1)
    sc2["items"] = ["a"] * 9
    sc2["reveal"] = [{"step": i, "at_sentence": 0} for i in range(9)]
    assert any("超过上限" in e for e in _verdict([sc2], 1)["errors"])


def test_v4_rejects_reveal_out_of_range_or_decreasing():
    sc = _good_scene(1)
    sc["reveal"] = [{"step": 0, "at_sentence": 1}, {"step": 1, "at_sentence": 0}]
    assert any("句序号" in e for e in _verdict([sc], 1)["errors"])
    sc2 = _good_scene(1)
    sc2["reveal"] = [{"step": 0, "at_sentence": 0}, {"step": 1, "at_sentence": 9}]
    assert any("句序号" in e for e in _verdict([sc2], 1)["errors"])


def test_v4_table_must_match_header():
    sc = _sc("table", 1, id="P01.b01", beat="P01.b01", header=["a", "b"], rows=[["1"]])
    sc["reveal"] = [{"step": 0, "at_sentence": 0}]
    assert any("行列数" in e for e in _verdict([sc], 1)["errors"])


def test_v5_blocks_text_requests_and_budget_overflow():
    ill = _good_scene(1, "illustration")
    ill["image"] = {"subject": "写上标题的海报", "focus_x": 0.5, "version": 0, "file": None}
    assert any("禁用词" in e for e in _verdict([ill], 1)["errors"])

    scenes = []
    for i in range(1, 4):
        s = _good_scene(i, "illustration")
        s["image"] = {"subject": "蒸汽机", "focus_x": 0.5, "version": 0, "file": None}
        scenes.append(s)
    v = _verdict(scenes, 3, allowance=2)
    assert any("超过本章额度" in e for e in v["errors"])


def test_v5_warns_when_images_disabled():
    ill = _good_scene(1, "illustration")
    ill["image"] = {"subject": "蒸汽机", "focus_x": 0.5, "version": 0, "file": None}
    v = _verdict([ill], 1, images_enabled=False, allowance=0)
    assert any("生图已关闭" in w for w in v["warnings"])


def test_prompt_carries_fixed_anchor_and_sha_depends_on_reference():
    sc = _good_scene(1, "illustration")
    sc["image"] = {"subject": "蒸汽机", "focus_x": 0.5, "version": 0, "file": None}
    p = build_prompt(sc, DEFAULT_STYLE_ANCHOR, "landscape")
    assert DEFAULT_STYLE_ANCHOR in p and "16:9" in p and "蒸汽机" in p
    assert image_sha(p, "1024x1024", "") != image_sha(p, "1024x1024", "abc")


def test_allowance_and_aspect_ratio():
    assert image_allowance(10, 2.5, True) == 25
    assert image_allowance(1.5, 2.5, True) == 3
    assert image_allowance(10, 2.5, False) == 0
    assert aspect_ratio("portrait") == "9:16" and aspect_ratio("landscape") == "16:9"


def test_normalize_visual_restores_missing_anchor_and_clamps_rate():
    cfg, warn = normalize_visual({"theme": "nope", "bible": {"style_anchor": "   "},
                                  "images": {"max_per_minute": 99}})
    assert cfg["theme"] == "ink"
    assert "文字" in cfg["bible"]["style_anchor"] or "字母" in cfg["bible"]["style_anchor"]
    assert cfg["images"]["max_per_minute"] == 6.0
    assert warn


def test_canvas_sizes_for_both_orientations():
    assert canvas_size("landscape", "1080P") == (1920, 1080)
    assert canvas_size("portrait", "1080P") == (1080, 1920)
    assert canvas_size("landscape", "720P") == (1280, 720)


def test_explicit_font_env_must_exist():
    with pytest.raises(FontError):
        find_cjk_font({FONT_ENV: "/no/such/font.ttf"})


def test_system_cjk_font_found_or_skipped():
    try:
        assert find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体（渲染测试会跳过）")
