"""剧情覆盖与主题：全文不截断、情节拆解与缓存、关键情节必须入漫画、固定页数容量提示、收束页表达主题。"""

import pytest

from world_forge import engine, generate, proposals, repo, store
from world_forge.engine import _chunks


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def _long_story(world, llm, paragraphs=12, size=500):
    sid = _story(world, llm)
    body = "\n".join(f"第{i + 1}段情节：" + ("甲与乙在港口争执。" * (size // 9)) for i in range(paragraphs))
    body += "\n最后一段：灯塔熄灭，主题落在守护与放手之间。"
    (repo.story_dir(world, sid) / "story.md").write_text(body, encoding="utf-8")
    return sid, body


# ---------- 切块与全文

def test_chunks_keep_every_paragraph_and_split_long_text():
    paras = [f"第{i}段" + "字" * 400 for i in range(10)]
    chunks = _chunks("\n".join(paras), size=2500)
    assert len(chunks) > 1
    joined = "\n".join(chunks)
    assert all(p in joined for p in paras), "切块不得丢失任何段落"


def test_long_story_tail_reaches_the_comic_not_truncated(world, llm, img):
    sid, body = _long_story(world, llm)
    engine.make_storyboard(llm, world, sid, pages="auto")
    cov = store.read_json(repo.story_dir(world, sid) / "coverage.json")
    texts = " ".join(e["event"] for e in cov["events"])
    assert "灯塔熄灭" in texts, "正文尾段必须进入情节拆解，不能被截断"
    assert cov["chunks"] > 1


# ---------- 覆盖报告与缓存

def test_every_essential_event_is_covered_in_the_storyboard(world, llm, img):
    sid, _ = _long_story(world, llm)
    board = engine.make_storyboard(llm, world, sid, pages="auto")
    rep = board["coverage"]
    assert rep["essential"] > 0
    assert rep["covered_essential"] == rep["essential"] and rep["missing"] == []
    assert rep["theme"]


def test_coverage_is_reused_when_source_is_unchanged(world, llm, img):
    sid, _ = _long_story(world, llm)
    engine.make_storyboard(llm, world, sid, pages="auto")
    path = repo.story_dir(world, sid) / "coverage.json"
    first = path.read_text(encoding="utf-8")
    engine.make_storyboard(llm, world, sid, pages="auto")
    assert path.read_text(encoding="utf-8") == first


def test_changing_the_text_triggers_a_fresh_breakdown(world, llm, img):
    sid, body = _long_story(world, llm)
    engine.make_storyboard(llm, world, sid, pages="auto")
    path = repo.story_dir(world, sid) / "coverage.json"
    old_hash = store.read_json(path)["source_hash"]
    (repo.story_dir(world, sid) / "story.md").write_text(body + "\n新增一段。", encoding="utf-8")
    engine.make_storyboard(llm, world, sid, pages="auto")
    assert store.read_json(path)["source_hash"] != old_hash


# ---------- 容量与校验

def test_fixed_pages_too_small_for_essential_events_gives_clear_error(world, llm, img):
    sid, _ = _long_story(world, llm, paragraphs=12)
    with pytest.raises(Exception) as info:
        engine.make_storyboard(llm, world, sid, pages=1)
    assert "容量" in str(info.value)


def test_beats_must_cover_all_essential_events():
    d = {"pages": [{"purpose": "铺垫：开场", "focus": "门", "covers": ["E01"], "theme_note": ""}]}
    errs = generate._validate_beats(d, None, essential=["E01", "E02"], all_ids=["E01", "E02"])
    assert errs and "E02" in errs[0]


def test_beats_page_covers_at_most_four_events():
    ids = [f"E{i:02d}" for i in range(1, 7)]
    d = {"pages": [{"purpose": "铺垫：开场", "focus": "门", "covers": ids, "theme_note": "x"}]}
    assert any("超过上限" in e for e in generate._validate_beats(d, None, essential=[], all_ids=ids))


def test_last_beat_page_must_carry_theme_note():
    d = {"pages": [{"purpose": "收束：结局", "focus": "灯", "covers": ["E01"], "theme_note": ""}]}
    errs = generate._validate_beats(d, None, essential=["E01"], all_ids=["E01"], require_theme=True)
    assert errs and "theme_note" in errs[0]


def test_storyboard_must_depict_every_covered_event():
    pages = [{"no": 1, "panels": [{"visual_en": "x", "event": "E01"}]}]
    errs = generate._validate_storyboard({"pages": pages}, None, covers=[["E01", "E02"]])
    assert errs and "E02" in errs[0]
    assert generate._validate_storyboard({"pages": pages}, None, covers=[["E01"]]) == []


def test_theme_validation_requires_a_theme_sentence():
    assert generate._validate_theme({"theme": ""})
    d = {"theme": "守护与放手", "carriers": ["E01", ""], "closing_line": "灯亮着"}
    assert generate._validate_theme(d) == [] and d["carriers"] == ["E01"]
