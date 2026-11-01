import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import project_store as ps
from novel_to_video import tool_api
from novel_to_video.providers.image import ImageResult, StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv
from novel_to_video.review import load_review


class FakeLLM:
    name = "fake"

    def __init__(self, bad_ids=(), fail_on_entity=None):
        self.bad_ids = set(bad_ids)
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        if "RULE_INPUT=" in prompt:
            info = json.loads(prompt.split("RULE_INPUT=", 1)[1])
            if info["name"] in self.bad_ids or info.get("kind") == "location" and info["name"] in self.bad_ids:
                return "not json at all"
            if info["kind"] == "location":
                return json.dumps({"visual_anchor_en": "rusted dome city street, overcast, [fake]"})
            return json.dumps({"gender": info.get("gender") or "female",
                               "visual_anchor_en": "young adult woman, slim, short dark hair, [fake]",
                               "voice_profile": "清亮，语速偏快"}, ensure_ascii=False)
        return "{}"


class FailingImage:
    name = "failing"

    def generate(self, prompt, save_path, *, ratio=None, size="1024x1024"):
        return ImageResult(False, error="HTTP 500：boom", non_retryable=False)


class FatalImage:
    name = "fatal"

    def generate(self, prompt, save_path, *, ratio=None, size="1024x1024"):
        return ImageResult(False, error="参数错误（HTTP 422）", non_retryable=True)


@pytest.fixture
def project(tmp_path):
    src = build_export(tmp_path / "src")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    return Path(res["data"]["project_dir"])


def _chars(proj):
    return {c["id"]: c for c in json.loads((proj / "global/characters.json").read_text(encoding="utf-8"))["characters"]}


def test_text_pass_fills_fields_and_marks_source(project):
    summary = gv.visual_text_pass(project, FakeLLM())
    assert set(summary["done"]) == {"char_001", "char_002", "loc_01"}
    chars = _chars(project)
    assert chars["char_001"]["visual_anchor_en"].startswith("young adult woman")
    assert chars["char_001"]["voice_profile"] == "清亮，语速偏快"
    # char_001 性别来自人设卡，不应被 LLM 覆盖
    assert chars["char_001"]["gender"] == "male"
    assert chars["char_001"].get("gender_source") is None
    # char_002 性别缺失，由 LLM 补全并标注来源
    assert chars["char_002"]["gender"] == "female"
    assert chars["char_002"]["gender_source"] == "fake"
    review = load_review(project)["items"]
    assert review["character:char_002"]["text_status"] == "pending"


def test_text_pass_is_resumable(project):
    gv.visual_text_pass(project, FakeLLM())
    llm = FakeLLM()
    summary = gv.visual_text_pass(project, llm)
    assert summary["done"] == [] and llm.calls == 0
    assert len(summary["skipped"]) == 3


def test_text_pass_isolates_failures(project):
    summary = gv.visual_text_pass(project, FakeLLM(bad_ids={"影子"}))
    assert [f["id"] for f in summary["failed"]] == ["char_002"]
    assert "char_001" in summary["done"] and "loc_01" in summary["done"]
    item = load_review(project)["items"]["character:char_002"]
    assert item["last_error"].startswith("G-2 失败")
    assert "char_002" not in [c["id"] for c in _chars(project).values() if c.get("voice_profile")]


def test_rule_llm_produces_reviewable_placeholder(project):
    summary = gv.visual_text_pass(project, RuleTextLLM())
    assert len(summary["done"]) == 3
    chars = _chars(project)
    assert "needs manual review" in chars["char_002"]["visual_anchor_en"]
    assert chars["char_002"]["visual_anchor_source"] == "rules"


def test_portrait_pass_and_approval_gate(project, tmp_path):
    gv.visual_text_pass(project, FakeLLM())
    st = gv.visual_status(project)
    assert st["ready"] is False

    summary = gv.portrait_pass(project, StubImageProvider())
    assert set(summary["done"]) == {"char_001", "char_002", "loc_01"}
    assert (project / _chars(project)["char_001"]["asset_path"]).is_file()

    for kind, eid in [("character", "char_001"), ("character", "char_002"), ("location", "loc_01")]:
        gv.approve(project, kind, eid, "text")
        gv.approve(project, kind, eid, "portrait")
    st = gv.visual_status(project)
    assert st["ready"] is True
    assert st["counts"] == {"total": 3, "approved": 3}


def test_portrait_failure_recorded_and_retry_skips_done(project):
    gv.visual_text_pass(project, FakeLLM())
    summary = gv.portrait_pass(project, FailingImage())
    assert len(summary["failed"]) == 3 and summary["stopped"] is None
    assert load_review(project)["items"]["character:char_001"]["portrait_status"] == "failed"

    summary2 = gv.portrait_pass(project, StubImageProvider())
    assert len(summary2["done"]) == 3


def test_non_retryable_error_stops_run(project):
    gv.visual_text_pass(project, FakeLLM())
    summary = gv.portrait_pass(project, FatalImage())
    assert summary["stopped"] is not None
    assert len(summary["failed"]) == 1


def test_regenerate_bumps_version_and_filename(project):
    gv.visual_text_pass(project, FakeLLM())
    gv.portrait_pass(project, StubImageProvider())
    first = _chars(project)["char_001"]["asset_path"]
    gv.portrait_pass(project, StubImageProvider(), ids=["char_001"], regenerate=True)
    second = _chars(project)["char_001"]["asset_path"]
    assert first != second and second.endswith("_v2.png")
    assert load_review(project)["items"]["character:char_001"]["portrait_version"] == 2


def test_manual_edit_marks_portrait_stale(project):
    gv.visual_text_pass(project, FakeLLM())
    gv.portrait_pass(project, StubImageProvider())
    gv.approve(project, "character", "char_001", "portrait")
    gv.update_entity(project, "character", "char_001", {"visual_anchor_en": "older man, grey coat"})
    item = load_review(project)["items"]["character:char_001"]
    assert item["portrait_status"] == "stale"
    assert _chars(project)["char_001"]["visual_anchor_source"] == "fake"  # 来源不被改写
    assert _chars(project)["char_001"]["visual_anchor_en"] == "older man, grey coat"


def test_manual_edit_rejects_protected_fields(project):
    with pytest.raises(gv.StepError):
        gv.update_entity(project, "character", "char_001", {"names": ["改名"]})
    with pytest.raises(gv.StepError):
        gv.update_entity(project, "character", "char_001", {"gender": "robot"})


def test_api_calls_are_logged_without_keys(project):
    gv.visual_text_pass(project, FakeLLM())
    gv.portrait_pass(project, StubImageProvider())
    log = (project / "logs/api_calls.jsonl").read_text(encoding="utf-8")
    assert '"kind": "text"' in log and '"kind": "image"' in log
    assert "Bearer" not in log and "api_key" not in log.lower()


def test_tool_api_flow_with_stub_env(project, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    r1 = tool_api.run_visual_text(str(project))
    assert r1["ok"], r1
    r2 = tool_api.run_portraits(str(project))
    assert r2["ok"] and len(r2["data"]["done"]) == 3
    status = tool_api.get_visual_status(str(project))
    assert status["ok"] and status["data"]["api_calls"]["image"] == 3
    res = tool_api.review_asset(str(project), "character", "char_001", "approve", "text")
    assert res["ok"]
    bad = tool_api.edit_asset(str(project), "character", "char_001", {"names": ["x"]})
    assert not bad["ok"]
