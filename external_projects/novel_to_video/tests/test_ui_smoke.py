"""Streamlit AppTest 冒烟：不启动浏览器，验证页面渲染与主要按钮流程。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from conftest import build_export

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_renders_import_page():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("导入 novel_writer 导出目录" in h.value for h in at.header)


def test_check_and_create_flow(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="imp_src").input(str(src)).run()
    at.text_input(key="imp_root").input(str(out)).run()
    at.button(key="imp_check").click().run()
    assert not at.exception
    assert any("将选中 3 章" in m.value for m in at.info)
    at.button(key="imp_create").click().run()
    assert not at.exception
    assert any("项目已创建" in s.value for s in at.success)
    assert len(list(out.iterdir())) == 1


def test_projects_page_lists_created(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    from novel_to_video import tool_api
    tool_api.create_project_from_source(str(out), str(src), title="书")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("📚 项目列表").run()
    at.text_input(key="proj_root").input(str(out)).run()
    assert not at.exception
    assert len(at.dataframe) >= 1
