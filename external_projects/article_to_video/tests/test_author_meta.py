from article_to_video.source.author_meta import parse_front_matter, resolve_author


def test_explicit_input_wins_over_everything():
    fm = {"author": "前言作者"}
    assert resolve_author("界面作者", fm, ["作者：文首作者"]) == ("界面作者", "input")


def test_front_matter_author_used_when_no_input():
    lines = ["---", "title: 某标题", "author: 张三", "---", "# 正文"]
    fm, start = parse_front_matter(lines)
    assert start == 4
    assert resolve_author("", fm, lines[start:]) == ("张三", "front_matter")


def test_chinese_front_matter_key():
    fm, _ = parse_front_matter(["---", "作者: 李四", "---"])
    assert resolve_author(None, fm, []) == ("李四", "front_matter")


def test_body_line_with_bold_markers():
    head = ["> **作者**：王五", "", "正文"]
    assert resolve_author("", {}, head) == ("王五", "body_line")


def test_no_author_is_never_guessed():
    assert resolve_author("", {}, ["# 标题", "正文里提到作者很多"]) == ("", "none")


def test_no_front_matter_without_closing_fence():
    assert parse_front_matter(["---", "只有开头"]) == ({}, 0)
    assert parse_front_matter(["# 标题"]) == ({}, 0)
