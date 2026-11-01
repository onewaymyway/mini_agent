"""性别线索检索与确认的测试。核心保证：正文线索永远不会自动判定性别，只有人工确认或人设卡明确字段才会生效。"""

import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import gender_evidence as ge
from novel_to_video import gender_review
from novel_to_video import tool_api
from novel_to_video.steps import global_visual as gv
from novel_to_video.providers.text import RuleTextLLM


# ---------------------------------------------------------------- 纯函数：证据与建议

CH = [
    ("第001章", "林远走进房间。他说：“我们走。”"),
    ("第002章", "苏槿看着他，目光复杂。"),
    ("第003章", "林远记得父亲曾经说的话。"),
    ("第004章", "林星看着哥哥，她笑了。"),
]


def test_non_human_is_never_suggested_gender():
    s = ge.suggest({"id": "char_x", "names": ["AI系统·执行者"]}, CH, [])
    assert s.suggested == "non_human" and s.evidence_total == 0


def test_no_persona_field_means_unknown_even_with_many_pronouns():
    # 代词必须与名字同句才计入（避免把相邻句的“他”算给该角色）
    big = [(f"第{i:03d}章", "林远他很冷静，他看着远方。") for i in range(200)]
    s = ge.suggest({"id": "char_001", "names": ["林远"]}, big, [])
    assert s.suggested == "unknown"
    assert s.basis == "none"
    assert s.evidence_total > 0


def test_pronoun_in_adjacent_sentence_is_not_attributed():
    s = ge.suggest({"id": "char_001", "names": ["林远"]},
                   [("第001章", "林远说话。他很冷静。")], [])
    assert s.evidence_total == 0


def test_persona_explicit_field_gives_high_confidence_basis():
    persona = [("林远.md", "# 林远\n性别：男\n")]
    s = ge.suggest({"id": "char_001", "names": ["林远"]}, CH, persona)
    assert s.suggested == "male" and s.basis == "persona_field"


def test_conflicting_persona_sources_do_not_decide():
    persona = [("a.md", "性别：男"), ("b.md", "性别：女")]
    s = ge.suggest({"id": "char_001", "names": ["林远"]}, CH, persona)
    assert s.suggested == "unknown"


def test_evidence_is_deterministic():
    a = ge.suggest({"id": "c", "names": ["林远"]}, CH, [])
    b = ge.suggest({"id": "c", "names": ["林远"]}, CH, [])
    assert a.to_dict() == b.to_dict()


def test_evidence_spreads_across_book():
    chapters = [(f"第{i:03d}章", f"林远的父亲在第{i}章出现。") for i in range(1, 101)]
    ev, total = ge.collect_evidence(["林远"], chapters, limit=8)
    assert total == 100 and len(ev) == 8
    wheres = [e.where for e in ev]
    assert wheres[0] < wheres[-1]  # 首尾跨度大，覆盖全书


# ---------------------------------------------------------------- 端到端：项目与确认

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="t")
    p = Path(res["data"]["project_dir"])
    return p


def _char(p, cid):
    return next(c for c in json.loads((p / "global" / "characters.json").read_text(encoding="utf-8"))["characters"]
                if c["id"] == cid)


def test_suggest_all_writes_file_without_touching_characters(project):
    before = (project / "global" / "characters.json").read_text(encoding="utf-8")
    gender_review.suggest_all(project)
    assert (project / gender_review.SUGGEST_FILE).is_file()
    assert (project / "global" / "characters.json").read_text(encoding="utf-8") == before


def test_confirm_writes_user_source_and_records_decision(project):
    gender_review.suggest_all(project)
    d = gender_review.confirm(project, "char_002", "female", note="看第 3 章的称呼")
    c = _char(project, "char_002")
    assert c["gender"] == "female" and c["gender_source"] == "user"
    assert d["choice"] == "female" and d["note"] == "看第 3 章的称呼"
    assert "char_002" not in gender_review.pending(project)


def test_confirm_unknown_clears_gender(project):
    gender_review.confirm(project, "char_002", "unknown")
    c = _char(project, "char_002")
    assert c["gender"] is None and c["gender_source"] is None
    assert "char_002" in gender_review.pending(project)


def test_invalid_choice_rejected(project):
    with pytest.raises(ValueError):
        gender_review.confirm(project, "char_002", "nonbinary")


def test_unknown_character_rejected(project):
    with pytest.raises(KeyError):
        gender_review.confirm(project, "char_999", "male")


def test_confirmation_is_appended_to_progress(project):
    gender_review.confirm(project, "char_002", "female")
    assert "性别确认" in (project / "PROGRESS.md").read_text(encoding="utf-8")


def test_manual_gender_edit_counts_as_user_confirmation(project):
    gv.update_entity(project, "character", "char_002", {"gender": "male"}, note="手动")
    assert _char(project, "char_002")["gender_source"] == "user"


def test_llm_gender_is_not_written(project):
    """回归：G-2 的文本模型即使返回性别，也不会写入角色。"""

    class LLM(RuleTextLLM):
        name = "fake"

        def ask(self, prompt, system=""):
            return json.dumps({"gender": "female", "visual_anchor_en": "young adult, dark jacket",
                               "voice_profile": "清亮"}, ensure_ascii=False)

    gv.visual_text_pass(project, LLM())
    c = _char(project, "char_002")
    assert c.get("gender") is None
    assert c.get("gender_source") is None


def test_readiness_requires_confirmed_gender(project):
    gv.visual_text_pass(project, RuleTextLLM())
    st = gv.visual_status(project)
    row = next(r for r in st["rows"] if r["id"] == "char_002")
    assert any("gender" in m for m in row["missing_fields"])
    gender_review.confirm(project, "char_002", "female")
    st = gv.visual_status(project)
    row = next(r for r in st["rows"] if r["id"] == "char_002")
    assert not any("gender" in m for m in row["missing_fields"])


def test_gender_page_renders(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    tool_api.create_project_from_source(str(out), str(src), title="t")
    app = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app, default_timeout=60).run()
    at.sidebar.radio[0].set_value("⚧ 性别确认").run()
    at.text_input(key="gen_root").input(str(out)).run()
    assert not at.exception
    assert any("性别确认" in h.value for h in at.header)
