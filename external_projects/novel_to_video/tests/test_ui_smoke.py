"""Streamlit AppTest 冒烟：不启动浏览器，验证页面渲染与主要按钮流程。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from conftest import build_export

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_renders_import_page():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    at.sidebar.radio[0].set_value("📥 导入导出目录").run()
    assert not at.exception
    assert any("导入 novel_writer 导出目录" in h.value for h in at.header)


def test_check_and_create_flow(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("📥 导入导出目录").run()
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


def test_assets_page_renders_with_project(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    from novel_to_video import tool_api
    res = tool_api.create_project_from_source(str(out), str(src), title="书")
    proj = res["data"]["project_dir"]
    tool_api.run_visual_text(proj)
    tool_api.run_portraits(proj)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("🎭 全书素材").run()
    at.text_input(key="assets_root").input(str(out)).run()
    assert not at.exception
    assert any("全书级素材（G-2 / G-3）" in h.value for h in at.header)
    assert any(b.key == "ok_p_character:char_001" for b in at.button)


def test_chapter_page_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    out = tmp_path / "out"
    from novel_to_video import tool_api
    res = tool_api.create_project_from_source(str(out), str(src), title="书")
    assert res["ok"]
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="nav").set_value("🎬 章节生产").run()
    at.text_input(key="chap_root").input(str(out)).run()
    assert not at.exception
    assert any("章节生产（C-1 … C-9）" in h.value for h in at.header)


def test_gear_and_auto_added_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    from novel_to_video import tool_api
    from novel_to_video.providers.image import StubImageProvider
    from novel_to_video.providers.text import RuleTextLLM
    from novel_to_video.steps import global_visual as gv
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(src), title="t")
    proj = Path(res["data"]["project_dir"])
    gv.visual_text_pass(proj, RuleTextLLM())
    gv.portrait_pass(proj, StubImageProvider())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("🚦 挡位与队列").run()
    assert not at.exception
    assert any("挡位与批量队列" in h.value for h in at.header)
    at.text_input(key="gear_root").input(str(out)).run()
    assert not at.exception
    assert any(s.label == "挡位" for s in at.selectbox)
    at.sidebar.radio[0].set_value("🧩 补建角色审核").run()
    at.text_input(key="auto_root").input(str(out)).run()
    assert not at.exception
    assert any("补建角色审核" in h.value for h in at.header)
    assert any("当前没有待审核" in i.value for i in at.info)


def test_final_page_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    from novel_to_video import tool_api
    from novel_to_video.providers.image import StubImageProvider
    from novel_to_video.providers.text import RuleTextLLM
    from novel_to_video.steps import global_visual as gv
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(src), title="t")
    proj = Path(res["data"]["project_dir"])
    gv.visual_text_pass(proj, RuleTextLLM())
    gv.portrait_pass(proj, StubImageProvider())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("🎞️ 全书合成").run()
    at.text_input(key="final_root").input(str(out)).run()
    assert not at.exception
    assert any("全书合成" in h.value for h in at.header)
    assert any("F-1 章节检查" in m.value for m in at.markdown)
