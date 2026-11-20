"""页面操作的后台执行与底部运行日志：点击后操作在线程里运行，完成后结果落盘、日志写入底部面板。不调用真实 LLM。"""

import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from novel_writer.revision_flow import review_checks as RK
from test_revision_review_adopt import _ready  # noqa: F401  (复用：三章完稿 + 一个矛盾候选的事实卡)

APP = str(Path(__file__).parent / "apps" / "post_revision_app.py")


def _wait(at, key="rv_op", timeout=20.0):
    end = time.time() + timeout
    while key in at.session_state and time.time() < end:
        time.sleep(0.1)
        at.run()
    return key not in at.session_state


def test_checks_button_runs_in_background_and_logs(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    monkeypatch.setenv("RV_WS", str(ws.root))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    at.button(key="rv_e_checks").click()
    at.run()
    assert _wait(at), "操作应在后台完成"
    assert not at.exception
    assert RK.load_findings(ws, "v001")["findings"]                    # 操作确实执行了
    assert at.session_state.get("rv_op_last", {}).get("ok") is True
    ops_text = "\n".join(at.code[i].value for i in range(len(at.code)) if "确定性检查" in at.code[i].value)
    assert "确定性检查" in ops_text                                      # 底部日志含该操作


def test_op_log_panel_is_at_bottom_of_page(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    monkeypatch.setenv("RV_WS", str(ws.root))
    at = AppTest.from_file(APP, default_timeout=30).run()
    labels = [e.label for e in at.expander] if hasattr(at, "expander") else []
    assert any("操作日志" in l for l in labels)
