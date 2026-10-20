"""阅读页与听书的持久化：刷新（新会话）后恢复上次阅读章节与听书参数（M7）。"""

from pathlib import Path

import pytest

from novel_writer import config, reading_state, tool_api
from novel_writer.fsutil import atomic_write_json
from novel_writer.workspace import Workspace
from test_m3 import make_blueprint  # 复用蓝图构造


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _book(tmp_path):
    r = tool_api.create_novel("雨夜钟声", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    atomic_write_json(ws.path("blueprint"), make_blueprint(3))
    for n in (1, 2, 3):
        tool_api.save_chapter(str(ws.root), n, f"# 第{n}章 标题{n}\n\n这是第{n}章的正文。他走了。\n")
    return ws


def _app(ws):
    from streamlit.testing.v1 import AppTest
    app = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    return at


def test_reading_chapter_survives_refresh(isolated):
    ws = _book(isolated)
    at = _app(ws)
    at.selectbox(key="rd_pick").set_value(2).run()
    assert not at.exception, at.exception
    assert reading_state.load(ws.root)["last_read_chapter"] == 2

    refreshed = _app(ws)   # 模拟刷新浏览器：新会话，session_state 清空
    assert refreshed.selectbox(key="rd_pick").value == 2
    assert not refreshed.exception, refreshed.exception


def test_restart_restores_from_file_without_session(isolated):
    ws = _book(isolated)
    reading_state.update(ws.root, last_read_chapter=3, last_listen_chapter=2, listen_rate=3.0, listen_voice="X")
    at = _app(ws)   # 模拟重启 Streamlit：文件存在，会话全新
    assert at.selectbox(key="rd_pick").value == 3
    assert at.selectbox(key="listen_start").value == 2
    assert not at.exception, at.exception


def test_stale_saved_chapter_falls_back_to_first(isolated):
    ws = _book(isolated)
    reading_state.update(ws.root, last_read_chapter=99)  # 章节已不存在
    at = _app(ws)
    assert at.selectbox(key="rd_pick").value == 1


def test_user_pick_saves_chapter_start_and_restarts_player(isolated):
    ws = _book(isolated)
    at = _app(ws)
    at.selectbox(key="listen_start").set_value(3).run()
    assert not at.exception, at.exception
    st_saved = reading_state.load(ws.root)
    assert st_saved["last_listen_chapter"] == 3
    assert st_saved["listen_segment"] == 0
    assert at.session_state["listen_nonce"] == 1   # 播放器以新 key 重新挂载


def test_saved_chapter_that_is_temporarily_unreadable_does_not_reset_to_first(isolated):
    ws = _book(isolated)
    reading_state.update(ws.root, last_listen_chapter=3, listen_segment=5)
    from novel_writer import chapter_ops as C
    C.chapter_file(ws, 3).unlink()   # 第 3 章正文文件暂时不存在，字数为 0，不再可听
    at = _app(ws)
    assert at.selectbox(key="listen_start").value == 2   # 回退到不晚于 3 的最近章，而不是第 1 章
    assert not at.exception, at.exception


def test_pending_sync_is_applied_before_selectbox_without_exception(isolated):
    ws = _book(isolated)
    at = _app(ws)
    at.session_state["listen_pending_sync"] = 3   # 模拟播放器切到第 3 章后的下一次运行
    at.run()
    assert not at.exception, at.exception
    assert at.selectbox(key="listen_start").value == 3
    assert "listen_pending_sync" not in at.session_state
