"""G6 全流程自动：导入后的全书素材无需任何人工操作，队列自动补设定、通过、生成定妆图并跑完各章。

对照：同样的项目在 G3 会停在「缺少定妆图」（G3 的自动只覆盖补建角色与镜头审核）。
"""
import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import auto_prepare as ap
from novel_to_video import batch, gates
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.review import entity_key, get_item, load_review

CH = "# 第{n}章 测试章{n}\n\n铁城的清晨很安静。\n\n林远低声说：“我们走。”\n"
SCRIPT = "### 场次 1｜铁城·清晨（外景）\n旁白：铁城的清晨很安静。\n林远：我们走。\n"
SHOTS = {"shots": [{"scene_no": 1, "location_id": "loc_01", "character_ids": ["char_001"],
                    "blocks": [{"type": "narration", "text": "铁城的清晨很安静。",
                                "speaker_id": None, "speaker_name": None},
                               {"type": "dialogue", "text": "我们走。",
                                "speaker_id": "char_001", "speaker_name": "林远"}],
                    "visual_hint_zh": "清晨", "visual_hint_en": "dawn street"}]}


class FakeLLM:
    name = "fake"

    def ask(self, prompt, system=""):
        if "TASK=script" in prompt:
            return SCRIPT
        if "TASK=shots" in prompt:
            return json.dumps(SHOTS, ensure_ascii=False)
        return RuleTextLLM().ask(prompt, system)


@pytest.fixture
def raw_proj(tmp_path, monkeypatch):
    """导入后什么素材工作都没做（没有 G-2、没有定妆图）。"""
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1, 2))
    for n in (1, 2):
        (src / "正文" / f"第{n:03d}章 测试章{n}.md").write_text(CH.format(n=n), encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    return Path(res["data"]["project_dir"])


def _run_one(p):
    from novel_to_video import chapter_pipeline as cp
    from novel_to_video.providers.tts import SilenceTTS
    from novel_to_video.providers.video import StubVideoProvider

    def run(no):
        return cp.run_chapter(p, no, text_llm=FakeLLM(), image=StubImageProvider(),
                              video=StubVideoProvider(), tts=SilenceTTS())
    return run


BUDGET = {"max_video_calls": 99, "max_image_calls": 99, "max_minutes": 99}


def test_g6_policy_and_budget():
    assert "G6" in gates.GEARS and gates.is_auto("G6", "shot_review") and gates.is_auto("G6", "asset_portrait")
    assert gates.is_auto("G6", "final_compose") is False          # 全书合成仍须人工确认
    assert gates.RETRY_LIMITS["G6"] is None                         # G6 不设固定重试上限（不跳过失败章节）
    assert gates.retry_limit("G6", {}) is None
    assert gates.retry_limit("G6", {"max_chapter_retries": 7}) == 7
    assert gates.retry_limit("G6", {"max_chapter_retries": 0}) is None   # 0 = 不限制
    assert gates.retry_limit("G5", {"max_chapter_retries": 7}) == 3      # 上限设置只对 G6 生效
    with pytest.raises(gates.GateError, match="G6 挡位必须设置预算上限"):
        gates.validate_budget({}, "G6")


def test_g3_stops_without_global_assets(raw_proj):
    """对照：G3 不会替用户补全书素材，章节停在缺定妆图。"""
    assert tool_api.set_gear(str(raw_proj), "G3", BUDGET)["ok"]
    res = batch.run_queue(raw_proj, [1], gear="G3", run_one=_run_one(raw_proj))
    assert res["results"][1]["status"] == "waiting_assets"


def test_g6_prepares_assets_and_runs_all_chapters(raw_proj):
    assert tool_api.set_gear(str(raw_proj), "G6", BUDGET)["ok"]
    prepare = lambda no: ap.prepare_chapter_assets(raw_proj, no, FakeLLM(), StubImageProvider())
    res = batch.run_queue(raw_proj, [1, 2], gear="G6", run_one=_run_one(raw_proj), prepare=prepare)
    assert res["halted"] is None
    assert [res["results"][n]["status"] for n in (1, 2)] == ["done", "done"]
    assert res["results"][1]["auto_prepared"]["progress"] is True
    assert res["results"][2].get("auto_prepared") is None          # 第 2 章素材已齐，不再重复补
    review = load_review(raw_proj)
    item = get_item(review, entity_key("character", "char_001"))
    assert item["text_status"] == "approved" and item["portrait_status"] == "approved"
    for n in (1, 2):
        assert (raw_proj / "chapters" / f"{n:03d}" / f"chapter_{n:03d}.mp4").is_file()


def test_g6_stops_when_prepare_makes_no_progress(raw_proj):
    assert tool_api.set_gear(str(raw_proj), "G6", BUDGET)["ok"]
    calls = []

    def prepare(no):
        calls.append(no)
        return {"progress": False}

    res = batch.run_queue(raw_proj, [1], gear="G6", run_one=_run_one(raw_proj), prepare=prepare)
    assert res["results"][1]["status"] == "waiting_assets"
    assert len(calls) == 1                                          # 没进展就不空转


def test_g6_prepare_respects_budget(raw_proj):
    assert tool_api.set_gear(str(raw_proj), "G6", {**BUDGET, "max_image_calls": 0})["ok"]
    called = []
    res = batch.run_queue(raw_proj, [1], gear="G6", run_one=_run_one(raw_proj),
                          prepare=lambda n: called.append(n) or {"progress": True})
    assert not called                                               # 图像预算为 0：队列在开章前就停止
    assert res["halted"] and "预算已用尽" in res["halted"]
