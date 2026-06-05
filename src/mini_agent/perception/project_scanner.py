"""
perception/project_scanner.py — 项目结构感知。

启动时扫描项目根目录，生成一份轻量的项目快照，注入到 system prompt。
扫描是只读操作，不依赖任何外部服务。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 需要读取的依赖文件映射：文件名 → 友好标签
_MANIFEST_FILES = {
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "package.json": "Node.js",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java/Maven",
    "build.gradle": "Java/Gradle",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "CMakeLists.txt": "C/C++",
}

# 跳过的目录（太大或无关）
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", "dist", "build", ".eggs", "*.egg-info", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "target", "vendor",
}

# 源代码扩展名 → 语言名
_LANG_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "React/JSX", ".tsx": "React/TSX", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".cpp": "C++", ".c": "C", ".cs": "C#",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".sh": "Shell", ".bash": "Bash", ".zsh": "Zsh",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sql": "SQL", ".md": "Markdown", ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".toml": "TOML",
}


@dataclass
class ProjectSnapshot:
    root: Path
    tree: str                            # 目录树文本（深度≤2）
    languages: list[str]                 # 检测到的语言列表
    dependencies: dict[str, str]         # {"Python": "anthropic, rich, ..."}
    git_branch: str                      # 当前 git 分支
    git_dirty: bool                      # 是否有未提交改动
    total_files: int                     # 源码文件总数
    key_files: list[str]                 # 重要文件（入口/配置）

    def to_prompt_block(self) -> str:
        """生成注入 system prompt 的文本块。"""
        lines = ["## Current project"]

        if self.git_branch:
            dirty = " (uncommitted changes)" if self.git_dirty else ""
            lines.append(f"- Git branch: `{self.git_branch}`{dirty}")

        if self.languages:
            lines.append(f"- Languages: {', '.join(self.languages)}")

        if self.total_files:
            lines.append(f"- Source files: {self.total_files}")

        if self.key_files:
            lines.append(f"- Key files: {', '.join(self.key_files[:8])}")

        if self.dependencies:
            for lang, deps in self.dependencies.items():
                lines.append(f"- {lang} deps: {deps}")

        if self.tree:
            lines.append(f"\n### Directory structure\n```\n{self.tree}\n```")

        return "\n".join(lines)


class ProjectScanner:
    """
    轻量项目扫描器。只读，无副作用。

    用法：
        scanner = ProjectScanner()
        snapshot = scanner.scan(Path.cwd())
        prompt_block = snapshot.to_prompt_block()
    """

    def scan(self, root: Path) -> ProjectSnapshot:
        tree = self._scan_tree(root, depth=2)
        langs, total, key_files = self._detect_languages(root)
        deps = self._read_manifests(root)
        branch, dirty = self._git_info(root)
        return ProjectSnapshot(
            root=root,
            tree=tree,
            languages=langs,
            dependencies=deps,
            git_branch=branch,
            git_dirty=dirty,
            total_files=total,
            key_files=key_files,
        )

    # ── Tree ──────────────────────────────────────────────────────────────────

    def _scan_tree(self, root: Path, depth: int = 2) -> str:
        lines: list[str] = [root.name + "/"]
        self._walk(root, lines, prefix="", current_depth=0, max_depth=depth)
        return "\n".join(lines[:80])  # 最多 80 行，避免太长

    def _walk(self, path: Path, lines: list, prefix: str,
              current_depth: int, max_depth: int) -> None:
        if current_depth >= max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return

        entries = [e for e in entries if e.name not in _SKIP_DIRS
                   and not e.name.startswith(".")]

        for i, entry in enumerate(entries):
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{connector}{entry.name}{suffix}")
            if entry.is_dir():
                extension = "    " if is_last else "│   "
                self._walk(entry, lines, prefix + extension,
                           current_depth + 1, max_depth)

    # ── Language detection ────────────────────────────────────────────────────

    def _detect_languages(self, root: Path) -> tuple[list[str], int, list[str]]:
        counts: dict[str, int] = {}
        total = 0
        key_files: list[str] = []

        _key_names = {
            "main.py", "app.py", "index.py", "server.py", "cli.py",
            "main.go", "main.rs", "index.js", "index.ts", "app.js",
            "setup.py", "pyproject.toml", "package.json", "Cargo.toml",
            "README.md", "CLAUDE.md",
        }

        try:
            for f in root.rglob("*"):
                if not f.is_file():
                    continue
                if any(skip in f.parts for skip in _SKIP_DIRS):
                    continue
                ext = f.suffix.lower()
                lang = _LANG_MAP.get(ext)
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1
                    total += 1
                if f.name in _key_names:
                    rel = str(f.relative_to(root))
                    if rel not in key_files:
                        key_files.append(rel)
        except Exception:
            pass

        # 按频率排序，取前 5 种语言
        langs = sorted(counts, key=lambda l: -counts[l])[:5]
        return langs, total, sorted(key_files)[:10]

    # ── Manifest parsing ──────────────────────────────────────────────────────

    def _read_manifests(self, root: Path) -> dict[str, str]:
        deps: dict[str, str] = {}
        for filename, lang in _MANIFEST_FILES.items():
            p = root / filename
            if not p.exists():
                continue
            try:
                snippet = self._extract_deps(p, filename)
                if snippet:
                    deps[lang] = snippet
            except Exception:
                pass
        return deps

    @staticmethod
    def _extract_deps(path: Path, filename: str) -> str:
        text = path.read_text(encoding="utf-8", errors="ignore")

        if filename == "package.json":
            try:
                data = json.loads(text)
                all_deps = {**data.get("dependencies", {}),
                            **data.get("devDependencies", {})}
                names = list(all_deps.keys())[:12]
                return ", ".join(names) + ("..." if len(all_deps) > 12 else "")
            except Exception:
                return ""

        if filename == "requirements.txt":
            pkgs = [
                line.split("==")[0].split(">=")[0].strip()
                for line in text.splitlines()
                if line.strip() and not line.startswith("#")
            ][:12]
            return ", ".join(pkgs)

        if filename == "pyproject.toml":
            # 简单正则提取 dependencies 数组
            import re
            deps_block = re.search(
                r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL
            )
            if deps_block:
                pkgs = re.findall(r'"([a-zA-Z0-9_\-]+)', deps_block.group(1))
                return ", ".join(pkgs[:12])
            return ""

        if filename == "go.mod":
            import re
            reqs = re.findall(r"^\s+([^\s]+)\s+v", text, re.MULTILINE)
            return ", ".join(
                r.split("/")[-1] for r in reqs[:10]
            )

        return ""

    # ── Git info ──────────────────────────────────────────────────────────────

    @staticmethod
    def _git_info(root: Path) -> tuple[str, bool]:
        try:
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=root, stderr=subprocess.DEVNULL, timeout=3
            ).decode().strip()
            dirty_output = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=root, stderr=subprocess.DEVNULL, timeout=3
            ).decode().strip()
            return branch, bool(dirty_output)
        except Exception:
            return "", False
