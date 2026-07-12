"""
prompts/manager.py — Prompt Management Module

统一管理所有 prompt 文件的加载、渲染和组合。

目录结构：
    prompts/
      system/          ← system prompt 片段（.md 文件）
        agent_core.md
        sandbox_mode.md
        project_context.md
        active_skills.md
      user/            ← user 角色的预设消息（.md 文件）
        compact_history.md
      fragments/       ← 细粒度 UI 文本片段（key: value 格式）
        cli_messages.md
        permission_labels.md

用法：
    pm = PromptManager()                        # 默认用包内 prompts/ 目录
    pm = PromptManager("/path/to/prompts")      # 自定义目录

    # 渲染 system prompt
    text = pm.render("system/agent_core")
    text = pm.render("system/project_context", claude_md_content="...")

    # 获取 fragment 键值
    msg = pm.fragment("cli_messages", "BANNER")
    msg = pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model="claude-opus-4-5")

    # 构建完整 system prompt
    full = pm.build_system_prompt(cfg, active_skills, skill_context)
"""

from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path
from typing import Any, Optional

# ── 模块内常量 ─────────────────────────────────────────────────────────────────

_DEFAULT_PROMPTS_DIR = Path(__file__).parent
_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")       # {{ variable }}
_FRAGMENT_PATTERN = re.compile(r"^(\w+):\s*(.*)$")       # KEY: value
_BLOCK_FRAGMENT_PATTERN = re.compile(                     # KEY: |\n  indented...
    r"^(\w+):\s*\|\s*\n((?:[ \t]+.*\n?)*)", re.MULTILINE
)


class PromptNotFoundError(FileNotFoundError):
    pass


class PromptRenderError(ValueError):
    pass


# ── PromptManager ──────────────────────────────────────────────────────────────

class PromptManager:
    """
    Loads prompt files from disk, renders variable placeholders,
    and assembles composite prompts.

    All prompt files are plain Markdown (.md).
    Lines starting with `#` are treated as comments and stripped before use.
    """

    def __init__(
        self,
        prompts_dir: Optional[Path | str] = None,
        custom_dir: Optional[Path | str] = None,
    ) -> None:
        self._root = Path(prompts_dir) if prompts_dir else _DEFAULT_PROMPTS_DIR
        # 用户自定义 prompts 目录（可选）。查找时优先于 _root，
        # 若自定义目录中不存在某文件，再回退到 _root（项目默认目录）。
        self._custom_root: Optional[Path] = Path(custom_dir) if custom_dir else None
        # Two-level cache: raw text per file, parsed fragments per fragment file
        self._raw_cache: dict[str, str] = {}
        self._fragment_cache: dict[str, dict[str, str]] = {}

    def set_custom_dir(self, custom_dir: Optional[Path | str]) -> None:
        """
        设置/更新用户自定义 prompts 目录，并清空缓存。

        加载顺序：custom_dir 优先，找不到则回退到默认 _root。
        传入 None 可清除自定义目录，恢复仅使用默认目录。
        """
        self._custom_root = Path(custom_dir) if custom_dir else None
        self.reload()

    @property
    def custom_dir(self) -> Optional[Path]:
        return self._custom_root

    def _search_roots(self) -> list[Path]:
        """返回按优先级排序的查找目录列表：自定义目录优先，默认目录回退。"""
        roots = []
        if self._custom_root is not None:
            roots.append(self._custom_root)
        roots.append(self._root)
        return roots

    # ── Core API ───────────────────────────────────────────────────────────────

    def render(self, prompt_name: str, **variables: Any) -> str:
        """
        Load a prompt file by logical name (relative path without .md),
        strip comments, substitute {{ variable }} placeholders, and return.

        Examples:
            pm.render("system/agent_core")
            pm.render("system/project_context", claude_md_content="...")
            pm.render("user/compact_history")

        Note: the first argument is named 'prompt_name' (not 'name') so that
        callers can freely use name="..." as a template variable without conflict.
        """
        raw = self._load(prompt_name)
        stripped = _strip_comments(raw)
        return _render_template(stripped, prompt_name, variables)

    def render_with(self, prompt_name: str, variables: dict) -> str:
        """
        Alternative to render() that accepts a plain dict instead of **kwargs.
        Useful when variable names clash with Python keywords.

        Example:
            pm.render_with("system/greet", {"name": "Alice"})
        """
        raw = self._load(prompt_name)
        stripped = _strip_comments(raw)
        return _render_template(stripped, prompt_name, variables)

    def fragment(self, file: str, key: str, **variables: Any) -> str:
        """
        Get a named text fragment from a fragments/ file.
        Supports {placeholder} substitution (single-brace, Python str.format style).

        Examples:
            pm.fragment("cli_messages", "BANNER")
            pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model="claude-3-5")
            pm.fragment("permission_labels", "SESSION_DENIED_MSG", tool_name="bash")
        """
        fragments = self._load_fragments(file)
        if key not in fragments:
            raise PromptNotFoundError(
                f"Fragment key {key!r} not found in {file!r}. "
                f"Available: {sorted(fragments)}"
            )
        value = fragments[key]
        # Detect {placeholder} patterns in the value
        import re as _re
        _placeholder_re = _re.compile(r"\{(\w+)\}")
        placeholders = _placeholder_re.findall(value)
        if placeholders and not variables:
            raise PromptRenderError(
                f"Fragment {file}/{key} requires variables {placeholders} but none were provided."
            )
        if variables:
            try:
                value = value.format(**variables)
            except KeyError as e:
                raise PromptRenderError(
                    f"Fragment {file}/{key} missing variable {e} "
                    f"(provided: {list(variables)})"
                ) from e
        return value

    def fragment_or(self, file: str, key: str, default: str = "", **variables: Any) -> str:
        """Like fragment() but returns `default` if key is missing."""
        try:
            return self.fragment(file, key, **variables)
        except PromptNotFoundError:
            return default

    # ── Composite builders ─────────────────────────────────────────────────────

    def build_system_prompt(
        self,
        claude_md_content: str = "",
        active_skills: Optional[list[str]] = None,
        skill_context: str = "",
        system_extra: str = "",
        sandbox: bool = False,
        current_time: str = "",
        agent_name: str = "orzooo",
        user_profile: str = "",
        env_info: str = "",
    ) -> str:
        """
        Assemble the complete system prompt from individual fragments.
        This is the single authoritative place where system prompts are composed.
        """
        parts: list[str] = []

        # 1. Core identity
        parts.append(self.render("system/agent_core", agent_name=agent_name))

        # 1b. Workspace hygiene & graceful operation standards
        try:
            parts.append(self.render("system/workspace_hygiene"))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.prompts.manager')
            pass

        # 1c. Execution planning capability
        try:
            parts.append(self.render("system/plan_mode"))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.prompts.manager')
            pass

        # 2. Current time
        if current_time.strip():
            parts.append(self.render("system/current_time", current_time=current_time))

        # 2b. Environment info (OS, Python, timezone, …)
        if env_info.strip():
            parts.append(env_info.strip())

        # 3. Project context (CLAUDE.md)
        if claude_md_content.strip():
            parts.append(
                self.render("system/project_context", claude_md_content=claude_md_content)
            )

        # 3b. User profile (long-term, cross-session)
        if user_profile.strip():
            parts.append(self.render("system/user_profile", user_profile=user_profile))

        # 4. Active skills
        if active_skills:
            skill_list = "\n".join(f"- {s}" for s in active_skills)
            parts.append(
                self.render(
                    "system/active_skills",
                    skill_list=skill_list,
                    skill_context=skill_context,
                )
            )

        # 5. Extra system text (e.g. from --system CLI flag)
        if system_extra.strip():
            parts.append(system_extra.strip())

        # 6. Orchestration capabilities (always before sandbox)
        try:
            from mini_agent.tools.orchestration import get_task_manager
            if get_task_manager() is not None:
                parts.append(self.render("system/orchestration"))
        except ImportError:
            pass

        # 6b. Custom sub-agent profiles (spawn_named_agent)
        try:
            from mini_agent.orchestrator.agent_profiles import get_profile_loader
            loader = get_profile_loader()
            if loader is not None and loader.available:
                lines = []
                for p in loader.get_catalog():
                    req = [i["name"] for i in p["inputs"] if i["required"]]
                    opt = [i["name"] for i in p["inputs"] if not i["required"]]
                    schema_hint = ""
                    if req or opt:
                        bits = []
                        if req:
                            bits.append(f"required: {req}")
                        if opt:
                            bits.append(f"optional: {opt}")
                        schema_hint = " (" + ", ".join(bits) + ")"
                    lines.append(f"- `{p['name']}`: {p['description']}{schema_hint}")
                parts.append(self.render("system/available_subagents", agent_list="\n".join(lines)))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.prompts.manager')
            pass

        # 7. 执行计划上下文（如有活跃计划，注入当前进度）
        try:
            from mini_agent.orchestrator.plan import get_plan
            plan = get_plan()
            if plan is not None:
                plan_block = plan.to_prompt_block()
                if plan_block:
                    parts.append(plan_block)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.prompts.manager')
            pass

        # 8. Sandbox mode warning (always last)
        if sandbox:
            parts.append(self.render("system/sandbox_mode"))

        return "\n\n".join(parts)

    def get_compact_prompt(self) -> str:
        """Return the user message used to compact conversation history."""
        return self.render("user/compact_history")

    # ── Introspection ──────────────────────────────────────────────────────────

    def list_prompts(self) -> list[str]:
        """Return all available prompt logical names (without .md)."""
        seen: set[str] = set()
        result: list[str] = []
        for root in self._search_roots():
            if not root.exists():
                continue
            for md_file in sorted(root.rglob("*.md")):
                rel = md_file.relative_to(root)
                # Exclude fragment files (they use key:value format, not templates)
                if rel.parts[0] != "fragments":
                    name = str(rel.with_suffix("")).replace(os.sep, "/")
                    if name not in seen:
                        seen.add(name)
                        result.append(name)
        return sorted(result)

    def list_fragments(self, file: str) -> list[str]:
        """Return all keys in a fragment file."""
        return sorted(self._load_fragments(file))

    def reload(self) -> None:
        """Clear all caches (useful during development / hot-reload)."""
        self._raw_cache.clear()
        self._fragment_cache.clear()

    # ── Internals ──────────────────────────────────────────────────────────────

    def _load(self, name: str) -> str:
        """Load raw prompt file content, with caching."""
        if name in self._raw_cache:
            return self._raw_cache[name]

        path = None
        for root in self._search_roots():
            candidate = root / (name + ".md")
            if not candidate.exists():
                # Also try without extension (caller may have passed full filename)
                candidate = root / name
            if candidate.exists():
                path = candidate
                break

        if path is None:
            available = self.list_prompts()
            raise PromptNotFoundError(
                f"Prompt {name!r} not found in {self._search_roots()}.\n"
                f"Available prompts: {available}"
            )

        content = path.read_text(encoding="utf-8")
        self._raw_cache[name] = content
        return content

    def _load_fragments(self, file: str) -> dict[str, str]:
        """Parse and cache a fragments file into {KEY: value} dict."""
        if file in self._fragment_cache:
            return self._fragment_cache[file]

        path = None
        for root in self._search_roots():
            candidate = root / "fragments" / (file + ".md")
            if candidate.exists():
                path = candidate
                break
        if path is None:
            raise PromptNotFoundError(
                f"Fragment file {file!r} not found in {[r / 'fragments' for r in self._search_roots()]}"
            )

        content = path.read_text(encoding="utf-8")
        fragments = _parse_fragments(content)
        self._fragment_cache[file] = fragments
        return fragments

    # ── Debug helpers ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        if self._custom_root:
            return (
                f"PromptManager(custom_dir={self._custom_root}, "
                f"root={self._root}, cached={len(self._raw_cache)} files)"
            )
        return f"PromptManager(root={self._root}, cached={len(self._raw_cache)} files)"


# ── Template rendering ─────────────────────────────────────────────────────────

def _strip_comments(text: str) -> str:
    """
    Remove lines that start with `#` (prompt file comments).
    Preserves all other content including blank lines.

    Matches both `# some comment` and a bare `#` used as a blank
    separator line within a comment block (e.g. a header like:
        # path/to/file.md
        #
        # description
    ) — without this, a lone `#` line would leak into the rendered
    output as a stray character since it doesn't start with "# "
    (hash + space) and `.strip()` only trims whitespace, not `#`.
    """
    lines = text.splitlines(keepends=True)
    cleaned = [
        line for line in lines
        if not (line.lstrip().startswith("# ") or line.strip() == "#")
    ]
    # Collapse leading/trailing blank lines
    return "".join(cleaned).strip()


def _render_template(text: str, name: str, variables: dict[str, Any]) -> str:
    """
    Substitute {{ variable }} placeholders.
    Raises PromptRenderError if any placeholder is unresolved.
    """
    if not variables:
        # Still validate — catch missing required vars even with no input
        missing = _VAR_PATTERN.findall(text)
        if missing:
            # Optional vars are OK — only error if caller provided nothing at all
            # and there are unfilled slots (design choice: warn, don't error)
            pass
        return text

    def _replace(m: re.Match) -> str:
        var = m.group(1)
        if var not in variables:
            # Leave unresolved placeholders in place (they may be optional)
            return m.group(0)
        val = variables[var]
        return str(val) if val is not None else ""

    return _VAR_PATTERN.sub(_replace, text)


def _parse_fragments(content: str) -> dict[str, str]:
    """
    Parse a fragment file into {KEY: value}.

    Supports two formats:
      KEY: single line value
      KEY: |
        multi
        line
        value
    Comment lines (# ...) are skipped.
    """
    fragments: dict[str, str] = {}

    # First pass: extract block fragments (KEY: |\n  indented...)
    for m in _BLOCK_FRAGMENT_PATTERN.finditer(content):
        key = m.group(1)
        block = m.group(2)
        # Dedent: find common leading whitespace and strip it
        lines = block.splitlines()
        dedented = textwrap.dedent("\n".join(lines)).strip()
        fragments[key] = dedented

    # Second pass: simple KEY: value lines (skip already-found block keys)
    for line in content.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        m = _FRAGMENT_PATTERN.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key not in fragments and value and not value.startswith("|"):
                fragments[key] = value

    return fragments


# ── Module-level singleton ─────────────────────────────────────────────────────

_default_manager: Optional[PromptManager] = None


def get_prompt_manager(
    prompts_dir: Optional[Path] = None,
    custom_dir: Optional[Path] = None,
) -> PromptManager:
    """
    Return the module-level singleton PromptManager.
    First call initializes it; subsequent calls return the cached instance.
    Pass prompts_dir on first call to override the default location.
    Pass custom_dir to set a user-specified override directory that is
    searched before the default/prompts_dir location.
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = PromptManager(prompts_dir, custom_dir)
    return _default_manager


def reset_prompt_manager() -> None:
    """Force re-initialization on next get_prompt_manager() call (useful in tests)."""
    global _default_manager
    _default_manager = None
