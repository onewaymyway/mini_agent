import pytest

from novel_writer import settings as S


def test_defaults_valid():
    d = S.validate_settings({})
    assert d["gear"] == "G0" and d["quality"] == "standard"


@pytest.mark.parametrize(
    "bad",
    [
        {"gear": "G9"},
        {"quality": "ultra"},
        {"step_overrides": {"x": "maybe"}},
        {"stage_gear_overrides": {"writing": "G7"}},
        {"budget": {"max_chapters_per_run": 0}},
        {"budget": {"max_llm_calls_per_run": True}},
        {"banned_words": "abc"},
        {"semi_auto_batch_chapters": 0},
    ],
)
def test_invalid_rejected(bad):
    with pytest.raises(S.SettingsError):
        S.validate_settings(bad)


def test_budget_null_means_unlimited_and_merge_keeps_defaults():
    d = S.validate_settings({"budget": {"max_chapters_per_run": None}})
    assert d["budget"]["max_chapters_per_run"] is None
    assert d["budget"]["max_llm_calls_per_run"] == 60


def test_load_corrupt_falls_back_and_save_roundtrip(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{oops", encoding="utf-8")
    assert S.load_settings(f)["gear"] == "G0"
    S.save_settings(f, {"gear": "G2", "step_overrides": {"write_chapter": "review"}})
    got = S.load_settings(f)
    assert got["gear"] == "G2" and got["step_overrides"] == {"write_chapter": "review"}
