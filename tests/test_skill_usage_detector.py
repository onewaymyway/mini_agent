"""
tests/test_skill_usage_detector.py — Skill 实际使用检测测试

覆盖：
  extract_declared_skills   — Track A 标签解析
  strip_skill_tags          — 标签剥离
  build_fingerprint         — 指纹提取
  score_response            — Track B 相似度评分
  SkillUsageDetector.detect — 双轨检测逻辑
  SkillLoader.record_usage  — 集成：只有实际使用才更新 tracker
  agent 集成               — _append_assistant_response 剥离标签
                            — _agentic_loop 后调用 record_usage
"""

from __future__ import annotations

import sys, os, time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from mini_agent.skills import Skill, SkillLoader
from mini_agent.skills.tracker import SkillUsageTracker
from mini_agent.skills.usage_detector import (
    extract_declared_skills,
    strip_skill_tags,
    build_fingerprint,
    score_response,
    SkillUsageDetector,
    UsageEvidence,
)


# ── 工厂 ──────────────────────────────────────────────────────────────────────

def make_skill(name: str, content: str, description: str = "") -> Skill:
    return Skill(
        name=name,
        description=description or f"Skill {name}",
        location=Path(f"/fake/{name}/SKILL.md"),
        content=content,
        trigger_words=[name.lower()],
    )


def make_loader(skill_defs: list[dict]) -> SkillLoader:
    loader = SkillLoader.__new__(SkillLoader)
    loader._dirs   = []
    loader._all    = {}
    loader._active = []
    loader.tracker = SkillUsageTracker()
    from mini_agent.skills.usage_detector import SkillUsageDetector
    loader.detector = SkillUsageDetector()
    for d in skill_defs:
        skill = make_skill(
            d["name"],
            d.get("content", f"# {d['name']}\n" + d["name"] * 10),
            d.get("description", ""),
        )
        loader._all[skill.name] = skill
    loader.detector.build_fingerprints(loader._all)
    return loader


# ── Track A：显式声明解析 ─────────────────────────────────────────────────────

class TestExtractDeclaredSkills:

    def test_single_skill_tag(self):
        text = "Here is the document.\n<skill_used>docx</skill_used>"
        assert extract_declared_skills(text) == ["docx"]

    def test_multiple_skills_comma_separated(self):
        text = "Done.\n<skill_used>docx,pdf</skill_used>"
        result = extract_declared_skills(text)
        assert set(result) == {"docx", "pdf"}

    def test_multiple_skills_with_spaces(self):
        text = "<skill_used>docx, pdf, excel</skill_used>"
        result = extract_declared_skills(text)
        assert set(result) == {"docx", "pdf", "excel"}

    def test_case_insensitive(self):
        text = "<SKILL_USED>DocX</SKILL_USED>"
        result = extract_declared_skills(text)
        assert "docx" in result

    def test_no_tag_returns_empty(self):
        assert extract_declared_skills("Just a response with no tags.") == []

    def test_multiple_tags(self):
        text = "<skill_used>docx</skill_used> ... <skill_used>pdf</skill_used>"
        result = extract_declared_skills(text)
        assert set(result) == {"docx", "pdf"}

    def test_whitespace_around_name(self):
        text = "<skill_used>  docx  </skill_used>"
        result = extract_declared_skills(text)
        assert "docx" in result


class TestStripSkillTags:

    def test_strips_tag_from_end(self):
        text = "Great response here.\n<skill_used>docx</skill_used>"
        result = strip_skill_tags(text)
        assert "<skill_used>" not in result
        assert "Great response here." in result

    def test_strips_multiple_tags(self):
        text = "Content.<skill_used>docx</skill_used><skill_used>pdf</skill_used>"
        result = strip_skill_tags(text)
        assert "<skill_used>" not in result

    def test_no_tags_unchanged(self):
        text = "Normal response without any tags."
        assert strip_skill_tags(text) == text

    def test_content_preserved(self):
        text = "I created a .docx file using python-docx.\n<skill_used>docx</skill_used>"
        result = strip_skill_tags(text)
        assert "python-docx" in result
        assert ".docx" in result


# ── Track B：指纹提取和评分 ──────────────────────────────────────────────────

class TestBuildFingerprint:

    def test_extracts_camelcase_terms(self):
        skill = make_skill("test", "Use ParagraphStyle and DocumentObject to format. " * 5)
        fp = build_fingerprint(skill)
        assert any("paragraphstyle" in t or "documentobject" in t
                   for t in fp.high_value)

    def test_extracts_file_extensions(self):
        skill = make_skill("docx", "Save as .docx format. Open .docx files. " * 5)
        fp = build_fingerprint(skill)
        assert any(".docx" in t for t in fp.high_value)

    def test_normal_terms_filtered_stopwords(self):
        skill = make_skill("test", "the and or but for is are was with. " * 10)
        fp = build_fingerprint(skill)
        # 停用词不应出现在指纹中
        stop = {"the", "and", "for", "with", "are", "was"}
        assert not (fp.normal & stop)

    def test_normal_terms_min_length(self):
        skill = make_skill("test", "use add get set run make new old file path. " * 10)
        fp = build_fingerprint(skill)
        # 这些都是通用词，被 STOP_WORDS 过滤
        common = {"file", "path", "make", "add", "get", "set", "run", "use"}
        assert not (fp.normal & common)

    def test_fingerprint_skill_name(self):
        skill = make_skill("excel", "Excel spreadsheet manipulation")
        fp = build_fingerprint(skill)
        assert fp.skill_name == "excel"

    def test_empty_content(self):
        skill = make_skill("empty", "")
        fp = build_fingerprint(skill)
        assert fp.total_terms == 0


class TestScoreResponse:

    def setup_method(self):
        # 构造一个有明确特征词的 skill
        self.skill = make_skill(
            "docx",
            "Use python-docx to create Word documents. "
            "Add ParagraphStyle for formatting. "
            "Save with DocumentBuilder pattern. " * 8,
        )
        self.fp = build_fingerprint(self.skill)

    def test_high_score_when_many_terms_matched(self):
        response = (
            "I used python-docx to create the document. "
            "Applied ParagraphStyle for the heading. "
            "Used DocumentBuilder to save the .docx file."
        )
        ev = score_response(response, self.fp, threshold=0.1)
        assert ev.detected is True
        assert ev.score > 0.1
        assert len(ev.matched_terms) > 0

    def test_low_score_when_unrelated(self):
        response = "The weather today is sunny. Let me tell you about cooking recipes."
        ev = score_response(response, self.fp, threshold=0.15)
        assert ev.detected is False
        assert ev.score < 0.15

    def test_empty_response(self):
        ev = score_response("", self.fp, threshold=0.15)
        assert ev.detected is False
        assert ev.score == 0.0

    def test_threshold_respected(self):
        # 构造有多个特征词但只命中一个的 skill
        skill = make_skill("t", (
            "SpecialWord AlphaToken BetaProcessor GammaHandler DeltaRouter "
            "EpsilonMapper ZetaBuilder EtaParser ThetaWriter IotaReader " * 4
        ))
        fp = build_fingerprint(skill)
        # 只包含一个特征词，score 较低
        response = "I used SpecialWord in my implementation."
        # 低 threshold 应该检测到
        ev_low = score_response(response, fp, threshold=0.01)
        assert ev_low.detected is True
        # 较高 threshold（要求命中很多词）不检测
        ev_high = score_response(response, fp, threshold=0.9)
        assert ev_high.detected is False

    def test_matched_terms_listed(self):
        response = "Used python-docx and ParagraphStyle."
        ev = score_response(response, self.fp, threshold=0.01)
        assert len(ev.matched_terms) >= 1


# ── SkillUsageDetector 双轨检测 ───────────────────────────────────────────────

class TestSkillUsageDetector:

    def setup_method(self):
        self.docx_skill = make_skill(
            "docx",
            "Use python-docx library to create .docx files. "
            "ParagraphStyle DocumentObject WordTable " * 6,
        )
        self.pdf_skill = make_skill(
            "pdf",
            "Use reportlab or pypdf to manipulate PDF files. "
            "PdfWriter PdfReader PageObject " * 6,
        )
        self.detector = SkillUsageDetector(threshold=0.1)
        self.detector.build_fingerprints({
            "docx": self.docx_skill,
            "pdf": self.pdf_skill,
        })

    def test_track_a_declared_skill_detected(self):
        response = "Created the file.\n<skill_used>docx</skill_used>"
        results = self.detector.detect(response, ["docx", "pdf"])
        assert results["docx"].detected is True
        assert results["docx"].track == "declared"
        assert results["docx"].declared is True

    def test_track_a_does_not_declare_unused_skill(self):
        response = "Created the file.\n<skill_used>docx</skill_used>"
        results = self.detector.detect(response, ["docx", "pdf"])
        # pdf 未被声明且回复中无 pdf 相关内容
        assert results["pdf"].declared is False

    def test_track_b_fingerprint_fallback(self):
        # 无声明标签，但包含 docx 特征词
        response = "I used python-docx to create a .docx document with ParagraphStyle."
        results = self.detector.detect(response, ["docx", "pdf"])
        assert results["docx"].detected is True
        assert results["docx"].track == "fingerprint"

    def test_unrelated_response_not_detected(self):
        response = "The capital of France is Paris. Here is a recipe for pasta."
        results = self.detector.detect(response, ["docx", "pdf"])
        assert results["docx"].detected is False
        assert results["pdf"].detected is False

    def test_detect_used_names_returns_only_detected(self):
        response = "Created a .docx file with python-docx and ParagraphStyle."
        used = self.detector.detect_used_names(response, ["docx", "pdf"])
        assert "docx" in used
        assert "pdf" not in used

    def test_skill_not_in_active_not_checked(self):
        # excel 不在 active_skills，不应出现在结果中
        response = "Used python-docx."
        results = self.detector.detect(response, ["docx"])
        assert "excel" not in results

    def test_unknown_skill_has_no_evidence(self):
        # skill 在 active 中但 detector 没有其指纹
        results = self.detector.detect("some response", ["unknown_skill"])
        assert "unknown_skill" in results
        assert results["unknown_skill"].detected is False

    def test_track_a_overrides_track_b(self):
        # 即使指纹分数低，只要有显式声明就判为 detected
        response = "No relevant terms here at all.\n<skill_used>docx</skill_used>"
        results = self.detector.detect(response, ["docx"])
        assert results["docx"].detected is True
        assert results["docx"].track == "declared"


# ── SkillLoader.record_usage 集成 ─────────────────────────────────────────────

class TestSkillLoaderRecordUsage:

    def setup_method(self):
        self.loader = make_loader([
            {
                "name": "docx",
                "content": (
                    "Use python-docx to create Word documents. "
                    "ParagraphStyle DocumentObject WordTable " * 6
                ),
            },
            {
                "name": "pdf",
                "content": (
                    "Use reportlab to generate PDF. "
                    "PdfWriter PdfReader PageObject " * 6
                ),
            },
        ])
        self.loader._active = ["docx", "pdf"]

    def test_record_usage_updates_tracker_for_used_skill(self):
        response = "I used python-docx and ParagraphStyle to format the .docx file."
        used = self.loader.record_usage(response)
        assert "docx" in used
        assert self.loader.tracker.get_record("docx") is not None

    def test_record_usage_does_not_update_tracker_for_unused_skill(self):
        response = "I used python-docx and ParagraphStyle to format the .docx file."
        self.loader.record_usage(response)
        # pdf 未被使用，tracker 中不应有记录
        assert self.loader.tracker.get_record("pdf") is None

    def test_record_usage_track_a_updates_tracker(self):
        response = "Done.\n<skill_used>pdf</skill_used>"
        used = self.loader.record_usage(response)
        assert "pdf" in used
        assert self.loader.tracker.get_record("pdf") is not None

    def test_record_usage_empty_response_no_update(self):
        used = self.loader.record_usage("")
        assert used == []
        assert self.loader.tracker.records == []

    def test_record_usage_no_active_skills_no_update(self):
        self.loader._active = []
        used = self.loader.record_usage("python-docx ParagraphStyle content")
        assert used == []

    def test_record_usage_call_count_increments(self):
        response = "python-docx ParagraphStyle DocumentObject WordTable content"
        self.loader.record_usage(response)
        self.loader.record_usage(response)
        rec = self.loader.tracker.get_record("docx")
        assert rec is not None
        assert rec.call_count == 2

    def test_activate_does_not_update_tracker(self):
        """激活 skill 本身不再更新 tracker，只有实际使用才更新。"""
        loader = make_loader([{"name": "docx", "content": "content"}])
        loader.activate("docx")
        # 仅激活，没有 record_usage 调用，tracker 应该没有记录
        assert loader.tracker.get_record("docx") is None

    def test_build_context_does_not_update_tracker(self):
        """build_context 不再更新 tracker（只有 record_usage 才更新）。"""
        self.loader.build_context()
        assert self.loader.tracker.records == []


# ── 标签剥离不污染历史 ────────────────────────────────────────────────────────

class TestTagStrippingInHistory:
    """验证 _append_assistant_response 会剥离 skill_used 标签。"""

    def _make_agent(self):
        from mini_agent.config import SessionStats
        from mini_agent.agent import Agent
        cfg = MagicMock()
        cfg.auto_save_session = False
        cfg.verbose = False
        agent = Agent.__new__(Agent)
        agent.cfg = cfg
        agent.skill_loader = None
        agent._history = []
        agent.stats = SessionStats()
        return agent

    def test_tag_stripped_from_history(self):
        from mini_agent.llm.base import LLMResponse, LLMUsage
        agent = self._make_agent()
        response = LLMResponse(
            text="Here is the result.\n<skill_used>docx</skill_used>",
            tool_calls=[],
            usage=LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            stop_reason="end_turn",
        )
        agent._append_assistant_response(response)
        last_msg = agent._history[-1]
        content_text = " ".join(
            b["text"] for b in last_msg["content"] if b.get("type") == "text"
        )
        assert "<skill_used>" not in content_text
        assert "Here is the result." in content_text

    def test_response_without_tag_unchanged(self):
        from mini_agent.llm.base import LLMResponse, LLMUsage
        agent = self._make_agent()
        response = LLMResponse(
            text="Normal response without tags.",
            tool_calls=[],
            usage=LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            stop_reason="end_turn",
        )
        agent._append_assistant_response(response)
        last_msg = agent._history[-1]
        text = last_msg["content"][0]["text"]
        assert text == "Normal response without tags."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
