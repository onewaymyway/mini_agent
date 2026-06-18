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
        except Exception:
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

@tool(
    name="grep",
    description=(
        "Search for a regex pattern in files. "
        "Returns file:line:content for each match, with optional surrounding context lines. "
        "Use context_lines to see code around each match without a separate read_file call. "
        "Reports total match count and truncation status."
    ),
    schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {"type": "string", "description": "File or directory to search"},
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
) -> str:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"[invalid regex: {e}]"

    context_lines = max(0, min(context_lines, 10))
    max_results = max(1, min(max_results, 500))
    root = Path(path).expanduser()

    # 收集所有匹配，记录 (fpath, line_index) 以便后续提取上下文
    Match = tuple  # (rel_path_str, all_lines_list, match_line_idx_0based)
    matches: list[Match] = []
    total_found = 0

    files = [root] if root.is_file() else sorted(root.rglob(file_pattern))
    for fpath in files:
        if not fpath.is_file():
            continue
        try:
            all_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        rel = str(fpath.relative_to(root)) if root.is_dir() else str(fpath)
        for i, line in enumerate(all_lines):
            if regex.search(line):
                total_found += 1
                if len(matches) < max_results:
                    matches.append((rel, all_lines, i))

    if not matches:
        return "(no matches)"

    # 渲染结果
    out: list[str] = []
    truncated = total_found > max_results

    if context_lines == 0:
        # 简洁模式：file:lineno:content
        for rel, all_lines, idx in matches:
            out.append(f"{rel}:{idx + 1}:{all_lines[idx].rstrip()}")
    else:
        # 上下文模式：按文件分组，块之间用分隔符
        # 先按文件名分组，再合并相邻/重叠的上下文块
        from itertools import groupby
        keyed = [(rel, idx) for rel, _, idx in matches]
        file_to_matches: dict[str, list] = {}
        for rel, all_lines, idx in matches:
            file_to_matches.setdefault(rel, (all_lines, []))[1].append(idx)

        for rel, (all_lines, idxs) in file_to_matches.items():
            # 合并重叠的上下文区间
            intervals: list[tuple[int, int, set[int]]] = []
            for idx in sorted(idxs):
                lo = max(0, idx - context_lines)
                hi = min(len(all_lines) - 1, idx + context_lines)
                if intervals and lo <= intervals[-1][1] + 1:
                    prev_lo, prev_hi, prev_hits = intervals[-1]
                    intervals[-1] = (prev_lo, max(prev_hi, hi), prev_hits | {idx})
                else:
                    intervals.append((lo, hi, {idx}))

            for seg_idx, (lo, hi, hit_set) in enumerate(intervals):
                if seg_idx > 0:
                    out.append("---")
                for ln in range(lo, hi + 1):
                    marker = ">" if ln in hit_set else " "
                    out.append(f"{rel}:{ln + 1}{marker} {all_lines[ln].rstrip()}")

    summary = f"\n[{total_found} match{'es' if total_found != 1 else ''} found"
    if truncated:
        summary += f", showing first {max_results}"
    summary += "]"
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

