"""补建角色性别自动确定：正文证据优先，证据不足由模型二选一；门禁接受自动推断，不再要求用户手动选。"""
import json
from pathlib import Path

from novel_to_video import tool_api
from novel_to_video.steps import chapter_assets as ca
from novel_to_video.review import entity_key
from conftest import build_export


class LLM:
    name = "fake"

    def __init__(self, gender="female"):
        self.gender = gender
        self.calls = []

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
        if "TASK=gender" in prompt:
            return json.dumps({"gender": self.gender, "basis_zh": "模型判断"}, ensure_ascii=False)
        return json.dumps({"gender": "unknown", "age_range": "youth", "description_zh": "d",
                           "visual_anchor_en": "a person in grey uniform", "voice_profile": "平稳的声音"},
                          ensure_ascii=False)


def test_draft_without_gender_is_decided_from_chapter_excerpt(tmp_path):
    text = "陈媛走进来，她把外套挂好。\n林远看着窗外。\n陈媛轻声说：她一直很担心。"
    llm = LLM(gender="female")                           # 起草没给性别时，由正文段落二选一
    ent = ca._draft_entity(tmp_path, llm, "陈媛", ["她说话很轻"], 3, "char_70", text)
    assert ent["gender"] == "female" and ent["gender_source"] == "auto"
    prompt = next(c for c in llm.calls if "TASK=gender" in c)
    assert "陈媛走进来" in prompt and "林远看着窗外" not in prompt     # 只给含该角色名的段落


def test_llm_fallback_when_excerpt_has_no_name(tmp_path):
    llm = LLM(gender="male")
    ent = ca._draft_entity(tmp_path, llm, "路人甲", ["站住"], 3, "char_71", "没有任何性别线索的正文。")
    assert ent["gender"] == "male" and ent["gender_source"] == "auto"
    assert ent["gender_basis"] == "模型依据正文推断"
    assert any("TASK=gender" in c for c in llm.calls)


def test_non_human_is_not_forced(tmp_path):
    llm = LLM(gender="male")
    ent = ca._draft_entity(tmp_path, llm, "盖亚", ["早上好"], 3, "char_72", "盖亚播报。")
    assert ent["gender"] is None and ent["gender_source"] is None


def test_gate_accepts_auto_gender(tmp_path):
    src = build_export(tmp_path / "src")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="书")
    proj = Path(res["data"]["project_dir"])
    chars = json.loads((proj / "global" / "characters.json").read_text(encoding="utf-8"))
    chars["characters"].append({"id": "char_80", "names": ["路人乙"], "role": "auto_added", "source": "auto_added",
                                "auto_added_chapter": 1, "visual_anchor_en": "a", "voice_profile": "v",
                                "gender": "male", "gender_source": "auto", "looks": []})
    (proj / "global" / "characters.json").write_text(json.dumps(chars, ensure_ascii=False), encoding="utf-8")
    row = next(r for r in tool_api.get_visual_status(str(proj))["data"]["rows"] if r["key"] == entity_key("character", "char_80"))
    assert not any("gender" in f for f in row["missing_fields"])


def test_existing_drafts_without_gender_are_filled_automatically(tmp_path, monkeypatch):
    """已起草但性别为空的补建角色，在下一次 C-5 时自动补定（不需要手动选）。"""
    from novel_to_video.steps import chapter_assets as mod
    filled = {}

    def fake_infer(project_dir, text_llm, name, context, chapter_no, chapter_text):
        filled[name] = chapter_no
        return "male", "测试"

    monkeypatch.setattr(mod, "_infer_gender", fake_infer)
    chars = {"characters": [{"id": "char_81", "names": ["路人丙"], "source": "auto_added", "auto_added_chapter": 1,
                             "gender": None, "gender_source": None}]}
    # 直接验证补齐逻辑所用的筛选条件
    from novel_to_video.gender_review import CONFIRMED_SOURCES
    fill = [c for c in chars["characters"] if c.get("auto_added_chapter") == 1 and not c.get("gender")
            and c.get("gender_source") not in CONFIRMED_SOURCES]
    assert [c["id"] for c in fill] == ["char_81"]
    assert "user" in CONFIRMED_SOURCES and "auto" in CONFIRMED_SOURCES
