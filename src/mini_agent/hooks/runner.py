"""
hooks/runner.py — 执行单个 hook 命令

每个 hook 是一条 shell 命令，通过 stdin 接收 JSON payload，
可选地向 stdout 输出 JSON 来表达决策：

    {"decision": "block", "reason": "..."}     # 阻断（仅 PreToolUse 有效）
    {"decision": "allow"}                       # 明确允许（默认行为）
    {"context": "额外注入的上下文文本"}          # 注入到下一轮 prompt（多事件通用）
    {"input": {...}}                            # 修改 PreToolUse 的工具调用参数
    {"user_input": "..."}                       # TurnEnd 专用：替代真实用户输入

hook 命令执行失败 / 超时 / 输出非 JSON，均视为 "allow"（不阻塞主流程），
但错误信息会记录在 HookResult.error 中，便于调试。

跨平台注意事项：
  - stdin/stdout/stderr 全部使用二进制模式 + 显式 UTF-8 编解码，
    避免 Windows 系统默认编码（GBK 等）导致的 UnicodeEncodeError。
  - shlex.split 在 Windows 下使用 posix=False，避免反斜杠路径被错误处理。
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


_IS_WINDOWS = sys.platform == "win32"


@dataclass
class HookResult:
    decision: str = "allow"          # "allow" | "block"
    reason: str = ""
    context: str = ""                # 注入到 prompt 的额外文本
    modified_input: Optional[dict] = None  # PreToolUse 修改后的工具参数
    user_input: Optional[str] = None  # TurnEnd hook 返回时，替代真实用户输入
    error: str = ""
    raw_stdout: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision == "block"


@dataclass
class HookSpec:
    command: str
    matcher: str = "*"           # 匹配工具名（PreToolUse/PostToolUse），"*" 表示全部
    timeout: float = 30.0
    cwd: Optional[Path] = None
    source: str = ""             # 来源说明（哪个配置文件/skill/agent profile）
    # [platform_filter] 平台/tag 限制：空 = 不限制，见 mini_agent.platform_filter
    platforms: Optional[list] = None
    tags: Optional[list] = None


def _split_command(command: str) -> list[str]:
    """跨平台 shell 命令分割。

    Windows 下 shlex.split 默认 posix=True 会把反斜杠当转义符处理，
    导致 Windows 路径被截断；改用 posix=False 可正确保留反斜杠。
    其他平台保持 posix=True 默认行为。
    """
    return shlex.split(command, posix=not _IS_WINDOWS)


def run_hook(spec: HookSpec, payload: dict[str, Any]) -> HookResult:
    """同步执行一个 hook 命令，返回解析后的结果。

    stdin/stdout/stderr 全部走二进制模式，payload 用 UTF-8 编码写入，
    输出用 UTF-8（errors='replace'）解码，彻底规避 Windows GBK 问题。
    """
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    try:
        _popen_kwargs = {
            "args": _split_command(spec.command) if isinstance(spec.command, str) else spec.command,
            "input": payload_bytes,
            "capture_output": True,
            "text": False,                          # 二进制模式，不依赖系统默认编码
            "timeout": spec.timeout,
            "cwd": str(spec.cwd) if spec.cwd else None,
        }
        if _IS_WINDOWS:
            _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            _popen_kwargs["start_new_session"] = True
        proc = subprocess.run(**_popen_kwargs)
    except subprocess.TimeoutExpired:
        return HookResult(decision="allow", error=f"hook timed out after {spec.timeout}s: {spec.command}")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.hooks.runner.run_hook')
        import traceback
        traceback.print_exc()
        return HookResult(decision="allow", error=f"hook failed to start: {e}")

    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()

    # 非零退出码且未给出 JSON 决策 -> 视为 block（约定：exit code 2 = block，类似 Claude Code）
    if proc.returncode == 2:
        reason = out or err or f"hook exited with code 2: {spec.command}"
        return HookResult(decision="block", reason=reason, error=err, raw_stdout=out)

    if not out:
        return HookResult(decision="allow", error=err, raw_stdout=out)

    try:
        data = json.loads(out)
    except Exception as _mini_agent_exc:
        # 非 JSON 输出：当作额外上下文文本附加（不阻断）
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.hooks.runner.run_hook')
        return HookResult(decision="allow", context=out, error=err, raw_stdout=out)

    if not isinstance(data, dict):
        return HookResult(decision="allow", context=out, error=err, raw_stdout=out)

    return HookResult(
        decision=str(data.get("decision", "allow")),
        reason=str(data.get("reason", "")),
        context=str(data.get("context", "")),
        modified_input=data.get("input") if isinstance(data.get("input"), dict) else None,
        user_input=str(data["user_input"]) if isinstance(data.get("user_input"), str) else None,
        error=err,
        raw_stdout=out,
    )
