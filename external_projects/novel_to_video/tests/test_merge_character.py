"""补建角色合并到已有角色：别名、改写镜头引用、清理下游产物、界面操作。"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import tool_api
from novel_to_video.merge_character import MergeError, merge_auto_added, merge_candidates
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_shots import validate_and_build

from test_chapter_pipeline import CHAPTER_TEXT, ChapterLLM

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    return p


def _run(p):
    return cp.run_chapter(p, 1, text_llm=ChapterLLM(), image=StubImageProvider(),
                          video=StubVideoProvider(), tts=SilenceTTS())


def _chars(p):
    return json.loads((p / "global/characters.json").read_text(encoding="utf-8"))["characters"]


def _auto(p):
    return next(c for c in _chars(p) if c.get("source") == "auto_added")


def test_merge_before_downstream_exists(proj):
    assert _run(proj)["status"] == "waiting_assets"
    new = _auto(proj)
    target = next(c for c in _chars(proj) if c["names"] == ["影子"])
    r = merge_auto_added(proj, new["id"], target["id"])
    assert r["aliases_added"] == ["新人"] and r["shots_cleared"] == {}   # 还没生成过下游产物，不清理
    chars = _chars(proj)
    assert all(c["id"] != new["id"] for c in chars)
    assert "新人" in next(c for c in chars if c["id"] == target["id"])["names"]
    shots = cio.load_shots(proj, 1)
    ids = [b["speaker_id"] for sh in shots["shots"] for b in sh["blocks"] if b["type"] == "dialogue"]
    assert ids == [next(c for c in _chars(proj) if c["names"] == ["林远"])["id"], target["id"]]   # 新人的台词改归影子
    for sh in shots["shots"]:
        assert new["id"] not in sh["character_ids"] and len(set(sh["character_ids"])) == len(sh["character_ids"])
    assert "新人" not in (shots.get("new_speakers") or [])
    assert f"character:{new['id']}" not in json.loads((proj / "global/review.json").read_text(encoding="utf-8"))["items"]
    assert tool_api.auto_added_queue(str(proj))["data"] == []
    # 重新运行不再卡在“等待补建角色”
    assert _run(proj)["status"] == "waiting_review"


def test_merge_clears_generated_downstream_and_marks_stale(proj):
    _run(proj)
    new = _auto(proj)
    gv.approve(proj, "character", new["id"], "text")
    _run(proj)
    gv.approve(proj, "character", new["id"], "portrait")
    assert _run(proj)["status"] == "waiting_review"
    cdir = cio.chapter_dir(proj, 1)
    assert list((cdir / "audio").glob("*.wav"))
    target = next(c for c in _chars(proj) if c["names"] == ["影子"])
    r = merge_auto_added(proj, new["id"], target["id"])
    assert r["shots_cleared"].get(1)
    hit = set(r["shots_cleared"][1])
    shots = {s["id"]: s for s in cio.load_shots(proj, 1)["shots"]}
    for sid in hit:
        assert shots[sid]["audio_key"] is None and shots[sid]["clip_key"] is None
        assert not (cdir / "audio" / f"{sid}.wav").exists() and not (cdir / "clips" / f"{sid}.mp4").exists()
    state = cio.load_state(proj, 1)["stages"]
    assert state["script"]["status"] == "done"            # 只换了说话人，剧本没动，不能标成过期
    assert cio.chapter_row(proj, 1)["status"] == "stale"
    assert _run(proj)["status"] == "waiting_review"       # 重新生成后可继续


def test_alias_makes_later_planning_resolve_by_name(proj):
    _run(proj)
    new = _auto(proj)
    target = next(c for c in _chars(proj) if c["names"] == ["影子"])
    merge_auto_added(proj, new["id"], target["id"])
    raw = [{"scene_no": 1, "location_id": "x", "blocks": [
        {"type": "dialogue", "text": "我也要去。", "speaker_id": None, "speaker_name": "新人"}]}]
    built = validate_and_build(raw, "新人：我也要去。", [], _chars(proj), 1)
    assert built["shots"][0]["blocks"][0]["speaker_id"] == target["id"] and built["new_speakers"] == []


def test_merge_validation(proj):
    _run(proj)
    new = _auto(proj)
    normal = next(c for c in _chars(proj) if c["names"] == ["林远"])
    with pytest.raises(MergeError, match="不是补建角色"):
        merge_auto_added(proj, normal["id"], new["id"])
    with pytest.raises(MergeError, match="不能合并到自己"):
        merge_auto_added(proj, new["id"], new["id"])
    with pytest.raises(MergeError, match="目标不存在"):
        merge_auto_added(proj, new["id"], "char_999")
    res = tool_api.merge_auto_added(str(proj), new["id"], "char_999")
    assert not res["ok"] and "目标不存在" in res["error"]["message"]
    cands = merge_candidates(proj, new["id"])
    assert new["id"] not in [c["id"] for c in cands] and {"林远", "影子"} <= {c["name"] for c in cands}


def test_merge_from_chapter_page(proj):
    _run(proj)
    new = _auto(proj)
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("🎬 章节生产").run()
    at.text_input(key="chap_root").input(str(proj.parent)).run()
    assert not at.exception, at.exception
    sel = next(s for s in at.selectbox if s.label == "合并到已有角色")
    target = next(c for c in _chars(proj) if c["names"] == ["影子"])
    sel.set_value(target["id"]).run()
    btn = next(b for b in at.button if (b.key or "").startswith("mg_btn_"))
    assert btn.disabled                                    # 没勾选确认前不能点
    next(c for c in at.checkbox if (c.key or "").startswith("mg_ok_")).check().run()
    next(b for b in at.button if (b.key or "").startswith("mg_btn_")).click().run()
    assert not at.exception, at.exception
    assert all(c["id"] != new["id"] for c in _chars(proj))
    assert "新人" in next(c for c in _chars(proj) if c["id"] == target["id"])["names"]
