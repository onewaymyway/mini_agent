"""R23 第二期：「📦 批量生成」页面与队列界面用的纯函数。"""
import pytest

from conftest import PROJECT_DIR
from article_to_video.batch import prep, runner, store

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402
from helpers_ui_nav import go_page  # noqa: E402

PAGE = "📦 批量生成"
ART = "# 文章标题\n\n作者：某作者\n\n## 第一节\n\n这是第一节的正文内容。\n"


def _app(home):
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    return go_page(at, PAGE)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def enq(home, title, text=None, author="甲"):
    return store.enqueue(home, source_text=text or ART + f"\n{title}\n", source_name=f"{title}.md",
                         title=title, author=author)["item"]


# ---------- 纯函数 ----------

def test_suggest_meta_uses_h1_and_author_line():
    m = prep.suggest_meta(ART, "x.md")
    assert m["title"] == "文章标题" and m["author"] == "某作者" and m["chars"] > 0 and m["minutes"] >= 0
    m2 = prep.suggest_meta("没有标题的一段正文。", "我的文件.md")
    assert m2["title"] == "我的文件" and m2["author"] == ""


def test_parse_and_format_delays():
    assert prep.parse_delays("30, 60，120") == [30.0, 60.0, 120.0]
    assert prep.format_delays([30.0, 60.0, 2.5]) == "30,60,2.5"
    for bad in ("", "abc", "-1", "9999"):
        with pytest.raises(ValueError):
            prep.parse_delays(bad)


def test_item_progress_and_rows(home):
    it = enq(home, "甲")
    data = store.load(home)
    assert prep.queue_rows(home, data)[0]["状态"] == "排队中" and prep.queue_rows(home, data)[0]["进度"] == "尚未开始"
    def run(pdir, params):
        (pdir / "pipeline.json").write_text(
            '{"state":"running","steps":[{"stage":"outline","result":"done"},{"stage":"script","result":"done"}]}',
            encoding="utf-8")
        return {"state": "done"}

    runner.run_queue(home, pipeline_runner=run)
    data = store.load(home)
    row = prep.queue_rows(home, data)[0]
    assert row["状态"] == "完成" and row["项目"]
    item = data["items"][0]
    assert prep.item_progress(home / item["project_id"], {**item, "state": "running"}) == "2/7 阶段完成，当前：配音"


def test_pending_estimate_counts_only_unfinished(home):
    enq(home, "甲")
    enq(home, "乙")
    n, chars, minutes = prep.pending_estimate(store.load(home))
    assert n == 2 and chars > 0 and minutes >= 0
    runner.run_queue(home, pipeline_runner=lambda p, q: {"state": "done"})
    assert prep.pending_estimate(store.load(home))[0] == 0


def test_start_background_runs_once(home, monkeypatch):
    enq(home, "甲")
    import threading
    started = threading.Event()
    release = threading.Event()

    def slow(pdir, params):
        started.set()
        release.wait(5)
        return {"state": "done"}

    assert runner.start_background(home, pipeline_runner=slow) is True
    assert started.wait(5)
    assert runner.start_background(home, pipeline_runner=slow) is False      # 已有运行者，不重复启动
    release.set()
    for _ in range(100):
        if not store.runner_alive(home):
            break
        import time
        time.sleep(0.05)
    assert store.load(home)["items"][0]["state"] == "done"


# ---------- 页面 ----------

def test_page_loads_empty_queue(home):
    at = _app(home)
    assert not at.exception, [e.value for e in at.exception]
    assert any("队列是空的" in i.value for i in at.info)
    start = next(b for b in at.button if b.key == "bq_start")
    assert start.disabled                                  # 没有排队项，开始不可点


def test_page_lists_items_and_start_button_enabled(home):
    enq(home, "甲")
    enq(home, "乙")
    at = _app(home)
    assert not at.exception, [e.value for e in at.exception]
    df = at.dataframe[0].value
    assert list(df["标题"]) == ["甲", "乙"] and list(df["状态"]) == ["排队中", "排队中"]
    assert not next(b for b in at.button if b.key == "bq_start").disabled


def test_start_button_launches_background_runner(home, monkeypatch):
    enq(home, "甲")
    called = []
    monkeypatch.setattr(runner, "start_background", lambda root, **kw: called.append(str(root)) or True)
    at = _app(home)
    next(b for b in at.button if b.key == "bq_start").click().run()
    assert not at.exception and called == [str(home)]


def test_stop_button_requests_stop_when_runner_alive(home):
    enq(home, "甲")
    store.acquire_runner(home)                              # 模拟运行者存活
    at = _app(home)
    assert any("队列运行中" in i.value for i in at.info)
    assert next(b for b in at.button if b.key == "bq_start").disabled
    next(b for b in at.button if b.key == "bq_stop").click().run()
    assert store.stop_requested(home)


def test_remove_and_requeue_from_page(home):
    enq(home, "甲")
    enq(home, "乙")
    store.claim_next(home)
    store.update_item(home, "q0001", state="failed", error="坏了")
    at = _app(home)
    next(b for b in at.button if b.key == "bq_requeue").click().run()
    assert [i["state"] for i in store.load(home)["items"]] == ["queued", "queued"]
    at = _app(home)
    next(s for s in at.selectbox if s.key == "bq_pick").set_value("q0002").run()
    next(b for b in at.button if b.key == "bq_remove").click().run()
    assert [i["id"] for i in store.load(home)["items"]] == ["q0001"]


def test_params_save_g3_and_g2_needs_budget(home):
    at = _app(home)
    # 先保存 G3（默认）与新的重试设置
    next(r for r in at.radio if r.key == "bq_gear").set_value("G3").run()
    at.number_input(key="bq_retries").set_value(2).run()
    at.text_input(key="bq_delays").set_value("5, 10").run()
    next(b for b in at.button if b.key == "bq_save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    p = store.load(home)["params"]
    assert p["gear"] == "G3" and p["max_retries"] == 2 and p["retry_delays"] == [5.0, 10.0]
    # G2 不填预算 → 报错，参数保持不变
    next(r for r in at.radio if r.key == "bq_gear").set_value("G2").run()
    next(b for b in at.button if b.key == "bq_save").click().run()
    assert any("预算" in e.value for e in at.error)
    assert store.load(home)["params"]["gear"] == "G3"
    # G2 填预算 → 成功
    at.number_input(key="bq_mi").set_value(20).run()
    next(b for b in at.button if b.key == "bq_save").click().run()
    assert store.load(home)["params"]["gear"] == "G2" and store.load(home)["params"]["max_images"] == 20


def test_params_bad_delays_shows_error(home):
    at = _app(home)
    at.text_input(key="bq_delays").set_value("abc").run()
    next(b for b in at.button if b.key == "bq_save").click().run()
    assert at.error and store.load(home)["params"]["retry_delays"] == [30.0, 60.0, 120.0]


def test_g3_shows_unlimited_cost_warning(home):
    at = _app(home)
    # 展开区内的内容在 AppTest 中始终渲染；默认挡位 G3 应有费用提示
    assert any("不设预算上限" in w.value for w in at.warning)


def test_other_pages_still_load(home):
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    for label in ("🧭 驾驶舱", "📥 新建项目", "📚 项目", "⚙️ 设置"):
        at = go_page(at, label)
        assert not at.exception, (label, [e.value for e in at.exception])


# ---------- 加入队列（上传 + 逐篇设置标题作者） ----------

def _upload(at, files):
    fu = at.file_uploader[0]
    fu.upload(files) if hasattr(fu, "upload") else fu.set_value(files)
    return at.run()


def test_upload_shows_editable_table_with_suggestions(home):
    at = _app(home)
    fu = at.file_uploader[0]
    fu.set_value([("甲.md", ART.encode("utf-8"), "text/markdown")])
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    ed = at.data_editor[0]
    row = ed.value.to_dict("records")[0] if hasattr(ed.value, "to_dict") else ed.value[0]
    assert row["标题"] == "文章标题" and row["作者"] == "某作者" and row["文件"] == "甲.md"
    assert any(b.key == "bq_add" and not b.disabled for b in at.button)


def test_add_button_enqueues_with_chosen_title_and_author(home):
    at = _app(home)
    at.file_uploader[0].set_value([("甲.md", ART.encode("utf-8"), "text/markdown"),
                                   ("乙.md", (ART + "\n乙的内容\n").encode("utf-8"), "text/markdown")])
    at.run()
    next(b for b in at.button if b.key == "bq_add").click().run()
    assert not at.exception, [e.value for e in at.exception]
    items = store.load(home)["items"]
    assert [i["title"] for i in items] == ["文章标题", "文章标题"] and [i["author"] for i in items] == ["某作者"] * 2
    assert [i["state"] for i in items] == ["queued", "queued"]
    # 加入后上传框被清空，可以继续一篇一篇地加
    assert not at.file_uploader[0].value


def test_can_add_more_while_queue_is_running(home):
    enq(home, "甲")
    store.acquire_runner(home)                              # 队列正在运行
    at = _app(home)
    at.file_uploader[0].set_value([("乙.md", (ART + "\n新加的\n").encode("utf-8"), "text/markdown")])
    at.run()
    next(b for b in at.button if b.key == "bq_add").click().run()
    assert [i["state"] for i in store.load(home)["items"]] == ["queued", "queued"]


def test_non_utf8_file_is_reported_not_crashing(home):
    at = _app(home)
    at.file_uploader[0].set_value([("坏.md", "中文".encode("gbk"), "text/markdown")])
    at.run()
    assert not at.exception and at.error
