"""驾驶舱测试：下一步优先级、停止请求、后台运行与锁、驾驶舱数据、页面渲染。"""

import json
import time
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import batch, cockpit, gender_review, runlock, runtime
from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv

CH = "# 第{n}章 测试章{n}\n\n铁城的清晨很安静。\n\n林远低声说：“我们走。”\n"


# ---------------------------------------------------------------- 下一步优先级（纯函数）

def _data(**over):
    base = {
        "selected": [1], "chapters": [], "run": {"busy": False},
        "gender": {"pending": 0}, "assets": {"ready": True, "not_ready": 0},
        "final": {"exists": False, "stale": False, "confirmed": False},
    }
    base.update(over)
    return base


def test_next_step_select_when_nothing_selected():
    assert cockpit.next_step(_data(selected=[]))["key"] == "select"


def test_next_step_running_takes_priority_over_everything():
    d = _data(run={"busy": True, "message": "第 1 章"}, gender={"pending": 3})
    assert cockpit.next_step(d)["key"] == "running"


def test_next_step_failed_before_gender_and_assets():
    d = _data(chapters=[{"no": 2, "status": "failed", "waiting": 0}], gender={"pending": 3},
              assets={"ready": False, "not_ready": 4})
    assert cockpit.next_step(d)["key"] == "fix"


def test_next_step_gender_before_assets_before_review():
    d = _data(gender={"pending": 2}, assets={"ready": False, "not_ready": 1},
              chapters=[{"no": 1, "status": "waiting_review", "waiting": 3}])
    assert cockpit.next_step(d)["key"] == "gender"
    d = _data(assets={"ready": False, "not_ready": 1},
              chapters=[{"no": 1, "status": "waiting_review", "waiting": 3}])
    assert cockpit.next_step(d)["key"] == "assets"


def test_next_step_review_then_run_then_confirm_then_done():
    review = _data(chapters=[{"no": 1, "status": "waiting_review", "waiting": 2}])
    assert cockpit.next_step(review)["key"] == "review"
    run = _data(chapters=[{"no": 1, "status": "pending", "waiting": 0}])
    assert cockpit.next_step(run)["key"] == "run"
    done = _data(chapters=[{"no": 1, "status": "done", "waiting": 0}])
    assert cockpit.next_step(done)["key"] == "confirm"
    done["final"]["confirmed"] = True
    assert cockpit.next_step(done)["key"] == "compose"
    done["final"].update(exists=True)
    assert cockpit.next_step(done)["key"] == "done"


# ---------------------------------------------------------------- 项目夹具

@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1, 2))
    for n in (1, 2):
        (src / "正文" / f"第{n:03d}章 测试章{n}.md").write_text(CH.format(n=n), encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    gender_review.confirm(p, "char_002", "female")
    for kind, eid in [("character", "char_001"), ("character", "char_002"), ("location", "loc_01")]:
        gv.approve(p, kind, eid, "text")
        gv.approve(p, kind, eid, "portrait")
    tool_api.set_gear(str(p), "G0")
    return p


def test_fresh_project_cockpit_reports_next_step(proj):
    d = tool_api.cockpit_status(str(proj))["data"]
    assert d["selected"] == [1, 2]
    assert d["next"]["key"] == "run"  # 素材与性别已完成，剩下运行队列
    assert d["run"]["busy"] is False and d["run"]["state"] == "idle"
    assert d["final"]["confirmed"] is False


def test_pending_gender_is_next_when_not_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    res = tool_api.create_project_from_source(str(tmp_path / "o"), str(src), title="t")
    p = Path(res["data"]["project_dir"])
    d = tool_api.cockpit_status(str(p))["data"]
    assert d["next"]["key"] == "gender"


# ---------------------------------------------------------------- 停止请求

def test_stop_request_halts_before_next_chapter(proj):
    seen = []

    def run_one(no):
        seen.append(no)
        if no == 1:
            runtime.request_stop(proj)  # 第 1 章运行中请求停止
        return {"status": "waiting_review"}

    res = batch.run_queue(proj, [1, 2], gear="G0", run_one=run_one)
    assert res["halted"] == "用户请求停止"
    assert seen == [1]
    assert list(res["results"]) == [1]


def test_stop_without_running_is_refused(proj):
    r = tool_api.stop_queue(str(proj))
    assert not r["ok"]


# ---------------------------------------------------------------- 后台运行与锁

def test_background_run_refused_while_locked(proj):
    runlock.acquire(proj)
    try:
        r = tool_api.start_queue_background(str(proj), [1])
        assert not r["ok"] and "正在运行" in r["error"]["message"]
    finally:
        runlock.release(proj)


def test_background_run_completes_and_releases_lock(proj):
    r = tool_api.start_queue_background(str(proj), [1], gear="G0")
    assert r["ok"], r
    deadline = time.time() + 180
    st = {}
    while time.time() < deadline:
        st = runtime.read_state(proj)
        if st.get("state") in ("done", "halted", "failed"):
            break
        time.sleep(0.5)
    assert st.get("state") == "done", st
    assert st["results"] == {"1": "waiting_review"}
    assert not runlock.inspect(proj)["held"]
    hist = st.get("history", [])
    assert hist and hist[-1]["chapter"] == 1


def test_cockpit_shows_run_history_and_chapter(proj):
    runtime.update(proj, state="running", chapter=2, phase="running", message="正在运行第 2 章", history=[])
    runlock.acquire(proj)
    try:
        d = tool_api.cockpit_status(str(proj))["data"]
        assert d["run"]["busy"] is True and d["run"]["chapter"] == 2
        assert d["next"]["key"] == "running"
    finally:
        runlock.release(proj)
        runtime.update(proj, state="idle")


# ---------------------------------------------------------------- 驾驶舱页面

def test_cockpit_page_renders(tmp_path, monkeypatch, proj):
    from streamlit.testing.v1 import AppTest
    app = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app, default_timeout=120).run()
    assert not at.exception
    at.text_input(key="ck_root").input(str(proj.parent)).run()
    assert not at.exception
    assert any("驾驶舱" in h.value for h in at.header)
    assert any("当前步骤" in m.label for m in at.metric)
