"""听书：正文清洗、分段上限、章节标题、跳转下标、JS 嵌入转义（M7）。"""

import json

from novel_writer.listen import MAX_CHARS, build_payload, chapter_segments, clean_line, payload_to_js


def test_clean_line_strips_markdown_and_skips_headings():
    assert clean_line("# 第一章 起") == ""
    assert clean_line("") == ""
    assert clean_line("**他**走了。") == "他走了。"
    assert clean_line("> 引语，很长。") == "引语，很长。"
    assert clean_line("- 列表项。") == "列表项。"
    assert clean_line("见[地图](http://x)处。") == "见地图处。"


def test_segments_start_with_title_and_respect_max_chars():
    long_sentence = "甲" * 500 + "。"
    segs = chapter_segments(3, "雨夜", "短句。\n\n" + long_sentence)
    assert segs[0] == "第3章，雨夜"
    assert all(len(s) <= MAX_CHARS for s in segs)
    assert "".join(segs[1:]).replace("。", "").count("甲") == 500


def test_clauses_are_split_before_hard_cut():
    text = "，".join(["字" * 60] * 6) + "。"  # 无句号的长句，应优先按逗号切
    segs = chapter_segments(1, "t", text)[1:]
    assert all(len(s) <= MAX_CHARS for s in segs)
    assert len(segs) >= 2


def test_empty_chapter_is_skipped_and_start_index_points_to_title():
    chapters = [
        {"chapter": 1, "title": "甲", "text": "一。二。"},
        {"chapter": 2, "title": "乙", "text": "   \n\n"},
        {"chapter": 3, "title": "丙", "text": "三。"},
    ]
    payload = build_payload(chapters)
    assert [c["ch"] for c in payload["chapters"]] == [1, 3]
    for c in payload["chapters"]:
        assert payload["segments"][c["start"]] == f"第{c['ch']}章，{c['title']}"
    assert payload["segments"][payload["chapters"][1]["start"]] == "第3章，丙"


def test_payload_json_is_safe_inside_script_tag():
    payload = build_payload([{"chapter": 1, "title": "</script>", "text": "正文。"}])
    js = payload_to_js(payload)
    assert "</script>" not in js
    assert json.loads(js.replace("<\\/", "</")) == payload
