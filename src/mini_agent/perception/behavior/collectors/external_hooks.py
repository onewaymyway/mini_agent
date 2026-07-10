"""
perception/behavior/collectors/external_hooks.py — git / 终端命令 外部上报脚本生成

这两类不是"本机常驻线程"采集器，而是"生成一小段脚本，装到 git hooks /
shell rc 里，由脚本自己在事件发生时 POST 给已有的 /v1/perception/report
接口"。复用同一套上报通道，不用再造一个新协议。

终端命令脱敏（客户端 + 服务端双重）：
  - 命令行本身可能包含密码/token（如 `mysql -p123456`、`curl -H "Authorization: Bearer xxx"`），
    含敏感关键字的整条命令直接丢弃，不上报，而不是打码后上报（打码规则很容易被绕过或漏配）。
  - 只上报命令名 + 参数结构，不上报命令的标准输出/错误输出。
"""

from __future__ import annotations

import re
from pathlib import Path


_SENSITIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"password", r"passwd", r"secret", r"token", r"api[_-]?key",
        r"authorization", r"-p\s*\S+",  # mysql -p<pwd> 之类
        r"ssh-add", r"gpg\s+--", r"aws\s+configure",
    ]
]


def is_sensitive_command(cmd: str) -> bool:
    return any(p.search(cmd) for p in _SENSITIVE_PATTERNS)


def redact_command(cmd: str, max_len: int = 200) -> str | None:
    """返回可安全上报的命令文本；命中敏感规则则返回 None（整条丢弃）。"""
    cmd = cmd.strip()
    if not cmd:
        return None
    if is_sensitive_command(cmd):
        return None
    return cmd[:max_len]


def generate_git_hook_script(report_url: str, api_token: str, report_token: str, repo_path: str) -> str:
    """生成 post-commit / post-checkout 共用的 hook 脚本内容（bash）。"""
    return f"""#!/bin/sh
# mini_agent behavior perception — auto-generated git hook
# 只上报 commit/checkout 的结构化元数据（分支名、commit 概要），不上报 diff 内容。
REPORT_URL="{report_url}"
API_TOKEN="{api_token}"
REPORT_TOKEN="{report_token}"
REPO="{repo_path}"

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
SUBJECT=$(git log -1 --pretty=%s 2>/dev/null | cut -c1-120)
HASH=$(git rev-parse --short HEAD 2>/dev/null)
FILES_CHANGED=$(git show --stat HEAD 2>/dev/null | tail -1)

EVENT_TYPE="commit"
if [ "$1" = "checkout" ]; then EVENT_TYPE="checkout"; fi

curl -s -m 2 -X POST "$REPORT_URL" \\
  -H "Authorization: Bearer $API_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d "{{\\"source\\":\\"git\\",\\"kind\\":\\"git\\",\\"token\\":\\"$REPORT_TOKEN\\",\\"events\\":[{{\\"event_type\\":\\"$EVENT_TYPE\\",\\"app_name\\":\\"git\\",\\"meta\\":{{\\"repo\\":\\"$REPO\\",\\"branch\\":\\"$BRANCH\\",\\"subject\\":\\"$SUBJECT\\",\\"hash\\":\\"$HASH\\"}}}}]}}" \\
  >/dev/null 2>&1 &
exit 0
"""


def install_git_hooks(repo_path, report_url: str, api_token: str, report_token: str) -> list[Path]:
    """在 <repo>/.git/hooks/ 下写入 post-commit 和 post-checkout 脚本，返回写入的路径列表。"""
    repo_path = Path(repo_path)
    hooks_dir = repo_path / ".git" / "hooks"
    if not hooks_dir.exists():
        raise RuntimeError(f"{repo_path} 不是一个 git 仓库（找不到 .git/hooks）")

    written = []
    for hook_name in ("post-commit", "post-checkout"):
        content = generate_git_hook_script(report_url, api_token, report_token, str(repo_path))
        path = hooks_dir / hook_name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        written.append(path)
    return written


def generate_shell_hook_snippet(report_url: str, api_token: str, report_token: str) -> str:
    """生成可以追加到 ~/.bashrc 或 ~/.zshrc 的 hook 片段。

    原理：在每条命令执行完（bash: PROMPT_COMMAND / zsh: precmd）异步 POST 上报，
    敏感命令（含密码/token 特征）在客户端直接跳过不发送。
    """
    return f"""
# >>> mini_agent behavior perception (terminal_command) >>>
__mini_agent_report_cmd() {{
  local cmd
  cmd=$(fc -ln -1 2>/dev/null | sed 's/^\\s*//')
  [ -z "$cmd" ] && return
  case "$cmd" in
    *password*|*passwd*|*secret*|*token*|*api_key*|*API_KEY*|*Authorization*|*-p\\ *|*ssh-add*|*aws\\ configure*) return ;;
  esac
  curl -s -m 2 -X POST "{report_url}" \\
    -H "Authorization: Bearer {api_token}" \\
    -H "Content-Type: application/json" \\
    -d "{{\\"source\\":\\"terminal\\",\\"kind\\":\\"terminal\\",\\"token\\":\\"{report_token}\\",\\"events\\":[{{\\"event_type\\":\\"command\\",\\"app_name\\":\\"shell\\",\\"meta\\":{{\\"cmd\\":\\"$(echo "$cmd" | cut -c1-200 | sed 's/\\"/\\\\\\"/g')\\",\\"cwd\\":\\"$PWD\\"}}}}]}}" \\
    >/dev/null 2>&1 &
}}
# bash
if [ -n "$BASH_VERSION" ]; then
  PROMPT_COMMAND="__mini_agent_report_cmd; ${{PROMPT_COMMAND:-}}"
fi
# zsh
if [ -n "$ZSH_VERSION" ]; then
  autoload -Uz add-zsh-hook 2>/dev/null
  add-zsh-hook precmd __mini_agent_report_cmd 2>/dev/null
fi
# <<< mini_agent behavior perception (terminal_command) <<<
"""
