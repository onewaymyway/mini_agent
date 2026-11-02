"""立即停止测试：停止后不再等待阻塞调用、子进程被终止、后台任务与队列立即收尾。

全部使用本地假函数（time.sleep 模拟“长时间等待的网络请求”），不产生真实 API 调用。
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from novel_to_video import cancel
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import jobs, runtime as rt, batch
from novel_to_video.project_store import write_json_atomic

from test_chapter_pipeline import ChapterLLM, _run, project  # noqa: F401  (fixture 与辅助函数)

MAX_STOP_LATENCY = 3.0   # 停止请求到代码返回的允许延迟（秒）


def _delayed(fn, seconds, *args):
    t = threading.Timer(seconds, fn, args=args)
    t.start()
    return t


# ----------------------------------------------------------------- cancel 原语

def test_call_returns_immediately_when_stopped(tmp_path):
    cancel.begin(tmp_path)
    with cancel.bind(tmp_path):
        _delayed(cancel.request, 0.3, tmp_path)
        t0 = time.monotonic()
        with pytest.raises(cancel.Cancelled):
            cancel.call(time.sleep, 30)          # 模拟 30 秒的模型/视频接口请求
        assert time.monotonic() - t0 < MAX_STOP_LATENCY


def test_call_passes_value_and_exceptions_through(tmp_path):
    cancel.begin(tmp_path)
    with cancel.bind(tmp_path):
        assert cancel.call(lambda x: x * 2, 21) == 42
        with pytest.raises(ValueError, match="boom"):
            cancel.call(lambda: (_ for _ in ()).throw(ValueError("boom")))


def test_call_without_binding_is_synchronous():
    # 未绑定项目（测试、命令行）时保持原有同步行为
    assert cancel.call(lambda: "ok") == "ok"


def test_sleep_is_interrupted(tmp_path):
    cancel.begin(tmp_path)
    with cancel.bind(tmp_path):
        _delayed(cancel.request, 0.2, tmp_path)
        t0 = time.monotonic()
        with pytest.raises(cancel.Cancelled):
            cancel.sleep(30)
        assert time.monotonic() - t0 < MAX_STOP_LATENCY


def test_running_subprocess_is_killed(tmp_path):
    cancel.begin(tmp_path)
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    with cancel.bind(tmp_path):
        _delayed(cancel.request, 0.5, tmp_path)
        t0 = time.monotonic()
        with pytest.raises(cancel.Cancelled):
            cancel.run_process(cmd)
        assert time.monotonic() - t0 < MAX_STOP_LATENCY
    assert cancel._PROCS[str(tmp_path.resolve())] == []   # 子进程已从登记中移除


def test_process_result_unchanged_when_not_stopped(tmp_path):
    cancel.begin(tmp_path)
    with cancel.bind(tmp_path):
        code, out, err = cancel.run_process([sys.executable, "-c", "print('hi')"])
    assert code == 0 and out.strip() == "hi" and err == ""


def test_begin_keeps_old_signal_set_for_orphan_threads(tmp_path):
    old = cancel.begin(tmp_path)
    cancel.request(tmp_path)
    new = cancel.begin(tmp_path)
    assert old.is_set() and not new.is_set()   # 上一轮遗留线程仍看到停止，新一轮不受影响


# ----------------------------------------------------------------- 后台任务

def _wait_state(path_fn, states, timeout=MAX_STOP_LATENCY):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = path_fn()
        if st.get("state") in states:
            return st
        time.sleep(0.05)
    return path_fn()


def test_background_job_stop_is_immediate(tmp_path):
    (tmp_path / "logs").mkdir()
    started = threading.Event()

    def fn():
        started.set()
        cancel.call(time.sleep, 60)          # 长时间等待的接口请求
        return {"done": ["never"]}

    res = jobs.start(tmp_path, name="t_stop", label="测试停止", scope="assets", fn=fn)
    assert res["ok"], res
    assert started.wait(2)
    t0 = time.monotonic()
    assert jobs.request_stop(tmp_path)["ok"]
    st = _wait_state(lambda: jobs.status(tmp_path), {"stopped", "done", "failed"})
    assert st["state"] == "stopped", st
    assert time.monotonic() - t0 < MAX_STOP_LATENCY
    # 锁随后释放（状态先于锁写入，避免旧线程覆盖新任务的状态）：短时间内可以再次启动
    deadline = time.monotonic() + MAX_STOP_LATENCY
    again = {"ok": False}
    while not again["ok"] and time.monotonic() < deadline:
        again = jobs.start(tmp_path, name="t_again", label="再次", scope="assets", fn=lambda: {"done": []})
        if not again["ok"]:
            time.sleep(0.05)
    assert again["ok"], again


def test_queue_stop_is_immediate_and_marked_stopped(tmp_path):
    (tmp_path / "logs").mkdir()
    started = threading.Event()

    def run_fn():
        started.set()
        cancel.call(time.sleep, 60)
        return {"ok": True, "data": {"results": {}, "halted": None}}

    res = rt.start_background(tmp_path, [1], "G0", run_fn)
    assert res["ok"], res
    assert started.wait(2)
    t0 = time.monotonic()
    rt.request_stop(tmp_path)
    st = _wait_state(lambda: rt.read_state(tmp_path), {"stopped", "done", "failed", "halted"})
    assert st["state"] == "stopped", st
    assert time.monotonic() - t0 < MAX_STOP_LATENCY
    assert "立即停止" in st["message"]


def test_batch_stops_current_chapter_and_starts_no_more(tmp_path):
    (tmp_path / "logs").mkdir()
    write_json_atomic(tmp_path / "project.json", {"budget": {}})
    called = []

    def run_one(no):
        called.append(no)
        if no == 1:
            cancel.call(time.sleep, 60)     # 第 1 章卡在模型请求中
        return {"status": "done", "message": ""}

    cancel.begin(tmp_path)
    with cancel.bind(tmp_path):
        _delayed(rt.request_stop, 0.3, tmp_path)
        t0 = time.monotonic()
        out = batch.run_queue(tmp_path, [1, 2, 3], gear="G0", run_one=run_one)
        assert time.monotonic() - t0 < MAX_STOP_LATENCY
    assert called == [1]                                  # 第 2、3 章不再开始
    assert out["results"][1]["status"] == "stopped"
    assert out["halted"] == cancel.STOP_MESSAGE


# ----------------------------------------------------------------- 单章流水线

def test_chapter_stop_in_clips_returns_stopped_and_resumable(project, monkeypatch):
    """C-8 视频阶段被停止：章节回到 pending、当前阶段记为待运行（不是失败），重新运行即可继续。"""
    def fake_clips(proj_dir, no, video):
        cancel.request(proj_dir)            # 用户在此刻点击“立即停止”
        cancel.call(time.sleep, 60)         # 视频接口请求被中断
        return {}

    # 与本测试无关的前置阶段直接放行（它们各自有专门的测试），使流程能到达 C-8
    monkeypatch.setattr(cp, "run_assets", lambda *a, **k: {"waiting": [], "errors": []})
    monkeypatch.setattr(cp, "run_audio", lambda *a, **k: {"errors": []})
    monkeypatch.setattr(cp, "run_prompts", lambda *a, **k: {"failed": []})
    monkeypatch.setattr(cp, "run_clips", fake_clips)
    cancel.begin(project)
    with cancel.bind(project):
        out = _run(project, ChapterLLM())
    assert out["status"] == "stopped", out
    row = cio.chapter_row(project, 1)
    assert row["status"] == "pending"
    assert "停止" in row["message"]
    stages = cio.load_state(project, 1)["stages"]
    assert all(v.get("status") != "failed" for v in stages.values())      # 没有任何阶段被记为失败
    paused = [k for k, v in stages.items() if v.get("status") == "pending" and v.get("error") == "已手动停止"]
    assert len(paused) == 1                                               # 停在第一个未完成的阶段
    assert stages["script"]["status"] == "done"                           # 之前完成的阶段保留


def test_chapter_stop_is_not_recorded_as_failure(project, monkeypatch):
    def fake_script(proj_dir, no, llm, force=False):
        cancel.request(proj_dir)
        cancel.check()
        return {"status": "pass"}

    monkeypatch.setattr(cp, "run_script", fake_script)
    cancel.begin(project)
    with cancel.bind(project):
        out = _run(project, ChapterLLM())
    assert out["status"] == "stopped"
    assert cio.chapter_row(project, 1)["status"] != "failed"
