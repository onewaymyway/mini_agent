"""
tests/test_skill_manager.py — Skill 动态管理工具测试

覆盖：
  - SkillLoader.get_catalog()       — 目录格式正确
  - SkillLoader.get_active_catalog() — 仅含激活项
  - SkillLoader.describe()          — 返回描述
  - skill_list 工具                 — 返回完整目录 JSON
  - skill_activate 工具             — 激活成功/已激活/不存在 三种路径
  - skill_deactivate 工具           — 卸载成功/未激活/不存在 三种路径
  - 批量激活和批量卸载
  - agent._build_system 中技能目录注入
"""

from __future__ import annotations

import json
import sys
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from mini_agent.skills import Skill, SkillLoader
from mini_agent.skills.tracker import SkillUsageTracker
from mini_agent.tools import ToolRegistry
from mini_agent.tools.skill_manager import register_skill_tools


# ── SkillLoader stub ──────────────────────────────────────────────────────────

def make_loader(skill_defs: list[dict]) -> SkillLoader:
    """
    构造一个不依赖文件系统的 SkillLoader。
    skill_defs: [{"name": "docx", "description": "...", "content": "..."}]
    """
    loader = SkillLoader.__new__(SkillLoader)
    loader._dirs  = []
    loader._all   = {}
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
            content      = d.get("content", f"# {d['name']} skill content"),
            trigger_words= d.get("trigger_words", [d["name"].lower()]),
        )
        loader._all[skill.name] = skill

    return loader


# ── SkillLoader 新方法 ────────────────────────────────────────────────────────

class TestSkillLoaderCatalog:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx",  "description": "Word document skill"},
            {"name": "pdf",   "description": "PDF skill"},
            {"name": "excel", "description": "Excel skill"},
        ])

    def test_get_catalog_returns_all_skills(self):
        catalog = self.loader.get_catalog()
        assert len(catalog) == 3
        names = {s["name"] for s in catalog}
        assert names == {"docx", "pdf", "excel"}

    def test_get_catalog_active_flag_false_by_default(self):
        catalog = self.loader.get_catalog()
        assert all(not s["active"] for s in catalog)

    def test_get_catalog_active_flag_true_after_activate(self):
        self.loader.activate("docx")
        catalog = self.loader.get_catalog()
        docx_entry = next(s for s in catalog if s["name"] == "docx")
        assert docx_entry["active"] is True
        pdf_entry = next(s for s in catalog if s["name"] == "pdf")
        assert pdf_entry["active"] is False

    def test_get_active_catalog_empty_when_none_active(self):
        assert self.loader.get_active_catalog() == []

    def test_get_active_catalog_only_active_skills(self):
        self.loader.activate("docx")
        self.loader.activate("pdf")
        active = self.loader.get_active_catalog()
        assert len(active) == 2
        names = {s["name"] for s in active}
        assert names == {"docx", "pdf"}

    def test_describe_returns_description(self):
        assert self.loader.describe("docx") == "Word document skill"

    def test_describe_nonexistent_returns_empty(self):
        assert self.loader.describe("nonexistent") == ""


# ── skill_list 工具 ───────────────────────────────────────────────────────────

class TestSkillListTool:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs"},
            {"name": "pdf",  "description": "PDF creation"},
        ])
        self.registry = ToolRegistry()
        register_skill_tools(self.registry, self.loader)

    def _call(self):
        td = self.registry.get("skill_list")
        assert td is not None
        return json.loads(td.fn())

    def test_returns_all_skills(self):
        result = self._call()
        assert len(result["skills"]) == 2

    def test_summary_counts_correct(self):
        self.loader.activate("docx")
        result = self._call()
        assert result["summary"]["active"]   == 1
        assert result["summary"]["inactive"] == 1
        assert result["summary"]["total"]    == 2

    def test_active_names_in_summary(self):
        self.loader.activate("pdf")
        result = self._call()
        assert "pdf" in result["summary"]["active_names"]
        assert "pdf" not in result["summary"]["inactive_names"]

    def test_empty_loader(self):
        loader = make_loader([])
        registry = ToolRegistry()
        register_skill_tools(registry, loader)
        td = registry.get("skill_list")
        result = json.loads(td.fn())
        assert result["skills"] == []


# ── skill_activate 工具 ───────────────────────────────────────────────────────

class TestSkillActivateTool:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs"},
            {"name": "pdf",  "description": "PDF creation"},
        ])
        self.registry = ToolRegistry()
        with patch("mini_agent.ui.renderer.print_skill_loaded"):
            register_skill_tools(self.registry, self.loader)

    def _call(self, names, reason="test reason"):
        td = self.registry.get("skill_activate")
        assert td is not None
        with patch("mini_agent.ui.renderer.print_skill_loaded"):
            return json.loads(td.fn(names=names, reason=reason))

    def test_activate_single_skill(self):
        result = self._call(["docx"])
        assert result["activated"] == ["docx"]
        assert "docx" in result["now_active"]
        assert result["results"][0]["status"] == "activated"

    def test_activate_multiple_skills(self):
        result = self._call(["docx", "pdf"])
        assert set(result["activated"]) == {"docx", "pdf"}
        assert set(result["now_active"]) == {"docx", "pdf"}

    def test_activate_already_active(self):
        self.loader.activate("docx")
        result = self._call(["docx"])
        assert result["results"][0]["status"] == "already_active"
        assert result["activated"] == []

    def test_activate_nonexistent_skill(self):
        result = self._call(["nonexistent"])
        assert result["results"][0]["status"] == "not_found"
        assert "available" in result["results"][0]
        assert result["activated"] == []

    def test_activate_mixed_valid_and_invalid(self):
        result = self._call(["docx", "nonexistent"])
        statuses = {r["name"]: r["status"] for r in result["results"]}
        assert statuses["docx"]        == "activated"
        assert statuses["nonexistent"] == "not_found"
        assert result["activated"] == ["docx"]

    def test_reason_preserved_in_result(self):
        result = self._call(["docx"], reason="user wants a word document")
        assert result["reason"] == "user wants a word document"

    def test_description_included_in_activated_result(self):
        result = self._call(["docx"])
        docx_result = result["results"][0]
        assert "description" in docx_result
        assert docx_result["description"] == "Word docs"


# ── skill_deactivate 工具 ─────────────────────────────────────────────────────

class TestSkillDeactivateTool:
    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs"},
            {"name": "pdf",  "description": "PDF creation"},
        ])
        self.loader.activate("docx")
        self.loader.activate("pdf")
        self.registry = ToolRegistry()
        register_skill_tools(self.registry, self.loader)

    def _call(self, names, reason="test reason"):
        td = self.registry.get("skill_deactivate")
        assert td is not None
        with patch("mini_agent.ui.renderer.print_info"):
            return json.loads(td.fn(names=names, reason=reason))

    def test_deactivate_single_skill(self):
        result = self._call(["docx"])
        assert result["deactivated"] == ["docx"]
        assert "docx" not in result["now_active"]
        assert "pdf" in result["now_active"]

    def test_deactivate_multiple_skills(self):
        result = self._call(["docx", "pdf"])
        assert set(result["deactivated"]) == {"docx", "pdf"}
        assert result["now_active"] == []

    def test_deactivate_not_active_skill(self):
        self.loader.deactivate("docx")   # pre-deactivate
        result = self._call(["docx"])
        assert result["results"][0]["status"] == "not_active"
        assert result["deactivated"] == []

    def test_deactivate_nonexistent_skill(self):
        result = self._call(["ghost"])
        assert result["results"][0]["status"] == "not_found"
        assert result["deactivated"] == []

    def test_reason_preserved(self):
        result = self._call(["docx"], reason="task complete")
        assert result["reason"] == "task complete"

    def test_deactivate_actually_removes_from_loader(self):
        self._call(["docx"])
        assert "docx" not in self.loader.active


# ── exclude()（Stage 3.2 / mini-agent eval --without-skill）────────────────────

class TestSkillLoaderExclude:
    """
    SkillLoader.exclude() 用于 eval 的 --without-skill 场景：必须保证被排除的
    skill 既不在 active 列表里，也不会被 auto_activate() 关键词命中重新拉起——
    这是它与已有 deactivate() 的本质区别（deactivate 只是取消激活，关键词命中
    仍会被 auto_activate 重新激活）。
    """

    def setup_method(self):
        self.loader = make_loader([
            {"name": "docx", "description": "Word docs", "trigger_words": ["docx", "word"]},
            {"name": "pdf",  "description": "PDF creation", "trigger_words": ["pdf"]},
        ])

    def test_exclude_removes_from_all(self):
        assert self.loader.exclude("docx") is True
        assert self.loader.get("docx") is None
        assert "pdf" in [s["name"] for s in self.loader.get_catalog()]

    def test_exclude_removes_from_active_if_currently_active(self):
        self.loader.activate("docx")
        assert "docx" in self.loader.active
        self.loader.exclude("docx")
        assert "docx" not in self.loader.active

    def test_exclude_nonexistent_skill_returns_false(self):
        assert self.loader.exclude("ghost") is False

    def test_excluded_skill_cannot_be_reactivated(self):
        self.loader.exclude("docx")
        assert self.loader.activate("docx") is False
        assert "docx" not in self.loader.active

    def test_excluded_skill_not_picked_up_by_auto_activate(self):
        self.loader.exclude("docx")
        newly = self.loader.auto_activate("please convert this to docx format")
        assert "docx" not in newly
        assert "docx" not in self.loader.active

    def test_other_skills_unaffected_by_exclude(self):
        self.loader.activate("pdf")
        self.loader.exclude("docx")
        assert "pdf" in self.loader.active
        assert self.loader.get("pdf") is not None

    def test_excluded_skill_absent_from_catalog(self):
        self.loader.exclude("docx")
        names = [s["name"] for s in self.loader.get_catalog()]
        assert "docx" not in names
        assert "pdf" in names


# ── 工具注册完整性 ────────────────────────────────────────────────────────────

class TestToolRegistration:
    def test_all_three_tools_registered(self):
        loader   = make_loader([{"name": "docx"}])
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        assert registry.get("skill_list")       is not None
        assert registry.get("skill_activate")   is not None
        assert registry.get("skill_deactivate") is not None

    def test_tools_require_no_approval(self):
        loader   = make_loader([{"name": "docx"}])
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        for name in ("skill_list", "skill_activate", "skill_deactivate"):
            td = registry.get(name)
            assert td.requires_approval is False, f"{name} should not require approval"

    def test_tools_have_descriptions(self):
        loader   = make_loader([{"name": "docx"}])
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        for name in ("skill_list", "skill_activate", "skill_deactivate"):
            td = registry.get(name)
            assert td.description, f"{name} has empty description"

    def test_tools_have_valid_json_schema(self):
        """schemas 中 required 的字段都应该存在于 properties 中。"""
        import jsonschema
        loader   = make_loader([{"name": "docx"}])
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        for name in ("skill_list", "skill_activate", "skill_deactivate"):
            td   = registry.get(name)
            schema = td.input_schema
            props    = set(schema.get("properties", {}).keys())
            required = set(schema.get("required", []))
            assert required <= props, (
                f"{name}: required fields {required - props} missing from properties"
            )


# ── system prompt 中技能目录注入（集成）──────────────────────────────────────

class TestSystemPromptSkillCatalog:
    """验证 _build_system 正确注入技能目录。"""

    def _make_agent_with_skills(self, skill_defs):
        """构造最小 agent stub，仅测试 _build_system 的 skill 目录注入部分。"""
        loader = make_loader(skill_defs)

        # 用 MagicMock 替换所有外部依赖
        cfg = MagicMock()
        cfg.skill_chunking_enabled = False
        cfg.project_scan_enabled   = False
        cfg.memory_enabled         = False
        cfg.system_extra           = ""
        cfg.sandbox                = False
        cfg.claude_md_content      = ""
        cfg.agent_name             = "test"

        from mini_agent.config import SessionStats
        from mini_agent.agent import Agent
        from mini_agent.context_builder import ContextBuilder

        agent = Agent.__new__(Agent)
        agent.cfg            = cfg
        agent.skill_loader   = loader
        agent._history       = []

        from mini_agent.history_manager import HistoryManager
        agent._hist      = HistoryManager(cfg=agent.cfg, skill_loader=getattr(agent, 'skill_loader', None))
        agent._hist._history = agent._history
        agent._project_snapshot = None
        agent._memory        = None
        agent._global_memory = None
        agent._cached_system = None
        agent._ctx_builder = ContextBuilder(
            cfg=cfg,
            skill_loader=loader,
            memory=None,
            global_memory=None,
            project_snapshot_getter=lambda: None,
            profile_text_getter=lambda: "",
        )

        return agent

    def test_inactive_skills_appear_in_system_prompt(self):
        agent = self._make_agent_with_skills([
            {"name": "docx", "description": "Word document creation"},
            {"name": "pdf",  "description": "PDF operations"},
        ])

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            result = agent._build_system()

        assert "docx" in result
        assert "Word document creation" in result
        assert "Available (not yet loaded)" in result

    def test_active_skills_shown_as_active(self):
        agent = self._make_agent_with_skills([
            {"name": "docx", "description": "Word document creation"},
        ])
        agent.skill_loader.activate("docx")

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            result = agent._build_system()

        assert "Currently active" in result
        assert "docx" in result

    def test_skill_tool_instructions_present(self):
        agent = self._make_agent_with_skills([{"name": "docx"}])

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            result = agent._build_system()

        assert "skill_activate" in result
        assert "skill_deactivate" in result
        assert "skill_list" in result

    def test_no_catalog_block_when_no_skills(self):
        agent = self._make_agent_with_skills([])

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            result = agent._build_system()

        # 没有技能时不应出现技能目录块
        assert "Available Skills" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
