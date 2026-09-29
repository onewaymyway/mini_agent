"""tests/test_kanban_cron_detail_dialog_delete.py — 看板「⏰ Cron 任务」详情弹窗
内的删除交互回归测试（next_doc/kanban_cron_detail_dialog_delete_rerun_bugfix.md）。

背景：详情弹窗是 `st.dialog`（正文本身是一个 fragment）。弹窗内调用不带
`scope` 的 `st.rerun()` 会整页重跑并把弹窗关掉；而\"删除\"需要\"点删除 →
二次确认 → 点确认删除\"两步，第一步就关弹窗会导致：详情页消失、任务还在、
再次点开详情直接是确认态。

弹窗的真实交互依赖浏览器里的 fragment 局部重跑，无头单测无法驱动（
`streamlit.testing.AppTest` 对 `st.dialog` 内的按钮不会做 fragment 级重跑，
新旧代码表现一致，没有鉴别力），因此这里分两层：
  1. 行为层：`_rerun_cron_dialog_only()` 的正常/降级路径；
  2. 结构层：锁定删除区块中\"哪些分支用局部重跑、哪些用整页重跑\"的约束，
     防止后续改动无意间改回整页 `st.rerun()`。
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
