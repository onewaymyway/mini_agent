"""
tests/test_skill_cli.py — /skill 命令行功能测试

覆盖：
  - /skills          列表展示（无技能 / 有技能 / 部分激活）
  - /skill on        单个激活 / 批量激活 / 已激活 / 不存在
  - /skill off       单个卸载 / 批量卸载 / 未激活 / 不存在
  - /skill info      显示内容 / 不存在
  - /skill reset     全部卸载 / 无激活时的提示
  - /skill（无参数） 显示用法
  - _suggest_skill   模糊匹配提示
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import patch, call, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from mini_agent.skills import Skill, SkillLoader
from mini_agent.skills.tracker import SkillUsageTracker


# ── SkillLoader stub（不依赖文件系统）────────────────────────────────────────

def make_loader(skill_defs: list[dict]) -> SkillLoader:
    loader = SkillLoader.__new__(SkillLoader)
    loader._dirs   = []
    loader._all    = {}
    loader._active = []
    loader.tracker = SkillUsageTracker()
    from mini_agent.skills.usage_detector import SkillUsageDetector
    loader.detector = SkillUsageDetector()
    loader.detector.build_fingerprints(loader._all)
    for d in skill_defs:
        skill = Skill(
            name         = d["name"],
            description  = d.get("description", f"Skill {d['name']}"),
            location     = Path(f"/fake/{d['name']}/SKILL.md"),
            content      = d.get("content", f"# {d['name']} skill\nSome content here."),
            trigger_words= d.get("trigger_words", [d["name"].lower()]),
        )
        loader._all[skill.name] = skill
    return loader


# ── 导入被测函数 ──────────────────────────────────────────────────────────────

# patch掉 renderer 和 prompts 避免真实 IO
import unittest.mock as _mock

# 伪造 pm.fragment 直接返回 key 本身，方便断言
_fake_pm = MagicMock()
_fake_pm.fragment.side_effect = lambda section, key, **kw: (
    key.format(**kw) if kw else key
)

with _mock.patch.dict("sys.modules", {
    "mini_agent.ui.renderer": _mock.MagicMock(),
    "mini_agent.prompts": _mock.MagicMock(),
}):
    import importlib
    # 让 main 模块用假的 renderer / pm
    import renderer as _R_mock
    import prompts as _P_mock

# 直接 import main 函数，但在测试里 patch renderer 和 pm
with patch("builtins.__import__", side_effect=lambda *a, **k: __import__(*a, **k)):
    pass  # 保持 import 正常

# 真正 import（rich 已安装，不会失败）
from mini_agent.cli.commands.skills import handle_skill_cmd as _handle_skill_cmd, handle_skills_list as _handle_skills_list, _suggest_skill


# ── helpers ───────────────────────────────────────────────────────────────────

class Captures:
    """收集 R.print_success / print_error / print_info 的调用参数。"""
    def __init__(self):
        self.success = []
        self.error   = []
        self.info    = []

    def patch_ctx(self):
        return (
            patch("mini_agent.cli.commands.skills.R.print_success", side_effect=lambda m: self.success.append(m)),
            patch("mini_agent.cli.commands.skills.R.print_error",   side_effect=lambda m: self.error.append(m)),
            patch("mini_agent.cli.commands.skills.R.print_info",    side_effect=lambda m: self.info.append(m)),
        )


def run_skill_cmd(args, loader):
    cap = Captures()
    with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
         cap.patch_ctx()[0], cap.patch_ctx()[1], cap.patch_ctx()[2], \
         patch("mini_agent.cli.commands.skills.R.console.print"), \
         patch("mini_agent.cli.commands.skills.R.print_markdown"):
        _handle_skill_cmd(args, loader)
    return cap


# ── /skill on ────────────────────────────────────────────────────────────────

class TestSkillOn:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs"},
            {"name": "pdf",  "description": "PDF creation"},
            {"name": "excel","description": "Excel sheets"},
        ])

    def test_activate_single(self):
        cap = run_skill_cmd(["on", "docx"], self.loader)
        assert "docx" in self.loader.active
        assert any("SKILL_ACTIVATED" in m or "docx" in m for m in cap.success)

    def test_activate_multiple(self):
        run_skill_cmd(["on", "docx", "pdf"], self.loader)
        assert "docx" in self.loader.active
        assert "pdf"  in self.loader.active

    def test_activate_all_three(self):
        run_skill_cmd(["on", "docx", "pdf", "excel"], self.loader)
        assert set(self.loader.active) == {"docx", "pdf", "excel"}

    def test_already_active_gives_info_not_error(self):
        self.loader.activate("docx")
        cap = run_skill_cmd(["on", "docx"], self.loader)
        assert len(cap.error) == 0
        assert any("SKILL_ALREADY_ACTIVE" in m or "docx" in m for m in cap.info)

    def test_nonexistent_gives_error(self):
        cap = run_skill_cmd(["on", "ghost"], self.loader)
        assert "ghost" not in self.loader.active
        assert len(cap.error) > 0

    def test_mixed_valid_invalid(self):
        cap = run_skill_cmd(["on", "docx", "ghost"], self.loader)
        assert "docx" in self.loader.active
        assert len(cap.error) > 0       # ghost → error
        assert len(cap.success) > 0     # docx  → success

    def test_no_names_gives_error(self):
        cap = run_skill_cmd(["on"], self.loader)
        assert len(cap.error) > 0
        assert self.loader.active == []


# ── /skill off ────────────────────────────────────────────────────────────────

class TestSkillOff:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs"},
            {"name": "pdf",  "description": "PDF creation"},
        ])
        self.loader.activate("docx")
        self.loader.activate("pdf")

    def test_deactivate_single(self):
        run_skill_cmd(["off", "docx"], self.loader)
        assert "docx" not in self.loader.active
        assert "pdf"  in     self.loader.active

    def test_deactivate_multiple(self):
        run_skill_cmd(["off", "docx", "pdf"], self.loader)
        assert self.loader.active == []

    def test_not_active_gives_info_not_error(self):
        self.loader.deactivate("docx")   # pre-deactivate
        cap = run_skill_cmd(["off", "docx"], self.loader)
        assert len(cap.error) == 0
        assert any("SKILL_NOT_ACTIVE" in m or "docx" in m for m in cap.info)

    def test_nonexistent_gives_error(self):
        cap = run_skill_cmd(["off", "ghost"], self.loader)
        assert len(cap.error) > 0

    def test_no_names_gives_error(self):
        cap = run_skill_cmd(["off"], self.loader)
        assert len(cap.error) > 0

    def test_mixed_valid_invalid(self):
        cap = run_skill_cmd(["off", "docx", "ghost"], self.loader)
        assert "docx" not in self.loader.active   # 成功卸载
        assert len(cap.error) > 0                  # ghost → error


# ── /skill info ───────────────────────────────────────────────────────────────

class TestSkillInfo:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs", "content": "# DocX\nWord skill content."},
        ])

    def test_info_shows_content(self):
        printed = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.console.print", side_effect=lambda *a, **k: printed.append(str(a))), \
             patch("mini_agent.cli.commands.skills.R.print_markdown", side_effect=lambda m: printed.append(m)), \
             patch("mini_agent.cli.commands.skills.R.print_error"), patch("mini_agent.cli.commands.skills.R.print_info"):
            _handle_skill_cmd(["info", "docx"], self.loader)
        combined = " ".join(printed)
        assert "docx" in combined
        assert "Word skill content" in combined

    def test_info_nonexistent_gives_error(self):
        cap = run_skill_cmd(["info", "ghost"], self.loader)
        assert len(cap.error) > 0

    def test_info_no_name_gives_error(self):
        cap = run_skill_cmd(["info"], self.loader)
        assert len(cap.error) > 0

    def test_info_shows_active_status(self):
        self.loader.activate("docx")
        printed = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.console.print", side_effect=lambda *a, **k: printed.append(str(a))), \
             patch("mini_agent.cli.commands.skills.R.print_markdown"), \
             patch("mini_agent.cli.commands.skills.R.print_error"), patch("mini_agent.cli.commands.skills.R.print_info"):
            _handle_skill_cmd(["info", "docx"], self.loader)
        assert any("active" in p for p in printed)


# ── /skill reset ──────────────────────────────────────────────────────────────

class TestSkillReset:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx"},
            {"name": "pdf"},
            {"name": "excel"},
        ])

    def test_reset_deactivates_all(self):
        self.loader.activate("docx")
        self.loader.activate("pdf")
        run_skill_cmd(["reset"], self.loader)
        assert self.loader.active == []

    def test_reset_reports_each_deactivated(self):
        self.loader.activate("docx")
        self.loader.activate("excel")
        cap = run_skill_cmd(["reset"], self.loader)
        # 每个成功卸载都应该有一条 success 消息
        assert len(cap.success) == 2

    def test_reset_with_no_active_skills(self):
        cap = run_skill_cmd(["reset"], self.loader)
        assert len(cap.success) == 0
        assert any("No active" in m for m in cap.info)

    def test_reset_does_not_affect_available(self):
        self.loader.activate("docx")
        run_skill_cmd(["reset"], self.loader)
        # 卸载后技能仍然可再次激活
        assert "docx" in self.loader.available


# ── /skill（无效子命令）───────────────────────────────────────────────────────

class TestSkillInvalidCmd:
    def setup_method(self):
        self.loader = make_loader([{"name": "docx"}])

    def test_no_args_gives_error(self):
        cap = run_skill_cmd([], self.loader)
        assert len(cap.error) > 0

    def test_unknown_action_gives_error(self):
        cap = run_skill_cmd(["load", "docx"], self.loader)
        assert len(cap.error) > 0


# ── _suggest_skill 模糊匹配 ──────────────────────────────────────────────────

class TestSuggestSkill:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx"},
            {"name": "doc-advanced"},
            {"name": "pdf"},
        ])

    def test_suggest_prefix_match(self):
        info_msgs = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.print_error"), \
             patch("mini_agent.cli.commands.skills.R.print_info", side_effect=lambda m: info_msgs.append(m)):
            _suggest_skill("doc", self.loader)
        # "doc" 是 "docx" 和 "doc-advanced" 的前缀
        assert any("docx" in m or "doc-advanced" in m for m in info_msgs)

    def test_no_suggestion_when_no_match(self):
        info_msgs = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.print_error"), \
             patch("mini_agent.cli.commands.skills.R.print_info", side_effect=lambda m: info_msgs.append(m)):
            _suggest_skill("zzz", self.loader)
        # 没有匹配，不输出 Did you mean
        assert not any("Did you mean" in m for m in info_msgs)


# ── /skills 列表 ─────────────────────────────────────────────────────────────

class TestSkillsList:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs",     "content": "x" * 400},
            {"name": "pdf",  "description": "PDF creation",  "content": "y" * 200},
        ])

    def test_list_with_no_skills(self):
        loader = make_loader([])
        printed = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.console.print", side_effect=lambda *a, **k: printed.append(str(a))):
            _handle_skills_list(loader)
        assert any("NO_SKILLS_FOUND" in p or "none" in p.lower() for p in printed)

    def test_list_shows_all_skills(self):
        """验证 Table 里包含两行（每个 skill 一行）。"""
        tables = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.console.print",
                   side_effect=lambda *a, **k: tables.append(a[0]) if a else None):
            _handle_skills_list(self.loader)
        from rich.table import Table
        table_objs = [t for t in tables if isinstance(t, Table)]
        assert len(table_objs) == 1
        assert table_objs[0].row_count == 2

    def test_list_shows_token_estimate(self):
        printed = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.console.print", side_effect=lambda *a, **k: printed.append(str(a))):
            _handle_skills_list(self.loader)
        # content 400 chars → ~100 tokens; 200 chars → ~50 tokens
        combined = " ".join(printed)
        assert "100" in combined or "tokens" in combined

    def test_list_shows_active_summary(self):
        self.loader.activate("docx")
        printed = []
        with patch("mini_agent.cli.commands.skills.pm", _fake_pm), \
             patch("mini_agent.cli.commands.skills.R.console.print", side_effect=lambda *a, **k: printed.append(str(a))):
            _handle_skills_list(self.loader)
        combined = " ".join(printed)
        assert "1 active" in combined or "active" in combined


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
