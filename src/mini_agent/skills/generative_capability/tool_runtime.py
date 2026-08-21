"""
tool_runtime.py
=================
供"探索蒸馏生成的脚本"在运行时重放动作序列使用的工具执行器注入点。

对应文档: next_doc/generative-capability-skill-plan.md 第 6 节 distill() /
          实施记录阶段三。

为什么需要这一层:
  - 蒸馏生成的 member 脚本（members/<id>/script.py）本身不实现任何具体的
    浏览器控制逻辑，它只保存"调用哪个工具、传什么参数"这样一份动作序列。
  - 真正执行这些工具调用的能力属于运行时环境（比如 agent 框架里已经
    注册的 browser-core 工具集合），不应该被每个蒸馏脚本各自硬编码依赖。
  - 因此约定一个模块级的执行器钩子：调用方（CapabilityEngine 或宿主
    agent 框架）在加载/执行任何 member 脚本前调用 set_tool_executor()
    注入一次，脚本内部通过 get_tool_executor() 取用，两者用同一份
    工具白名单语义（tool_name, tool_input）-> tool_output。
"""

from __future__ import annotations

from typing import Callable, Optional

_tool_executor: Optional[Callable[[str, dict], dict]] = None


def set_tool_executor(executor: Optional[Callable[[str, dict], dict]]) -> None:
    global _tool_executor
    _tool_executor = executor


def get_tool_executor() -> Optional[Callable[[str, dict], dict]]:
    return _tool_executor
