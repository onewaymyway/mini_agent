"""
[黑窗口修复] Windows 下子进程"黑色命令行一闪而逝"的统一止血点。

## 根因

daemon 模式下，守护进程本身是用 `DETACHED_PROCESS`（见
`cli/daemon.py::cmd_daemon_start`）拉起的——它自己**没有控制台**。

Windows 的规则是：`CreateProcess` 创建一个控制台子进程（cmd.exe /
powershell.exe / git.exe / python.exe 等）时，如果父进程本身带着控制台，
子进程会直接挂到父进程的控制台上，不会新开窗口；但如果父进程根本没有
控制台（daemon 这种情况），系统就必须临时**新分配一个控制台**给这个子
进程用，而这个新控制台默认是可见的——命令执行完子进程一退出，控制台跟
着销毁，于是就是用户看到的"黑框一闪而逝"。

daemon 背后一堆地方都会 shell 出去（workflow 的 shell step、hooks、
evolution 的 git 校验、agent_commit_guard、project_scanner 读分支状态、
tools/builtin.py 的 bash 工具等），每次调用都会各自弹一下，所以体感上
是"经常"闪。

## 用法

**不要**在各个调用点手动拼 `creationflags`——那样以后发现这个方案本身
有问题（比如某个特殊子进程确实需要看得见控制台、或者需要换一种隐藏
窗口的方式），就得挨个文件去改。

本模块把"隐藏窗口"这个决定封装成三个跟标准库同名、参数完全透传的函数：
`run` / `Popen` / `check_output`。项目里所有需要起子进程的地方，一律：

    from mini_agent.utils import win_subprocess

    win_subprocess.run(...)           # 而不是 subprocess.run(...)
    win_subprocess.Popen(...)         # 而不是 subprocess.Popen(...)
    win_subprocess.check_output(...)  # 而不是 subprocess.check_output(...)

调用方原有的 `creationflags`（比如 `subprocess.CREATE_NEW_PROCESS_GROUP`
这种跟"是否弹窗"无关、用于进程组管理的 flag）照常传，本模块只是在
Windows 上把 `CREATE_NO_WINDOW` OR 进去，不会覆盖调用方自己传的 flag；
非 Windows 平台上这三个函数就是对标准库同名函数的纯透传，行为完全不变。

以后如果要调整"怎么隐藏窗口"（换 STARTUPINFO 方案、或者反过来发现某类
子进程不该隐藏），只需要改这一个文件，不用再挨个调用点改。
"""

from __future__ import annotations

import subprocess as _subprocess
import sys
from typing import Any

IS_WINDOWS: bool = sys.platform == "win32"

# subprocess.CREATE_NO_WINDOW 在非 Windows 平台上不存在这个符号，
# 这里直接用其数值，避免到处判断平台。
CREATE_NO_WINDOW: int = 0x08000000


def _with_no_window(kwargs: dict) -> dict:
    """在 Windows 上把 CREATE_NO_WINDOW OR 进调用方已有的 creationflags；
    非 Windows 上原样返回（不改动、不新增任何 key）。"""
    if not IS_WINDOWS:
        return kwargs
    kwargs = dict(kwargs)
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    return kwargs


def run(*args: Any, **kwargs: Any):
    """等价于 ``subprocess.run``，Windows 上自动不弹控制台窗口。"""
    return _subprocess.run(*args, **_with_no_window(kwargs))


def Popen(*args: Any, **kwargs: Any):  # noqa: N802 (与 subprocess.Popen 保持同名)
    """等价于 ``subprocess.Popen``，Windows 上自动不弹控制台窗口。"""
    return _subprocess.Popen(*args, **_with_no_window(kwargs))


def check_output(*args: Any, **kwargs: Any):
    """等价于 ``subprocess.check_output``，Windows 上自动不弹控制台窗口。"""
    return _subprocess.check_output(*args, **_with_no_window(kwargs))
