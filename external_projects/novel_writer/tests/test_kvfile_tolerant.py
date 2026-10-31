"""文件头解析：容忍 LLM 常见的尾逗号；不放松其他错误（未转义引号等仍然报错）。"""

import pytest

from novel_writer import character_store as CS
from novel_writer import kvfile


def test_strip_trailing_commas_only_outside_strings():
    assert kvfile.strip_trailing_commas('[{"a":1}],') == '[{"a":1}]'
    assert kvfile.strip_trailing_commas('[1,2,],') == '[1,2]'
    assert kvfile.strip_trailing_commas('{"a":1,}') == '{"a":1}'
    assert kvfile.strip_trailing_commas('"a,}", ') == '"a,}" '        # 字符串内容原样保留（尾逗号后的空白保留）
    assert kvfile.strip_trailing_commas('"x\\",}"') == '"x\\",}"'      # 转义引号不结束字符串
    assert kvfile.strip_trailing_commas('[1,2]') == '[1,2]'            # 没有尾逗号则不变


@pytest.mark.parametrize("raw", [
    '[{"chapter":30,"status":"首次激活"}],',                          # 数组后的尾逗号（char_021 的形态）
    '{"char_001":{"type":"父子"},"char_019":{"type":"对立"}},',       # 对象后的尾逗号（relationships）
    '[1,2,],',                                                         # 数组内部的尾逗号
])
def test_loads_value_accepts_trailing_commas(raw):
    assert kvfile.loads_value(raw) is not None


def test_loads_value_still_rejects_unescaped_quotes():
    with pytest.raises(ValueError):
        kvfile.loads_value('"首次"激活""')                            # 字符串内的英文双引号：真正的错误


def _record_text(state_log_comma=True, rel_comma=True, state_key_change="投影出现"):
    sl = '[{"chapter":30,"status":"首次激活","key_change":"' + state_key_change + '"}]' + ("," if state_log_comma else "")
    rel = '{"char_001":{"type":"父子"}}' + ("," if rel_comma else "")
    return (
        "---\n"
        'id: "char_021"\n'
        'name: "AI投影"\n'
        'role: "配角"\n'
        'introduced_chapter: 30\n'
        "state_log: " + sl + "\n"
        "relationships: " + rel + "\n"
        "---\n正文\n"
    )


def test_decode_accepts_trailing_commas_and_record_is_valid():
    rec = kvfile.decode(_record_text(), "char_021.md")
    assert rec["state_log"][0]["chapter"] == 30
    assert rec["relationships"]["char_001"]["type"] == "父子"
    assert CS.record_problems(rec, "char_021.md", expected_id="char_021") == []


def test_trailing_comma_file_is_readable_so_repair_dir_leaves_it_alone(tmp_path):
    """尾逗号现在被视为可读：repair_dir 不改写这类文件，也不报问题。"""
    base = tmp_path / "角色"
    base.mkdir()
    f = base / "char_021.md"
    f.write_text(_record_text(), encoding="utf-8")
    assert CS.repair_dir(base) == []
    assert CS.problems_for_dir(base) == []


def test_decode_still_reports_unescaped_quote_with_line_number():
    bad = _record_text(state_key_change='投影"出现"')
    with pytest.raises(kvfile.KVFormatError) as exc:
        kvfile.decode(bad, "char_021.md")
    assert "第 6 行的 state_log 不是合法 JSON" in exc.value.problems[0]
