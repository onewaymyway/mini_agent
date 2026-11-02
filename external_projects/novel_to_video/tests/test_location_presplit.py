"""地点细化 L4：区域预拆解（提议 → 用户确认后写入；默认关闭）。"""
import json
from pathlib import Path

import pytest

from novel_to_video import tool_api
from novel_to_video.steps import location_presplit as lp
from novel_to_video.steps.global_visual import StepError

from test_chapter_pipeline import project  # noqa: F401  (fixture)


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        if "TASK=presplit" in prompt:
            return json.dumps({"spots": [
                {"name": "市政厅办公室", "spot_kind": "内景", "reason": "剧本多次在此对话"},
                {"name": "东三街", "spot_kind": "exterior", "reason": "追逐戏发生地"},
                {"name": "东三街", "spot_kind": "exterior", "reason": "重复项，应去掉"},
                {"name": "", "reason": "空名称，应去掉"},
            ]}, ensure_ascii=False)
        return json.dumps({"visual_anchor_en": "steampunk street, brass pipes, grey fog"}, ensure_ascii=False)


def _area(proj):
    locs = json.loads((proj / "global" / "locations.json").read_text(encoding="utf-8"))["locations"]
    return next(l for l in locs if (l.get("level") or "area") == "area")


def _locs(proj):
    return json.loads((proj / "global" / "locations.json").read_text(encoding="utf-8"))["locations"]


def test_enabled_by_default(project):  # noqa: F811
    area = _area(project)
    cands = lp.propose_spots(project, area["id"], FakeLLM())
    assert [c["name"] for c in cands] == ["市政厅办公室", "东三街"]


def test_disabled_refuses_and_does_not_call_model(project):  # noqa: F811
    assert tool_api.set_location_presplit(str(project), False)["ok"]
    area = _area(project)
    llm = FakeLLM()
    with pytest.raises(StepError):
        lp.propose_spots(project, area["id"], llm)
    assert llm.calls == 0


def test_propose_returns_candidates_without_writing(project):  # noqa: F811
    area = _area(project)
    before = (project / "global" / "locations.json").read_text(encoding="utf-8")
    cands = lp.propose_spots(project, area["id"], FakeLLM())
    assert [c["name"] for c in cands] == ["市政厅办公室", "东三街"]          # 去重、去空
    assert cands[0]["spot_kind"] == "interior" and cands[1]["spot_kind"] == "exterior"
    assert (project / "global" / "locations.json").read_text(encoding="utf-8") == before   # 候选不落盘


def test_confirm_writes_spots_under_area_with_anchor_and_review(project):  # noqa: F811
    assert tool_api.set_location_presplit(str(project), True)["ok"]
    area = _area(project)
    cands = lp.propose_spots(project, area["id"], FakeLLM())
    added = lp.confirm_spots(project, area["id"], cands, FakeLLM())
    assert len(added) == 2
    new = [l for l in _locs(project) if l["id"] in added]
    for l in new:
        assert l["level"] == "spot" and l["parent_id"] == area["id"] and l["source"] == "auto_added"
        assert l["visual_anchor_en"]
    assert {l["spot_kind"] for l in new} == {"interior", "exterior"}
    review = json.loads((project / "global" / "review.json").read_text(encoding="utf-8"))
    assert all(review["items"][f"location:{i}"]["text_status"] in ("pending", "approved") for i in added)


def test_confirm_twice_does_not_duplicate(project):  # noqa: F811
    assert tool_api.set_location_presplit(str(project), True)["ok"]
    area = _area(project)
    cands = lp.propose_spots(project, area["id"], FakeLLM())
    first = lp.confirm_spots(project, area["id"], cands, FakeLLM())
    second = lp.confirm_spots(project, area["id"], cands, FakeLLM())
    assert len(first) == 2 and second == []


def test_rejects_non_area_parent(project):  # noqa: F811
    assert tool_api.set_location_presplit(str(project), True)["ok"]
    area = _area(project)
    assert tool_api.set_location_presplit(str(project), True)["ok"]
    lp.confirm_spots(project, area["id"], [{"name": "甲处", "spot_kind": "interior", "reason": ""}], FakeLLM())
    spot_id = next(l["id"] for l in _locs(project) if l.get("name") == "甲处")
    with pytest.raises(StepError):
        lp.propose_spots(project, spot_id, FakeLLM())
    res = tool_api.presplit_propose(str(project), spot_id)
    assert res["ok"] is False


def test_tool_api_round_trip(project):  # noqa: F811
    assert tool_api.set_location_presplit(str(project), True)["ok"]
    area = _area(project)
    from novel_to_video.steps import location_presplit as m
    orig = m.propose_spots
    try:
        m.propose_spots = lambda pdir, aid, llm: [{"name": "码头仓库", "spot_kind": "exterior", "reason": "x"}]
        m_confirm = m.confirm_spots
        res = tool_api.presplit_propose(str(project), area["id"], text_provider="rules")
        assert res["ok"] and res["data"]["candidates"][0]["name"] == "码头仓库"
    finally:
        m.propose_spots = orig
