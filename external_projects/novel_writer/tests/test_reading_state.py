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


def test_resume_segment_only_for_saved_chapter():
    state = {"last_listen_chapter": 4, "listen_segment": 17}
    assert rs.resume_segment(state, 4) == 17      # 同一章：接着听
    assert rs.resume_segment(state, 2) is None    # 换了起始章：从该章开头
    assert rs.resume_segment({"last_listen_chapter": 4, "listen_segment": None}, 4) is None
    assert rs.resume_segment({"last_listen_chapter": 4, "listen_segment": True}, 4) is None
    assert rs.resume_segment({"last_listen_chapter": 4, "listen_segment": -1}, 4) is None


def test_position_is_per_book(tmp_path):
    book_a, book_b = tmp_path / "a", tmp_path / "b"
    rs.update(book_a, last_listen_chapter=3, listen_segment=12)
    rs.update(book_b, last_listen_chapter=1, listen_segment=2)
    a, b = rs.load(book_a), rs.load(book_b)
    assert (a["last_listen_chapter"], a["listen_segment"]) == (3, 12)
    assert (b["last_listen_chapter"], b["listen_segment"]) == (1, 2)
    assert rs.load(tmp_path / "c")["listen_segment"] is None   # 第三本书没有记录


def test_resolve_chapter_falls_back_to_nearest_earlier_not_first():
    avail = [1, 2, 4, 5]
    assert rs.resolve_chapter(4, avail) == 4          # 存在：原样
    assert rs.resolve_chapter(3, avail) == 2          # 暂时不可听：回退到不晚于它的最近章
    assert rs.resolve_chapter(9, avail) == 5          # 超出末尾：最后一个可听章
    assert rs.resolve_chapter(0, avail) == 1          # 早于所有章：第一章
    assert rs.resolve_chapter(None, avail) == 1
    assert rs.resolve_chapter(3, []) is None
