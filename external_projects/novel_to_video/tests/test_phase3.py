"""Phase 3 测试：挡位、预算、批量队列、分级回退与 stale 传播、补建审核数据。"""

import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import gates
from novel_to_video import project_store as ps
from novel_to_video import revision as rev
from novel_to_video import shot_review as sr
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv


# ---------------------------------------------------------------- 挡位策略（纯函数）

def test_gear_policy_monotonic_auto():
    # G 越高，自动检查点只增不减（G0→G3）
    for cp in ("asset_text", "chapter_script", "shot_review", "chapter_compose", "auto_add"):
        seen = [gates.is_auto(g, cp) for g in gates.GEARS]
        assert seen == sorted(seen), cp


def test_shot_review_never_auto_below_g3():
    assert not gates.is_auto("G0", "shot_review")
    assert not gates.is_auto("G1", "shot_review")
    assert not gates.is_auto("G2", "shot_review")
    assert gates.is_auto("G3", "shot_review")


def test_final_compose_always_manual():
    for g in gates.GEARS:
        assert gates.is_auto(g, "final_compose") is False


def test_unknown_gear_rejected():
    with pytest.raises(gates.GateError):
        gates.normalize_gear("G9")


def test_g3_requires_full_budget():
    with pytest.raises(gates.GateError, match="预算上限"):
        gates.validate_budget({"max_video_calls": 5}, "G3")
    gates.validate_budget({"max_video_calls": 5, "max_image_calls": 5, "max_minutes": 10}, "G3")
    gates.validate_budget({}, "G2")  # 非 G3 不强制


def test_budget_exhaustion_blocks():
    b = {"max_video_calls": 2, "max_image_calls": 2, "max_minutes": 10}
    with pytest.raises(gates.GateError, match="video"):
        gates.require_budget(b, {"video": 2, "image": 0}, 0, "G3")
    with pytest.raises(gates.GateError, match="耗时"):
        gates.require_budget(b, {"video": 0, "image": 0}, 10, "G3")
    gates.require_budget(b, {"video": 1, "image": 1}, 9.9, "G3")


# ---------------------------------------------------------------- 项目夹具（3 章）

CH = "# 第{n}章 测试章{n}\n\n铁城的清晨很安静。\n\n林远低声说：“我们走。”\n"
SCRIPT = "### 场次 1｜铁城·清晨（外景）\n旁白：铁城的清晨很安静。\n林远：我们走。\n"
SHOTS = {"shots": [{"scene_no": 1, "location_id": "loc_01", "character_ids": [],
                    "blocks": [{"type": "narration", "text": "铁城的清晨很安静。",
                                "speaker_id": None, "speaker_name": None},
                               {"type": "dialogue", "text": "我们走。",
                                "speaker_id": None, "speaker_name": "林远"}],
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
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1, 2, 3))
    for n in (1, 2, 3):
        (src / "正文" / f"第{n:03d}章 测试章{n}.md").write_text(CH.format(n=n), encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    return p


def _set_gear(p, gear, budget=None):
    r = tool_api.set_gear(str(p), gear, budget)
    assert r["ok"], r
    return r


def _fake_run_one(p):
    """以 Phase 2 的真实流水线运行一章，但使用假 LLM 与 stub 媒体。"""
    from novel_to_video import chapter_pipeline as cp
    from novel_to_video.providers.tts import SilenceTTS
    from novel_to_video.providers.video import StubVideoProvider

    def run(no):
        return cp.run_chapter(p, no, text_llm=FakeLLM(), image=StubImageProvider(),
                              video=StubVideoProvider(), tts=SilenceTTS())
    return run


# ---------------------------------------------------------------- 批量与挡位

def test_g0_stops_at_shot_review(proj):
    _set_gear(proj, "G0")
    from novel_to_video import batch
    res = batch.run_queue(proj, [1], gear="G0", run_one=_fake_run_one(proj))
    assert res["results"][1]["status"] == "waiting_review"
    assert res["halted"] is None


def test_g3_auto_approves_and_completes_chapter(proj):
    _set_gear(proj, "G3", {"max_video_calls": 50, "max_image_calls": 50, "max_minutes": 60})
    from novel_to_video import batch
    res = batch.run_queue(proj, [1], gear="G3", run_one=_fake_run_one(proj))
    r = res["results"][1]
    assert r["status"] == "done", r
    assert r["auto_approved"] == ["s01"]
    assert (proj / "chapters" / "001" / "chapter_001.mp4").is_file()


def test_budget_halts_remaining_chapters(proj):
    _set_gear(proj, "G3", {"max_video_calls": 0, "max_image_calls": 50, "max_minutes": 60})
    from novel_to_video import batch
    res = batch.run_queue(proj, [1, 2, 3], gear="G3", run_one=lambda n: {"status": "waiting_review"})
    assert res["halted"] and "预算已用尽" in res["halted"]
    assert res["results"] == {}


def test_queue_continues_past_failed_chapter(proj):
    from novel_to_video import batch
    seq = {1: {"status": "failed", "message": "x"}, 2: {"status": "waiting_review"}}
    res = batch.run_queue(proj, [1, 2], gear="G0", run_one=lambda n: seq[n])
    assert res["results"][1]["status"] == "failed"
    assert res["results"][2]["status"] == "waiting_review"


def test_g3_refuses_to_run_without_budget(proj):
    r = tool_api.set_gear(str(proj), "G3", {"max_video_calls": None})
    assert not r["ok"]
    assert ps.read_json(proj / "project.json")["gear"] == "G0"


# ---------------------------------------------------------------- 分级回退与 stale

def _complete_chapter(p, no):
    from novel_to_video import batch
    r = batch.run_queue(p, [no], gear="G0", run_one=_fake_run_one(p))["results"][no]
    assert r["status"] == "waiting_review", r
    for sh in cio.load_shots(p, no)["shots"]:
        sr.approve(p, no, sh["id"])
    done = cp_run(p, no)
    assert done["status"] == "done", done
    return done


def cp_run(p, no):
    from novel_to_video import chapter_pipeline as cp
    from novel_to_video.providers.tts import SilenceTTS
    from novel_to_video.providers.video import StubVideoProvider
    return cp.run_chapter(p, no, text_llm=FakeLLM(), image=StubImageProvider(),
                          video=StubVideoProvider(), tts=SilenceTTS())


def test_editing_chapter2_does_not_touch_chapters_1_and_3(proj):
    _complete_chapter(proj, 1)
    _complete_chapter(proj, 3)
    _complete_chapter(proj, 2)
    snap = {n: (cio.chapter_dir(proj, n) / f"chapter_{n:03d}.mp4").read_bytes() for n in (1, 3)}
    st1 = cio.load_state(proj, 1)
    st3 = cio.load_state(proj, 3)
    out = tool_api.invalidate_shot(str(proj), 2, "s01", "clip_only")
    assert out["ok"], out
    assert out["data"]["chapter_stale"] is True
    # 1、3 章的成片、状态、审核记录完全不变
    for n in (1, 3):
        assert (cio.chapter_dir(proj, n) / f"chapter_{n:03d}.mp4").read_bytes() == snap[n]
    assert cio.load_state(proj, 1) == st1
    assert cio.load_state(proj, 3) == st3
    assert cio.chapter_row(proj, 1)["status"] == "done"
    assert cio.chapter_row(proj, 3)["status"] == "done"
    assert cio.chapter_row(proj, 2)["status"] == "stale"


def test_clip_only_keeps_audio_and_prompt(proj):
    _complete_chapter(proj, 1)
    cdir = cio.chapter_dir(proj, 1)
    before = cio.load_shots(proj, 1)["shots"][0]
    audio_before = (cdir / "audio" / "sh_001.wav").is_file()
    rev.invalidate(proj, 1, "s01", "clip_only")
    after = cio.load_shots(proj, 1)["shots"][0]
    assert not (cdir / "clips" / "sh_001.mp4").exists()
    assert after["prompt_en"] == before["prompt_en"]
    assert (cdir / "audio" / "sh_001.wav").is_file() == audio_before


def test_dialogue_change_clears_audio_and_prompt(proj):
    _complete_chapter(proj, 1)
    cdir = cio.chapter_dir(proj, 1)
    rev.invalidate(proj, 1, "s01", "dialogue")
    sh = cio.load_shots(proj, 1)["shots"][0]
    assert not (cdir / "audio" / "sh_001.wav").exists()
    assert sh["prompt_en"] is None and sh["clip_key"] is None
    assert cio.load_state(proj, 1)["stages"]["script"]["status"] == "stale"


def test_shot_plan_truncates_tail(proj):
    _complete_chapter(proj, 1)
    shots = cio.load_shots(proj, 1)
    shots["shots"].append({**shots["shots"][0], "id": "s02"})
    cio.save_shots(proj, 1, shots)
    rev.invalidate(proj, 1, "s01", "shot_plan")
    after = cio.load_shots(proj, 1)
    assert after["shots"] == []
    assert after["invalidated_from"] == "s01"


def test_final_video_marked_stale_when_chapter_changes(proj):
    _complete_chapter(proj, 1)
    exp = proj / "exports"
    exp.mkdir(exist_ok=True)
    (exp / "video.mp4").write_bytes(b"x")
    assert not rev.final_is_stale(proj)
    rev.invalidate(proj, 1, "s01", "clip_only")
    assert rev.final_is_stale(proj)
    assert tool_api.final_status(str(proj))["data"]["final_stale"] is True


def test_unknown_level_rejected(proj):
    _complete_chapter(proj, 1)
    with pytest.raises(ValueError):
        rev.invalidate(proj, 1, "s01", "rewrite_all")


# ---------------------------------------------------------------- 状态视图与补建

def test_final_status_ready_only_when_all_done(proj):
    st = tool_api.final_status(str(proj))["data"]
    assert st["ready"] is False and st["pending"] == [1, 2, 3]


def test_auto_added_queue_lists_only_auto_added(proj):
    res = tool_api.auto_added_queue(str(proj))
    assert res["ok"] and res["data"] == []


def test_queue_respects_selection(proj):
    r = tool_api.set_gear(str(proj), "G0")
    assert r["ok"]
    rows = ps.load_project(proj)
    assert [c["no"] for c in rows["chapters"] if c.get("selected")] == [1, 2, 3]
