"""R23 补充：共用作者。共用作者非空时对队列里所有文章生效；为空时逐篇使用加入队列时设置的作者。"""
import json

import pytest

from conftest import PROJECT_DIR
from article_to_video.batch import prep, runner, store
from article_to_video.project_store import read_json

ART = "# 文章标题\n\n作者：文中作者\n\n## 第一节\n\n这是第一节的正文内容。\n"


def ok_runner(pdir, params):
    return {"state": "done", "outputs": []}


def enq(root, title, author, text=None):
    return store.enqueue(root, source_text=text or ART + f"\n{title}\n", source_name=f"{title}.md",
                         title=title, author=author)["item"]


def meta_author(root, item_id):
    item = store.get_item(root, item_id)
    return read_json(root / item["project_id"] / "source" / "meta.json")["author"]["name"]


# ---------- 纯规则 ----------

def test_shared_author_defaults_to_empty_and_is_stripped(tmp_path):
    assert store.load(tmp_path)["params"]["author"] == ""
    p, _ = store.normalize_params({"author": "  共用甲  "})
    assert p["author"] == "共用甲"
    p, _ = store.normalize_params({"author": None})
    assert p["author"] == ""


def test_effective_author_shared_wins_otherwise_item_author():
    item = {"author": "逐篇乙"}
    assert store.effective_author({"author": "共用甲"}, item) == "共用甲"
    assert store.effective_author({"author": ""}, item) == "逐篇乙"
    assert store.effective_author({"author": ""}, {"author": ""}) == ""          # 都没有 = 不署名


def test_shared_author_is_saved_with_other_params(tmp_path):
    store.set_params(tmp_path, {"author": "共用甲", "gear": "G3"})
    p = store.load(tmp_path)["params"]
    assert p["author"] == "共用甲" and p["gear"] == "G3"


# ---------- 运行时 ----------

def test_runner_uses_shared_author_over_item_author(tmp_path):
    store.set_params(tmp_path, {"author": "共用甲"})
    it = enq(tmp_path, "篇一", author="逐篇乙")
    runner.run_queue(tmp_path, pipeline_runner=ok_runner)
    assert meta_author(tmp_path, it["id"]) == "共用甲"


def test_runner_uses_item_author_when_shared_is_empty(tmp_path):
    it = enq(tmp_path, "篇二", author="逐篇乙")
    runner.run_queue(tmp_path, pipeline_runner=ok_runner)
    assert meta_author(tmp_path, it["id"]) == "逐篇乙"


def test_shared_author_applies_to_items_not_yet_started_only(tmp_path):
    a = enq(tmp_path, "先", author="逐篇甲")
    b = enq(tmp_path, "后", author="逐篇乙")
    calls = []

    def run(pdir, params):
        calls.append(1)
        if len(calls) == 1:
            store.set_params(tmp_path, {"author": "中途设置"})   # 第一篇运行中修改共用作者
        return {"state": "done", "outputs": []}

    runner.run_queue(tmp_path, pipeline_runner=run)
    assert meta_author(tmp_path, a["id"]) == "逐篇甲"            # 已在运行的那篇不受影响
    assert meta_author(tmp_path, b["id"]) == "中途设置"          # 之后开始的那篇使用新的共用作者


def test_queue_rows_show_the_author_that_will_be_used(tmp_path):
    enq(tmp_path, "篇三", author="逐篇乙")
    data = store.load(tmp_path)
    assert prep.queue_rows(tmp_path, data)[0]["作者"] == "逐篇乙"
    store.set_params(tmp_path, {"author": "共用甲"})
    assert prep.queue_rows(tmp_path, store.load(tmp_path))[0]["作者"] == "共用甲"


# ---------- 界面 ----------

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402
from helpers_ui_nav import go_page  # noqa: E402

PAGE = "📦 批量生成"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def _app():
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    return go_page(at, PAGE)


def test_params_panel_saves_shared_author(home):
    at = _app()
    at.text_input(key="bq_author").set_value("共用甲").run()
    next(b for b in at.button if b.key == "bq_save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert store.load(home)["params"]["author"] == "共用甲"


def test_add_table_uses_shared_author_and_disables_column(home):
    store.set_params(home, {"author": "共用甲"})
    at = _app()
    at.file_uploader[0].set_value([("甲.md", ART.encode("utf-8"), "text/markdown")])
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    ed = at.data_editor[0]
    row = ed.value.to_dict("records")[0] if hasattr(ed.value, "to_dict") else ed.value[0]
    assert row["作者"] == "共用甲"
    next(b for b in at.button if b.key == "bq_add").click().run()
    assert store.load(home)["items"][0]["author"] == "共用甲"


def test_add_table_lets_you_set_author_when_shared_is_empty(home):
    at = _app()
    at.file_uploader[0].set_value([("甲.md", ART.encode("utf-8"), "text/markdown")])
    at.run()
    row = at.data_editor[0].value
    row = row.to_dict("records")[0] if hasattr(row, "to_dict") else row[0]
    assert row["作者"] == "文中作者"                                      # 逐篇作者默认取文章里的作者行
    next(b for b in at.button if b.key == "bq_add").click().run()
    assert store.load(home)["items"][0]["author"] == "文中作者"
