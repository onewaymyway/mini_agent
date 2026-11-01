"""LLM JSON 解析用 json_repair 兜底；自动修正次数默认 5、上限 5，可在界面与接口设置。"""
import json

import pytest

from novel_to_video import project_store as ps
from novel_to_video import tool_api
from novel_to_video.steps.global_visual import StepError, parse_json_object


def test_strict_json_unchanged():
    assert parse_json_object('{"shots": [{"a": 1}]}') == {"shots": [{"a": 1}]}


def test_missing_comma_is_repaired():
    # 现场错误：Expecting ',' delimiter（少逗号）
    bad = '{"shots": [{"text": "我们走。" "speaker": "林远"}]}'
    obj = parse_json_object(bad)
    assert obj["shots"][0]["text"] == "我们走。"
    assert obj["shots"][0]["speaker"] == "林远"


def test_truncated_json_is_repaired_to_a_dict():
    obj = parse_json_object('{"shots": [{"scene_no": 1, "blocks": [{"text": "走吧"}')
    assert isinstance(obj, dict) and "shots" in obj


def test_unrepairable_text_still_raises_step_error():
    with pytest.raises(StepError):
        parse_json_object("没有任何结构的普通文字")


def test_default_retries_is_five():
    assert ps.DEFAULT_AUTO_FIX_RETRIES == 5
    assert ps.DEFAULT_OPTIONS["script_max_retries"] == 5
    assert ps.DEFAULT_OPTIONS["shots_max_retries"] == 5


def test_retries_cap_is_five(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({}), encoding="utf-8")
    assert tool_api.set_shots_retries(str(tmp_path), 5)["ok"]
    assert tool_api.set_script_retries(str(tmp_path), 5)["ok"]
    assert not tool_api.set_shots_retries(str(tmp_path), 6)["ok"]


def test_legacy_project_without_key_uses_five(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({}), encoding="utf-8")
    assert ps.auto_fix_retries(tmp_path, "shots_max_retries") == 5


def test_explicit_zero_still_disables(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"shots_max_retries": 0}), encoding="utf-8")
    assert ps.auto_fix_retries(tmp_path, "shots_max_retries") == 0
