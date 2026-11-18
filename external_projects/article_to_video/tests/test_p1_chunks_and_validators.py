from article_to_video.planning.chunks import MAX_GROUP_CHARS, build_groups, section_catalog
from article_to_video.planning.spine_math import partition
from article_to_video.source.md_parse import parse_markdown
from article_to_video.validators.coverage import validate_spine
from article_to_video.validators.fidelity import validate_digest
from conftest import REFERENCE_ARTICLE


def _doc(text):
    return parse_markdown(text), text.splitlines()


def test_groups_cover_every_block_exactly_once_on_reference():
    text = REFERENCE_ARTICLE.read_text(encoding="utf-8")
    doc, lines = _doc(text)
    groups = build_groups(lines, {"blocks": doc["blocks"], "sections": doc["sections"]})
    seen = [bid for g in groups for bid in g["block_ids"]]
    assert sorted(seen) == sorted(b["id"] for b in doc["blocks"])
    assert len(seen) == len(set(seen))
    assert all(g["chars"] <= MAX_GROUP_CHARS or len(g["block_ids"]) == 1 for g in groups)


def test_long_section_is_split_at_block_boundaries():
    body = "\n\n".join(f"第{i}段" + "字" * 900 for i in range(10))
    doc, lines = _doc("# 长章\n\n" + body)
    groups = build_groups(lines, {"blocks": doc["blocks"], "sections": doc["sections"]}, max_chars=2500)
    assert len(groups) > 1
    assert all(g["section_ids"] == ["s01"] for g in groups)


def test_section_catalog_skips_rule_only_preamble():
    doc, lines = _doc("---\n---\n\n# 甲\n\n正文")
    cat = section_catalog(lines, {"blocks": doc["blocks"], "sections": doc["sections"]})
    assert [c["id"] for c in cat] == ["s01"]


def test_partition_is_contiguous_and_gives_k_parts():
    groups = partition([10, 10, 10, 10, 10, 10, 10, 10], 4)
    assert len(groups) == 4
    assert [i for g in groups for i in g] == list(range(8))
    assert partition([1, 1], 5) == [[0], [1]]


def test_digest_rejects_fabricated_quote_and_number():
    src = "这是原文。核心是通用计算。今年投入3000亿元。"
    good = {"summary": "s", "core_claims": ["c"], "key_numbers": ["3000亿元"],
            "quotable": ["核心是通用计算"]}
    assert validate_digest(good, src) == []
    bad_quote = {**good, "quotable": ["原文从未说过这句话"]}
    assert any("逐字" in x for x in validate_digest(bad_quote, src))
    bad_num = {**good, "key_numbers": ["9999亿"]}
    assert any("key_numbers" in x for x in validate_digest(bad_num, src))


def test_digest_ignores_markdown_emphasis_when_matching():
    src = "> **核心是通用计算**"
    assert validate_digest({"summary": "s", "core_claims": ["c"], "quotable": ["核心是通用计算"]}, src) == []


def _spine(parts, cuts=(), hooks=("h",), quotes=()):
    return {"parts": parts, "cuts": list(cuts), "hook_candidates": list(hooks),
            "selected_hook": 0, "golden_quotes": list(quotes)}


def _part(pid, ids, w=1.0, title="章"):
    return {"id": pid, "title": title, "section_ids": ids, "weight": w}


def test_coverage_flags_missing_duplicate_and_unknown():
    ids = ["s01", "s02", "s03"]
    missing = validate_spine(_spine([_part("P01", ["s01", "s02"])]), ids)
    assert any("s03" in e for e in missing["errors"])
    dup = validate_spine(_spine([_part("P01", ["s01", "s02"]), _part("P02", ["s02", "s03"])]), ids)
    assert any("重复" in e for e in dup["errors"])
    unknown = validate_spine(_spine([_part("P01", ["s01", "s02", "s03", "s99"])]), ids)
    assert any("未知小节 s99" in e for e in unknown["errors"])


def test_cut_counts_as_covered():
    ids = ["s01", "s02"]
    ok = validate_spine(_spine([_part("P01", ["s01"])], cuts=[{"section_ids": ["s02"], "reason": "重复"}]), ids)
    assert ok["errors"] == []


def test_fabricated_golden_quote_is_rejected():
    digests = [{"quotable": ["真的原话"]}]
    res = validate_spine(_spine([_part("P01", ["s01"])], quotes=["编造的话"]), ["s01"], digests)
    assert any("金句" in e for e in res["errors"])


def test_no_hook_is_error_and_part_count_is_warning():
    res = validate_spine(_spine([_part("P01", ["s01"])], hooks=()), ["s01"])
    assert any("钩子" in e for e in res["errors"])
    ids = [f"s{i:02d}" for i in range(1, 9)]
    res2 = validate_spine(_spine([_part("P01", ids)]), ids)
    assert res2["errors"] == [] and res2["warnings"]
