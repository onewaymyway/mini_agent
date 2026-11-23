from article_garden import charter as ch
from article_garden import charter_form as cf


def test_defaults_for_empty_and_garbage():
    for raw in (None, {}, "oops", [1, 2]):
        c, _ = ch.normalize_charter(raw)
        assert c["sources"]["min_credibility"] == "B" and c["style"]["length"] == [1500, 3500]
        assert c["scope"] == {"in": [], "out": []}


def test_invalid_fields_fall_back_with_warnings():
    c, w = ch.normalize_charter({
        "scope": {"in": ["A", "B", "A"], "out": ["B"]},
        "style": {"length": [500, 100]},
        "sources": {"min_credibility": "z"},
        "terms": ["not", "a", "dict"],
    })
    assert c["scope"]["in"] == ["A"] and c["scope"]["out"] == ["B"]  # 范围外优先、去重
    assert c["style"]["length"] == [1500, 3500] and c["sources"]["min_credibility"] == "B" and c["terms"] == {}
    assert len(w) >= 4


def test_terms_aliases_normalized():
    c, _ = ch.normalize_charter({"terms": {"智能体": ["Agent", "智能体", " Agent "], "": ["x"]}})
    assert c["terms"] == {"智能体": ["Agent"]}


def test_save_roundtrip_and_backup(tmp_path):
    p = tmp_path / "charter.yaml"
    ch.save_charter(p, {"domain": "A", "scope": {"in": ["x"]}})
    ch.save_charter(p, {"domain": "B", "scope": {"in": ["y"]}})
    assert ch.load_charter(p)[0]["domain"] == "B"
    assert ch.load_charter(tmp_path / "charter.yaml.bak")[0]["domain"] == "A"


def test_unparsable_charter_gives_default_and_warning(tmp_path):
    p = tmp_path / "charter.yaml"
    p.write_text("a: [unclosed", encoding="utf-8")
    c, w = ch.load_charter(p)
    assert c["sources"]["min_credibility"] == "B" and w and "无法解析" in w[0]


def test_form_roundtrip():
    charter, _ = ch.normalize_charter({"domain": "AI", "scope": {"in": ["a", "b"]},
                                       "terms": {"智能体": ["Agent", "AI Agent"]}})
    rows = cf.terms_to_rows(charter["terms"])
    back = cf.form_to_charter(domain="AI", scope_in=cf.list_to_lines(["a", "b"]), scope_out="", primary="", levels="入门\n进阶",
                              tone="平实", length_min=1000, length_max=2000, must_have="摘要", ai_disclosure=True,
                              term_rows=rows, prefer="", avoid="", min_credibility="B", disclaimer="", forbidden="")
    full, w = ch.normalize_charter(back)
    assert full["terms"] == charter["terms"] and full["scope"]["in"] == ["a", "b"] and w == []


def test_rows_to_terms_merges_duplicates_and_skips_blank():
    rows = [{"规范术语": "RAG", "别名（逗号分隔）": "检索增强，检索增强生成"}, {"规范术语": "RAG", "别名（逗号分隔）": "检索增强、R.A.G"},
            {"规范术语": " ", "别名（逗号分隔）": "x"}, {"规范术语": "LLM", "别名（逗号分隔）": ""}]
    assert cf.rows_to_terms(rows) == {"RAG": ["检索增强", "检索增强生成", "R.A.G"], "LLM": []}
