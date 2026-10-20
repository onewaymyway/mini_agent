"""M3 测试：章节 python 步骤、连续写章、修改循环（3 轮升级、不推进下一章）、写作台/审校/阅读的数据层。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import json

import pytest

from fakes import FakeInvoker, drive_to_completion, make_blueprint, statuses
from novel_writer import chapter_ops as C
from novel_writer import config, tool_api
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def new_book(tmp_path, total=3, gear="G3"):
    r = tool_api.create_novel("雨夜钟声", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=total)
    ws = Workspace(r["data"]["workspace"])
    atomic_write_json(ws.path("blueprint"), make_blueprint(total))
    return ws


def settings(ws, **updates):
    cur = ws.settings()
    cur.update(updates)
    ws.save_settings(cur)


def run_until(ws, inv, reasons=("waiting_review",), limit=200):
    """反复运行直到 reason 不在 reasons 中（自动批准 waiting_review）。"""
    from fakes import approve_waiting

    last = None
    for _ in range(limit):
        last = run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
        assert last["ok"], last
        if last["reason"] not in reasons:
            return last
        approve_waiting(ws)
    raise AssertionError(f"未收敛：{last}")


# ── 单元：审稿标记 / 配额 / 情节任务 / 风格提示 / 机器校验 / 上下文包 ─────────


def test_red_items_parses_common_markdown_forms():
    text = "# 报告\n- 🔴 第一条\n* 🔴 第二条\n| 🔴 表格条目 |\n- 🟡 建议\n无 🔴 的说明\n"
    assert C.count_red(text) == 3
    assert C.red_items(None) == []


def test_word_quota_carries_previous_shortfall(tmp_path):
    ws = new_book(tmp_path)
    ws.chapter_path(1, "开篇", total_chapters=3).write_text("字" * 2000, encoding="utf-8")
    base = C.h_word_quota(ws, _step(ws, "ch01.word_quota"), ws.settings())
    assert base["summary"].startswith("配额 4000 字")
    ws.chapter_path(1, "开篇", total_chapters=3).write_text("字" * 2000, encoding="utf-8")
    C.h_word_quota(ws, _step(ws, "ch02.word_quota"), ws.settings())  # 第 1 章配额已由上一行写入
    q2 = json.loads(ws.path("quota_file", n=2).read_text(encoding="utf-8"))
    assert q2["base"] == 4000 and q2["carry"] == 800 and q2["quota"] == 4800  # 短缺 2000，上限 20%


def test_plot_tasks_split_plant_and_resolve(tmp_path):
    ws = new_book(tmp_path)
    atomic_write_json(ws.path("plot_legacy"), {"foreshadowing": [
        {"id": "F1", "name": "旧钟", "plant_chapter": 2, "resolve_chapter": 3},
        {"id": "F2", "name": "信件", "plant_chapter": 1, "resolve_chapter": 2}]})
    C.h_plot_tasks(ws, _step(ws, "ch02.plot_tasks"), ws.settings())
    tasks = json.loads(ws.path("chapter_tasks", n=2).read_text(encoding="utf-8"))
    assert [t["id"] for t in tasks["plant"]] == ["F1"] and [t["id"] for t in tasks["resolve"]] == ["F2"]


def test_style_hint_merges_banned_words(tmp_path):
    ws = new_book(tmp_path)
    settings(ws, banned_words=["仿佛"])
    atomic_write_json(ws.path("style_profile"), {"tone": "冷峻", "banned_words": ["突然"]})
    C.h_style_hint(ws, _step(ws, "ch01.style_hint"), ws.settings())
    text = ws.path("style_hint", n=1).read_text(encoding="utf-8")
    assert "冷峻" in text and "仿佛" in text and "突然" in text


def test_machine_check_flags_banned_duplicate_and_length(tmp_path):
    ws = new_book(tmp_path)
    settings(ws, banned_words=["糟糕"])
    shared = "这一段在前一章已经出现过，主角站在冰冷的雨里，久久地望着远处的灯火，什么也没有说。"
    ws.chapter_path(1, "旧章", total_chapters=3).write_text("# 旧\n\n" + shared + "\n", encoding="utf-8")
    body = "# 新\n\n" + shared + "\n\n糟糕的事情发生了。\n\n" + "长" * 700 + "\n"
    ws.chapter_path(2, "新章", total_chapters=3).write_text(body, encoding="utf-8")
    rep = C.write_machine_report(ws, 2, ws.settings())
    joined = " ".join(rep["hard"])
    assert "禁用词" in joined and "重复" in joined
    assert any("超长段落" in w for w in rep["warn"])
    assert json.loads(ws.path("machine_report", nn=2).read_text(encoding="utf-8"))["words"] == rep["words"]


def test_context_pack_is_sectioned_and_capped(tmp_path):
    ws = new_book(tmp_path)
    ws.path("chapter_summary", nn=1).parent.mkdir(parents=True, exist_ok=True)
    ws.path("chapter_summary", nn=1).write_text("第一章概述：主角收到信件。" * 400, encoding="utf-8")
    ws.path("character_card", name="李凌云").parent.mkdir(parents=True, exist_ok=True)
    ws.path("character_card", name="李凌云").write_text("# 李凌云\n冷静。", encoding="utf-8")
    text = C.build_context(ws, 2)
    for section in ("本章蓝图要求", "字数配额", "出场角色人设", "最近章节概述", "作者意图"):
        assert f"## {section}" in text
    assert "冷静" in text and "已截断" in text and len(text) < 12000


def _step(ws, sid):
    from novel_writer.pipeline import plan_index, build_plan

    return plan_index(build_plan(ws))[sid]


# ── 连续写三章（桩 LLM）────────────────────────────────────────────────────


def test_three_chapters_written_end_to_end(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    for n in (1, 2, 3):
        assert C.chapter_file(ws, n) is not None
        assert ws.path("chapter_summary", nn=n).is_file()
        assert statuses(ws)[f"ch{n:02d}.sync_plot"] == st.ST_PASSED
        assert json.loads(ws.path("machine_report", nn=n).read_text(encoding="utf-8"))["hard"] == []
    assert not any(".revise." in c for c in inv.calls)  # 没有 🔴，不进入修改循环
    assert inv.calls.count("ch02.write_chapter") == 1


# ── 验收：注入硬错误 → 三轮修改 → 升级人工 → 不推进下一章 ─────────────────────


def test_hard_error_escalates_after_three_rounds_and_blocks_next_chapter(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, red=[2])
    last = run_until(ws, inv, reasons=("waiting_review",))
    assert last["reason"] == "waiting_user"
    s = statuses(ws)
    assert s["ch02.revise_loop"] == st.ST_WAITING_USER
    # 恰好三轮：三次修改写作 + 三次复核
    assert [c for c in inv.calls if ".revise.r" in c] == ["ch02.revise.r1", "ch02.revise.r2", "ch02.revise.r3"]
    assert [c for c in inv.calls if ".review.r" in c] == ["ch02.review.r1", "ch02.review.r2", "ch02.review.r3"]
    # 不推进：第三章一步都没开始
    assert all(s[f"ch03.{x}"] == st.ST_PENDING for x in ("word_quota", "write_chapter", "sync_plot"))
    assert "ch03.write_chapter" not in inv.calls
    assert (ws.studio / "inbox" / "ch02_escalation.json").is_file()
    entry = st.get_step_state(ws, "ch02.revise_loop")
    assert entry["needs_user"][0]["options"] == ["按审稿意见再修改一轮", "重新核查（我已手动修改）", "接受风险继续"]
    assert entry["needs_user"][1]["id"] == "feedback"
    assert "修改 3 轮后仍有" in ws.path("revision_log", nn=2).read_text(encoding="utf-8")


def test_accept_risk_continues_past_escalation(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, red=[2])
    run_until(ws, inv)
    assert tool_api.answer_step(str(ws.root), "ch02.revise_loop", {"decision": "接受风险继续"})["ok"]
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    assert statuses(ws)["ch03.sync_plot"] == st.ST_PASSED
    assert "接受风险" in ws.path("revision_log", nn=2).read_text(encoding="utf-8")
    assert not (ws.studio / "answers" / "ch02.revise_loop.json").exists()  # 答复已消费


def test_manual_fix_then_recheck_passes_without_new_rounds(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, red=[2])
    run_until(ws, inv)
    rounds_before = len([c for c in inv.calls if ".revise.r" in c])
    inv.red.discard(2)  # 作者手动改好了
    assert tool_api.answer_step(str(ws.root), "ch02.revise_loop", {"decision": "重新核查（我已手动修改）"})["ok"]
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    assert len([c for c in inv.calls if ".revise.r" in c]) == rounds_before
    assert statuses(ws)["ch03.sync_plot"] == st.ST_PASSED


def test_manual_save_marks_downstream_stale(isolated):
    ws = new_book(isolated, total=2)
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    res = tool_api.save_chapter(str(ws.root), 1, "# 第一章\n\n作者重写的一段话。")
    assert res["ok"], res
    assert "ch01.review" in res["data"]["stale"] or "ch01.write_chapter" in res["data"]["stale"]
    assert "作者手动保存" in ws.path("revision_log", nn=1).read_text(encoding="utf-8")


# ── 写作台 / 审校 / 阅读 的数据层 ──────────────────────────────────────────


def test_chapter_views_and_export(isolated):
    ws = new_book(isolated, total=2)
    drive_to_completion(ws, FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS)
    overview = tool_api.chapters(str(ws.root))["data"]["chapters"]
    assert [r["chapter"] for r in overview] == [1, 2] and all(r["words"] > 3000 for r in overview)
    detail = tool_api.chapter(str(ws.root), 2)["data"]
    assert detail["text"].startswith("# 第2章") and "本章蓝图要求" in detail["context"]
    assert detail["machine"]["hard"] == [] and detail["quota"] == 4000
    out = tool_api.export(str(ws.root))["data"]
    assert out["chapters"] == 2 and (ws.root / out["file"]).read_text(encoding="utf-8").count("# 第") == 2


def test_chapter_detail_rejects_out_of_range(isolated):
    ws = new_book(isolated, total=2)
    assert not tool_api.chapter(str(ws.root), 9)["ok"]


# ── Streamlit 页面冒烟（写作台 / 审校 / 阅读）────────────────────────────────


def test_ui_writing_review_reading_pages_and_escalation_button(isolated):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path

    root_app = Path(__file__).resolve().parent.parent / "app.py"
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, red=[2])
    run_until(ws, inv)  # 第二章升级人工

    at = AppTest.from_file(str(root_app), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 10

    at.selectbox(key="wr_chapter").set_value(1).run()
    assert not at.exception, at.exception
    at.selectbox(key="rv_chapter").set_value(2).run()
    assert not at.exception, at.exception
    at.button(key="rv_accept_2").click().run()
    assert not at.exception, at.exception
    assert st.get_step_state(ws, "ch02.revise_loop")["status"] == st.ST_PENDING  # 答复已提交，等待续跑
    assert (ws.studio / "answers" / "ch02.revise_loop.json").is_file()  # 答复已落盘，等待续跑
