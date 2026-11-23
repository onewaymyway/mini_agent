"""netproxy.py — 各类网络请求是否使用系统代理。默认一律不使用。

* 生图（requests）：用 `session.trust_env = False` 即可，不读环境变量与系统代理。
* 大模型（经 mini_agent 的 LLMHelper，内部客户端不由本项目构造）：调用期间临时设置
  NO_PROXY=* 并移除代理环境变量，让 requests/httpx/urllib 都不走代理；调用结束恢复。
  计数 + 锁保证多线程嵌套时只在最外层进入与退出时改动环境。
  注意：同进程内其它线程在这段时间发起的请求也会暂时直连（其它线程的请求在此期间也可能暂时直连）。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Dict, Optional

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_LOCK = threading.Lock()
_DEPTH = 0
_SAVED: Dict[str, Optional[str]] = {}


def _enter() -> None:
    global _DEPTH
    with _LOCK:
        if _DEPTH == 0:
            _SAVED.clear()
            for k in _PROXY_VARS + ("NO_PROXY", "no_proxy"):
                _SAVED[k] = os.environ.get(k)
            for k in _PROXY_VARS:
                os.environ.pop(k, None)
            os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
        _DEPTH += 1


def _exit() -> None:
    global _DEPTH
    with _LOCK:
        _DEPTH -= 1
        if _DEPTH == 0:
            for k, v in _SAVED.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _SAVED.clear()


@contextmanager
def proxy_policy(use_system_proxy: bool):
    """use_system_proxy=False 时，块内的请求不使用代理；True 时不做任何改动。"""
    if use_system_proxy:
        yield
        return
    _enter()
    try:
        yield
    finally:
        _exit()
