"""M6 测试：结局设计与终审接入、质量档位步骤差异、best-of-N 择优、txt/docx/epub 导出。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, approve_waiting, make_blueprint, statuses
from novel_writer import config, gates as G, tool_api
from novel_writer import pipeline as P
from novel_writer import exporter
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer.chapter_ops import h_best_pick
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def new_book(tmp_path, *, total=4, gear="G3", quality="standard", blueprint=None):
    r = tool_api.create_novel("终稿测试", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, quality=quality, total_chapters_planned=total)
    assert r["ok"], r
    ws = Workspace(r["data"]["workspace"])
    atomic_write_json(ws.path("blueprint"), blueprint or make_blueprint(total))
    return ws


def ids(ws):
    return [s.id for s in P.build_plan(ws)]


def by_id(ws):
    return {s.id: s for s in P.build_plan(ws)}


# ── 一、结局设计与终审 ─────────────────────────────────────────────────────


def test_ending_design_placed_before_final_act(tmp_path):
    bp = make_blueprint(6)
    bp["structure"]["acts"] = [{"act": 1, "chapter_range": [1, 3]}, {"act": 2, "chapter_range": [4, 6]}]
    ws = new_book(tmp_path, total=6, blueprint=bp)
    order = ids(ws)
    assert order.index("act_review:1") < order.index("design_ending") < order.index("ch04.word_quota")


def test_ending_design_falls_back_to_last_chapter_without_acts(tmp_path):
    ws = new_book(tmp_path, total=5)
    order = ids(ws)
    assert order.index("ch04.sync_plot") < order.index("design_ending") < order.index("ch05.word_quota")


def test_final_review_is_last_and_reviewed_in_g3(tmp_path):
    ws = new_book(tmp_path, total=3, gear="G3")
    order = ids(ws)
    assert order[-1] == "final_review"
    assert G.resolve_gate(by_id(ws)["final_review"], ws.settings()).gate == "review"
    assert G.resolve_gate(by_id(ws)["design_ending"], ws.settings()).gate == "review"


# ── 二、质量档位步骤差异 ───────────────────────────────────────────────────


def test_draft_quality_merges_plot_sync_and_drops_reader_sim(tmp_path):
    ws = new_book(tmp_path, total=6, quality="draft")
    plan = by_id(ws)
    assert "ch01.sync_plot" not in plan
    assert any("plot_tracker" in o for o in plan["ch01.sync"].outputs)
    assert not any(i.endswith(".reader_sim") for i in plan)
    assert "ch01.alt_write" not in plan


def test_standard_quality_keeps_current_behaviour(tmp_path):
    ws = new_book(tmp_path, total=6, quality="standard")
    plan = by_id(ws)
    assert "ch01.sync_plot" in plan and "ch05.reader_sim" in plan
    assert "ch03.reader_sim" not in plan and "ch01.alt_write" not in plan
    assert "patrol:10" not in plan


def test_premium_quality_adds_best_of_n_reader_sim_every_three_and_patrol(tmp_path):
    ws = new_book(tmp_path, total=10, quality="premium")
    plan = by_id(ws)
    assert plan["ch01.machine_check"].deps == ("ch01.best_pick",)
    assert plan["ch01.best_pick"].deps == ("ch01.alt_write",)
    assert plan["ch01.alt_write"].deps == ("ch01.write_chapter",)
    assert "ch03.reader_sim" in plan and "ch06.reader_sim" in plan and "ch05.reader_sim" not in plan
    assert "patrol:10" in plan and plan["patrol:10"].skill == "revision-editor"


# ── 三、best-of-N 择优 ─────────────────────────────────────────────────────


def _pick(ws, n=1):
    step = by_id(ws)[f"ch{n:02d}.best_pick"]
    return h_best_pick(ws, step, ws.settings())


def test_best_pick_keeps_a_on_tie_and_prefers_b_when_a_is_worse(tmp_path):
    ws = new_book(tmp_path, total=2, quality="premium", gear="G0")
    good = "他站在门口，雨一直下。\n\n她没有回头，只是把伞收起。\n"
    chapter = ws.chapter_path(1, "测试", total_chapters=2)
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text("# 第1章\n\n" + good, encoding="utf-8")
    cand = ws.path("candidate_alt", nn=1)
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text("# 第1章\n\n" + good, encoding="utf-8")
    assert _pick(ws)["summary"].endswith("A稿")
    assert "他站在门口" in chapter.read_text(encoding="utf-8")

    # 只有正文 A 含禁用词（硬问题），候选稿 B 干净 -> 采用 B
    atomic_write_json(ws.path("studio_settings"), {**ws.settings(), "banned_words": ["黑夜"]})
    chapter.write_text("# 第1章\n\n" + good + "黑夜里有人敲门。\n", encoding="utf-8")
    cand.write_text("# 第1章\n\n" + good, encoding="utf-8")
    assert _pick(ws)["summary"].endswith("B稿")
    assert "黑夜" not in chapter.read_text(encoding="utf-8")
    assert ws.path("best_pick_report", nn=1).is_file()


def test_best_pick_without_candidate_is_noop(tmp_path):
    ws = new_book(tmp_path, total=2, quality="premium", gear="G0")
    chapter = ws.chapter_path(1, "测试", total_chapters=2)
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text("# 第1章\n\n他推门而入。\n", encoding="utf-8")
    assert "保留正文" in _pick(ws)["summary"]


def test_premium_chapter_end_to_end_keeps_clean_a(tmp_path):
    # 桩执行器写出的候选稿只有几个字，字数偏离很大 -> 择优应保留 A（正文）
    ws = new_book(tmp_path, total=3, quality="premium", gear="G3")
    inv = FakeInvoker(ws.root)
    for _ in range(200):
        if statuses(ws).get("ch01.best_pick") == st.ST_PASSED:
            break
        run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
        approve_waiting(ws)
    assert statuses(ws)["ch01.best_pick"] == st.ST_PASSED
    assert "ch01.alt_write" in inv.calls
    import json
    report = json.loads(ws.path("best_pick_report", nn=1).read_text(encoding="utf-8"))
    assert report["winner"] == "A"


# ── 四、导出 ───────────────────────────────────────────────────────────────


def _book_with_text(tmp_path):
    ws = new_book(tmp_path, total=2, gear="G0")
    for n, title in ((1, "开端"), (2, "转折")):
        p = ws.chapter_path(n, title, total_chapters=2)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# 第{n}章 {title}\n\n段落甲 & <标签>\n\n段落乙\n", encoding="utf-8")
    return ws


def test_export_txt_and_md(tmp_path):
    ws = _book_with_text(tmp_path)
    txt = tool_api.export(str(ws.root), "txt")["data"]
    body = (ws.root / txt["file"]).read_text(encoding="utf-8")
    assert "【第1章 开端】" in body and "段落甲 & <标签>" in body
    assert tool_api.export(str(ws.root))["data"]["file"].endswith(".md")


def test_export_docx_is_valid_package_with_text(tmp_path):
    ws = _book_with_text(tmp_path)
    out = tool_api.export(str(ws.root), "docx")["data"]
    with zipfile.ZipFile(ws.root / out["file"]) as z:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
        root = ET.fromstring(z.read("word/document.xml"))
    texts = [t.text for t in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
    assert texts[0] == "终稿测试"
    assert any("段落甲 & <标签>" == t for t in texts)
    assert out["format"] == "docx" and out["chapters"] == 2


def test_export_epub_structure(tmp_path):
    ws = _book_with_text(tmp_path)
    out = tool_api.export(str(ws.root), "epub")["data"]
    path = ws.root / out["file"]
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert names[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        for name in names:
            if name.endswith((".xml", ".opf", ".xhtml")):
                ET.fromstring(z.read(name))  # 每个 XML 片段都必须是良构的
        assert "OEBPS/chapter001.xhtml" in names and "OEBPS/chapter002.xhtml" in names
        assert "段落甲 &amp; &lt;标签&gt;".encode() in z.read("OEBPS/chapter001.xhtml")


def test_export_rejects_unknown_format_and_empty_book(tmp_path):
    ws = new_book(tmp_path, total=2, gear="G0")
    assert not tool_api.export(str(ws.root), "pdf")["ok"]
    res = tool_api.export(str(ws.root), "epub")
    assert not res["ok"] and "没有任何正文" in res["error"]["message"]


def test_exporter_handles_empty_parts_gracefully(tmp_path):
    exporter.write_epub(tmp_path / "x.epub", "空", ["# 只有标题"])
    with zipfile.ZipFile(tmp_path / "x.epub") as z:
        ET.fromstring(z.read("OEBPS/chapter001.xhtml"))


def test_ui_reading_export_epub(tmp_path):
    from streamlit.testing.v1 import AppTest

    ws = _book_with_text(tmp_path)
    at = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    at.selectbox(key="rd_fmt").set_value("epub").run()
    assert not at.exception, at.exception
    at.button(key="rd_export").click().run()
    assert not at.exception, at.exception
    assert list((ws.root / "导出").glob("*.epub")), "应生成 epub 文件"
