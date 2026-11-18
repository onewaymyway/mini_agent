"""P4 分镜：参考文章的导演结果、缓存与过期、人工编辑的保存与拦截、C2 确认。"""
import pytest

from article_to_video.planning.errors import PlanError
from article_to_video.visual import storyboard as sbm
from article_to_video.visual.config import save_visual_config
from article_to_video.visual.director import split_chunks
from article_to_video.visual.states import expected_state_count
from helpers_p4 import scripted_project


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    d, _spine = scripted_project(tmp_path)
    return d


def test_all_parts_plan_without_errors_on_reference_article(pdir):
    from article_to_video.planning.runner import load_spine
    spine = load_spine(pdir)
    for p in spine["parts"]:
        sb = sbm.plan_storyboard(pdir, p["id"])
        assert sb["errors"] == [], (p["id"], sb["errors"])
        assert sb["scenes"][0]["beat"].startswith(p["id"])
        assert sb["images_planned"] <= sb["image_allowance"]


def test_reference_structures_are_represented(pdir):
    types = set()
    for pid in ("P01", "P02", "P03", "P06"):
        types |= {s["type"] for s in sbm.plan_storyboard(pdir, pid)["scenes"]}
    assert {"table", "diagram", "formula", "list"} <= types


def test_no_three_identical_types_in_a_row(pdir):
    for pid in ("P01", "P02", "P03", "P04", "P05", "P06"):
        types = [s["type"] for s in sbm.plan_storyboard(pdir, pid)["scenes"]]
        assert all(len(set(types[i:i + 3])) > 1 for i in range(len(types) - 2)), pid


def test_plan_reuses_unchanged_storyboard(pdir):
    first = sbm.plan_storyboard(pdir, "P01")
    again = sbm.plan_storyboard(pdir, "P01")
    assert first["input_sha"] == again["input_sha"]
    assert first["generated_at"] == again["generated_at"]


def test_visual_settings_change_makes_storyboard_stale_and_blocks_confirm(pdir):
    sbm.plan_storyboard(pdir, "P01")
    sbm.confirm_storyboard(pdir, "P01")
    assert sbm.storyboard_status(pdir, "P01")["confirmed"]
    save_visual_config(pdir, {"images": {"enabled": False}})
    st = sbm.storyboard_status(pdir, "P01")
    assert st["stale"] and not st["confirmed"]
    with pytest.raises(PlanError):
        sbm.confirm_storyboard(pdir, "P01")


def test_edit_recomputes_reveal_and_invalid_edit_is_not_saved(pdir):
    sb = sbm.plan_storyboard(pdir, "P02")
    kp = next(s for s in sb["scenes"] if s["type"] == "keypoint")
    raw = {"scenes": [dict(s) for s in sb["scenes"]]}
    target = next(s for s in raw["scenes"] if s["id"] == kp["id"])
    target["items"] = ["第一条要点", "第二条要点", "第三条要点"]
    saved = sbm.save_storyboard(pdir, "P02", raw)
    got = next(s for s in saved["scenes"] if s["id"] == kp["id"])
    assert len(got["reveal"]) == 3 == expected_state_count(got)
    assert saved["confirmed"] is False

    bad = {"scenes": [dict(s) for s in saved["scenes"]]}
    next(s for s in bad["scenes"] if s["id"] == kp["id"])["items"] = ["字" * 61]
    with pytest.raises(PlanError):
        sbm.save_storyboard(pdir, "P02", bad)
    on_disk = sbm.load_storyboard(pdir, "P02")
    assert next(s for s in on_disk["scenes"] if s["id"] == kp["id"])["items"] == ["第一条要点", "第二条要点", "第三条要点"]


def test_table_items_are_derived_from_rows(pdir):
    sb = sbm.plan_storyboard(pdir, "P01")
    tbl = next(s for s in sb["scenes"] if s["type"] == "table")
    raw = {"scenes": [dict(s) for s in sb["scenes"]]}
    next(s for s in raw["scenes"] if s["id"] == tbl["id"])["items"] = ["被改掉的条目"]
    saved = sbm.save_storyboard(pdir, "P01", raw)
    got = next(s for s in saved["scenes"] if s["id"] == tbl["id"])
    assert got["items"] == ["、".join(r) for r in tbl["rows"]]


def test_confirm_requires_existing_storyboard(pdir):
    with pytest.raises(PlanError):
        sbm.confirm_storyboard(pdir, "P03")


def test_split_chunks_on_real_reference_text_stays_short():
    text = "从康波周期到超级AI，我们正在进入第六次技术革命，还是走向一个后康波时代，这是本文的核心问题。"
    assert all(len(c) <= 60 for c in split_chunks(text))
