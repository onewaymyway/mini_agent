"""tests/test_kanban_external_projects_compact_card.py — 看板「🗂️ 外部项目」卡片精简展示。

约定：卡片默认只显示项目名、健康状态、最近一次执行摘要、Streamlit 启动管理
（状态/打开/启动/停止/重启）；其余功能全部在点「📂 详情」之后才渲染，
详情里的后端请求（改进积压、状态看板）也只在展开后才发起。

用 `streamlit.testing.v1.AppTest` 驱动真实的 `render_external_projects_tab()`，
后端 client 与 Streamlit 应用状态均用内存假对象替代。
"""
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")

SCRIPT = '''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {app_dir!r})
import app
from mini_agent.external_projects import web_app as wa
from mini_agent.external_projects.manifest import WebAppSpec

WEB_STATE = {web_state!r}          # None = 项目没有 web_app 块
LAST_RUN = {last_run!r}


class FakeClient:
    calls = []

    def external_projects_status(self):
        return {{"projects": [{{
            "name": "demo", "path": "/x/demo", "health": "healthy", "enabled": True,
            "last_run": LAST_RUN,
            "recent_runs": [], "entrypoints": [],
            "kanban_view": None,
        }}]}}

    def external_project_backlog(self, name, status=""):
        app.st.session_state["backlog_calls"] = app.st.session_state.get("backlog_calls", 0) + 1
        return {{"items": []}}


if WEB_STATE is None:
    app._load_web_app_entries = lambda: {{}}
else:
    spec = WebAppSpec(port=8599)
    app._load_web_app_entries = lambda: {{"demo": SimpleNamespace(name="demo", root=Path("/x/demo"), spec=spec, error=None)}}
    wa.get_status = lambda name, root, spec: wa.WebAppStatus(
        name=name, state=WEB_STATE, port=spec.port, host=None, url="http://127.0.0.1:8599",
        bind="127.0.0.1" if WEB_STATE != wa.STATE_STOPPED else None)

app.render_external_projects_tab(FakeClient())
'''

OK_RUN = {"entrypoint": "scan", "exit_code": 0, "finished_at": "2026-10-14T02:30:00Z"}
FAIL_RUN = {"entrypoint": "scan", "exit_code": 1, "finished_at": "2026-10-14T02:30:00Z",
            "detail": "Traceback boom"}

# 只应出现在「详情」里的控件 key
DETAIL_KEYS = ["ext_proj_enabled_demo", "ext_proj_refresh_ep_demo", "ext_backlog_submit_demo",
               "ext_review_gen_demo", "unregister_ext_proj_demo"]


def _run(web_state=None, last_run=OK_RUN):
    code = SCRIPT.format(app_dir=APP_DIR, web_state=web_state, last_run=last_run)
    return AppTest.from_string(code, default_timeout=30).run()


def _keys(at):
    return {b.key for b in at.button} | {c.key for c in at.checkbox}


def _toggle(at):
    btn = [b for b in at.button if b.key == "ext_proj_detail_btn_demo"]
    assert btn, [b.key for b in at.button]
    btn[0].click().run()
    return at


def test_default_is_compact_and_details_hidden():
    at = _run()
    assert not at.exception
    keys = _keys(at)
    assert "ext_proj_detail_btn_demo" in keys
    for k in DETAIL_KEYS:
        assert k not in keys, k
    assert [e.label for e in at.expander] == ["➕ 注册新项目"]   # 项目卡片内没有任何折叠区
    assert [b for b in at.button if b.key == "ext_proj_detail_btn_demo"][0].label == "📂 详情"


def test_compact_card_shows_key_summary():
    at = _run(last_run=OK_RUN)
    assert any("**demo**" in m.value for m in at.markdown)
    captions = " ".join(c.value for c in at.caption)
    assert "最近一次执行：scan" in captions and "✅ 成功" in captions
    assert "自动调度：开" in captions
    assert "路径：" not in captions                         # 路径属于详情


def test_failed_run_shows_hint_but_not_traceback_until_details():
    at = _run(last_run=FAIL_RUN)
    assert "❌ 失败" in " ".join(c.value for c in at.caption)
    assert not [c for c in at.code if "Traceback boom" in c.value]
    _toggle(at)
    assert [c for c in at.code if "Traceback boom" in c.value]


def test_details_render_only_after_click_and_can_collapse():
    at = _run()
    assert "backlog_calls" not in at.session_state          # 未展开时不请求改进积压
    _toggle(at)
    assert not at.exception
    keys = _keys(at)
    for k in DETAIL_KEYS:
        assert k in keys, k
    assert [b for b in at.button if b.key == "ext_proj_detail_btn_demo"][0].label == "🔼 收起"
    assert any("路径：/x/demo" in c.value for c in at.caption)
    _toggle(at)                                             # 再点一次收起
    assert not (_keys(at) & set(DETAIL_KEYS))
    assert at.session_state["backlog_calls"] >= 1           # 展开后才发起请求


def test_web_app_management_stays_in_compact_card_when_stopped():
    at = _run(web_state="stopped")
    assert not at.exception
    keys = _keys(at)
    assert "webapp_start_demo" in keys                      # 启动按钮
    assert "webapp_auth_demo" in keys                       # 启动选项（登录）
    assert any(s.key == "webapp_bind_demo" for s in at.selectbox)   # 监听地址
    assert not (keys & set(DETAIL_KEYS))


def test_web_app_running_shows_stop_and_restart_in_compact_card():
    at = _run(web_state="running")
    assert not at.exception
    keys = _keys(at)
    assert {"webapp_stop_demo", "webapp_restart_demo"} <= keys
    assert not (keys & set(DETAIL_KEYS))
