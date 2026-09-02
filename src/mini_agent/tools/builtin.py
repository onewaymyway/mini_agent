"""
Built-in tools: bash, file I/O, glob, grep, patch, web_search.
All are registered in the default registry via @tool().
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
import textwrap
import time
import unicodedata
from collections import deque
from difflib import unified_diff
from pathlib import Path
from typing import Optional

from . import tool  # noqa


# ── bash ──────────────────────────────────────────────────────────────────────

@tool(
    name="bash",
    description=(
        "Execute a shell command in the project environment. "
        "Returns stdout + stderr. Timeout: 30s by default. "
        "Working directory is the project root unless overridden."
    ),
    schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30)",
            },
            "workdir": {
                "type": "string",
                "description": "Working directory (default: current dir)",
            },
        },
        "required": ["command"],
    },
    requires_approval=True,
)
def bash(command: str, timeout: int = 300, workdir: Optional[str] = None) -> str:
    """Execute a shell command and return combined stdout/stderr."""
    import os
    cwd = Path(workdir).expanduser() if workdir else Path.cwd()

    # 注入 Python UTF-8 环境变量，避免 Windows GBK 终端导致子进程
    # print(emoji/中文) 时抛出 UnicodeEncodeError。
    # PYTHONUTF8=1  : Python 3.7+ UTF-8 模式（影响 stdin/stdout/stderr/文件默认编码）
    # PYTHONIOENCODING=utf-8 : 兼容旧版 Python，强制 I/O 编码
    # 二者同时设置，兼容性最佳；不影响非 Python 进程。
    _env = os.environ.copy()
    _env.setdefault("PYTHONUTF8", "1")
    _env.setdefault("PYTHONIOENCODING", "utf-8")

    if _BASH_STREAM_OUTPUT_ENABLED:
        return _bash_stream(command, timeout=timeout, cwd=cwd, env=_env)

    # [SYS-BASH-HANG-FIX] 两个已知会导致"永久卡死、timeout 形同虚设"的坑：
    # 1) 不给 stdin 会继承父进程的 stdin；一旦命令触发交互式提示
    #    （git 密码/host-key 确认、apt/npm 的 [Y/n]、误入分页器或 REPL），
    #    子进程会永久阻塞等待输入，谁都救不了它。→ 显式 stdin=DEVNULL，
    #    让这类命令要么走非交互 flag 正常跑完，要么立刻因读不到输入而报错退出。
    # 2) subprocess.run(shell=True, timeout=...) 超时后只杀得掉 /bin/sh 这一层；
    #    如果命令派生了孙子进程（后台服务、"cmd &" 没 disown 等），孙子进程会
    #    继续占着 stdout/stderr 管道的写端不放。run() 内部超时后还会再调一次
    #    不带 timeout 的 communicate() 收尾，这一步没有孙子进程也会等到天荒地老。
    #    → 用 start_new_session=True 把整棵进程树放进独立进程组，超时时
    #    os.killpg 一锅端，而不是只 kill 最外层的 proc。
    import platform
    import signal

    _is_windows = platform.system() == "Windows"
    _popen_kwargs: dict = {"stdin": subprocess.DEVNULL}
    if _is_windows:
        _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        _popen_kwargs["start_new_session"] = True

    def decode(data: bytes) -> str:
        for enc in ("utf-8", "gbk", "cp936"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                pass
        return data.decode("utf-8", errors="replace")

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_popen_kwargs,
        )
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.bash')
        import traceback
        traceback.print_exc()
        return f"[error: {e}]"

    def _kill_process_tree():
        if _is_windows:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree()
        # 进程组已被强制杀掉，这里的 communicate() 只是回收管道里
        # 已经产生的残留输出，不会再无限期阻塞。
        try:
            stdout_b, stderr_b = proc.communicate(timeout=5)
        except Exception:
            stdout_b, stderr_b = b"", b""
        combined = (decode(stdout_b) + decode(stderr_b)).rstrip()
        note = f"[timeout after {timeout}s — partial output above, process killed]"
        return (combined + "\n" + note) if combined else note
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.bash')
        import traceback
        traceback.print_exc()
        _kill_process_tree()
        return f"[error: {e}]"

    combined = (decode(stdout_b) + decode(stderr_b)).rstrip()
    if proc.returncode != 0:
        combined += f"\n[exit code: {proc.returncode}]"

    return combined or "(no output)"


# [SYS-BASH-STREAM] bash 工具"边跑边看"开关，由 AppConfig.bash_stream_output_enabled
# 驱动。工具函数本身（被 ToolRegistry.call 以 **tool_input 方式调用）拿不到 cfg，
# 沿用本文件里 configure_web_search(cfg) 同款写法：Agent.__init__ 时注入一次，
# 之后 bash() 读这个模块级变量。
# [SYS-BASH-HANG-FIX] 默认改为 True：非流式路径虽然也修了进程组 kill 的坑，
# 但流式路径能让调用方（人/日志）实时看到已经产生的输出，排查"卡在哪一步"
# 更直接，作为默认体验更好。仍可通过 AppConfig.bash_stream_output_enabled=False
# 显式关闭，行为会退回到刚修复过的非流式路径（同样不会再卡死）。
_BASH_STREAM_OUTPUT_ENABLED = True


def configure_bash(cfg) -> None:
    """注入 AppConfig，控制 bash 工具是否边执行边把输出实时打印到终端。"""
    global _BASH_STREAM_OUTPUT_ENABLED
    _BASH_STREAM_OUTPUT_ENABLED = bool(getattr(cfg, "bash_stream_output_enabled", False))


def _bash_decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _bash_stream(command: str, *, timeout: int, cwd: Path, env: dict) -> str:
    """bash() 的流式实现：逐行读取子进程输出，边读边打印到终端；

    超时时不再像旧版那样直接丢弃已产生的输出、只返回一句
    "[timeout after Ns]"——而是把 timeout 之前已经拿到的内容原样保留在
    返回结果里，并在末尾追加一个明确的超时标记，让调用方（无论是 LLM
    还是人）既能看到已经发生了什么，也能明确知道命令没有正常跑完。

    超时检测用独立看门狗线程（threading.Timer）强制 kill 进程，而不是
    "每读到一行就检查一次时间"——后者对"命令长时间不产生任何输出"（比如
    纯 `sleep N`）完全无效，因为 readline() 会一直阻塞到有数据或进程退出
    才返回，循环体内的时间检查根本没有机会被执行到。
    """
    import mini_agent.ui.renderer as R
    import threading
    import signal
    import platform

    _is_windows = platform.system() == "Windows"
    # [SYS-BASH-HANG-FIX] 同上：显式关闭 stdin，避免命令触发交互式提示
    # （git 密码/host-key 确认、apt/npm 的 [Y/n]、误入分页器或 REPL）时
    # 永久阻塞等待输入。
    _popen_kwargs: dict = {"stdin": subprocess.DEVNULL}
    if _is_windows:
        # Windows 没有 os.setsid/os.killpg 这套 POSIX 进程组机制，改用
        # CREATE_NEW_PROCESS_GROUP，超时时配合 taskkill /T /F 按进程树整棵杀。
        _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # [BUGFIX] shell=True 时 command 实际是 "/bin/sh -c <command>"，
        # proc 只是这层 shell 本身；shell 派生出的孙子进程（真正干活的那个）
        # 默认继承同一个 stdout 管道写端。如果超时只 kill 掉 shell 这一层，
        # 孙子进程会继续占着管道写端不放，readline() 拿不到 EOF，会一路
        # 阻塞到孙子进程自然结束——超时机制形同虚设。这里用
        # start_new_session=True 把整棵进程树放进独立进程组，超时时对
        # 整个进程组发信号（os.killpg），才能真正杀干净。
        _popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 合并到一路，保证时间顺序不错乱
            bufsize=0,
            **_popen_kwargs,
        )
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin._bash_stream')
        import traceback
        traceback.print_exc()
        return f"[error: {e}]"

    timed_out_flag = threading.Event()

    def _kill_on_timeout():
        timed_out_flag.set()
        if _is_windows:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream._kill_on_timeout')
                try:
                    proc.kill()
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream._kill_on_timeout')
                    pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream._kill_on_timeout')
                try:
                    proc.kill()
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream._kill_on_timeout')
                    pass

    watchdog = threading.Timer(timeout, _kill_on_timeout)
    watchdog.daemon = True
    watchdog.start()

    chunks: list[bytes] = []
    try:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, b""):
            chunks.append(line)
            try:
                R.console.print(_bash_decode(line), end="")
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream')
                pass  # 终端打印失败不应影响命令本身的执行/结果收集
        proc.wait()
    finally:
        watchdog.cancel()
        # 进程被 kill 后，管道里可能还残留一点没读完的缓冲内容，补读一次。
        if proc.stdout is not None:
            try:
                rest = proc.stdout.read()
                if rest:
                    chunks.append(rest)
                    try:
                        R.console.print(_bash_decode(rest), end="")
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream')
                        pass
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._bash_stream')
                pass

    combined = _bash_decode(b"".join(chunks)).rstrip()

    if timed_out_flag.is_set():
        # 关键行为：超时不丢弃已产生的部分输出，同时明确告知调用方"没跑完"。
        note = f"[timeout after {timeout}s — partial output above, process killed]"
        return (combined + "\n" + note) if combined else note

    returncode = proc.returncode
    if returncode not in (0, None):
        combined += f"\n[exit code: {returncode}]"

    return combined or "(no output)"


# ── read_file ─────────────────────────────────────────────────────────────────

# [SYS-LARGEFILE] 默认大文件阈值（字节），可通过 config 覆盖
_DEFAULT_LARGE_FILE_THRESHOLD = 20000  # 100 KB

# 模块级配置引用（由 configure_large_file 注入，None 时使用默认值）
_tool_trim_cfg = None


def configure_large_file(cfg) -> None:
    """注入 ToolTrimConfig，供 read_file / list_dir 读取大文件相关配置。"""
    global _tool_trim_cfg
    _tool_trim_cfg = cfg


def _large_file_threshold() -> int:
    """返回当前生效的大文件字节阈值。"""
    if _tool_trim_cfg is not None:
        return getattr(_tool_trim_cfg, "large_file_threshold_bytes", _DEFAULT_LARGE_FILE_THRESHOLD)
    return _DEFAULT_LARGE_FILE_THRESHOLD


def _fmt_size(nbytes: int) -> str:
    """将字节数格式化为人类可读的大小字符串。"""
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    else:
        return f"{nbytes / (1024 * 1024):.1f} MB"


@tool(
    name="read_file",
    description=(
        "Read the contents of a file. "
        "Supports optional line range (1-indexed, inclusive). "
        "Returns file text with line numbers prefixed. "
        "IMPORTANT: For large files (> 20 KB), always check file size first via list_dir "
        "or use grep/glob to locate the relevant section before reading. "
        "Prefer start_line/end_line to read only what you need. "
        "Pass force=true only when the full file is truly necessary."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "start_line": {"type": "integer", "description": "First line to read (1-indexed)"},
            "end_line": {"type": "integer", "description": "Last line to read (inclusive)"},
            "force": {
                "type": "boolean",
                "description": (
                    "Force reading the full file even if it exceeds the large-file threshold. "
                    "Only use when the entire file content is truly necessary."
                ),
            },
        },
        "required": ["path"],
    },
    requires_approval=False,
)
def read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    force: bool = False,
) -> str:
    """Read a file, optionally a line range. Large files are intercepted unless force=true."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error: file not found: {path}]"
    if not p.is_file():
        return f"[error: not a file: {path}]"

    # [SYS-LARGEFILE] 大文件检查：仅在未指定行范围且未强制时拦截
    if not force and start_line is None and end_line is None:
        try:
            file_size = p.stat().st_size
            threshold = _large_file_threshold()
            if file_size > threshold:
                # 统计行数（仍不读入内存，用二进制计换行符）
                try:
                    with p.open("rb") as f:
                        line_count = f.read().count(b"\n") + 1
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.tools.builtin.read_file')
                    line_count = None
                line_info = f", {line_count} lines" if line_count is not None else ""
                return (
                    f"[large file: {_fmt_size(file_size)}{line_info} — {path}]\n"
                    f"Reading the full file is expensive. Consider:\n"
                    f"  • grep to locate relevant patterns first\n"
                    f"  • read_file with start_line/end_line to read a specific range\n"
                    f"  • read_file with force=true if the full content is truly needed"
                )
        except OSError:
            pass  # stat 失败时放行，走正常路径

    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.read_file')
        return f"[error reading {path}: {e}]"

    sl = (start_line - 1) if start_line else 0
    el = end_line if end_line else len(lines)
    sl = max(0, sl)
    el = min(len(lines), el)
    selected = lines[sl:el]

    numbered = "".join(f"{sl + i + 1:6}\t{line}" for i, line in enumerate(selected))
    return numbered or "(empty file)"


# ── write_file ────────────────────────────────────────────────────────────────

@tool(
    name="write_file",
    description=(
        "Overwrite (or create) a file with the given content. "
        "Creates parent directories as needed. "
        "Use patch_file for targeted edits instead of full rewrites. "
        "For large files (>200 lines or containing special characters), prefer writing in chunks: "
        "write .part1 / .part2 files separately, then merge with "
        "bash('cat file.part1 file.part2 > file && rm file.part1 file.part2')."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Full file content"},
        },
        "required": ["path", "content"],
    },
    requires_approval=True,
)
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates parents)."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        return f"Written {lines} lines to {path}"
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.write_file')
        return f"[error writing {path}: {e}]"


# ── create_file ───────────────────────────────────────────────────────────────

@tool(
    name="create_file",
    description=(
        "Create a new file. Fails if the file already exists (use write_file to overwrite). "
        "For large files (>200 lines or containing special characters), prefer writing in chunks: "
        "create .part1 / .part2 files separately, then merge with "
        "bash('cat file.part1 file.part2 > file && rm file.part1 file.part2')."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    requires_approval=True,
)
def create_file(path: str, content: str = "") -> str:
    p = Path(path).expanduser()
    if p.exists():
        return f"[error: file already exists: {path}. Use write_file to overwrite.]"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
        return f"Created {path}"
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.create_file')
        return f"[error creating {path}: {e}]"


# ── delete_file ───────────────────────────────────────────────────────────────

@tool(
    name="delete_file",
    description="Delete a file. Does NOT delete directories.",
    schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    requires_approval=True,
)
def delete_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error: not found: {path}]"
    if p.is_dir():
        return f"[error: {path} is a directory. Use bash rm -rf carefully.]"
    try:
        p.unlink()
        return f"Deleted {path}"
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.delete_file')
        return f"[error: {e}]"


# ── list_dir ──────────────────────────────────────────────────────────────────

@tool(
    name="list_dir",
    description=(
        "List files and directories. Optional depth limit (default 2). "
        "Displays file sizes to help identify large files before reading them. "
        "Files exceeding the large-file threshold are marked with ⚠."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (default: cwd)"},
            "depth": {"type": "integer", "description": "Max recursion depth (default 2)"},
            "show_size": {
                "type": "boolean",
                "description": "Show file sizes (default true). Set false to reduce output length.",
            },
        },
        "required": [],
    },
    requires_approval=False,
)
def list_dir(path: str = ".", depth: int = 2, show_size: bool = True) -> str:
    # [SYS-LARGEFILE] show_size 可由 config 默认值覆盖
    if _tool_trim_cfg is not None:
        show_size = getattr(_tool_trim_cfg, "list_dir_show_size", show_size)
    root = Path(path).expanduser()
    if not root.exists():
        return f"[error: not found: {path}]"
    lines: list[str] = []
    _walk(root, root, depth, 0, lines, show_size=show_size)
    return "\n".join(lines) or "(empty)"


def _walk(
    base: Path,
    current: Path,
    max_depth: int,
    level: int,
    out: list[str],
    show_size: bool = True,
) -> None:
    prefix = "  " * level
    try:
        entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        out.append(f"{prefix}[permission denied]")
        return
    # [SYS-LARGEFILE] 获取当前配置的阈值和标记符
    threshold = _large_file_threshold()
    warn_marker = (
        getattr(_tool_trim_cfg, "large_file_warn_marker", "⚠")
        if _tool_trim_cfg is not None
        else "⚠"
    )
    for entry in entries:
        if entry.name.startswith(".") and level > 0:
            continue  # skip hidden unless top-level
        if entry.is_file():
            if show_size:
                try:
                    size = entry.stat().st_size
                    size_str = _fmt_size(size)
                    marker = f" {warn_marker}" if size > threshold else ""
                    out.append(f"{prefix}📄 {entry.name:<40} {size_str:>8}{marker}")
                except OSError:
                    out.append(f"{prefix}📄 {entry.name}")
            else:
                out.append(f"{prefix}📄 {entry.name}")
        else:
            out.append(f"{prefix}📁 {entry.name}")
            if entry.is_dir() and level < max_depth - 1:
                _walk(base, entry, max_depth, level + 1, out, show_size=show_size)


# ── tree_summary ──────────────────────────────────────────────────────────────

# 默认忽略的目录名（不递归进入）
_TREE_IGNORE_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env", ".env",
    "dist", "build", ".build", "target",
    ".idea", ".vscode",
})


@tool(
    name="tree_summary",
    description=(
        "Show a compact directory skeleton: only directories with file counts and total sizes. "
        "Much more token-efficient than list_dir for large projects. "
        "Use at the start of a task to understand project layout without reading individual files. "
        "Common build/cache dirs (.git, __pycache__, node_modules, .venv, etc.) are skipped automatically."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Root directory (default: cwd)"},
            "depth": {"type": "integer", "description": "Max recursion depth (default 3)"},
            "show_files": {
                "type": "boolean",
                "description": "Also list individual filenames under each directory (default false)",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden directories like .agent, .claude (default false)",
            },
        },
        "required": [],
    },
    requires_approval=False,
)
def tree_summary(
    path: str = ".",
    depth: int = 3,
    show_files: bool = False,
    include_hidden: bool = False,
) -> str:
    root = Path(path).expanduser()
    if not root.exists():
        return f"[error: not found: {path}]"
    if not root.is_dir():
        return f"[error: not a directory: {path}]"

    lines: list[str] = []
    lines.append(f"📁 {root.name}/")
    _tree_walk(root, depth, 0, lines, show_files=show_files, include_hidden=include_hidden)

    # 全局统计
    total_files, total_bytes = _tree_count(root, include_hidden=include_hidden)
    lines.append(f"\n{total_files} files, {_fmt_size(total_bytes)} total")
    return "\n".join(lines)


def _tree_walk(
    current: Path,
    max_depth: int,
    level: int,
    out: list[str],
    show_files: bool,
    include_hidden: bool,
) -> None:
    if level >= max_depth:
        return
    prefix = "  " * (level + 1)

    try:
        entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        out.append(f"{prefix}[permission denied]")
        return

    dirs, files = [], []
    for e in entries:
        name = e.name
        if name.startswith(".") and not include_hidden:
            continue
        if e.is_dir():
            if name in _TREE_IGNORE_DIRS:
                continue
            dirs.append(e)
        elif e.is_file():
            files.append(e)

    # 显示文件列表（可选）
    if show_files:
        threshold = _large_file_threshold()
        warn_marker = (
            getattr(_tool_trim_cfg, "large_file_warn_marker", "⚠")
            if _tool_trim_cfg is not None else "⚠"
        )
        for f in files:
            try:
                sz = f.stat().st_size
                marker = f" {warn_marker}" if sz > threshold else ""
                out.append(f"{prefix}📄 {f.name:<38} {_fmt_size(sz):>8}{marker}")
            except OSError:
                out.append(f"{prefix}📄 {f.name}")
    else:
        # 紧凑模式：只显示当前目录的文件数 + 大小
        if files:
            try:
                total_sz = sum(f.stat().st_size for f in files)
                large_count = sum(
                    1 for f in files
                    if f.stat().st_size > _large_file_threshold()
                )
                size_str = _fmt_size(total_sz)
                warn = f"  ⚠ {large_count} large" if large_count else ""
                out.append(f"{prefix}({len(files)} files, {size_str}{warn})")
            except OSError:
                out.append(f"{prefix}({len(files)} files)")

    # 递归子目录
    for d in dirs:
        try:
            sub_files, sub_bytes = _tree_count(d, include_hidden=include_hidden)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.tools.builtin._tree_walk')
            sub_files, sub_bytes = 0, 0
        size_str = f"  [{_fmt_size(sub_bytes)}, {sub_files} files]" if not show_files else ""
        out.append(f"{prefix}📁 {d.name}/{size_str}")
        _tree_walk(d, max_depth, level + 1, out, show_files=show_files, include_hidden=include_hidden)


def _tree_count(directory: Path, include_hidden: bool = False) -> tuple[int, int]:
    """递归统计目录下的文件数和总字节数，跳过忽略目录。"""
    total_files = 0
    total_bytes = 0
    try:
        for entry in directory.iterdir():
            name = entry.name
            if name.startswith(".") and not include_hidden:
                continue
            if entry.is_file():
                total_files += 1
                try:
                    total_bytes += entry.stat().st_size
                except OSError:
                    pass
            elif entry.is_dir() and name not in _TREE_IGNORE_DIRS:
                sub_f, sub_b = _tree_count(entry, include_hidden=include_hidden)
                total_files += sub_f
                total_bytes += sub_b
    except PermissionError:
        pass
    return total_files, total_bytes


# ── glob ──────────────────────────────────────────────────────────────────────

@tool(
    name="glob",
    description="Find files matching a glob pattern (e.g. '**/*.py'). Returns matching paths.",
    schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern"},
            "root": {"type": "string", "description": "Root directory (default: cwd)"},
        },
        "required": ["pattern"],
    },
    requires_approval=False,
)
def glob(pattern: str, root: str = ".") -> str:
    base = Path(root).expanduser()
    matches = sorted(base.glob(pattern))
    if not matches:
        return "(no matches)"
    return "\n".join(str(m.relative_to(base)) for m in matches[:200])


# ── grep ──────────────────────────────────────────────────────────────────────

# [daemon 因单个巨型文件 MemoryError 崩溃后看板连不上 bugfix]
# 原实现对匹配到的每个文件都无条件 `read_text().splitlines()`，把整份
# 文件一次性读进内存；扫到几百 MB～几 GB 的巨型文件（日志/数据转储/
# 误把二进制文件当文本）时 MemoryError。这不是"抛出去被 try/except
# 接住就没事"的错误——Python 进程真正逼近系统内存上限时，OOM 影响的是
# 进程里所有并发工作，包括处理看板 HTTP 请求的 uvicorn 事件循环，表现
# 出来就是这条 grep 报错之后看板 API 也一起连不上。
#
# 现在的做法：不再区分"大文件/小文件走不同路径"，所有文件统一用
# `f.readline(限定长度)` 逐行流式读取（见 `_grep_file_streaming`），
# 内存占用只取决于 context_lines（前后各留最多 context_lines 行缓冲）
# 和 `_GREP_MAX_LINE_CHARS`（单行长度上限，防止个别没有换行符、把整个
# 文件当一"行"的病态文件把这层防线绕过去），与文件总大小完全无关——
# 100MB、1GB 的文件和 1KB 的文件走的是同一条代码路径，内存峰值不会
# 随文件变大而增长。
#
# `max_file_size_mb`/`skip_large_files`/`skip_binary_files` 三个参数只是
# "要不要搜这个文件"的过滤器（跳过巨型文件通常是因为没意义/慢，跳过
# 二进制文件是因为搜不出有意义的结果），不是内存安全的必要条件——即使
# 传 `skip_large_files=False` 强制搜索超大文件，也不会内存爆炸，因为
# 读取方式本身就是流式的。默认沿用"跳过"是为了避免误扫到日志目录/
# node_modules 里的大文件时把一次 grep 拖得很慢。
_GREP_MAX_LINE_CHARS = 1_000_000  # 单行（含没有真正换行符的病态大文件）读取上限


def _looks_binary(fpath: Path) -> bool:
    """只读前 8KB 做二进制嗅探（含 NUL 字节即判定为二进制），不读整个
    文件。避免把日志目录里偶尔混进来的二进制/压缩/数据文件当文本整个
    扫一遍——这类文件即使体积不大，`errors="replace"` 解码出来的内容
    对 grep 结果也没有意义，纯粹是浪费时间。`skip_binary_files=False`
    时不调用这个函数，交给正常的流式文本读取处理。"""
    try:
        with open(fpath, "rb") as fh:
            chunk = fh.read(8192)
        return b"\x00" in chunk
    except OSError:
        return False


def _grep_file_streaming(
    fpath: Path, regex: "re.Pattern", rel: str, context_lines: int,
    quota_remaining: list[int],
) -> tuple[int, list[str]]:
    """逐行流式扫描单个文件，不把整份文件读进内存。

    内存占用只取决于：① `context_lines`（"匹配前"缓冲区最多存
    context_lines 行，"匹配后"缓冲同样最多 context_lines 行）；
    ② `_GREP_MAX_LINE_CHARS`（单行读取上限，防止没有换行符的病态文件
    把一整个文件当成一行读进来）。与文件总行数/总大小无关。

    quota_remaining：跨文件共享的剩余 max_results 配额，用可变的单
    元素列表在调用方和这里之间原地传递/扣减——grep() 会依次对多个文件
    调用这个函数，配额要跨文件累计扣减，同一个列表对象被反复传入。

    返回 (这个文件里的总匹配数, 这个文件贡献的输出行列表)。总匹配数
    不管配额是否已经用完都要如实计入，用于最终"共 N 处匹配"的准确
    统计——配额只影响"渲染多少"，不影响"数出多少"。
    """
    file_out: list[str] = []
    file_total = 0

    with open(fpath, "r", encoding="utf-8", errors="replace", newline="") as fh:
        if context_lines == 0:
            idx = 0
            while True:
                line = fh.readline(_GREP_MAX_LINE_CHARS)
                if not line:
                    break
                text = line.rstrip("\r\n")
                if regex.search(text):
                    file_total += 1
                    if quota_remaining[0] > 0:
                        quota_remaining[0] -= 1
                        file_out.append(f"{rel}:{idx + 1}:{text}")
                idx += 1
            return file_total, file_out

        # 有上下文行的模式：固定大小滑动窗口维护"匹配前 N 行"缓冲，
        # 命中后再继续收 N 行"匹配后"上下文；相邻/重叠的上下文窗口会
        # 自动合并成一个块（用 last_emitted_idx 判断两次命中之间是否
        # 有间隙），跟改造前"先收集全部匹配再统一按区间合并"的效果
        # 一致，只是现在是边读边判定，不需要保留整份文件。
        before_buf: deque[tuple[int, str]] = deque(maxlen=context_lines)
        after_remaining = 0
        block_open = False
        last_emitted_idx = -1
        idx = 0
        while True:
            line = fh.readline(_GREP_MAX_LINE_CHARS)
            if not line:
                break
            text = line.rstrip("\r\n")
            is_match = bool(regex.search(text))
            if is_match:
                file_total += 1
                if quota_remaining[0] > 0:
                    quota_remaining[0] -= 1
                    if not block_open or idx - 1 > last_emitted_idx:
                        if file_out:
                            file_out.append("---")
                        for bidx, btext in before_buf:
                            if bidx > last_emitted_idx:
                                file_out.append(f"{rel}:{bidx + 1}  {btext}")
                        block_open = True
                    file_out.append(f"{rel}:{idx + 1}> {text}")
                    last_emitted_idx = idx
                    after_remaining = context_lines
                elif after_remaining > 0:
                    # 配额已经用完的命中行：不单独渲染成新块，但如果它
                    # 恰好落在前一个已接受命中的"匹配后"窗口内，仍然要
                    # 作为普通上下文行（不带 `>` 标记）展示——跟改造前
                    # "整个区间内的行都展示，只有真正被采纳的命中才标
                    # `>`"的语义保持一致。
                    file_out.append(f"{rel}:{idx + 1}  {text}")
                    last_emitted_idx = idx
                    after_remaining -= 1
            elif after_remaining > 0:
                file_out.append(f"{rel}:{idx + 1}  {text}")
                last_emitted_idx = idx
                after_remaining -= 1

            before_buf.append((idx, text))
            idx += 1

    return file_total, file_out


@tool(
    name="grep",
    description=(
        "Search for a regex pattern in files. "
        "Returns file:line:content for each match, with optional surrounding context lines. "
        "Use context_lines to see code around each match without a separate read_file call. "
        "Reports total match count and truncation status. "
        "Reads files line-by-line (streaming), never loading a whole file into memory, so "
        "large files are memory-safe by default; max_file_size_mb/skip_large_files only "
        "control whether a big file is searched at all (for speed), not memory safety. "
        "PERFORMANCE: always pass a specific `path` (a subdirectory or single file) instead of "
        "leaving it at the default '.' — searching the whole project root recursively is slow "
        "when the tree has many files or very large files. Narrow the search to the "
        "directory/module you actually care about whenever you can infer or already know it "
        "(e.g. from a prior tree_summary/glob call); only fall back to the project root when "
        "you genuinely don't know where the target might be."
    ),
    schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {
                "type": "string",
                "description": (
                    "File or directory to search. Prefer a narrow, specific path "
                    "(e.g. 'src/mini_agent/tools') over the default '.' — this is the single "
                    "biggest factor in how fast the search runs on large trees."
                ),
            },
            "file_pattern": {"type": "string", "description": "Glob filter, e.g. '*.py'"},
            "case_sensitive": {"type": "boolean", "description": "Default true"},
            "context_lines": {
                "type": "integer",
                "description": "Lines of context before and after each match (default 0, max 10)",
            },
            "max_results": {
                "type": "integer",
                "description": "Max number of matching lines to return (default 100)",
            },
            "max_file_size_mb": {
                "type": "number",
                "description": (
                    "Skip files larger than this many MB (default 20). Only affects which "
                    "files get searched, not memory usage — matched files are always read "
                    "line-by-line, never loaded whole into memory. Set skip_large_files=false "
                    "to search large files too instead of raising this number."
                ),
            },
            "skip_large_files": {
                "type": "boolean",
                "description": (
                    "Default true: skip files bigger than max_file_size_mb. Set false to "
                    "search big files anyway (still memory-safe — streamed line-by-line)."
                ),
            },
            "skip_binary_files": {
                "type": "boolean",
                "description": (
                    "Default true: skip files that look binary (sniffed from the first 8KB). "
                    "Set false to search them too (as text, decode errors replaced)."
                ),
            },
        },
        "required": ["pattern"],
    },
    requires_approval=False,
)
def grep(
    pattern: str,
    path: str = ".",
    file_pattern: str = "*",
    case_sensitive: bool = True,
    context_lines: int = 0,
    max_results: int = 100,
    max_file_size_mb: float = 20.0,
    skip_large_files: bool = True,
    skip_binary_files: bool = True,
) -> str:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"[invalid regex: {e}]"

    context_lines = max(0, min(context_lines, 10))
    max_results = max(1, min(max_results, 500))
    max_file_size_bytes = max(0.0, max_file_size_mb) * 1024 * 1024
    root = Path(path).expanduser()

    files = [root] if root.is_file() else sorted(root.rglob(file_pattern))

    out: list[str] = []
    total_found = 0
    # 用单元素列表而不是普通 int，方便在 _grep_file_streaming 里原地
    # 扣减并让这里立刻看到跨文件累计后的最新值。
    quota_remaining = [max_results]
    skipped_large = 0
    skipped_binary = 0

    for fpath in files:
        if not fpath.is_file():
            continue
        if skip_large_files:
            try:
                if fpath.stat().st_size > max_file_size_bytes:
                    skipped_large += 1
                    continue
            except OSError:
                continue
        if skip_binary_files and _looks_binary(fpath):
            skipped_binary += 1
            continue

        rel = str(fpath.relative_to(root)) if root.is_dir() else str(fpath)
        try:
            file_total, file_out = _grep_file_streaming(fpath, regex, rel, context_lines, quota_remaining)
        except MemoryError:
            # 双重兜底：正常情况下不会走到这里（逐行读取 + 单行长度上限
            # 已经让内存占用与文件大小无关），但万一某种极端情况仍然
            # 触发，也绝不能让这次 MemoryError 有机会波及整个进程——
            # 跳过这个文件，继续处理其它文件。
            skipped_large += 1
            continue
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.tools.builtin.grep')
            continue
        total_found += file_total
        out.extend(file_out)

    truncated = total_found > max_results

    if total_found == 0:
        note = "(no matches)"
        extra = []
        if skipped_large:
            extra.append(f"{skipped_large} 个文件因体积超过 {max_file_size_mb:g}MB 被跳过（可传 skip_large_files=false 强制搜索）")
        if skipped_binary:
            extra.append(f"{skipped_binary} 个文件被判定为二进制而跳过（可传 skip_binary_files=false 强制搜索）")
        if extra:
            note += f"（{'，'.join(extra)}）"
        return note

    summary = f"\n[{total_found} match{'es' if total_found != 1 else ''} found"
    if truncated:
        summary += f", showing first {max_results}"
    summary += "]"
    if skipped_large:
        summary += f"\n[{skipped_large} 个文件因体积超过 {max_file_size_mb:g}MB 被跳过未搜索，可传 skip_large_files=false 强制搜索]"
    if skipped_binary:
        summary += f"\n[{skipped_binary} 个文件被判定为二进制而跳过，可传 skip_binary_files=false 强制搜索]"
    out.append(summary)

    return "\n".join(out)

# ── patch_file ────────────────────────────────────────────────────────────────

@tool(
    name="patch_file",
    description=(
        "Apply a targeted find-and-replace edit to a file. "
        "old_string must match existing content (include enough surrounding lines to be unique). "
        "If exact match fails, a whitespace-normalized fallback is attempted automatically. "
        "On failure, the closest candidate in the file is shown to help you correct old_string. "
        "Returns a unified diff on success."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit"},
            "old_string": {"type": "string", "description": "Content to replace (must be unique in file)"},
            "new_string": {"type": "string", "description": "Replacement content"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    requires_approval=True,
)
def patch_file(path: str, old_string: str, new_string: str) -> str:
    """Apply a string replacement and return the diff."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error: file not found: {path}]"
    try:
        original = p.read_text(encoding="utf-8")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.patch_file')
        return f"[error reading {path}: {e}]"

    # ── 第一步：精确匹配 ───────────────────────────────────────────────────────
    count = original.count(old_string)
    matched_str = None  # 实际用于替换的原文片段
    used_fallback = False

    if count == 1:
        matched_str = old_string
    elif count > 1:
        return f"[error: old_string matches {count} locations — be more specific]"
    else:
        # ── 第二步：whitespace-normalized 回退 ───────────────────────────────
        # 归一化：去除行尾空白，统一换行符
        def _norm(s: str) -> str:
            return "\n".join(line.rstrip() for line in s.splitlines())

        norm_orig = _norm(original)
        norm_old = _norm(old_string)
        norm_count = norm_orig.count(norm_old)

        if norm_count == 1:
            # 定位归一化匹配在原文中对应的实际行范围
            lines_before = norm_orig[: norm_orig.index(norm_old)].count("\n")
            old_line_count = norm_old.count("\n") + 1
            orig_lines = original.splitlines(keepends=True)
            matched_str = "".join(orig_lines[lines_before: lines_before + old_line_count])
            used_fallback = True
        elif norm_count > 1:
            return (
                f"[error: old_string not found exactly, "
                f"and whitespace-normalized fallback matches {norm_count} locations — be more specific]"
            )
        else:
            # ── 第三步：彻底失败，返回最近似候选 ─────────────────────────────
            candidate = _find_patch_candidate(original, old_string)
            msg = "[error: old_string not found in file"
            if old_string != old_string.strip():
                msg += " (note: old_string has leading/trailing whitespace)"
            msg += "]"
            if candidate:
                msg += f"\n\nClosest candidate in file (use as old_string):\n{candidate}"
            return msg

    updated = original.replace(matched_str, new_string, 1)
    try:
        p.write_text(updated, encoding="utf-8")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.patch_file')
        return f"[error writing {path}: {e}]"

    diff = "".join(
        unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    result = diff or "(no diff — content unchanged)"
    if used_fallback:
        result += "\n[note: matched after whitespace normalization]"
    return result


def _find_patch_candidate(original: str, old_string: str) -> str:
    """
    在 original 中找与 old_string 最相似的实际片段，用于错误提示。
    以 old_string 首行为锚，提取原文中相同行数的片段。
    """
    old_lines = old_string.splitlines()
    if not old_lines:
        return ""
    first_stripped = old_lines[0].strip()
    if not first_stripped:
        return ""

    orig_lines = original.splitlines()
    n = len(old_lines)

    # 找首行最相近的原文行
    anchor = -1
    for i, line in enumerate(orig_lines):
        ls = line.strip()
        if first_stripped and (first_stripped in ls or ls.startswith(first_stripped[:30])):
            anchor = i
            break

    if anchor == -1:
        # 次级回退：以 old_string 前 30 个非空字符做子串搜索
        needle = old_string.strip()[:30]
        if needle:
            joined = "\n".join(orig_lines)
            idx = joined.find(needle)
            if idx != -1:
                anchor = joined[:idx].count("\n")

    if anchor == -1:
        return ""

    return "\n".join(orig_lines[anchor: anchor + n])


# ── patch_file_simple ─────────────────────────────────────────────────────────

@tool(
    name="patch_file_simple",
    description=(
        "Apply a targeted find-and-replace edit to a file using start/end line anchors. "
        "More robust than patch_file for long replacements: instead of matching the full old string, "
        "you provide the first and last lines of the region to replace, along with their expected line numbers. "
        "Both the line number AND the line content must match exactly (after stripping trailing whitespace). "
        "Returns a unified diff on success, or a detailed error with the actual file content on mismatch."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit"},
            "old_string_start": {
                "type": "string",
                "description": "Exact content of the first line of the region to replace",
            },
            "old_string_start_line_num": {
                "type": "integer",
                "description": "1-based line number where old_string_start must appear",
            },
            "old_string_end": {
                "type": "string",
                "description": "Exact content of the last line of the region to replace",
            },
            "old_string_end_line_num": {
                "type": "integer",
                "description": "1-based line number where old_string_end must appear (must be >= old_string_start_line_num)",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement content (replaces the entire region from start line to end line, inclusive)",
            },
        },
        "required": [
            "path",
            "old_string_start",
            "old_string_start_line_num",
            "old_string_end",
            "old_string_end_line_num",
            "new_string",
        ],
    },
    requires_approval=True,
)
def patch_file_simple(
    path: str,
    old_string_start: str,
    old_string_start_line_num: int,
    old_string_end: str,
    old_string_end_line_num: int,
    new_string: str,
) -> str:
    """Replace a line range in a file using start/end line anchors with validation."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error: file not found: {path}]"
    try:
        original = p.read_text(encoding="utf-8")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.patch_file_simple')
        return f"[error reading {path}: {e}]"

    lines = original.splitlines(keepends=True)
    total_lines = len(lines)

    # ── 参数合法性检验 ────────────────────────────────────────────────────────
    errors: list[str] = []

    start_idx = old_string_start_line_num - 1  # 转为 0-based
    end_idx = old_string_end_line_num - 1

    if old_string_start_line_num < 1:
        errors.append(f"old_string_start_line_num must be >= 1, got {old_string_start_line_num}")
    if old_string_end_line_num < 1:
        errors.append(f"old_string_end_line_num must be >= 1, got {old_string_end_line_num}")
    if old_string_end_line_num < old_string_start_line_num:
        errors.append(
            f"old_string_end_line_num ({old_string_end_line_num}) must be "
            f">= old_string_start_line_num ({old_string_start_line_num})"
        )
    if errors:
        return "[error: invalid parameters]\n" + "\n".join(f"  • {e}" for e in errors)

    if old_string_start_line_num > total_lines:
        return (
            f"[error: old_string_start_line_num={old_string_start_line_num} exceeds "
            f"file length ({total_lines} lines): {path}]"
        )
    if old_string_end_line_num > total_lines:
        return (
            f"[error: old_string_end_line_num={old_string_end_line_num} exceeds "
            f"file length ({total_lines} lines): {path}]"
        )

    # ── 行内容验证 ────────────────────────────────────────────────────────────
    actual_start_line = lines[start_idx].rstrip("\n").rstrip("\r")
    actual_end_line = lines[end_idx].rstrip("\n").rstrip("\r")

    expected_start = old_string_start.rstrip("\n").rstrip("\r")
    expected_end = old_string_end.rstrip("\n").rstrip("\r")

    mismatch_msgs: list[str] = []

    if actual_start_line != expected_start:
        mismatch_msgs.append(
            f"Line {old_string_start_line_num} content mismatch:\n"
            f"  expected: {repr(expected_start)}\n"
            f"  actual:   {repr(actual_start_line)}"
        )

    if actual_end_line != expected_end:
        mismatch_msgs.append(
            f"Line {old_string_end_line_num} content mismatch:\n"
            f"  expected: {repr(expected_end)}\n"
            f"  actual:   {repr(actual_end_line)}"
        )

    if mismatch_msgs:
        # 提供上下文帮助调试
        ctx_start = max(0, start_idx - 2)
        ctx_end = min(total_lines, end_idx + 3)
        context_lines_str = "".join(
            f"  {i + 1:>6}  {lines[i]}" if lines[i].endswith("\n") else f"  {i + 1:>6}  {lines[i]}\n"
            for i in range(ctx_start, ctx_end)
        )
        detail = "\n".join(mismatch_msgs)
        return (
            f"[error: line content does not match expected value]\n\n"
            f"{detail}\n\n"
            f"File context (lines {ctx_start + 1}–{ctx_end}):\n"
            f"{context_lines_str}"
        )

    # ── 执行替换 ──────────────────────────────────────────────────────────────
    # 保留 new_string 末尾换行逻辑：
    # 若 new_string 不以换行结尾，且被替换区域后面还有内容，则补一个换行
    replacement = new_string
    if replacement and not replacement.endswith("\n") and end_idx + 1 < total_lines:
        replacement += "\n"

    updated_lines = lines[:start_idx] + [replacement] + lines[end_idx + 1:]
    updated = "".join(updated_lines)

    try:
        p.write_text(updated, encoding="utf-8")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.patch_file_simple')
        return f"[error writing {path}: {e}]"

    diff = "".join(
        unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    region_desc = (
        f"line {old_string_start_line_num}"
        if old_string_start_line_num == old_string_end_line_num
        else f"lines {old_string_start_line_num}–{old_string_end_line_num}"
    )
    result = diff or "(no diff — content unchanged)"
    result += f"\n[replaced {region_desc} in {path}]"
    return result


# ── diff_files ────────────────────────────────────────────────────────────────

@tool(
    name="diff_files",
    description=(
        "Compare two files and return a unified diff. "
        "Useful for reviewing changes between versions, comparing configs, or auditing edits. "
        "context_lines controls how many unchanged lines to show around each change (default 3)."
    ),
    schema={
        "type": "object",
        "properties": {
            "path_a": {"type": "string", "description": "First file (shown as 'before')"},
            "path_b": {"type": "string", "description": "Second file (shown as 'after')"},
            "context_lines": {"type": "integer", "description": "Unchanged lines around each hunk (default 3)"},
        },
        "required": ["path_a", "path_b"],
    },
    requires_approval=False,
)
def diff_files(path_a: str, path_b: str, context_lines: int = 3) -> str:
    """Return a unified diff between two files."""
    pa, pb = Path(path_a).expanduser(), Path(path_b).expanduser()
    for label, p in (("path_a", pa), ("path_b", pb)):
        if not p.exists():
            return f"[error: {label} not found: {p}]"
        if not p.is_file():
            return f"[error: {label} is not a file: {p}]"
    try:
        lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.diff_files')
        return f"[error reading files: {e}]"

    context_lines = max(0, min(context_lines, 20))
    diff = "".join(
        unified_diff(
            lines_a,
            lines_b,
            fromfile=str(pa),
            tofile=str(pb),
            n=context_lines,
        )
    )
    if not diff:
        return f"(files are identical: {path_a} == {path_b})"

    # 统计变更行数
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return f"{diff}\n[+{added} -{removed} lines changed]"


# ── web_search ────────────────────────────────────────────────────────────────

# 由 Agent 在初始化时注入（见 agent.py），供 web_search() 工具使用。
# 未注入时 web_search() 会使用一个仅含默认值的 AppConfig（即 duckduckgo，无 key）。
_web_search_cfg = None


# ── view_raw_result [SYS-RAWSTORE] ───────────────────────────────────────────
#
# [改进：next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
#  第1节] RawResultStore 已从"session 内内存 LRU + 模块级全局单例传递 id"
# 改为"落盘到 <project_root>/.agent/raw_results/<session_id>/ + 直接传路径"。
# 本工具不再依赖任何模块级全局状态（原来的 configure_raw_result_store()/
# 模块级 `_raw_result_store` 单例已删除——那正是"多个 Agent/SubAgent 实例
# 在同一进程内先后构造，互相覆盖全局指针"这个 bug 的根源）。现在提示文案
# 里给的就是一个普通文件路径，本工具只是 read_file 的一个薄别名，直接按
# 路径读取磁盘文件，语义上与 read_file 完全一致，保留独立工具名只是为了
# 兼容历史上"trimmed 结果里提示调用 view_raw_result"这句话，不强制迁移
# 调用方去认识 read_file。


@tool(
    name="view_raw_result",
    description=(
        "View the full, untruncated original output of a previous tool call "
        "that was truncated or LLM-summarized (its trimmed result will mention "
        "a file path you can pass here). Equivalent to read_file on that path — "
        "supports an optional line range so you don't have to dump everything "
        "back into context."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The file path mentioned in a previously trimmed/summarized tool result.",
            },
            "start_line": {"type": "integer", "description": "First line to return (1-indexed)"},
            "end_line": {"type": "integer", "description": "Last line to return (inclusive)"},
        },
        "required": ["path"],
    },
    requires_approval=False,
)
def view_raw_result(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    """Retrieve the full original content of a previously truncated/summarized tool result by path."""
    import os

    if not path or not os.path.isfile(path):
        return f"[error: no raw result file found at path={path!r} (it may have been cleaned up)]"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return f"[error: failed to read {path!r}: {exc}]"

    if start_line is None and end_line is None:
        return content

    lines = content.splitlines(keepends=True)
    sl = (start_line - 1) if start_line else 0
    el = end_line if end_line else len(lines)
    sl = max(0, sl)
    el = min(len(lines), el)
    selected = lines[sl:el]
    numbered = "".join(f"{sl + i + 1:6}\t{line}" for i, line in enumerate(selected))
    return numbered or "(empty range)"


# ── recall_decisions [决策/取舍知识提炼计划 5.4 节，路径 C] ──────────────────
# 只读工具，供 agent 在自己意识到"这是个需要取舍的架构/技术决定"时主动调用，
# 查一遍 wiki/decisions/*.md 里是否已经讨论过。不同于路径 B（每轮启发式门控
# 自动触发），这里完全由 agent 自主决定要不要查——哪怕门控没命中，agent 主动
# 查也能拿到收益，覆盖"用户直接问技术选型"这类门控关键词可能漏判的场景。

_decision_recall_paths = None       # AgentPaths，由 configure_decision_recall 注入
_decision_recall_llm_call = None    # 可选，供 wiki_shelf_search 的 LLM 精排阶段使用


def configure_decision_recall(paths, llm_call=None) -> None:
    """由 Agent 初始化时注入 AgentPaths + 可选 llm_call，供 recall_decisions() 使用。"""
    global _decision_recall_paths, _decision_recall_llm_call
    _decision_recall_paths = paths
    _decision_recall_llm_call = llm_call


@tool(
    name="recall_decisions",
    description=(
        "Search past architectural/design decisions recorded in this project's "
        "decision wiki (wiki/decisions/*.md). Call this BEFORE proposing a new "
        "approach, reconsidering a past choice, or answering a question about "
        "why something was built a certain way — it tells you whether the topic "
        "was already settled (adopted) or already tried and rejected (overturned), "
        "so you don't re-litigate a decision or repeat a rejected approach."
    ),
    schema={
        "type": "object",
        "properties": {
            "proposal": {
                "type": "string",
                "description": "The topic or new approach you're about to propose/discuss, in your own words.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tag filters to narrow the search.",
            },
        },
        "required": ["proposal"],
    },
    requires_approval=False,
)
def recall_decisions(proposal: str, tags: Optional[list] = None) -> str:
    """查询相关历史决策，命中时返回可直接阅读的提醒文字。"""
    if _decision_recall_paths is None:
        return "[error: decision recall is not enabled]"
    try:
        from mini_agent.evolution.decision_recall import recall_related_decisions
        note = recall_related_decisions(
            _decision_recall_paths, proposal, tags=tags, llm_call=_decision_recall_llm_call,
        )
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.builtin.recall_decisions')
        return f"[error: decision recall failed: {e}]"
    return note or "No related historical decisions found for this topic."


def configure_web_search(cfg) -> None:
    """注入 AppConfig，供 web_search 工具读取 provider/api_key/timeout 等配置。"""
    global _web_search_cfg
    _web_search_cfg = cfg


@tool(
    name="web_search",
    description=(
        "Search the web for up-to-date information. "
        "Returns titles, URLs, and snippets of the top results. "
        "Use when you need documentation, error messages, or recent info. "
        "Default provider is DuckDuckGo (free, no API key). "
        "Optionally pass 'provider' to use a different backend for this call "
        "(e.g. 'brave', 'serper', 'tavily' — requires the matching *_API_KEY env var)."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5)",
            },
            "provider": {
                "type": "string",
                "description": (
                    "Override the configured search provider for this call only. "
                    "One of: duckduckgo, brave, serper, tavily."
                ),
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
)
def web_search(query: str, max_results: Optional[int] = None, provider: Optional[str] = None) -> str:
    from mini_agent.config import AppConfig
    from mini_agent.web_search import WebSearchError, create_web_search_provider

    cfg = _web_search_cfg or AppConfig()
    n = max_results or getattr(cfg.web_search, "max_results", 5)

    try:
        impl = create_web_search_provider(cfg, provider=provider)
        results = impl.search(query, max_results=n)
        return impl.format_results(query, results)
    except WebSearchError as exc:
        return f"[web_search error] {exc}"
    except ValueError as exc:
        # 未知 provider 名称
        return f"[web_search error] {exc}"


# ── record_artifact [产出物看板] ──────────────────────────────────────────────

# 由 Agent 初始化时注入 (project_root, session_id_getter)，供 record_artifact()
# 工具使用；未注入时工具直接报错提示（不会静默失败误导 Agent）。
_artifact_project_root = None
_artifact_session_id_getter = None


def configure_artifact_tool(project_root, session_id_getter) -> None:
    """由 Agent 初始化时注入 project_root 与 session_id 的懒引用（session 可能
    在 Agent 构造完成后才创建/恢复，所以用 getter 而不是直接传值）。"""
    global _artifact_project_root, _artifact_session_id_getter
    _artifact_project_root = project_root
    _artifact_session_id_getter = session_id_getter


@tool(
    name="record_artifact",
    description=(
        "Register one or more output files (documents, images, PDFs, etc.) as a "
        "single 'artifact' so the user can view them in the Artifacts Dashboard "
        "instead of a plain file path printed to the terminal. "
        "\n\n"
        "USE THIS whenever you have just produced a deliverable that is awkward "
        "to show in a chat/CLI response — a Word/PowerPoint/Excel document, a "
        "PDF, an image or chart, a rendered diagram, etc. — and you want the "
        "user to be able to open/preview/download it properly. "
        "\n\n"
        "Do NOT use this for plain text/code/markdown you can just show inline "
        "in your response, and do not call it again for a file you already "
        "registered and haven't changed. "
        "\n\n"
        "After a successful call, tell the user their output is ready and that "
        "they can view it in the Artifacts Dashboard (Kanban app -> "
        "'🖼️ 产出预览' tab); the tool result includes a manifest_id you can "
        "mention if useful."
    ),
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short human-readable title for this artifact, e.g. '季度报告生成' or 'Sales trend chart'.",
            },
            "files": {
                "type": "array",
                "description": (
                    "List of output files belonging to this artifact. Each item is "
                    "either a plain file path string, or an object "
                    "{path, type?, title?} where type is one of "
                    "image|document|pdf|code|text|other (auto-inferred from the "
                    "file extension if omitted)."
                ),
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["image", "document", "pdf", "code", "text", "other"],
                                },
                                "title": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    ]
                },
            },
            "description": {
                "type": "string",
                "description": "Optional longer description of what this artifact is / how it was produced.",
            },
        },
        "required": ["title", "files"],
    },
    requires_approval=False,
)
def record_artifact(title: str, files: list, description: Optional[str] = None) -> str:
    """登记一次产出物，供「产出预览」看板展示（详见 docs/artifacts-dashboard-guide.md）。"""
    if _artifact_project_root is None:
        return "[error: record_artifact is not configured (missing project_root); this should not happen — please report it)]"

    session_id = _artifact_session_id_getter() if _artifact_session_id_getter else ""
    if not session_id:
        return "[error: no active session_id available; cannot register artifact without a session]"

    if not files:
        return "[error: 'files' must contain at least one file]"

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.storage.artifacts import record_artifact as _record_artifact

        paths = AgentPaths(_artifact_project_root)
        manifest = _record_artifact(
            paths,
            session_id,
            title,
            files,
            description=description,
            source={"tool": "record_artifact", "auto_detected": False},
        )
    except (ValueError, OSError) as exc:
        return f"[error: failed to register artifact: {exc}]"

    file_lines = "\n".join(f"  - [{f.type}] {f.title} ({f.path})" for f in manifest.files)
    return (
        f"Artifact registered: manifest_id={manifest.manifest_id!r}\n"
        f"Title: {manifest.title}\n"
        f"Files:\n{file_lines}\n"
        f"The user can view this in the Artifacts Dashboard (产出预览 tab)."
    )