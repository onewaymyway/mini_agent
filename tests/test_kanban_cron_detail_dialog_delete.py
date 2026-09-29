"""tests/test_kanban_cron_detail_dialog_delete.py — 看板「⏰ Cron 任务」详情弹窗
与「📌 目标看板」Goal 详情弹窗内的 rerun 交互回归测试
（next_doc/kanban_cron_detail_dialog_delete_rerun_bugfix.md）。

背景：详情弹窗是 `st.dialog`（正文本身是一个 fragment）。弹窗内调用不带
`scope` 的 `st.rerun()` 会整页重跑并把弹窗关掉；而\"删除\"需要\"点删除 →
二次确认 → 点确认删除\"两步，第一步就关弹窗会导致：详情页消失、任务还在、
再次点开详情直接是确认态。

弹窗的真实交互依赖浏览器里的 fragment 局部重跑，无头单测无法驱动（
`streamlit.testing.AppTest` 对 `st.dialog` 内的按钮不会做 fragment 级重跑，
新旧代码表现一致，没有鉴别力），因此这里分两层：
  1. 行为层：`_rerun_cron_dialog_only()` 的正常/降级路径；
  2. 结构层：锁定\"哪些分支用局部重跑、哪些用整页重跑\"的约束（Cron 弹窗
     内除\"删除成功\"外一律局部重跑；Goal 弹窗的删除同理），防止后续改动
     无意间改回整页 `st.rerun()`。
真实浏览器（Chromium + Streamlit）下的端到端验证记录见上述 next_doc 文档。
"""
import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import app  # noqa: E402


class _FakeSt:
    def __init__(self, fragment_error: bool = False):
        self.calls: list[tuple] = []
        self._fragment_error = fragment_error

    def rerun(self, scope: str = "app"):
        self.calls.append(("rerun", scope))
        if scope == "fragment" and self._fragment_error:
            raise app.StreamlitInvalidLayoutContextError("not in fragment rerun")


def test_confirm_key_is_per_job():
    assert app._cron_delete_confirm_key("job_a") == "cron_tab_confirm_delete_job_a"
    assert app._cron_delete_confirm_key("job_a") != app._cron_delete_confirm_key("job_b")


def test_rerun_dialog_only_uses_fragment_scope(monkeypatch):
    fake = _FakeSt()
    monkeypatch.setattr(app, "st", fake)
    app._rerun_cron_dialog_only()
    assert fake.calls == [("rerun", "fragment")]


def test_rerun_dialog_only_falls_back_to_full_rerun(monkeypatch):
    # 弹窗刚被打开的那一轮全量重跑里，Streamlit 不允许 scope="fragment"，
    # 必须退回整页 rerun，行为等价于修复前，不能把异常抛给用户。
    fake = _FakeSt(fragment_error=True)
    monkeypatch.setattr(app, "st", fake)
    app._rerun_cron_dialog_only()
    assert fake.calls == [("rerun", "fragment"), ("rerun", "app")]


def _delete_block_source() -> str:
    src = inspect.getsource(app._render_cron_job_details)
    start = src.index("🗑️ 删除任务")
    end = src.index("系统内置任务，不可删除")
    return src[start:end]


def test_delete_block_enter_confirm_and_cancel_keep_dialog_open():
    block = _delete_block_source()
    # 进入确认态、取消两条分支都必须走弹窗局部重跑
    assert block.count("_rerun_cron_dialog_only()") == 2
    # 整页 st.rerun() 只允许出现一次：删除成功后刷新背后的任务列表
    assert len(re.findall(r"\bst\.rerun\(", block)) == 1


def test_delete_block_full_rerun_only_after_successful_delete():
    block = _delete_block_source()
    ok_branch = block[block.index("else:\n                        st.session_state.pop(confirm_key"):]
    assert "st.rerun()" in ok_branch
    # 失败分支（`_error`）里不能整页 rerun，否则错误提示会随弹窗一起消失
    err_branch = block[block.index('result.get("_error")'):block.index("else:\n                        st.session_state.pop(confirm_key")]
    assert "st.rerun" not in err_branch
    assert "st.error(" in err_branch


def test_delete_success_message_survives_full_rerun():
    block = _delete_block_source()
    assert "_CRON_FLASH_KEY" in block


def test_card_detail_button_resets_stale_confirm_state():
    src = inspect.getsource(app._render_cron_job_card)
    idx = src.index("_show_cron_job_detail_dialog(client, job)")
    assert "_cron_delete_confirm_key(job_id)" in src[:idx]


def test_tab_renders_flash_message():
    src = inspect.getsource(app.render_cron_jobs_tab)
    assert "_CRON_FLASH_KEY" in src


# ─────────────────────────────────────────────────────────────────────
# 第二轮：Cron 弹窗其它操作 + Goal 弹窗删除
# ─────────────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    def cron_jobs(self):
        if self._exc:
            raise self._exc
        return self._resp


def test_fetch_cron_job_fresh_found():
    c = _FakeClient({"jobs": [{"id": "a", "enabled": False}, {"id": "b"}]})
    assert app._fetch_cron_job_fresh(c, "a") == ({"id": "a", "enabled": False}, False)


def test_fetch_cron_job_fresh_gone_when_deleted():
    c = _FakeClient({"jobs": [{"id": "b"}]})
    assert app._fetch_cron_job_fresh(c, "a") == (None, True)


@pytest.mark.parametrize("client", [
    _FakeClient({"_error": "boom"}),
    _FakeClient(None),
    _FakeClient(exc=RuntimeError("net")),
])
def test_fetch_cron_job_fresh_failure_keeps_snapshot(client):
    # 请求失败不能被当成\"任务已被删除\"，否则一次网络抖动就会把弹窗清空
    assert app._fetch_cron_job_fresh(client, "a") == (None, False)


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FlashSt:
    def __init__(self, state):
        self.session_state = state
        self.events: list[tuple] = []

    def container(self):
        self.events.append(("container",))
        return _Ctx()

    def success(self, m):
        self.events.append(("success", m))

    def warning(self, m):
        self.events.append(("warning", m))

    def error(self, m):
        self.events.append(("error", m))


def test_flash_always_occupies_a_container_even_when_empty(monkeypatch):
    # 提示位必须固定占位：提示时有时无会让后面的 expander 位置移动、展开状态丢失
    fake = _FlashSt({})
    monkeypatch.setattr(app, "st", fake)
    app._pop_and_render_flash("k")
    assert fake.events == [("container",)]


def test_flash_accepts_tuple_and_list_and_is_one_shot(monkeypatch):
    fake = _FlashSt({"k": ("success", "ok"), "l": [("success", "a"), ("warning", "b")]})
    monkeypatch.setattr(app, "st", fake)
    app._pop_and_render_flash("k")
    app._pop_and_render_flash("l")
    assert ("success", "ok") in fake.events
    assert [e for e in fake.events if e[0] in ("success", "warning")][1:] == [("success", "a"), ("warning", "b")]
    assert "k" not in fake.session_state and "l" not in fake.session_state
    fake.events.clear()
    app._pop_and_render_flash("k")  # 第二次不再展示
    assert fake.events == [("container",)]


def test_cron_dialog_done_sets_flash_then_reruns_dialog_only(monkeypatch):
    fake = _FakeSt()
    fake.session_state = {}
    monkeypatch.setattr(app, "st", fake)
    app._cron_dialog_done("job_a", "success", "已保存。")
    assert fake.session_state[app._cron_dialog_flash_key("job_a")] == ("success", "已保存。")
    assert fake.calls == [("rerun", "fragment")]


def test_cron_details_only_delete_success_uses_full_rerun():
    src = inspect.getsource(app._render_cron_job_details)
    # 整个详情函数里整页 st.rerun() 只允许出现一次：删除成功后刷新背后的列表
    assert len(re.findall(r"\bst\.rerun\(", src)) == 1
    # 各个操作（重置/意见/立即运行/启停/优先级/配置保存与恢复）都走 _cron_dialog_done
    assert src.count("_cron_dialog_done(") >= 7


def test_cron_dialog_refetches_job_and_refreshes_list_on_dismiss():
    src = inspect.getsource(app._show_cron_job_detail_dialog)
    assert "_fetch_cron_job_fresh(" in src
    assert 'on_dismiss="rerun"' in src


def _strip_comments(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _goal_delete_block_source() -> str:
    src = inspect.getsource(app._render_goal_card_details)
    return _strip_comments(src[src.index("🗑️ 删除目标"):])


def test_goal_delete_block_keeps_dialog_open_except_on_success():
    block = _goal_delete_block_source()
    assert block.count("_rerun_dialog_only()") == 2
    assert len(re.findall(r"\bst\.rerun\(", block)) == 1
    ok_idx = block.index("st.rerun()")
    err_branch = block[block.index('result.get("_error")'):block.index("else:\n                            st.session_state.pop(confirm_key")]
    assert "st.rerun" not in err_branch and ok_idx > block.index(err_branch)
    assert "_GOAL_FLASH_KEY" in block


def test_goal_dialog_resets_stale_confirm_state_and_refreshes_on_dismiss():
    src = inspect.getsource(app._show_goal_detail_dialog)
    assert "confirm_delete_goal_" in src[:src.index("    @st.dialog(")]
    assert 'on_dismiss="rerun"' in src


def test_kanban_tab_renders_goal_flash():
    assert "_GOAL_FLASH_KEY" in inspect.getsource(app.render_kanban_tab)
