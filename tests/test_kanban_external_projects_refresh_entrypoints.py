"""tests/test_kanban_external_projects_refresh_entrypoints.py — 看板「🗂️ 外部项目」
项目卡片上「🔄 更新 entrypoints」按钮的 UI 链路测试（点击 → 调后端 → rerun →
展示结果），以及卡片内时间字段显示为本地时间。

用 `streamlit.testing.v1.AppTest` 驱动真实的 `render_external_projects_tab()`，
后端 client 用内存假对象替代。脚本用 `from_string` 生成，以便把 app 目录的
绝对路径写进去（`from_function` 不能引用闭包变量）。
"""
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")

SCRIPT_TEMPLATE = '''
import sys
sys.path.insert(0, {app_dir!r})
import app

REFRESH_RESULT = {refresh_result!r}


class FakeClient:
    calls = []

    def external_projects_status(self):
        return {{"projects": [{{
            "name": "demo", "path": "/x/demo", "health": "healthy", "enabled": True,
            "last_run": {{"entrypoint": "scan", "exit_code": 0,
                         "finished_at": "2026-10-14T02:30:00Z"}},
            "recent_runs": [{{"entrypoint": "scan", "trigger": "daemon", "exit_code": 0,
                             "started_at": "2026-10-14T02:29:00+00:00",
                             "finished_at": "2026-10-14T02:30:00+00:00"}}],
            "entrypoints": [],
        }}]}}

    def refresh_external_project_entrypoints(self, name):
        FakeClient.calls.append(name)
        return REFRESH_RESULT

    def external_project_backlog(self, name, status=""):
        return {{"items": []}}


app.render_external_projects_tab(FakeClient())
'''

OK_RESULT = {
    "project": "demo", "enabled": True, "removed": ["ext:demo:old"],
    "registered": ["ext:demo:scan"], "entrypoints": ["scan", "manual"],
    "unscheduled": ["manual"], "cron_synced": True,
}


@pytest.fixture(autouse=True)
def _shanghai_tz():
    if not hasattr(time, "tzset"):
        pytest.skip("需要 time.tzset")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _run(refresh_result):
    code = SCRIPT_TEMPLATE.format(app_dir=APP_DIR, refresh_result=refresh_result)
    return AppTest.from_string(code, default_timeout=30).run()


def _refresh_button(at):
    btns = [b for b in at.button if b.key == "ext_proj_refresh_ep_demo"]
    assert btns, [b.key for b in at.button]
    return btns[0]


def test_card_times_are_shown_in_local_time():
    at = _run(OK_RESULT)
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any("最近一次执行" in c and "2026-10-14 10:30:00" in c for c in captions)
    assert any("2026-10-14 10:29:00 → 2026-10-14 10:30:00" in c for c in captions)
    # 原始 UTC 串不应再出现
    assert not any("02:30:00Z" in c or "+00:00" in c for c in captions)


def test_refresh_button_click_shows_result_after_rerun():
    at = _run(OK_RESULT)
    _refresh_button(at).click().run()
    assert not at.exception
    assert any("删除旧定时任务 1 个，重新注册 1 个" in s.value for s in at.success)
    captions = [c.value for c in at.caption]
    assert any("ext:demo:scan" in c for c in captions)
    assert any("manual" in c and "仅可手动触发" in c for c in captions)


def test_refresh_result_is_shown_only_once():
    at = _run(OK_RESULT)
    _refresh_button(at).click().run()
    assert at.success
    at.run()  # 再普通重跑一次，提示不应重复出现
    assert not any("重新注册" in s.value for s in at.success)


def test_refresh_error_is_shown():
    at = _run({"_error": "project.yaml 解析失败，已保留现有定时任务不变: boom"})
    _refresh_button(at).click().run()
    assert any("更新失败" in e.value and "已保留现有定时任务不变" in e.value for e in at.error)


def test_refresh_when_cron_not_synced_shows_warning():
    res = dict(OK_RESULT, cron_synced=False, removed=[], registered=[])
    at = _run(res)
    _refresh_button(at).click().run()
    assert any("cron 调度器当前不可用" in w.value for w in at.warning)
