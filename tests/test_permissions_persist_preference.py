"""
tests/test_permissions_persist_preference.py
— api/routes.py::_persist_permission_preference() 回归测试

背景：/v1/permissions/{req_id} 路由处理 "always"/"deny_always" 模式时，
曾经是一段未完成的占位代码：

    checker = getattr(bridge, "permission_checker", None)
    if checker is not None: pass

AgentBridge 对象上从来没有 "permission_checker" 这个属性（真正存在的是
self.agent），所以 getattr 永远返回 None，这个分支永远不会执行——意味着
无论 CLI 还是 web demo 通过纯 HTTP 路径选择"以后总是允许/拒绝这个工具"，
这个偏好从来没有被真正持久化到 PermissionGuard 的白/黑名单
（_allow_list/_denied_tools），下次同样的工具调用还会再问一次。这与
daemon 本地终端的 CLI 交互（PermissionGuard._prompt_with_http 的 CLI
分支直接调用 self._add_allow()/self._denied_tools.add()）行为不一致。

修复后，链路是 bridge.agent.guard（AgentBridge 持有 self.agent，
Agent 持有 self.guard）。本文件验证这条链路在各种边界情况下都正确：
guard 不存在、tool_name 缺失、_add_allow 抛异常等都不应该让整个请求
失败（respond() 已经让审批本身生效，持久化偏好失败不是致命错误）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.api import routes


def _make_bridge_with_guard(guard) -> MagicMock:
    bridge = MagicMock()
    bridge.agent = MagicMock()
    bridge.agent.guard = guard
    return bridge


def _make_pending_info(tool_name="bash", tool_input=None):
    info = MagicMock()
    info.tool_name = tool_name
    info.tool_input = tool_input if tool_input is not None else {"command": "ls"}
    return info


def test_persist_always_calls_add_allow():
    guard = MagicMock()
    bridge = _make_bridge_with_guard(guard)
    pending_info = _make_pending_info("bash", {"command": "ls"})

    routes._persist_permission_preference(bridge, "always", pending_info)

    guard._add_allow.assert_called_once_with("bash", {"command": "ls"})
    guard._denied_tools.add.assert_not_called()


def test_persist_deny_always_adds_to_denied_tools_and_saves():
    guard = MagicMock()
    bridge = _make_bridge_with_guard(guard)
    pending_info = _make_pending_info("delete_file", {"path": "/tmp/x"})

    routes._persist_permission_preference(bridge, "deny_always", pending_info)

    guard._denied_tools.add.assert_called_once_with("delete_file")
    guard._save_permissions.assert_called_once()
    guard._add_allow.assert_not_called()


def test_persist_once_mode_does_nothing():
    """mode="once" 是最常见情况，不应该触碰白/黑名单。"""
    guard = MagicMock()
    bridge = _make_bridge_with_guard(guard)
    pending_info = _make_pending_info()

    routes._persist_permission_preference(bridge, "once", pending_info)

    guard._add_allow.assert_not_called()
    guard._denied_tools.add.assert_not_called()
    guard._save_permissions.assert_not_called()


def test_persist_guard_missing_does_not_raise():
    """bridge.agent 为 None（agent 还没启动）或 bridge.agent.guard 不存在时，
    应该安全跳过，不抛异常。"""
    bridge = MagicMock()
    bridge.agent = None
    pending_info = _make_pending_info()

    routes._persist_permission_preference(bridge, "always", pending_info)  # 不应该抛异常


def test_persist_pending_info_none_does_not_raise():
    """pending_info 为 None（理论上不应该发生，respond() 成功前已经
    校验过，但防御性处理）时不应该抛异常。"""
    guard = MagicMock()
    bridge = _make_bridge_with_guard(guard)

    routes._persist_permission_preference(bridge, "always", None)
    guard._add_allow.assert_not_called()


def test_persist_empty_tool_name_skipped():
    """tool_name 为空字符串时跳过持久化（没有有效的工具名可以加白名单）。"""
    guard = MagicMock()
    bridge = _make_bridge_with_guard(guard)
    pending_info = _make_pending_info(tool_name="", tool_input={})

    routes._persist_permission_preference(bridge, "always", pending_info)
    guard._add_allow.assert_not_called()


def test_persist_add_allow_exception_does_not_propagate():
    """guard._add_allow() 内部抛异常（比如写文件失败）不应该向上传播——
    respond() 已经成功，这次审批本身已经生效，持久化偏好失败不是
    致命错误，不应该让整个 HTTP 响应失败。"""
    guard = MagicMock()
    guard._add_allow.side_effect = OSError("disk full")
    bridge = _make_bridge_with_guard(guard)
    pending_info = _make_pending_info()

    routes._persist_permission_preference(bridge, "always", pending_info)  # 不应该抛异常


def test_persist_save_permissions_exception_does_not_propagate():
    guard = MagicMock()
    guard._save_permissions.side_effect = OSError("disk full")
    bridge = _make_bridge_with_guard(guard)
    pending_info = _make_pending_info()

    routes._persist_permission_preference(bridge, "deny_always", pending_info)  # 不应该抛异常


def test_persist_old_placeholder_attribute_not_used():
    """
    回归测试：确认修复后的实现不再依赖那个从未真正存在的
    'permission_checker' 属性名——用一个故意不设置 .agent.guard、
    但设置了 .permission_checker 的 bridge mock，确认仍然不会触发
    持久化（证明代码路径走的是 bridge.agent.guard，不是旧的
    bridge.permission_checker）。
    """
    bridge = MagicMock()
    bridge.agent = None  # 没有 agent，guard 链路拿不到
    bridge.permission_checker = MagicMock()  # 故意设置旧属性名做对照
    pending_info = _make_pending_info()

    routes._persist_permission_preference(bridge, "always", pending_info)

    # 不应该通过旧属性名做任何事——bridge.permission_checker 完全没被触碰
    bridge.permission_checker._add_allow.assert_not_called()
