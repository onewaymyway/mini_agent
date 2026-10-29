"""阅读页与听书的持久化：刷新（新会话）后恢复上次阅读章节与听书参数（M7）。"""

from pathlib import Path

import pytest

from fakes import put_blueprint
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
    put_blueprint(ws, make_blueprint(3))
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


# ── 进度不被重置（保存的章节暂时不可听 / 新章节生成 / 载荷更新）─────────────────────────


def _listen_args(at):
    import json
    for e in at.get("component_instance"):
        if "listen" in e.proto.component_name:
            a = json.loads(e.proto.json_args)
            return a, at.selectbox(key="listen_start").value
    raise AssertionError("没有找到听书组件")


def test_saved_chapter_temporarily_unreadable_never_restarts_from_book_start(isolated):
    """复现：保存的章节暂时读不到时，旧代码把 resume_ch 传成 None，播放器从全书第一段开始。"""
    from novel_writer import chapter_ops as C
    ws = _book(isolated)
    reading_state.update(ws.root, last_listen_chapter=3, listen_segment=1, listen_anchor="这是第3章")
    at = _app(ws)
    assert _listen_args(at)[0]["resume_ch"] == 3
    f = C.chapter_file(ws, 3)
    text = f.read_text(encoding="utf-8")
    f.unlink()                                            # 第 3 章正文被重写的瞬间
    refreshed = _app(ws)                                  # 此时刷新页面（新会话，载荷不含第 3 章）
    args, sel = _listen_args(refreshed)
    assert args["resume_ch"] == 2 and args["resume_seg"] == 0      # 最近的前一章开头，而不是 None / 全书开头
    assert sel == 2
    assert reading_state.load(ws.root)["last_listen_chapter"] == 3  # 保存的位置没有被回退值覆盖
    assert any("暂时无法读取" in w.value for w in refreshed.warning)
    f.write_text(text, encoding="utf-8")                  # 正文恢复
    refreshed.run()
    args, sel = _listen_args(refreshed)
    assert args["resume_ch"] == 3 and args["resume_seg"] == 1 and sel == 3   # 恢复后自动回到原位置，无需任何操作
    assert not any("章节内容有更新" in i.value for i in refreshed.info)


def test_new_chapter_is_pushed_live_without_prompt_or_position_change(isolated):
    """章节改写：载荷自动更新（播放器原地接收，key 不变所以不重建），不弹提示，位置不变。"""
    ws = _book(isolated)
    reading_state.update(ws.root, last_listen_chapter=2, listen_segment=1)
    at = _app(ws)
    before, key_before = _listen_args(at)[0], at.get("component_instance")[0].proto.id
    tool_api.save_chapter(str(ws.root), 3, "# 第3章 标题3\n\n这是被流水线改写后更长的第3章正文。他走了。他又回来了。\n")
    at.run()
    after, sel = _listen_args(at)
    assert after["payload"] != before["payload"] and "被流水线改写" in "".join(after["payload"]["segments"])
    assert (after["resume_ch"], after["resume_seg"], sel) == (2, 1, 2)   # 位置不变，不回到第一章
    assert not any("章节内容有更新" in i.value for i in at.info)         # 没有提示
    assert not any(b.key == "listen_reload" for b in at.button)           # 没有「载入更新」按钮
    assert at.get("component_instance")[0].proto.id == key_before        # 组件 key 未变 → 不重建、不打断朗读


def test_player_always_gets_a_resume_chapter(isolated):
    ws = _book(isolated)
    at = _app(ws)                                          # 从未保存过位置：第一个可听章节开头
    args, sel = _listen_args(at)
    assert (args["resume_ch"], args["resume_seg"], sel) == (1, 0, 1)


# ── 选择框的选项随新章节/标题变化而被重置 ─────────────────────────────────────────────────


def _book5(tmp_path):
    """计划 5 章，先写 3 章；之后再写第 4 章，模拟流水线持续生成。"""
    r = tool_api.create_novel("雨夜钟声", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=5)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(5))
    for n in (1, 2, 3):
        tool_api.save_chapter(str(ws.root), n, f"# 第{n}章 标题{n}\n\n这是第{n}章的正文。他走了。\n")
    return ws


def test_new_chapter_appears_in_picker_and_selection_stays(isolated):
    """新章节生成：选择框立即出现新章节，且当前选中的章节不被重置回第一项。"""
    ws = _book5(isolated)
    reading_state.update(ws.root, last_listen_chapter=3, listen_segment=1)
    at = _app(ws)
    opts_before = list(at.selectbox(key="listen_start").options)
    tool_api.save_chapter(str(ws.root), 4, "# 第4章 标题4\n\n新生成的第四章正文。他来了。\n")
    at.run()
    assert len(at.selectbox(key="listen_start").options) == len(opts_before) + 1
    assert at.selectbox(key="listen_start").value == 3                             # 没有被重置回第一项
    assert not any("章节内容有更新" in i.value for i in at.info)
    args, sel = _listen_args(at)
    assert (args["resume_ch"], args["resume_seg"], sel) == (3, 1, 3)               # 仍在原位置


def test_reset_selectbox_is_restored_and_saved_position_untouched(isolated):
    """即使选择框被重置回第一项（控件身份变化的后果），也恢复为之前设定的章节，不改写已保存的进度。"""
    ws = _book5(isolated)
    reading_state.update(ws.root, last_listen_chapter=3, listen_segment=1)
    at = _app(ws)
    assert at.selectbox(key="listen_start").value == 3
    at.session_state["listen_start"] = 1          # 模拟被 Streamlit 重置为第一项
    at.run()
    assert at.selectbox(key="listen_start").value == 3
    args, _ = _listen_args(at)
    assert (args["resume_ch"], args["resume_seg"]) == (3, 1)
    saved = reading_state.load(ws.root)
    assert (saved["last_listen_chapter"], saved["listen_segment"]) == (3, 1)


def test_user_pick_is_not_mistaken_for_a_reset(isolated):
    ws = _book5(isolated)
    reading_state.update(ws.root, last_listen_chapter=3, listen_segment=1)
    at = _app(ws)
    at.selectbox(key="listen_start").set_value(2).run()
    assert at.selectbox(key="listen_start").value == 2                           # 用户主动改选，不会被"恢复"回 3
    saved = reading_state.load(ws.root)
    assert (saved["last_listen_chapter"], saved["listen_segment"]) == (2, 0)
    args, _ = _listen_args(at)
    assert (args["resume_ch"], args["resume_seg"]) == (2, 0)
