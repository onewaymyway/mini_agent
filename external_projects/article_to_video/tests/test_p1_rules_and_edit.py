import pytest

from article_to_video.planning import outline_edit as oe
from article_to_video.planning.chunks import build_groups, section_catalog
from article_to_video.planning.rules import rules_digest, rules_spine
from article_to_video.planning.spine_math import recompute_budgets
from article_to_video.source.md_parse import parse_markdown
from article_to_video.validators.coverage import validate_spine
from conftest import REFERENCE_ARTICLE


@pytest.fixture(scope="module")
def reference():
    text = REFERENCE_ARTICLE.read_text(encoding="utf-8")
    doc = parse_markdown(text)
    lines = text.splitlines()
    catalog = section_catalog(lines, doc)
    groups = build_groups(lines, doc)
    blocks_by_id = {b["id"]: b for b in doc["blocks"]}
    digests = [rules_digest(g, blocks_by_id) for g in groups]
    meta = {"title": "参考", "author": "", "mode": "condensed", "target_minutes": 10.0}
    spine = rules_spine(catalog, digests, meta)
    return {"text": text, "lines": lines, "catalog": catalog, "digests": digests,
            "spine": spine, "section_ids": [c["id"] for c in catalog]}


def test_rules_outline_on_reference_has_5_to_7_parts(reference):
    spine = reference["spine"]
    assert 5 <= len(spine["parts"]) <= 7
    assert spine["engine"] == "rules"
    assert validate_spine(spine, reference["section_ids"], reference["digests"])["errors"] == []


def test_rules_outline_covers_every_section_once(reference):
    covered = [s for p in reference["spine"]["parts"] for s in p["section_ids"]]
    assert sorted(covered) == sorted(reference["section_ids"])


def test_rules_budgets_add_up_to_target(reference):
    spine = reference["spine"]
    total = sum(p["budget_minutes"] for p in spine["parts"])
    assert abs(total - spine["target_minutes"]) < 0.1


def test_rules_quotes_and_numbers_are_verbatim(reference):
    from article_to_video.validators.fidelity import squash
    src = squash(reference["text"])
    for q in reference["spine"]["golden_quotes"]:
        assert squash(q) in src
    for d in reference["digests"]:
        for n in d["key_numbers"]:
            assert squash(n) in src


def test_rules_hook_always_present(reference):
    assert reference["spine"]["hook_candidates"]


def test_edit_rename_and_hook_and_target(reference):
    s = reference["spine"]
    s2 = oe.rename_part(s, 0, "新标题")
    assert s2["parts"][0]["title"] == "新标题" and s2["confirmed"] is False
    assert s["parts"][0]["title"] != "新标题"  # 入参不被修改
    s3 = oe.select_hook(s, 0)
    assert s3["selected_hook"] == 0
    s4 = oe.set_target_minutes(s, 20)
    assert abs(sum(p["budget_minutes"] for p in s4["parts"]) - 20) < 0.1


def test_edit_merge_and_split_keep_validity(reference):
    s = reference["spine"]
    n = len(s["parts"])
    merged = oe.merge_with_next(s, 0)
    assert len(merged["parts"]) == n - 1
    assert validate_spine(merged, reference["section_ids"], reference["digests"])["errors"] == []
    big = max(range(n), key=lambda i: len(s["parts"][i]["section_ids"]))
    split = oe.split_part(s, big, 1)
    assert len(split["parts"]) == n + 1
    assert validate_spine(split, reference["section_ids"], reference["digests"])["errors"] == []


def test_edit_split_bounds_and_last_merge_are_rejected(reference):
    s = reference["spine"]
    with pytest.raises(ValueError):
        oe.split_part(s, 0, 0)
    with pytest.raises(ValueError):
        oe.merge_with_next(s, len(s["parts"]) - 1)


def test_restore_cut_moves_section_back_in_order(reference):
    s = reference["spine"]
    victim = s["parts"][-1]["section_ids"][-1]
    cut_spine = {**s, "parts": [dict(p) for p in s["parts"]]}
    cut_spine["parts"][-1] = {**cut_spine["parts"][-1],
                              "section_ids": cut_spine["parts"][-1]["section_ids"][:-1]}
    cut_spine["cuts"] = [{"section_ids": [victim], "reason": "测试"}]
    restored = oe.restore_cut(cut_spine, 0, len(cut_spine["parts"]) - 1, reference["catalog"])
    assert restored["cuts"] == []
    assert restored["parts"][-1]["section_ids"][-1] == victim
    assert validate_spine(restored, reference["section_ids"], reference["digests"])["errors"] == []
