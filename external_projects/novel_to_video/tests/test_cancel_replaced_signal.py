"""立即停止：运行期间重新 begin（替换信号对象）后，停止请求仍必须中断仍在运行的旧线程，并终止其子进程。"""
import subprocess
import sys
import threading
import time
from pathlib import Path

from novel_to_video import cancel, joblog
from novel_to_video.providers import skill_providers as sp


def _run_in_bound_thread(d, fn, out):
    def target():
        cancel.begin(d)
        with cancel.bind(d), joblog.bind(d, "chapter"):
            t0 = time.monotonic()
            try:
                out["value"] = fn()
            except cancel.Cancelled:
                out["cancelled"] = time.monotonic() - t0
    th = threading.Thread(target=target, daemon=True)
    th.start()
    return th


def test_stop_reaches_thread_after_signal_was_replaced(tmp_path):
    out = {}
    th = _run_in_bound_thread(tmp_path, lambda: cancel.call(time.sleep, 30), out)
    time.sleep(0.4)
    cancel.begin(tmp_path)                      # 运行期间又开始了一轮，替换了项目的信号对象
    time.sleep(0.2)
    cancel.request(tmp_path)
    th.join(5)
    assert "cancelled" in out and out["cancelled"] < 3, out


def test_child_process_started_before_replacement_is_killed(tmp_path):
    out = {}
    args = [sys.executable, "-c", "import time; time.sleep(60)"]
    th = _run_in_bound_thread(tmp_path, lambda: cancel.run_process(args), out)
    time.sleep(0.6)
    cancel.begin(tmp_path)                      # 替换后子进程登记不能丢
    time.sleep(0.2)
    killed = cancel.request(tmp_path)
    th.join(5)
    assert killed >= 1 and "cancelled" in out, (killed, out)


def test_video_provider_stop_after_replacement(tmp_path):
    class sp_cancel(BaseException):
        pass

    class FakeClient:
        def __init__(self):
            self.cancel_event = None

        def generate_video(self, **kw):
            try:
                while True:
                    if self.cancel_event is not None and self.cancel_event.wait(0.2):
                        raise sp_cancel()
            except sp_cancel:
                return {"cancelled": True, "success": False}

    prov = sp.SkillVideoProvider.__new__(sp.SkillVideoProvider)
    prov.client = FakeClient()
    prov.poll_interval_sec = 0.2
    prov.max_wait_sec = 3600
    prov.use_system_proxy = False
    out = {}
    th = _run_in_bound_thread(tmp_path, lambda: prov.generate("p", tmp_path / "x.mp4", seconds=12, reference_images=[]), out)
    time.sleep(0.6)
    cancel.begin(tmp_path)
    time.sleep(0.2)
    cancel.request(tmp_path)
    th.join(5)
    assert "cancelled" in out and out["cancelled"] < 3, out
