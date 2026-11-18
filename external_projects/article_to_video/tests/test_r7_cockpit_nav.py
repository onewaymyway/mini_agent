"""R7：驾驶舱为主入口。侧边栏只保留全局入口；步骤在驾驶舱内切换；流程总览的状态与下一步正确。"""
import pytest

from conftest import PROJECT_DIR
from helpers_p1 import make_project
from helpers_ui_nav import COCKPIT, go_page, go_step

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from article_to_video.planning.runner import confirm_spine, plan_outline  # noqa: E402
from article_to_video.planning.runner import load_spine  # noqa: E402
from ui.cockpit_page import overview, _next_hint  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def _app():
    return AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()


def test_sidebar_has_only_global_pages(home):
    at = _app()
    assert not at.exception
    options = list(at.sidebar.radio[0].options)
    assert options == [COCKPIT, "📥 新建项目", "📚 项目", "⚙️ 设置"]


def test_cockpit_is_the_default_page(home):
    make_project(home)
    at = _app()
    assert any(h.value == "🧭 驾驶舱" for h in at.header)


def test_cockpit_step_selector_lists_control_and_all_steps(home):
    make_project(home)
    at = _app()
    radio = next(r for r in at.radio if r.key == "cockpit_step")
    assert list(radio.options) == ["🧭 总控", "🗺️ 大纲", "📝 口播稿", "🔊 配音", "🎞️ 分镜", "🎬 合成导出"]


@pytest.mark.parametrize("step,heading", [
    ("🧭 总控", "🧭 驾驶舱"),          # 总控沿用驾驶舱的主标题
    ("🗺️ 大纲", "🗺️ 大纲（C0）"),
    ("📝 口播稿", "📝 口播稿（C1）"),
    ("🔊 配音", "🔊 配音（S5）"),
    ("🎞️ 分镜", "🎞️ 分镜（C2）"),
    ("🎬 合成导出", "🎬 合成导出（P5）"),
])
def test_each_step_renders_inside_cockpit(home, step, heading):
    make_project(home)
    at = _app()
    radio = next(r for r in at.radio if r.key == "cockpit_step")
    radio.set_value(step).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == heading for h in at.header)


def test_step_switch_does_not_leave_old_step_content(home):
    pdir = make_project(home)
    plan_outline(pdir, engine="rules")
    at = _app()
    go_step(at, "🗺️ 大纲")
    assert any(h.value == "🗺️ 大纲（C0）" for h in at.header)
    go_step(at, "🔊 配音")
    assert not any(h.value == "🗺️ 大纲（C0）" for h in at.header)
    assert any(h.value == "🔊 配音（S5）" for h in at.header)


def test_overview_status_follows_the_outline(home):
    pdir = make_project(home)
    rows = {r["步骤"]: r for r in overview(pdir)}
    assert rows["🗺️ 大纲"]["状态"] == "未生成"
    assert "大纲" in _next_hint(overview(pdir))

    plan_outline(pdir, engine="rules")
    rows = {r["步骤"]: r for r in overview(pdir)}
    assert rows["🗺️ 大纲"]["状态"] == "待确认"
    assert rows["📝 口播稿"]["状态"] == "未完成"

    confirm_spine(pdir)
    rows = {r["步骤"]: r for r in overview(pdir)}
    assert rows["🗺️ 大纲"]["状态"] == "已确认" and rows["🗺️ 大纲"]["完成"]
    assert rows["🗺️ 大纲"]["说明"].endswith("章")


def test_overview_hint_when_everything_done(home):
    rows = [{"步骤": s, "状态": "已确认", "说明": "", "完成": True} for s in ["a", "b"]]
    assert _next_hint(rows) == "全部步骤已完成。"


def test_overview_table_is_shown_on_cockpit(home):
    make_project(home)
    at = _app()
    assert any(h.value == "流程总览" for h in at.subheader)
    assert len(at.table) >= 1
