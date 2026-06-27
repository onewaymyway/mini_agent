"""
hooks/runner.py — 执行单个 hook 命令

每个 hook 是一条 shell 命令，通过 stdin 接收 JSON payload，
可选地向 stdout 输出 JSON 来表达决策：

    {"decision": "block", "reason": "..."}     # 阻断（仅 PreToolUse 有效）
    {"decision": "allow"}                       # 明确允许（默认行为）
    {"context": "额外注入的上下文文本"}          # 注入到下一轮 prompt（多事件通用）
    {"input": {...}}                            # 修改 PreToolUse 的工具调用参数

hook 命令执行失败 / 超时 / 输出非 JSON，均视为 "allow"（不阻塞主流程），
但错误信息会记录在 HookResult.error 中，便于调试。
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


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


def run_hook(spec: HookSpec, payload: dict[str, Any]) -> HookResult:
    """同步执行一个 hook 命令，返回解析后的结果。"""
    try:
        proc = subprocess.run(
            shlex.split(spec.command) if isinstance(spec.command, str) else spec.command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=spec.timeout,
            cwd=str(spec.cwd) if spec.cwd else None,
        )
    except subprocess.TimeoutExpired:
        return HookResult(decision="allow", error=f"hook timed out after {spec.timeout}s: {spec.command}")
    except Exception as e:
        return HookResult(decision="allow", error=f"hook failed to start: {e}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    # 非零退出码且未给出 JSON 决策 -> 视为 block（约定：exit code 2 = block，类似 Claude Code）
    if proc.returncode == 2:
        reason = out or err or f"hook exited with code 2: {spec.command}"
        return HookResult(decision="block", reason=reason, error=err, raw_stdout=out)

    if not out:
        return HookResult(decision="allow", error=err, raw_stdout=out)

    try:
        data = json.loads(out)
    except Exception:
        # 非 JSON 输出：当作额外上下文文本附加（不阻断）
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
