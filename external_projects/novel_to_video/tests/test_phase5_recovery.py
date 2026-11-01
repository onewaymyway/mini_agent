"""Phase 5 崩溃恢复演练。

演练一：子进程运行队列时被 SIGKILL，主进程检测到遗留锁并回收章节状态。
演练二：心跳超时（进程仍在但卡死）时，锁视为遗留。
演练三：项目正在被另一进程持有时，拒绝重复运行，不破坏状态。
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import runlock as rl
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv

ROOT = Path(__file__).resolve().parent.parent
CH = "# 第{n}章 测试章{n}\n\n铁城的清晨很安静。\n\n林远低声说：“我们走。”\n"


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
    return p


def test_stale_lock_from_dead_pid_is_reclaimed(proj):
    # 伪造一个已退出进程的锁（选一个几乎不可能存在的 pid）
    (proj / rl.LOCK_FILE).write_text(json.dumps({
        "pid": 999999, "host": __import__("socket").gethostname(),
        "heartbeat": time.time()}), encoding="utf-8")
    st = rl.inspect(proj)
    assert st["held"] is False and st["stale"] is True
    rl.acquire(proj)  # 不应抛出
    rl.release(proj)
    assert not (proj / rl.LOCK_FILE).exists()


def test_heartbeat_timeout_marks_lock_stale(proj):
    (proj / rl.LOCK_FILE).write_text(json.dumps({
        "pid": os.getpid(), "host": __import__("socket").gethostname(),
        "heartbeat": time.time() - rl.STALE_SEC - 10}), encoding="utf-8")
    st = rl.inspect(proj, now=time.time())
    assert st["stale"] is True and "心跳" in st["reason"]


def test_live_lock_blocks_second_run(proj):
    rl.acquire(proj)  # 当前进程持有
    try:
        r = tool_api.run_queue(str(proj), [1], gear="G0")
        assert not r["ok"] and "正在运行" in r["error"]["message"]
    finally:
        rl.release(proj)


def test_crashed_running_chapter_is_recovered(proj):
    # 章节停在 running，且没有活锁（模拟进程被杀）
    cio.set_chapter_status(proj, 1, "running", "进行中")
    (proj / rl.LOCK_FILE).write_text(json.dumps({
        "pid": 999999, "host": __import__("socket").gethostname(),
        "heartbeat": time.time()}), encoding="utf-8")
    fixed = rl.recover_running_chapters(proj)
    assert fixed == [{"no": 1, "to": "pending"}]
    assert cio.chapter_row(proj, 1)["status"] == "pending"
    assert cio.chapter_row(proj, 2)["status"] == "pending"  # 未运行的章节不受影响


def test_recovery_refuses_when_lock_is_live(proj):
    cio.set_chapter_status(proj, 1, "running", "进行中")
    rl.acquire(proj)
    try:
        with pytest.raises(rl.LockBusy):
            rl.recover_running_chapters(proj)
        assert cio.chapter_row(proj, 1)["status"] == "running"  # 不误改
    finally:
        rl.release(proj)


def test_recovery_chooses_waiting_review_when_shots_pending(proj):
    tool_api.run_chapter(str(proj), 1)  # 生成镜头与视频，停在等待审核
    cio.set_chapter_status(proj, 1, "running", "进行中")
    rl.recover_running_chapters(proj)
    assert cio.chapter_row(proj, 1)["status"] in ("waiting_review", "pending")


def test_sigkill_during_queue_leaves_recoverable_state(proj, tmp_path):
    """演练一：真实子进程运行队列，中途 SIGKILL，主进程回收。"""
    script = tmp_path / "victim.py"
    script.write_text(
        "import sys, os, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "os.environ['NOVEL_TO_VIDEO_PROVIDERS'] = 'stub'\n"
        "from novel_to_video import tool_api, runlock\n"
        "from pathlib import Path\n"
        "orig = tool_api._run_queue_locked\n"
        "def slow(*a, **k):\n"
        "    print('LOCKED', flush=True)\n"
        "    time.sleep(60)\n"
        "    return orig(*a, **k)\n"
        "tool_api._run_queue_locked = slow\n"
        f"tool_api.run_queue({str(proj)!r}, [1, 2], gear='G0')\n",
        encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True,
                            encoding="utf-8")
    try:
        line = proc.stdout.readline()
        assert "LOCKED" in line, line
        assert (proj / rl.LOCK_FILE).is_file()
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    # 锁遗留，持有进程已死
    st = rl.inspect(proj)
    assert st["stale"] is True and st["held"] is False
    # 另一进程可以回收并重新获取锁
    rl.acquire(proj)
    rl.release(proj)
    # 章节状态可以正常回收（这里章节未进入 running 也应安全无操作）
    rl.recover_running_chapters(proj)
