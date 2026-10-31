"""托管挡遇到 stale 步骤：派生/报告类自动重跑；正文与全书设定类仍停下来确认。"""

import pytest

from novel_writer import autopilot as AP
from novel_writer import steps as S


def _step(step_id, category):
    return S.StepDef(id=step_id, title=step_id, stage="act", category=category,
                     executor=S.EXECUTOR_SKILL, skill="x")


@pytest.mark.parametrize("step_id, category, want", [
    # 派生/报告类：自动重跑
    ("ch05.context_pack", S.CAT_CHAPTER_PREP, "refresh"),
    ("ch05.plot_tasks", S.CAT_CHAPTER_PREP, "refresh"),
    ("act_review:2", S.CAT_ACT, "refresh"),
    ("act_pacing:2", S.CAT_ACT, "refresh"),
    ("act_theme:2", S.CAT_ACT, "refresh"),
    ("act_worldcheck:2", S.CAT_ACT, "refresh"),
    ("act_ideas:2", S.CAT_ACT, "refresh"),
    # 会改写正文或全书设定：仍然停下来确认
    ("ch05.write_chapter", S.CAT_CHAPTER_WRITE, "confirm"),
    ("ch05.revise_loop", S.CAT_CHAPTER_REVIEW, "confirm"),
    ("ch05.archive", S.CAT_CHAPTER_REVIEW, "confirm"),
    ("act_apply:2", S.CAT_ACT, "confirm"),
    ("act_extract:2", S.CAT_ACT, "confirm"),
    ("plan_act:2", S.CAT_ACT, "confirm"),
    ("final_review", S.CAT_ACT, "confirm"),
])
def test_stale_decision_matrix(step_id, category, want):
    assert AP.stale_decision(_step(step_id, category), {}) == want


def test_switch_off_restores_confirmation_for_every_stale_step():
    step = _step("ch05.context_pack", S.CAT_CHAPTER_PREP)
    assert AP.stale_decision(step, {}) == "refresh"
    assert AP.stale_decision(step, {"autopilot_auto_refresh": False}) == "confirm"


def test_setting_defaults_and_validation():
    from novel_writer import settings as SET
    assert SET.DEFAULT_SETTINGS["autopilot_auto_refresh"] is True
    merged = SET.validate_settings({"autopilot_auto_refresh": False})
    assert merged["autopilot_auto_refresh"] is False
    with pytest.raises(SET.SettingsError):
        SET.validate_settings({"autopilot_auto_refresh": "yes"})
