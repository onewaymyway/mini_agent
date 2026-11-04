"""世界观页面：五个分类同时显示（分区），而不是通过选择框切换。"""
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA

PROJECT = Path(__file__).resolve().parent.parent


def test_world_page_shows_all_categories_at_once(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("港", base=str(tmp_path))["data"]["root"]
    BA.world_add(root, "rules", {"text": "雾中见死者", "cost": "失去记忆"})
    BA.world_add(root, "locations", "钟楼")
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    app = PROJECT / "tests" / "apps" / "brainstorm_page_app.py"
    at = AppTest.from_file(str(app), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    # 不再有分类选择框
    assert not any(sb.key == "bs_world_kind" for sb in at.selectbox)
    headers = [h.value for h in at.subheader]
    for label in ("规则（1）", "地点（1）", "势力（0）", "时间线（0）", "禁忌（0）"):
        assert label in headers, headers
    # 每个分类都有自己的新增表单
    form_keys = {f.key for f in at.form}
    assert {"bs_world_add_rules", "bs_world_add_locations", "bs_world_add_factions",
            "bs_world_add_timeline", "bs_world_add_taboos"} <= form_keys
    shown = " ".join(str(m.value) for m in at.markdown)
    assert "雾中见死者" in shown and "钟楼" in shown                     # 已有条目在各自分区里可见
