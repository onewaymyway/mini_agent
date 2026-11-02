"""流水线表格：传给滚动组件的数据（展示列、当前步骤、运行状态高亮）。"""

import ui.step_table as T


def test_payload_hides_internal_fields_and_marks_running(monkeypatch):
    captured = {}
    monkeypatch.setattr(T, "_table", lambda **kw: captured.update(kw))
    rows = [
        {"__id": "ch01.write", "下一步": "", "步骤": "ch01.write", "状态": "🔵 运行中"},
        {"__id": "ch02.write", "下一步": "▶", "步骤": "ch02.write", "状态": "⚪ 待运行"},
    ]
    T.render_step_table(rows, current_id="ch01.write", statuses={"ch01.write": "running", "ch02.write": "pending"})
    data = captured["data"]
    assert data["columns"] == ["下一步", "步骤", "状态"]          # 不含 __id
    assert data["current"] == "ch01.write"
    assert [r["__status"] for r in data["rows"]] == ["running", "pending"]
    assert data["rows"][0]["__id"] == "ch01.write"


def test_empty_pipeline_is_valid(monkeypatch):
    captured = {}
    monkeypatch.setattr(T, "_table", lambda **kw: captured.update(kw))
    T.render_step_table([], current_id=None, statuses={})
    assert captured["data"] == {"columns": [], "rows": [], "current": None}


def test_long_text_columns_wrap_in_component_html():
    """回归：标题等长文本列必须可换行并限宽，否则会把整列撑得特别宽。"""
    html = (T._COMPONENT_DIR / "index.html").read_text(encoding="utf-8")
    assert '"标题": "wrap-title"' in html and '"说明": "wrap"' in html and '"步骤": "wrap-id"' in html
    assert "td.wrap-title" in html and "overflow-wrap: anywhere" in html
    assert "max-width: 22rem" in html
