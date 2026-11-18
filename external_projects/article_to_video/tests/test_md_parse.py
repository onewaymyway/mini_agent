from article_to_video.source.md_parse import parse_markdown, plain_text


def _ids(blocks):
    return [b["id"] for b in blocks]


def test_block_ids_follow_section_numbering():
    doc = "前言段落\n\n# 第一章\n\n正文\n\n# 第二章\n\n又一段"
    p = parse_markdown(doc)
    assert [s["id"] for s in p["sections"]] == ["s01", "s02"]
    assert p["blocks"][0]["id"] == "s00.p01"
    assert p["blocks"][0]["kind"] == "paragraph"
    assert "s01.p01" in _ids(p["blocks"])
    assert "s02.p02" in _ids(p["blocks"])


def test_h2_is_recorded_on_following_blocks():
    doc = "# 章\n\n## 小节甲\n\n内容一\n\n## 小节乙\n\n内容二"
    blocks = parse_markdown(doc)["blocks"]
    by_text = {b.get("text", b.get("plain")): b for b in blocks}
    assert by_text["内容一"]["h2"] == "小节甲"
    assert by_text["内容二"]["h2"] == "小节乙"


def test_list_and_continuation_lines():
    doc = "- 第一条\n  接着说明\n- 第二条"
    b = parse_markdown(doc)["blocks"][0]
    assert b["kind"] == "list"
    assert b["items"] == ["第一条 接着说明", "第二条"]
    assert b["ordered"] is False


def test_table_with_separator_row():
    doc = "| 时代 | 能源 |\n| --- | :---: |\n| 蒸汽 | 煤 |\n| 电力 | 电 |"
    b = parse_markdown(doc)["blocks"][0]
    assert b["kind"] == "table"
    assert b["header"] == ["时代", "能源"]
    assert b["rows"] == [["蒸汽", "煤"], ["电力", "电"]]


def test_quote_strips_markers_and_joins_lines():
    doc = "> 第一句\n>\n> **第二句**"
    b = parse_markdown(doc)["blocks"][0]
    assert b["kind"] == "quote"
    assert b["text"] == "第一句\n**第二句**"
    assert b["plain"] == "第一句\n第二句"


def test_bracket_formula_on_its_own_lines():
    doc = "前文\n[\n\\text{产出} = A \\cdot K\n]\n后文"
    blocks = parse_markdown(doc)["blocks"]
    math = [b for b in blocks if b["kind"] == "math"][0]
    assert math["src"] == "bracket"
    assert math["content"] == "\\text{产出} = A \\cdot K"
    assert blocks[-1]["kind"] == "paragraph"


def test_dollar_and_boxed_formulas():
    doc = "$$E=mc^2$$\n\n\\boxed{\nX = {a + b}\n}"
    blocks = [b for b in parse_markdown(doc)["blocks"] if b["kind"] == "math"]
    assert blocks[0]["src"] == "dollar" and blocks[0]["content"] == "E=mc^2"
    assert blocks[1]["src"] == "boxed"


def test_code_block_is_classified_and_keeps_source_lines():
    doc = "# 章\n\n```\n资本\n ↓\nAI\n```\n"
    b = [x for x in parse_markdown(doc)["blocks"] if x["kind"] == "code"][0]
    assert b["shape"] == "chain"
    assert b["nodes"] == ["资本", "AI"]
    assert b["line_start"] == 3 and b["line_end"] == 7


def test_front_matter_is_excluded_from_body():
    doc = "---\nauthor: 赵六\n---\n# 标题\n\n正文"
    p = parse_markdown(doc)
    assert p["front_matter"] == {"author": "赵六"}
    assert p["sections"][0]["title"] == "标题"
    assert all(b["kind"] != "rule" for b in p["blocks"])


def test_image_and_rule():
    blocks = parse_markdown("![示意图](a.png)\n\n---").get("blocks")
    assert blocks[0]["kind"] == "image" and blocks[0]["url"] == "a.png"
    assert blocks[1]["kind"] == "rule"


def test_plain_text_strips_inline_markup():
    assert plain_text("**重点**和[链接](http://x)与`代码`") == "重点和链接与代码"


def test_unclosed_code_block_does_not_hang():
    p = parse_markdown("```\n一直没关")
    assert p["blocks"][0]["kind"] == "code"
