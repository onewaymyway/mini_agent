"""单章时长设为 0 = 不限制时长与字数：不设字数预算，校验不查字数，提示词按原文改编。"""
import json

from novel_to_video import prompts
from novel_to_video.steps.chapter_script import check_script, target_chars_for

SCRIPT = "### 场次 1｜公寓·清晨（内景）\n" + "旁白：" + "很长的旁白。" * 400 + "\n"


def _project(tmp_path, duration):
    data = {}
    if duration is not None:
        data["target_chapter_duration_sec"] = duration
    (tmp_path / "project.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_zero_duration_means_no_target(tmp_path):
    assert target_chars_for(_project(tmp_path, 0)) == 0


def test_negative_duration_also_unlimited(tmp_path):
    assert target_chars_for(_project(tmp_path, -5)) == 0


def test_unset_duration_keeps_default_90s(tmp_path):
    assert target_chars_for(_project(tmp_path, None)) > 0


def test_explicit_duration_still_budgeted(tmp_path):
    assert target_chars_for(_project(tmp_path, 60)) > 0


def test_unlimited_script_is_not_failed_for_length():
    # 字数远超任何预算，但 target_chars=0 时不应因字数失败
    result = check_script(SCRIPT, source=SCRIPT, known_names=[], target_chars=0)
    assert result["status"] == "pass"
    assert not any("口播总字数" in i["message"] for i in result["issues"])


def test_budgeted_script_still_fails_when_over(tmp_path):
    result = check_script(SCRIPT, source=SCRIPT, known_names=[], target_chars=100)
    assert result["status"] == "fail"


def test_unlimited_prompt_has_no_budget_wording():
    p = prompts.script_prompt(1, "测试", "原文" * 10, 0)
    assert "不限制" in p
    assert "绝不能超过" not in p and "允许" not in p


def test_budgeted_prompt_keeps_budget_wording():
    p = prompts.script_prompt(1, "测试", "原文" * 10, 500)
    assert "绝不能超过" in p


def test_unlimited_fix_prompt_has_no_budget_wording():
    p = prompts.script_fix_prompt(1, "测试", "原文", 0, "旧剧本", [{"level": "error", "message": "x"}])
    assert "不限制" in p and "绝不能超过" not in p
