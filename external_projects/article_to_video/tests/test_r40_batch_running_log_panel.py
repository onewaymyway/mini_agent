"""R40：批量页底部的「当前文章的运行日志」可滚动，并显示正在运行那篇的项目日志。"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from article_to_video import joblog
from article_to_video.batch import store
from helpers_ui_nav import go_page
from test_r23_queue_ui import PAGE, PROJECT_DIR, enq, home  # noqa: F401  (fixtures/helpers)


def test_running_item_log_is_shown_at_the_bottom_in_a_scrollable_box(home):
    item = enq(home, "甲")
    proj = Path(home) / "proj_甲"
    store.update_item(home, item["id"], state="running", project_id=proj.name)
    for i in range(3):
        joblog.log(proj, "deck", f"步骤记录第{i}条")
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    at = go_page(at, PAGE)
    assert not at.exception, [e.value for e in at.exception]
    codes = [c.value for c in at.code]
    assert any("步骤记录第2条" in c for c in codes)
    # 放在页面最底部：日志代码块之后不再有输入控件
    assert any("当前文章的运行日志" in m.value for m in at.markdown) or any(
        "当前文章的运行日志" in s.value for s in at.subheader)


def test_no_running_item_shows_hint(home):
    enq(home, "乙")
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    at = go_page(at, PAGE)
    assert not at.exception
    assert any("当前没有正在运行的文章" in c.value for c in at.caption)
