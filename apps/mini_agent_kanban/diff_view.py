"""
[看板与自主性改进方案 Track I] 结构化 diff 解析。

第八轮实施记录里"进化提案"tab 的 diff 展示是一整块 `st.code(diff_text, language="diff")`，
第九轮"未完成/待续"标注为可选增强项：把 unified diff 拆成按文件分组的结构，
附带每个文件的增删行数统计，方便看板做"先看摘要，再展开单个文件"的展示，
而不必一次性阅读整份 diff 文本。

本模块只做纯文本解析，不依赖 Streamlit / 网络请求，方便单独单元测试
（见 `tests/test_kanban_diff_view.py`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileDiff:
    """单个文件在一份 unified diff 里对应的片段。"""

    path: str
    additions: int = 0
    deletions: int = 0
    body: str = ""
    is_binary: bool = False
    change_type: str = "modified"  # modified / added / deleted / renamed

    @property
    def summary(self) -> str:
        if self.is_binary:
            return f"{self.path}（二进制文件，无法显示逐行差异）"
        return f"{self.path}  +{self.additions} / -{self.deletions}"


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """把一份 unified diff（`git diff`/`git show` 输出格式）解析成按文件分组的列表。

    解析策略保守：只依赖 `diff --git a/... b/...` 作为文件边界，行首 `+`/`-`
    （排除 `+++`/`---` 头部行）统计增删行数，`Binary files ... differ` 识别二进制文件。
    解析失败或格式不认识的内容不会抛异常，而是整体归入一个 path 为空字符串的
    "未分类" FileDiff，保证调用方总能拿到至少一项可展示的内容。
    """
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    files: list[FileDiff] = []
    current: FileDiff | None = None
    current_lines: list[str] = []
    leading_lines: list[str] = []

    def _flush():
        nonlocal current, current_lines
        if current is not None:
            current.body = "\n".join(current_lines)
            if current.change_type == "renamed":
                old_p = getattr(current, "_old_path", "")
                new_p = getattr(current, "_new_path", current.path)
                current.path = f"{old_p} → {new_p}"
            files.append(current)
        current = None
        current_lines = []

    for line in lines:
        if line.startswith("diff --git "):
            _flush()
            parts = line[len("diff --git "):].split(" b/", 1)
            old_path = parts[0][2:] if parts[0].startswith("a/") else parts[0]
            new_path = parts[1] if len(parts) > 1 else old_path
            # 先用 `diff --git` 行里的两个路径给出一个默认展示值（大多数场景下
            # old/new 相同，即普通 modified）；如果后面遇到 `rename from/to`
            # 或 `--- /dev/null`/`+++ /dev/null`，再据实修正为 renamed/added/deleted。
            current = FileDiff(path=new_path or old_path, change_type="modified")
            current._old_path = old_path  # type: ignore[attr-defined]
            current._new_path = new_path  # type: ignore[attr-defined]
            current_lines = [line]
            continue

        if current is None:
            # 文件头之前的内容（比如 `commit xxx` / `Author:` 之类的元信息）
            leading_lines.append(line)
            continue

        current_lines.append(line)
        if line.startswith("rename from "):
            current._old_path = line[len("rename from "):]  # type: ignore[attr-defined]
        elif line.startswith("rename to "):
            current._new_path = line[len("rename to "):]  # type: ignore[attr-defined]
            current.change_type = "renamed"
        elif line.startswith("--- "):
            if line[4:].strip() == "/dev/null":
                current.change_type = "added"
        elif line.startswith("+++ "):
            if line[4:].strip() == "/dev/null":
                current.change_type = "deleted"
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            current.is_binary = True
        elif line.startswith("+") and not line.startswith("+++"):
            current.additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.deletions += 1

    _flush()

    if not files:
        # 整份内容都没能识别出 `diff --git` 边界（比如纯文本 diff 或已知格式外的输出），
        # 保守地整体作为一个"未分类"条目返回，保证调用方总有内容可展示。
        return [FileDiff(path="", body=diff_text)]

    return files


def summarize_files(files: list[FileDiff]) -> str:
    """生成一行摘要，例如：`3 个文件改动 · +42 / -7`。"""
    if not files or (len(files) == 1 and not files[0].path):
        return ""
    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)
    return f"{len(files)} 个文件改动 · +{total_add} / -{total_del}"
