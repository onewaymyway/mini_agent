"""
tests/test_step_runner.py — evolution/step_runner.py 单元测试

覆盖：正常完成、抛异常、超时三种结果，以及超时后原线程副作用不影响
run_step() 本身立即返回（不阻塞主流程等待原线程跑完）。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

from mini_agent.evolution.step_runner import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_TIMEOUT,
    run_step,
)


def test_run_step_ok():
    result, info = run_step("noop", lambda: 42, timeout_seconds=5)
    assert result == 42
    assert info.status == STATUS_OK
    assert info.name == "noop"
    assert info.error is None


def test_run_step_error_returns_default():
    def _boom():
        raise ValueError("boom")

    result, info = run_step("boom", _boom, timeout_seconds=5, default="fallback")
    assert result == "fallback"
    assert info.status == STATUS_ERROR
    assert "boom" in info.error


def test_run_step_timeout_returns_default_promptly():
    def _slow():
        time.sleep(2.0)
        return "too-late"

    start = time.monotonic()
    result, info = run_step("slow", _slow, timeout_seconds=0.3, default=[])
    elapsed = time.monotonic() - start

    assert result == []
    assert info.status == STATUS_TIMEOUT
    # run_step 本身应该在超时预算附近就返回，不等原线程跑完（2s）
    assert elapsed < 1.0


def test_step_result_to_dict_roundtrip():
    _, info = run_step("x", lambda: 1, timeout_seconds=5)
    d = info.to_dict()
    assert d["name"] == "x"
    assert d["status"] == "ok"
    assert isinstance(d["elapsed_seconds"], float)
