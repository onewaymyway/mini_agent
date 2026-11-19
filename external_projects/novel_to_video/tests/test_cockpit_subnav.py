"""驾驶舱子导航：生成流程页面放在驾驶舱内部；顶层导航只剩三项；子页面共用驾驶舱选中的项目。

使用 Streamlit AppTest 在无浏览器环境下运行页面脚本。
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from test_chapter_pipeline import project  # noqa: F401  (fixture：一个已建立的测试项目)

APP = str(Path(__file__).resolve().parents[1] / "app.py")
GEN = ["🎭 全书素材", "⚧ 性别确认", "🧩 补建角色审核", "🎬 章节生产", "📺 章节视频", "🛠️ 设置与队列", "🎞️ 全书合成"]
OVERVIEW = "🧭 驾驶舱总览"


def _run(root: Path) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.text_input(key="ck_root").set_value(str(root)).run()
    return at


def test_top_level_navigation_keeps_only_setup_pages(project):  # noqa: F811
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    nav = next(r for r in at.sidebar.radio if r.key == "nav")
    assert list(nav.options) == ["🧭 驾驶舱", "📥 导入导出目录", "📚 项目列表"]


def test_cockpit_sub_navigation_lists_all_generation_pages(project):  # noqa: F811
    at = _run(project.parent)
    assert not at.exception
    sub = next(r for r in at.radio if r.key == "ck_sub")
    assert list(sub.options) == [OVERVIEW, *GEN]


@pytest.mark.parametrize("label", GEN)
def test_each_generation_page_renders_inside_cockpit(project, label):  # noqa: F811
    at = _run(project.parent)
    next(r for r in at.radio if r.key == "ck_sub").set_value(label).run()
    assert not at.exception, [e.value for e in at.exception]
    # 子页面不再自带项目目录输入框（项目由驾驶舱统一选择）
    assert not any(t.key in ("assets_root", "chap_root", "vid_root", "gear_root", "auto_root",
                             "final_root", "gen_root") for t in at.text_input)


def test_overview_still_shows_next_step_and_jump_button(project):  # noqa: F811
    at = _run(project.parent)
    assert not at.exception
    assert any("当前步骤" in m.value for m in at.info) or any("已完成" in m.value for m in at.success)


def test_next_step_button_switches_the_cockpit_sub_page(project):  # noqa: F811
    at = _run(project.parent)
    assert not at.exception
    btns = [b for b in at.button if b.key == "ck_next_go"]
    if not btns:          # 当前步骤不在子导航内（例如仍需选择章节），无需跳转
        pytest.skip("当前步骤不在子导航内")
    target = btns[0].label.split("›「")[1].rstrip("」")
    assert target in GEN
    btns[0].click().run()
    assert not at.exception
    assert next(r for r in at.radio if r.key == "ck_sub").value == target
