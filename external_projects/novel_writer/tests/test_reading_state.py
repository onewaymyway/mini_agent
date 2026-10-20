"""阅读/听书持久化状态：默认值、增量更新、损坏文件兜底、章节有效性（M7）。"""

import json

from novel_writer import reading_state as rs


def test_defaults_when_no_file(tmp_path):
    st = rs.load(tmp_path)
    assert st == rs.DEFAULTS
    assert not (tmp_path / "_studio" / rs.STATE_NAME).exists()


def test_update_persists_and_only_writes_changes(tmp_path):
    rs.update(tmp_path, last_read_chapter=3)
    rs.update(tmp_path, last_listen_chapter=5, listen_rate=2.5, listen_voice="Xiaoxiao")
    st = rs.load(tmp_path)
    assert st["last_read_chapter"] == 3
    assert st["last_listen_chapter"] == 5
    assert st["listen_rate"] == 2.5
    assert st["listen_voice"] == "Xiaoxiao"
    path = tmp_path / "_studio" / rs.STATE_NAME
    before = path.stat().st_mtime_ns
    rs.update(tmp_path, last_read_chapter=3)  # 相同值不写盘
    assert path.stat().st_mtime_ns == before


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "_studio").mkdir()
    (tmp_path / "_studio" / rs.STATE_NAME).write_text("{不是json", encoding="utf-8")
    assert rs.load(tmp_path) == rs.DEFAULTS
    rs.update(tmp_path, last_read_chapter=2)  # 损坏后仍可写回
    assert rs.load(tmp_path)["last_read_chapter"] == 2


def test_unknown_field_is_rejected(tmp_path):
    try:
        rs.update(tmp_path, bogus=1)
    except KeyError:
        return
    raise AssertionError("应拒绝未知字段")


def test_chapter_or_none_checks_availability():
    assert rs.chapter_or_none(3, [1, 2, 3]) == 3
    assert rs.chapter_or_none(9, [1, 2, 3]) is None   # 章节已被删除
    assert rs.chapter_or_none("x", [1, 2, 3]) is None
    assert rs.chapter_or_none(None, [1, 2, 3]) is None


def test_file_is_plain_json_for_inspection(tmp_path):
    rs.update(tmp_path, last_listen_chapter=4)
    data = json.loads((tmp_path / "_studio" / rs.STATE_NAME).read_text(encoding="utf-8"))
    assert data["last_listen_chapter"] == 4
