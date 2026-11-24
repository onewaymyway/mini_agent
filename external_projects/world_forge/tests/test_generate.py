import json

import pytest

from world_forge import generate


def test_parse_json_handles_fences_and_prose():
    raw = "好的，结果如下：\n```json\n{\"a\": 1}\n```\n以上。"
    assert generate.parse_json(raw) == {"a": 1}


def test_parse_json_repairs_trailing_comma():
    assert generate.parse_json('{"a": [1, 2,],}') == {"a": [1, 2]}


def test_storyboard_validation_rejects_empty_panels():
    errs = generate._validate_storyboard({"pages": [{"no": 1, "panels": []}]})
    assert errs


class _Second:
    def __init__(self):
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({"pages": [{"no": 1, "panels": []}]})
        return json.dumps({"pages": [{"no": 1, "panels": [{"visual_en": "ok", "narration": "", "dialogue": [],
                                                           "characters": []}]}]})


def test_retry_once_on_invalid_structure():
    llm = _Second()
    out = generate.storyboard_draft(llm, context={})
    assert llm.calls == 2
    assert out["pages"][0]["panels"][0]["visual_en"] == "ok"


def test_short_draft_rejects_empty():
    class Empty:
        def ask(self, prompt, system=""):
            return "  "

    with pytest.raises(generate.GenerationError):
        generate.short_draft(Empty(), context={}, form="novel")
