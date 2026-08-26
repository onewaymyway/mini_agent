"""tests/test_git_push_guard.py — `git push` 权限兜底拦截回归测试

背景：daemon/cron 例行维护会话通常跑在 `auto_approve=True` 下（没有人在场
可以审批），`PermissionGuard.check()` 原来的逻辑是：只要不在 sandbox 模式、
且 `auto_approve=True`，risky 工具（含 bash）直接放行——`git push` 作为
一条普通 bash 命令，会被无声地推到远端，用户完全不知情。

用户明确要求：daemon 例行维护/自动批准场景下禁止 agent 自行 push，除非
用户在交互会话里明确指示。本文件验证 `permissions.py` 里新增的 `git push`
专门分支：

  1. `is_git_push_command()` 对各种写法的识别（含 `-C <path>`、`--force`
     等常见变体，以及不应误判的 `git push` 子串出现在别处的情况）。
  2. `auto_approve=True` 或 headless 模式下，`git push` 一律被拒绝，且
     不会尝试任何审批交互（`_prompt`/`_prompt_with_http` 不应被调用）。
  3. 交互场景（`auto_approve=False`，无 HTTP gate）下，`git push` 强制
     走一次 `_prompt()`，即使用户之前给 bash 开过"全放行"白名单（验证
     不会被 `_is_allowed()` 的 allow_list 短路绕过）。
  4. 普通（非 push）bash 命令在 `auto_approve=True` 下的既有行为不受
     影响，仍然直接放行——本次改动只窄范围拦截 push，不应该误伤其他
     git 操作（比如 commit/pull/fetch）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.permissions import (
    PermissionGuard,
    is_git_push_command,
    set_headless_mode,
)


def _guard(tmp_path, **kwargs) -> PermissionGuard:
    return PermissionGuard(project_root=tmp_path, **kwargs)


def teardown_function(_fn):
    # is_git_push_command 无状态；但 set_headless_mode 是模块级全局开关，
    # 每个用例结束都要复位，避免测试之间互相污染。
    set_headless_mode(False)


# ── is_git_push_command() 识别 ──────────────────────────────────────────


def test_is_git_push_command_matches_plain_push():
    assert is_git_push_command("git push")
    assert is_git_push_command("git push origin main")
    assert is_git_push_command("git push --force origin main")
    assert is_git_push_command("git -C /repo push")
    assert is_git_push_command("cd /repo && git push")
    assert is_git_push_command("git add -A; git commit -m x; git push")


def test_is_git_push_command_does_not_match_other_git_commands():
    assert not is_git_push_command("git commit -m 'push config update'")
    assert not is_git_push_command("git pull")
    assert not is_git_push_command("git fetch")
    assert not is_git_push_command("git log --oneline")
    assert not is_git_push_command("")
    assert not is_git_push_command(None)


# ── auto_approve / headless 场景：一律拒绝，不发起任何审批交互 ──────────


def test_auto_approve_blocks_git_push_without_prompting(tmp_path):
    guard = _guard(tmp_path, auto_approve=True)
    with patch.object(guard, "_prompt") as mock_prompt, \
         patch.object(guard, "_prompt_with_http") as mock_prompt_http:
        allowed = guard.check("bash", {"command": "git push origin main"})
    assert allowed is False
    mock_prompt.assert_not_called()
    mock_prompt_http.assert_not_called()


def test_headless_mode_blocks_git_push_even_without_auto_approve(tmp_path):
    set_headless_mode(True)
    guard = _guard(tmp_path, auto_approve=False)
    with patch.object(guard, "_prompt") as mock_prompt:
        allowed = guard.check("bash", {"command": "git push"})
    assert allowed is False
    mock_prompt.assert_not_called()


def test_auto_approve_still_allows_plain_git_commit(tmp_path):
    # 回归：本次改动不应该误伤 git commit/pull 等其它 git 子命令。
    guard = _guard(tmp_path, auto_approve=True)
    assert guard.check("bash", {"command": "git commit -m 'wip'"}) is True
    assert guard.check("bash", {"command": "git pull"}) is True


# ── 交互场景：强制走一次人工确认，不被 allow_list 短路 ──────────────────


def test_interactive_git_push_forces_prompt_even_with_blanket_bash_allow(tmp_path):
    guard = _guard(tmp_path, auto_approve=False)
    # 模拟用户此前对 bash 开过"全放行"白名单（path_prefix=""）。
    guard._add_allow("bash", {"command": "ls"})
    with patch.object(guard, "_prompt", return_value=True) as mock_prompt:
        allowed = guard.check("bash", {"command": "git push origin main"})
    assert allowed is True
    mock_prompt.assert_called_once()
    # 确认走的是"危险操作"标记（is_dangerous=True）
    _, kwargs = mock_prompt.call_args
    args = mock_prompt.call_args.args
    assert args[2] is True  # is_dangerous 位置参数


def test_interactive_git_push_denied_when_user_declines(tmp_path):
    guard = _guard(tmp_path, auto_approve=False)
    with patch.object(guard, "_prompt", return_value=False) as mock_prompt:
        allowed = guard.check("bash", {"command": "git push"})
    assert allowed is False
    mock_prompt.assert_called_once()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
