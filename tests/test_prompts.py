"""
tests/test_prompts.py

Full test suite for the PromptManager:
  - file loading & caching
  - comment stripping
  - {{ variable }} template rendering
  - fragment parsing (single-line & block)
  - {placeholder} substitution in fragments
  - build_system_prompt composition
  - get_compact_prompt
  - error handling (missing keys, missing files, unresolved vars)
  - reload() cache invalidation
  - list_prompts() introspection
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

# ── Make project root importable ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.prompts.manager import (
    PromptManager,
    PromptNotFoundError,
    PromptRenderError,
    _strip_comments,
    _render_template,
    _parse_fragments,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_pm(tmp_path: Path, files: dict[str, str]) -> PromptManager:
    """Create a PromptManager wired to a tmp directory with given files."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return PromptManager(tmp_path)


# ══════════════════════════════════════════════════════════════════════════════
# Unit tests for internal helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestStripComments(unittest.TestCase):

    def test_removes_comment_lines(self):
        text = "# This is a comment\nHello world\n# Another comment\nFoo"
        result = _strip_comments(text)
        self.assertNotIn("# This", result)
        self.assertNotIn("# Another", result)
        self.assertIn("Hello world", result)
        self.assertIn("Foo", result)

    def test_keeps_inline_hash(self):
        """A # mid-line (not at the start) should be preserved."""
        text = "color: #ff0000\n# comment\ntext"
        result = _strip_comments(text)
        self.assertIn("#ff0000", result)
        self.assertNotIn("# comment", result)

    def test_empty_input(self):
        self.assertEqual(_strip_comments(""), "")

    def test_only_comments(self):
        text = "# one\n# two\n# three"
        self.assertEqual(_strip_comments(text), "")

    def test_strips_leading_trailing_blank_lines(self):
        text = "\n\n# comment\nContent\n\n"
        result = _strip_comments(text)
        self.assertEqual(result.strip(), "Content")

    def test_strips_bare_hash_separator_line(self):
        """A lone '#' line (no trailing space), commonly used as a blank
        separator inside a comment header block (e.g. '# path\\n#\\n# desc'),
        must be stripped too — not just lines starting with '# '. Without
        this, a stray '#' character leaks into the start of the rendered
        prompt (regression: goal_judge.md / goal_spec_builder.md headers)."""
        text = "# path/to/file.md\n#\n# description here\n\nActual content"
        result = _strip_comments(text)
        self.assertEqual(result, "Actual content")
        self.assertNotIn("#", result)

    def test_bare_hash_with_trailing_whitespace_is_stripped(self):
        text = "# header\n#   \nContent"
        result = _strip_comments(text)
        self.assertEqual(result, "Content")


class TestRenderTemplate(unittest.TestCase):

    def test_basic_substitution(self):
        text = "Hello {{ name }}!"
        result = _render_template(text, "test", {"name": "World"})
        self.assertEqual(result, "Hello World!")

    def test_multiple_vars(self):
        text = "{{ a }} + {{ b }} = {{ c }}"
        result = _render_template(text, "test", {"a": "1", "b": "2", "c": "3"})
        self.assertEqual(result, "1 + 2 = 3")

    def test_unresolved_placeholder_left_as_is(self):
        """Variables not in the dict are left untouched (they may be optional)."""
        text = "Hello {{ name }}, your score is {{ score }}."
        result = _render_template(text, "test", {"name": "Alice"})
        self.assertIn("Alice", result)
        self.assertIn("{{ score }}", result)

    def test_no_variables(self):
        text = "Plain text, no vars."
        result = _render_template(text, "test", {})
        self.assertEqual(result, text)

    def test_none_value_renders_empty(self):
        text = "Value: {{ val }}"
        result = _render_template(text, "test", {"val": None})
        self.assertEqual(result, "Value: ")

    def test_numeric_value(self):
        text = "Count: {{ n }}"
        result = _render_template(text, "test", {"n": 42})
        self.assertEqual(result, "Count: 42")

    def test_whitespace_inside_braces(self):
        """{{ var }} and {{var}} should both work."""
        text = "{{x}} and {{ y }}"
        result = _render_template(text, "test", {"x": "A", "y": "B"})
        self.assertEqual(result, "A and B")


class TestParseFragments(unittest.TestCase):

    def test_simple_key_value(self):
        content = "KEY1: value one\nKEY2: value two\n"
        frags = _parse_fragments(content)
        self.assertEqual(frags["KEY1"], "value one")
        self.assertEqual(frags["KEY2"], "value two")

    def test_comment_lines_skipped(self):
        content = "# This is a comment\nKEY: value\n"
        frags = _parse_fragments(content)
        self.assertIn("KEY", frags)
        self.assertNotIn("# This is a comment", frags)

    def test_block_value(self):
        content = textwrap.dedent("""\
            BANNER: |
              Line one
              Line two
              Line three
        """)
        frags = _parse_fragments(content)
        self.assertIn("BANNER", frags)
        val = frags["BANNER"]
        self.assertIn("Line one", val)
        self.assertIn("Line two", val)
        self.assertIn("Line three", val)

    def test_empty_content(self):
        self.assertEqual(_parse_fragments(""), {})

    def test_only_comments(self):
        content = "# one\n# two\n"
        self.assertEqual(_parse_fragments(content), {})

    def test_value_with_colon(self):
        """Values that contain colons should be parsed correctly."""
        content = "URL: https://example.com/path\n"
        frags = _parse_fragments(content)
        self.assertEqual(frags["URL"], "https://example.com/path")


# ══════════════════════════════════════════════════════════════════════════════
# Integration tests using a real temp directory
# ══════════════════════════════════════════════════════════════════════════════

class TestPromptManagerLoad(unittest.TestCase):

    def setUp(self):
        import tempfile, os
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_plain_file(self):
        pm = make_pm(self.tmp, {"system/hello.md": "# comment\nHello world"})
        result = pm.render("system/hello")
        self.assertEqual(result, "Hello world")

    def test_render_with_variable(self):
        pm = make_pm(self.tmp, {"system/greet.md": "# comment\nHello {{ name }}!"})
        # Use render_with() when the variable name clashes with the prompt_name param
        result = pm.render_with("system/greet", {"name": "Alice"})
        self.assertEqual(result, "Hello Alice!")

    def test_render_kwargs_with_safe_varname(self):
        pm = make_pm(self.tmp, {"system/greet2.md": "Count: {{ count }}"})
        result = pm.render("system/greet2", count=42)
        self.assertEqual(result, "Count: 42")

    def test_missing_prompt_raises(self):
        pm = make_pm(self.tmp, {})
        with self.assertRaises(PromptNotFoundError):
            pm.render("system/nonexistent")

    def test_caching(self):
        pm = make_pm(self.tmp, {"system/cached.md": "Content"})
        r1 = pm.render("system/cached")
        r2 = pm.render("system/cached")
        self.assertEqual(r1, r2)
        self.assertEqual(len(pm._raw_cache), 1)

    def test_reload_clears_cache(self):
        pm = make_pm(self.tmp, {"system/evolving.md": "Version 1"})
        pm.render("system/evolving")
        self.assertEqual(len(pm._raw_cache), 1)
        pm.reload()
        self.assertEqual(len(pm._raw_cache), 0)
        # After reload, updated content should be read
        (self.tmp / "system" / "evolving.md").write_text("Version 2")
        result = pm.render("system/evolving")
        self.assertEqual(result, "Version 2")

    def test_list_prompts(self):
        pm = make_pm(self.tmp, {
            "system/a.md": "A",
            "system/b.md": "B",
            "user/c.md": "C",
            "fragments/x.md": "X: y",   # fragments excluded
        })
        names = pm.list_prompts()
        self.assertIn("system/a", names)
        self.assertIn("system/b", names)
        self.assertIn("user/c", names)
        # Fragment files should NOT appear in list_prompts
        self.assertNotIn("fragments/x", names)


class TestPromptManagerFragments(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_frag_pm(self, content: str) -> PromptManager:
        return make_pm(self.tmp, {"fragments/ui.md": content})

    def test_simple_fragment(self):
        pm = self._make_frag_pm("HELLO: World\n")
        self.assertEqual(pm.fragment("ui", "HELLO"), "World")

    def test_fragment_with_placeholder(self):
        pm = self._make_frag_pm("MSG: Hello {name}!\n")
        self.assertEqual(pm.fragment("ui", "MSG", name="Bob"), "Hello Bob!")

    def test_fragment_missing_key_raises(self):
        pm = self._make_frag_pm("A: alpha\n")
        with self.assertRaises(PromptNotFoundError):
            pm.fragment("ui", "MISSING")

    def test_fragment_missing_placeholder_raises(self):
        pm = self._make_frag_pm("MSG: Hello {name}!\n")
        with self.assertRaises(PromptRenderError):
            pm.fragment("ui", "MSG")   # name not provided

    def test_fragment_or_returns_default(self):
        pm = self._make_frag_pm("A: alpha\n")
        result = pm.fragment_or("ui", "MISSING_KEY", default="fallback")
        self.assertEqual(result, "fallback")

    def test_fragment_missing_file_raises(self):
        pm = PromptManager(self.tmp)
        with self.assertRaises(PromptNotFoundError):
            pm.fragment("no_such_file", "KEY")

    def test_list_fragments(self):
        pm = self._make_frag_pm("A: one\nB: two\nC: three\n")
        keys = pm.list_fragments("ui")
        self.assertIn("A", keys)
        self.assertIn("B", keys)
        self.assertIn("C", keys)

    def test_block_fragment(self):
        content = textwrap.dedent("""\
            BANNER: |
              Line 1
              Line 2
            OTHER: simple
        """)
        pm = self._make_frag_pm(content)
        banner = pm.fragment("ui", "BANNER")
        self.assertIn("Line 1", banner)
        self.assertIn("Line 2", banner)
        self.assertEqual(pm.fragment("ui", "OTHER"), "simple")

    def test_fragment_cache(self):
        pm = self._make_frag_pm("KEY: value\n")
        pm.fragment("ui", "KEY")
        pm.fragment("ui", "KEY")
        self.assertEqual(len(pm._fragment_cache), 1)


class TestBuildSystemPrompt(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_prompt_pm(self) -> PromptManager:
        """Create PM with the real prompt files from the project."""
        real_prompts = PROJECT_ROOT / "src" / "mini_agent" / "prompts"
        return PromptManager(real_prompts)

    def test_core_prompt_included(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt()
        self.assertIn("coding assistant", result.lower())

    def test_claude_md_included_when_provided(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(claude_md_content="## My Project\nUse tabs.")
        self.assertIn("My Project", result)
        self.assertIn("Use tabs.", result)

    def test_claude_md_excluded_when_empty(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(claude_md_content="")
        self.assertNotIn("Project context", result)

    def test_skills_included_when_active(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(
            active_skills=["python-expert"],
            skill_context="Use type hints everywhere.",
        )
        self.assertIn("python-expert", result)
        self.assertIn("Use type hints everywhere.", result)

    def test_skills_excluded_when_none(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(active_skills=None)
        self.assertNotIn("Active skills", result)

    def test_sandbox_section_included(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(sandbox=True)
        self.assertIn("SANDBOX", result)

    def test_sandbox_section_excluded_when_off(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(sandbox=False)
        self.assertNotIn("SANDBOX", result)

    def test_system_extra_included(self):
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(system_extra="Always respond in French.")
        self.assertIn("Always respond in French.", result)

    def test_composition_order(self):
        """Sandbox section must appear after all other sections."""
        pm = self._make_prompt_pm()
        result = pm.build_system_prompt(
            claude_md_content="Project info",
            system_extra="Extra instruction",
            sandbox=True,
        )
        sandbox_pos = result.find("SANDBOX")
        extra_pos = result.find("Extra instruction")
        core_pos = result.find("coding assistant")
        self.assertGreater(sandbox_pos, core_pos)
        self.assertGreater(sandbox_pos, extra_pos)


class TestGetCompactPrompt(unittest.TestCase):

    def test_returns_non_empty_string(self):
        real_pm = PromptManager(PROJECT_ROOT / "src" / "mini_agent" / "prompts")
        result = real_pm.get_compact_prompt()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 20)

    def test_contains_meaningful_content(self):
        real_pm = PromptManager(PROJECT_ROOT / "src" / "mini_agent" / "prompts")
        result = real_pm.get_compact_prompt()
        # Should ask for a summary of some kind
        lower = result.lower()
        self.assertTrue(
            any(word in lower for word in ["summary", "summarize", "concise", "decisions"]),
            f"Compact prompt doesn't seem to ask for a summary: {result[:200]}"
        )


class TestRealPromptFiles(unittest.TestCase):
    """Smoke-test the actual prompt files in the project."""

    def setUp(self):
        self.pm = PromptManager(PROJECT_ROOT / "src" / "mini_agent" / "prompts")

    def test_all_system_prompts_render(self):
        for name in self.pm.list_prompts():
            if name.startswith("system/"):
                # Render with dummy vars so optional ones don't block
                try:
                    result = self.pm.render(
                        name,
                        claude_md_content="dummy",
                        skill_list="- dummy",
                        skill_context="dummy context",
                    )
                    self.assertIsInstance(result, str, f"{name} rendered non-string")
                except PromptNotFoundError:
                    self.fail(f"Prompt file {name!r} not found unexpectedly")

    def test_cli_messages_fragments_loadable(self):
        keys = self.pm.list_fragments("cli_messages")
        self.assertIn("BANNER", keys)
        self.assertIn("BYE_MSG", keys)
        self.assertIn("HISTORY_CLEARED", keys)

    def test_permission_labels_fragments_loadable(self):
        keys = self.pm.list_fragments("permission_labels")
        self.assertIn("DANGEROUS_LABEL", keys)
        self.assertIn("CHOICE_HINT", keys)
        self.assertIn("SESSION_DENIED_MSG", keys)

    def test_banner_has_content(self):
        banner = self.pm.fragment("cli_messages", "BANNER", version="test")
        self.assertGreater(len(banner.strip()), 0)

    def test_session_denied_msg_interpolation(self):
        msg = self.pm.fragment("permission_labels", "SESSION_DENIED_MSG", tool_name="bash")
        self.assertIn("bash", msg)

    def test_sandbox_blocked_interpolation(self):
        msg = self.pm.fragment("permission_labels", "SANDBOX_BLOCKED", tool_name="write_file")
        self.assertIn("write_file", msg)


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
