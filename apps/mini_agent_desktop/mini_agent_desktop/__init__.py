"""mini_agent Windows 桌面常驻客户端（托盘/快速面板/Toast）。

纯 HTTP/SSE 客户端：只通过 daemon 的 /v1/* 接口与 mini_agent 通信，
不 import mini_agent 核心包。设计见 next_doc/windows_desktop_resident_plan.md。
"""

__version__ = "0.4.0"
