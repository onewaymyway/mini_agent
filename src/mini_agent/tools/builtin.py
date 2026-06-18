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
    cwd = Path(workdir).expanduser() if workdir else Path.cwd()

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
        )

        def decode(data: bytes) -> str:
            for enc in ("utf-8", "gbk", "cp936"):
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    pass

            return data.decode("utf-8", errors="replace")

        stdout = decode(result.stdout)
        stderr = decode(result.stderr)

        combined = (stdout + stderr).rstrip()

        if result.returncode != 0:
            combined += f"\n[exit code: {result.returncode}]"

        return combined or "(no output)"

    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"[error: {e}]"


# ── read_file ─────────────────────────────────────────────────────────────────

# [SYS-LARGEFILE] 默认大文件阈值（字节），可通过 config 覆盖
_DEFAULT_LARGE_FILE_THRESHOLD = 20000  # 20 KB

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
        "IMPORTANT: For large files (> 100 KB), always check file size first via list_dir "
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
                except Exception:
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
        "Use patch_file for targeted edits instead of full rewrites."
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
        return f"[error writing {path}: {e}]"


# ── create_file ───────────────────────────────────────────────────────────────

@tool(
    name="create_file",
    description="Create a new file. Fails if the file already exists (use write_file to overwrite).",
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

@tool(
    name="grep",
    description=(
        "Search for a regex pattern in files. "
        "Returns file:line:content for each match. "
        "Max 100 results."
    ),
    schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {"type": "string", "description": "File or directory to search"},
            "file_pattern": {"type": "string", "description": "Glob filter, e.g. '*.py'"},
            "case_sensitive": {"type": "boolean", "description": "Default true"},
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
) -> str:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"[invalid regex: {e}]"

    root = Path(path).expanduser()
    results: list[str] = []

    files = [root] if root.is_file() else root.rglob(file_pattern)
    for fpath in files:
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = fpath.relative_to(root) if root.is_dir() else fpath
                results.append(f"{rel}:{i}:{line.rstrip()}")
                if len(results) >= 100:
                    results.append("[… truncated at 100 results]")
                    return "\n".join(results)

    return "\n".join(results) or "(no matches)"


# ── patch_file ────────────────────────────────────────────────────────────────

@tool(
    name="patch_file",
    description=(
        "Apply a targeted find-and-replace edit to a file. "
        "old_string must exactly match existing content (be as specific as possible). "
        "new_string is the replacement. Returns a unified diff."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit"},
            "old_string": {"type": "string", "description": "Exact content to replace"},
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
        return f"[error reading {path}: {e}]"

    count = original.count(old_string)
    if count == 0:
        return "[error: old_string not found in file]"
    if count > 1:
        return f"[error: old_string matches {count} locations — be more specific]"

    updated = original.replace(old_string, new_string, 1)
    try:
        p.write_text(updated, encoding="utf-8")
    except Exception as e:
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
    return diff or "(no diff — content unchanged)"


# ── web_search ────────────────────────────────────────────────────────────────

# 由 Agent 在初始化时注入（见 agent.py），供 web_search() 工具使用。
# 未注入时 web_search() 会使用一个仅含默认值的 AppConfig（即 duckduckgo，无 key）。
_web_search_cfg = None


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

