"""
tests/test_persona_draft_llm_quality.py

对应文档: next_doc/persona_draft_llm_quality_improvement_plan.md

覆盖四个阶段的改动：
  A. draft_outline_with_llm() 按 target_type 切换 prompt。
  B. generate_persona_topic_question()：成功/None/异常/空/过长五种场景。
  C. synthesize_persona_draft_with_llm() + draft_persona_markdown() 的
     LLM 润色整合：成功替换正文+tone、失败/无 llm_helper 时精确回退到
     规则版行为（含 frontmatter/缺失维度提示/安全提示不受影响）。
  D. render_persona_prompt() 对 tone 字段的显式提示注入。
"""

from __future__ import annotations

import pytest

from mini_agent.evolution.capability_learning import (
    CapabilityQuestion,
    CapabilityTrack,
    OutlineTopic,
    draft_outline_with_llm,
    draft_persona_markdown,
    generate_persona_topic_question,
    synthesize_persona_draft_with_llm,
)


def _make_track(**overrides) -> CapabilityTrack:
    defaults = dict(
        track_id="cap_test123456",
        title="老李投顾",
        persona_desc="一个经验丰富、说话直接的证券投顾角色",
        target_type="persona",
        wiki_tag="capability:test",
        outline=[
            OutlineTopic(topic_id="t1", name="性格特征"),
            OutlineTopic(topic_id="t2", name="说话习惯"),
        ],
    )
    defaults.update(overrides)
    return CapabilityTrack(**defaults)


def _answered_question(topic_id: str, answer: str) -> CapabilityQuestion:
    return CapabilityQuestion(
        question_id=f"q_{topic_id}",
        track_id="cap_test123456",
        topic_id=topic_id,
        question="占位问题",
        status="answered",
        answer=answer,
    )


# ── 阶段 A：draft_outline_with_llm 按 target_type 分流 ──────────────────

class TestDraftOutlinePromptByTargetType:
    def test_persona_prompt_asks_for_character_dimensions(self):
        captured = {}

        def stub_llm(prompt: str) -> str:
            captured["prompt"] = prompt
            return "性格特征\n说话习惯\n背景经历"

        draft_outline_with_llm("老李投顾", "证券投顾角色", stub_llm, target_type="persona")
        assert "人设" in captured["prompt"]
        assert "知识点" not in captured["prompt"]

    def test_knowledge_prompt_unchanged_by_default(self):
        captured = {}

        def stub_llm(prompt: str) -> str:
            captured["prompt"] = prompt
            return "基础概念\n进阶技巧\n实战案例"

        draft_outline_with_llm("股票分析能力", "希望具备分析能力", stub_llm)
        assert "知识点或能力维度" in captured["prompt"]
        assert "人设塑造" not in captured["prompt"]


# ── 阶段 B：persona 追问文案 LLM 化 ──────────────────────────────────────

class TestGeneratePersonaTopicQuestion:
    def test_llm_none_returns_none(self):
        assert generate_persona_topic_question("性格特征", "desc", None) is None

    def test_llm_success_returns_question(self):
        question = generate_persona_topic_question(
            "性格特征", "desc", lambda p: "这个角色遇到客户抱怨亏损时通常怎么回应？"
        )
        assert question == "这个角色遇到客户抱怨亏损时通常怎么回应？"

    def test_llm_exception_returns_none(self):
        def boom(p):
            raise RuntimeError("network down")

        assert generate_persona_topic_question("性格特征", "desc", boom) is None

    def test_llm_empty_returns_none(self):
        assert generate_persona_topic_question("性格特征", "desc", lambda p: "") is None

    def test_llm_too_long_returns_none(self):
        assert generate_persona_topic_question("性格特征", "desc", lambda p: "x" * 61) is None


# ── 阶段 C：LLM 润色整合 ─────────────────────────────────────────────────

class TestSynthesizePersonaDraftWithLLM:
    def test_llm_none_returns_none(self):
        track = _make_track()
        assert synthesize_persona_draft_with_llm(track, "raw material", None) is None

    def test_valid_json_returns_tone_and_body(self):
        track = _make_track()

        def stub_llm(prompt: str) -> str:
            return '{"tone": "直接、老练", "body": "# 老李投顾\\n\\n润色后的正文"}'

        result = synthesize_persona_draft_with_llm(track, "raw material", stub_llm)
        assert result == {"tone": "直接、老练", "body": "# 老李投顾\n\n润色后的正文"}

    def test_json_wrapped_in_code_fence_still_parses(self):
        track = _make_track()

        def stub_llm(prompt: str) -> str:
            return '```json\n{"tone": "从容", "body": "正文"}\n```'

        result = synthesize_persona_draft_with_llm(track, "raw material", stub_llm)
        assert result == {"tone": "从容", "body": "正文"}

    def test_invalid_json_returns_none(self):
        track = _make_track()
        result = synthesize_persona_draft_with_llm(track, "raw material", lambda p: "not json")
        assert result is None

    def test_missing_body_field_returns_none(self):
        track = _make_track()
        result = synthesize_persona_draft_with_llm(
            track, "raw material", lambda p: '{"tone": "从容"}'
        )
        assert result is None

    def test_llm_exception_returns_none(self):
        track = _make_track()

        def boom(p):
            raise RuntimeError("boom")

        assert synthesize_persona_draft_with_llm(track, "raw material", boom) is None

    def test_tone_truncated_to_20_chars(self):
        track = _make_track()
        long_tone = "语" * 30
        result = synthesize_persona_draft_with_llm(
            track, "raw material", lambda p: f'{{"tone": "{long_tone}", "body": "正文"}}'
        )
        assert len(result["tone"]) == 20


class TestDraftPersonaMarkdownLLMIntegration:
    def test_no_llm_helper_behaves_like_before(self):
        track = _make_track()
        questions = [_answered_question("t1", "沉稳但偶尔毒舌")]
        text = draft_persona_markdown(track, questions)
        assert "tone: \n" in text
        assert "- 沉稳但偶尔毒舌" in text
        assert "未经 LLM 润色" in text
        # t2 没有回答，应该出现缺失维度提示
        assert "说话习惯" in text
        assert "草稿完成度提示" in text

    def test_llm_success_replaces_body_and_tone(self):
        track = _make_track()
        questions = [_answered_question("t1", "沉稳但偶尔毒舌")]

        def stub_llm(prompt: str) -> str:
            return (
                '{"tone": "沉稳、毒舌", "body": '
                '"# 老李投顾\\n\\n润色后的角色描述\\n\\n## 性格特征\\n\\n'
                '沉稳但偶尔犀利。\\n\\n## 说话习惯\\n\\n（暂无信息，尚待用户回答相关问题）"}'
            )

        text = draft_persona_markdown(track, questions, llm_helper=stub_llm)
        assert "tone: 沉稳、毒舌" in text
        assert "润色后的角色描述" in text
        assert "已经过 LLM 润色" in text
        # 缺失维度提示仍然基于规则版材料计算，不受 LLM 输出影响
        assert "草稿完成度提示" in text
        assert "说话习惯" in text

    def test_llm_failure_falls_back_to_rule_based(self):
        track = _make_track()
        questions = [_answered_question("t1", "沉稳但偶尔毒舌")]

        def boom(prompt: str) -> str:
            raise RuntimeError("llm down")

        text = draft_persona_markdown(track, questions, llm_helper=boom)
        assert "tone: \n" in text
        assert "- 沉稳但偶尔毒舌" in text
        assert "未经 LLM 润色" in text

    def test_real_person_warning_preserved_with_llm(self):
        track = _make_track(persona_desc="模仿马斯克本人说话的角色")
        questions = []

        def stub_llm(prompt: str) -> str:
            return '{"tone": "自信", "body": "# 老李投顾\\n\\n正文"}'

        text = draft_persona_markdown(track, questions, llm_helper=stub_llm)
        assert "安全提示" in text


# ── 阶段 D：tone 注入 render_persona_prompt ──────────────────────────────

class TestRenderPersonaPromptToneInjection:
    def test_tone_present_adds_explicit_line(self):
        from mini_agent.orchestrator.persona_profiles import PersonaProfile, render_persona_prompt

        persona = PersonaProfile(
            name="jarvis", display_name="贾维斯", description="d",
            tone="沉稳、简练、偶尔调侃", body="你是贾维斯。",
        )
        rendered = render_persona_prompt(persona)
        assert "语气/说话风格：沉稳、简练、偶尔调侃" in rendered
        assert "你是贾维斯。" in rendered

    def test_empty_tone_unchanged_behavior(self):
        from mini_agent.orchestrator.persona_profiles import PersonaProfile, render_persona_prompt

        persona = PersonaProfile(
            name="jarvis", display_name="贾维斯", description="d",
            tone="", body="你是贾维斯。",
        )
        rendered = render_persona_prompt(persona)
        assert "语气/说话风格" not in rendered


if __name__ == "__main__":
    pytest.main([__file__])
