"""R15：逐章生成全部分镜——批量函数（跳过/生成/失败隔离/停止）与界面按钮。"""
import time

import pytest

from article_to_video import cancel, joblog
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import load_spine
from article_to_video.visual import storyboard as sbm
from ui import jobs
from helpers_p4 import scripted_project


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    d, _spine = scripted_project(tmp_path)
    return d


def _part_ids(pdir):
    return [p["id"] for p in load_spine(pdir)["parts"]]


def _msgs(pdir):
    return [r["message"] for r in joblog.tail(pdir, 500, ["storyboard"])]


def _run(pdir, **kw):
    with joblog.bind(pdir, "storyboard"):
        return sbm.plan_all_storyboards(pdir, **kw)


def test_batch_generates_every_chapter_with_confirmed_script(pdir):
    res = _run(pdir)
    ids = _part_ids(pdir)
    assert res["generated"] == len(ids) and res["skipped"] == 0 and res["failed"] == 0
    for pid in ids:
        assert sbm.load_storyboard(pdir, pid) is not None
    msgs = _msgs(pdir)
    assert any("批量分镜：共" in m for m in msgs)
    assert any("批量分镜结束：生成" in m for m in msgs)


def test_second_batch_skips_current_chapters_and_keeps_edits(pdir):
    _run(pdir)
    pid = _part_ids(pdir)[0]
    sb = sbm.load_storyboard(pdir, pid)
    sb["scenes"][0]["emphasis"] = ["手工编辑"]
    from article_to_video.project_store import write_json_atomic
    write_json_atomic(sbm._sb_path(pdir, pid), sb)

    res = _run(pdir)
    assert res["generated"] == 0 and res["skipped"] == len(_part_ids(pdir))
    assert sbm.load_storyboard(pdir, pid)["scenes"][0]["emphasis"] == ["手工编辑"]


def test_force_regenerates_all_and_overwrites_edits(pdir):
    _run(pdir)
    res = _run(pdir, force=True)
    assert res["generated"] == len(_part_ids(pdir))


def test_unconfirmed_script_is_skipped_with_reason(pdir):
    from article_to_video.script.writer import load_script
    from article_to_video.project_store import write_json_atomic
    pid = _part_ids(pdir)[-1]
    s = load_script(pdir, pid)
    s["confirmed"] = False
    write_json_atomic(pdir / "parts" / pid / "script.json", s)
    res = _run(pdir)
    row = next(r for r in res["results"] if r["part"] == pid)
    assert row["action"] == "skipped" and row["reason"] == "口播稿未确认"


def test_one_failing_chapter_does_not_stop_others(pdir, monkeypatch):
    ids = _part_ids(pdir)
    bad = ids[1]
    real = sbm.plan_storyboard

    def flaky(pdir_, pid, **kw):
        if pid == bad:
            raise PlanError(f"{pid} 分镜模型输出 3 次仍不合格：测试")
        return real(pdir_, pid, **kw)

    monkeypatch.setattr(sbm, "plan_storyboard", flaky)
    with pytest.raises(PlanError) as ei:
        _run(pdir)
    assert bad in str(ei.value)
    for pid in ids:
        if pid != bad:
            assert sbm.load_storyboard(pdir, pid) is not None
    assert sbm.load_storyboard(pdir, bad) is None
    assert any("分镜生成失败" in m for m in _msgs(pdir))


def test_stop_request_halts_before_next_chapter(pdir, monkeypatch):
    ids = _part_ids(pdir)
    real = sbm.plan_storyboard
    calls = []

    def stopping(pdir_, pid, **kw):
        calls.append(pid)
        out = real(pdir_, pid, **kw)
        if pid == ids[0]:
            raise cancel.Cancelled()
        return out

    monkeypatch.setattr(sbm, "plan_storyboard", stopping)
    with pytest.raises(cancel.Cancelled):
        _run(pdir)
    assert calls == [ids[0]]


def test_batch_button_runs_as_background_job(pdir):
    """界面：点击「生成全部章节分镜」启动后台任务，完成后每章都有分镜。"""
    job = None
    ok = jobs.start(pdir, "批量测试", lambda: sbm.plan_all_storyboards(pdir), scope="storyboard")
    assert ok
    t0 = time.time()
    while time.time() - t0 < 60:
        job = jobs.status(pdir)
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done", job
    assert all(sbm.load_storyboard(pdir, pid) is not None for pid in _part_ids(pdir))
