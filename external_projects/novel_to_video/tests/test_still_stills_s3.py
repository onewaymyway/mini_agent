"""动态漫模式 S3：C-8a 场景图生成与审核（桩图像接口）。"""
import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import shot_review as sr
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.steps import chapter_stills as cs
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_assets import run_assets
from novel_to_video.steps.chapter_audio import run_audio
from novel_to_video.steps.chapter_clips import run_prompts
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from test_chapter_pipeline import CHAPTER_TEXT, ChapterLLM


def _project(tmp_path, mode="still"):
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试",
                                              options={"render_mode": mode})
    assert res["ok"], res
    proj = Path(res["data"]["project_dir"])
    gv.visual_text_pass(proj, RuleTextLLM())
    gv.portrait_pass(proj, StubImageProvider())
    return proj


def _prepare(proj):
    """把第 1 章推进到镜头、配音与提示词齐备（与视频模式的单章流程相同的步骤）。"""
    llm, image = ChapterLLM(), StubImageProvider()
    assert run_script(proj, 1, llm)["status"] == "pass"
    run_shots(proj, 1, llm)
    r = run_assets(proj, 1, llm, image)                     # 第一轮：新出现的角色等待文本确认
    assert r["waiting"]
    chars = json.loads((proj / "global/characters.json").read_text(encoding="utf-8"))["characters"]
    new = next(c for c in chars if c.get("auto_added_chapter") == 1)
    gv.approve(proj, "character", new["id"], "text")
    run_assets(proj, 1, llm, image)                         # 第二轮：生成定妆图，等待确认
    gv.approve(proj, "character", new["id"], "portrait")
    assert not run_assets(proj, 1, llm, image)["waiting"]
    run_audio(proj, 1, SilenceTTS())
    run_prompts(proj, 1, None)


@pytest.fixture
def proj(tmp_path):
    p = _project(tmp_path)
    _prepare(p)
    return p


def _shots(proj):
    return cio.load_shots(proj, 1)["shots"]


def _stills_dir(proj):
    return proj / "chapters" / "001" / "stills"


# ---------- 生成

def test_stills_generated_one_per_shot_and_stored_as_versions(proj):
    r = cs.run_stills(proj, 1, StubImageProvider())
    ids = [sh["id"] for sh in _shots(proj)]
    assert r["failed"] == [] and r["generated"] == ids
    for sh in _shots(proj):
        assert sh["still_file"] == f"stills/{sh['id']}_v001.png"
        assert (proj / "chapters/001" / sh["still_file"]).is_file()
        assert sh["still_key"]
    stage = cio.load_state(proj, 1)["stages"]["stills"]
    assert stage["status"] == "done"


def test_second_run_is_idempotent(proj):
    cs.run_stills(proj, 1, StubImageProvider())
    again = cs.run_stills(proj, 1, StubImageProvider())
    assert again["generated"] == []
    assert all(p.name.endswith("_v001.png") for p in _stills_dir(proj).iterdir())


def test_stills_are_pending_until_approved(proj):
    cs.run_stills(proj, 1, StubImageProvider())
    states = sr.shot_states(proj, 1)
    assert set(states.values()) == {"pending"}


def test_references_passed_to_provider_capped_at_three(proj):
    img = StubImageProvider()
    cs.run_stills(proj, 1, img)
    assert img.reference_log and all(len(c["refs"]) <= 3 for c in img.reference_log)
    assert all(c["prompt"] for c in img.reference_log)


def test_blocked_without_portraits(proj, monkeypatch):
    monkeypatch.setattr(cs, "missing_portraits", lambda p, n: ["某地点"])
    r = cs.run_stills(proj, 1, StubImageProvider())
    assert r["blocked"] == ["某地点"] and r["generated"] == []
    assert cio.load_state(proj, 1)["stages"]["stills"]["status"] == "waiting"


def test_provider_failure_marks_shot_and_keeps_others(proj):
    class Flaky(StubImageProvider):
        def __init__(self):
            super().__init__()
            self.n = 0

        def generate(self, prompt, save_path, **kw):
            self.n += 1
            if self.n == 1:
                from novel_to_video.providers.image import ImageResult
                return ImageResult(False, error="HTTP 500")
            return super().generate(prompt, save_path, **kw)

    r = cs.run_stills(proj, 1, Flaky())
    shots = _shots(proj)
    assert r["failed"] == [shots[0]["id"]]
    assert shots[0]["still_error"] == "HTTP 500" and not shots[0].get("still_file")
    assert sr.shot_states(proj, 1)[shots[0]["id"]] == "failed"


# ---------- 审核

def test_approve_refused_before_a_scene_image_exists(proj):
    with pytest.raises(ValueError, match="场景图"):
        sr.approve(proj, 1, _shots(proj)[0]["id"])


def test_approve_then_stale_after_prompt_change_and_regenerate(proj):
    cs.run_stills(proj, 1, StubImageProvider())
    sid = _shots(proj)[0]["id"]
    sr.approve(proj, 1, sid)
    assert sr.shot_states(proj, 1)[sid] == "approved"

    sr.edit(proj, 1, sid, visual_hint_en="a different street at dusk")
    run_prompts(proj, 1, None)                              # 提示词变化
    cs.run_stills(proj, 1, StubImageProvider())             # 重新生成 → 新版本
    assert sr.shot_states(proj, 1)[sid] == "stale"
    files = sorted(p.name for p in _stills_dir(proj).iterdir() if p.name.startswith(sid))
    assert files == [f"{sid}_v001.png", f"{sid}_v002.png"]  # 旧版本保留


def test_reject_clears_pointer_keeps_history_and_regenerates_as_new_version(proj):
    cs.run_stills(proj, 1, StubImageProvider())
    sid = _shots(proj)[0]["id"]
    sr.reject(proj, 1, sid, "人物太小")
    shot = next(s for s in _shots(proj) if s["id"] == sid)
    assert shot["still_file"] is None and shot["still_key"] is None
    assert (_stills_dir(proj) / f"{sid}_v001.png").is_file()     # 旧图没有被删除
    assert sr.shot_states(proj, 1)[sid] == "missing"

    cs.run_stills(proj, 1, StubImageProvider())
    shot = next(s for s in _shots(proj) if s["id"] == sid)
    assert shot["still_file"] == f"stills/{sid}_v002.png"
    assert sr.shot_states(proj, 1)[sid] == "pending"


def test_reject_still_requires_note(proj):
    cs.run_stills(proj, 1, StubImageProvider())
    with pytest.raises(ValueError):
        sr.reject(proj, 1, _shots(proj)[0]["id"], "  ")


def test_all_approved_shows_every_shot_approved(proj):
    cs.run_stills(proj, 1, StubImageProvider())
    for sh in _shots(proj):
        sr.approve(proj, 1, sh["id"])
    assert set(sr.shot_states(proj, 1).values()) == {"approved"}


# ---------- 接口与模式边界

def test_tool_api_run_stills_on_still_project(proj):
    res = tool_api.run_stills(str(proj), 1, image_provider="stub")
    assert res["ok"], res
    assert res["data"]["failed"] == []


def test_tool_api_refuses_video_project(tmp_path):
    video = _project(tmp_path / "v", mode="video")
    res = tool_api.run_stills(str(video), 1, image_provider="stub")
    assert not res["ok"] and "动态漫" in res["error"]["message"]


def test_video_review_unchanged_for_video_projects(tmp_path):
    """视频项目仍按 clip_key / clips/ 审核：备好镜头但尚未生成视频时，全部为 missing。"""
    video = _project(tmp_path / "vv", mode="video")
    _prepare(video)
    assert sr.project_mode(video) == "video"
    states = sr.shot_states(video, 1)
    assert states and set(states.values()) == {"missing"}
    sh = _shots(video)[0]
    assert sr.asset_file(video, 1, sh) == video / "chapters/001/clips" / f"{sh['id']}.mp4"
    with pytest.raises(ValueError, match="视频"):
        sr.approve(video, 1, sh["id"])
