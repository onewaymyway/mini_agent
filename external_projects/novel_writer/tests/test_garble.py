"""乱码检测、乱码守卫（自动重生成 ≤3 次，之后 NeedsUser）、上下文污染检查。桩执行器，不调用真实 LLM。"""

from __future__ import annotations

import os

import pytest

from fakes import FakeInvoker, make_blueprint
from novel_writer import chapter_ops as C
from novel_writer import config, tool_api
from novel_writer.contamination import scan_contamination
from novel_writer.fsutil import atomic_write_json
from novel_writer.garble import is_garbled
from novel_writer.runner import NeedsUser
from novel_writer.workspace import Workspace

NORMAL = ("清晨的街道上，行人渐渐多了起来。林小雨站在医院门口，看着手里的工作证发呆。"
          "她已经在这家医院工作了三年，每天早上七点准时到岗。\n"
          "老先生姓陈，穿着一件洗得发白的中山装，手里拄着一根竹杖。他看见她，露出一个温和的笑容。\n")


def mojibake(text: str) -> str:
    """复现真实故障：UTF-8 字节被按 GBK 解码，再以 UTF-8 保存。"""
    return text.encode("utf-8").decode("gbk", errors="replace")


# ── 检测器 ─────────────────────────────────────────────────────────────────

def test_normal_prose_is_not_garbled():
    assert not is_garbled(NORMAL * 10).garbled


def test_mojibake_is_detected_by_rare_ratio_and_qmarks():
    report = is_garbled(mojibake(NORMAL * 10))
    assert report.garbled
    assert report.rare_ratio > 0.30


def test_replacement_and_private_use_chars_are_hard_signals():
    assert is_garbled(NORMAL + "\ufffd").garbled
    assert is_garbled(NORMAL + "\ue160").garbled


def test_few_rare_chars_in_normal_text_do_not_trigger():
    text = NORMAL * 10 + "他用的是一个罕见的字：龘。"
    assert not is_garbled(text).garbled


# ── 乱码守卫 ───────────────────────────────────────────────────────────────

@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("乱码测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    atomic_write_json(ws.path("blueprint"), make_blueprint(3))
    return ws


def write_chapter_file(ws, n, text):
    path = ws.root / "正文" / f"第{n:02d}章 测试.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def guard_step(n):
    return C._chapter_step(n, "garble_guard")


def test_guard_passes_clean_chapter_without_regeneration(book):
    write_chapter_file(book, 1, NORMAL * 5)
    inv = FakeInvoker(book.root)
    result = C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert "未发现乱码" in result["summary"]
    assert not any("write_chapter" in c for c in inv.calls)


def test_guard_regenerates_and_recovers(book):
    path = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    inv = FakeInvoker(book.root)
    result = C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert "通过" in result["summary"]
    assert sum("write_chapter" in c for c in inv.calls) == 1
    assert not is_garbled(path.read_text(encoding="utf-8")).garbled


def test_guard_gives_up_after_three_regenerations_and_asks_user(book):
    write_chapter_file(book, 1, mojibake(NORMAL * 5))
    inv = FakeInvoker(book.root, fail={"ch01.write_chapter"})  # 每次重生成都失败，正文保持乱码
    with pytest.raises(NeedsUser) as exc:
        C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert sum("write_chapter" in c for c in inv.calls) == C.MAX_GARBLE_REGENS
    options = exc.value.items[0]["options"]
    assert options == C.GARBLE_OPTIONS


def test_guard_accept_option_passes_without_regeneration(book):
    atomic_write_json(book.studio / "inbox" / "dummy.json", {})  # 确保目录存在
    write_chapter_file(book, 1, mojibake(NORMAL * 5))
    from novel_writer.runner import _answers_file
    answers = _answers_file(book, guard_step(1).id)
    answers.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(answers, {"answers": {"decision": C.GARBLE_OPTIONS[2]}})
    inv = FakeInvoker(book.root)
    result = C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert "接受" in result["summary"]
    assert not inv.calls


def test_guard_is_wired_before_machine_check(book):
    from novel_writer import steps as S
    steps = {s.id: s for s in S.chapter_steps(1, "测试", total=3, prev_last="x")}
    assert steps["ch01.machine_check"].deps == ("ch01.garble_guard",)
    assert steps["ch01.garble_guard"].deps == ("ch01.write_chapter",)


# ── 上下文污染检查 ─────────────────────────────────────────────────────────

def _touch(path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_contamination_flags_stale_context_and_summary(book):
    base = 1_700_000_000
    write_chapter_file(book, 19, NORMAL * 5)                      # 修复后的第 19 章
    os.utime(book.root / "正文" / "第19章 测试.md", (base + 100, base + 100))
    _touch(book.path("context_pack", n=20), base + 50)            # 早于修复：旧上下文
    _touch(book.path("chapter_summary", nn=20), base + 50)
    _touch(book.path("context_pack", n=21), base + 200)           # 修复后重建：干净
    _touch(book.path("chapter_summary", nn=21), base + 200)
    write_chapter_file(book, 21, NORMAL * 5)
    report = scan_contamination(book, garbled_chapter=19, chapters=[20, 21])
    kinds = {(f["chapter"], f["kind"]) for f in report["findings"]}
    assert (20, "stale_context") in kinds
    assert (20, "summary_before_fix") in kinds
    assert (21, "stale_context") not in kinds
    assert report["contaminated"] is True


def test_contamination_detects_garbled_later_chapter(book):
    write_chapter_file(book, 19, NORMAL * 5)
    write_chapter_file(book, 20, mojibake(NORMAL * 5))
    report = scan_contamination(book, garbled_chapter=19, chapters=[20])
    assert any(f["kind"] == "garbled_text" and f["chapter"] == 20 for f in report["findings"])


def test_contamination_clean_book_reports_no_findings(book):
    base = 1_700_000_000
    write_chapter_file(book, 19, NORMAL * 5)
    os.utime(book.root / "正文" / "第19章 测试.md", (base, base))
    for n in (20, 21):
        write_chapter_file(book, n, NORMAL * 5)
        _touch(book.path("context_pack", n=n), base + 10)
        _touch(book.path("chapter_summary", nn=n), base + 10)
    report = scan_contamination(book, garbled_chapter=19, chapters=[20, 21])
    assert report["findings"] == [] and report["contaminated"] is False


# ── 候选稿 B 乱码 / 择优 ────────────────────────────────────────────────────

from novel_writer import steps as S  # noqa: E402


def test_premium_chain_puts_candidate_guard_before_best_pick():
    steps = {s.id: s for s in S.chapter_steps(1, "测试", total=3, prev_last="x", quality=S.PREMIUM)}
    assert steps["ch01.alt_guard"].deps == ("ch01.alt_write",)
    assert steps["ch01.best_pick_prep"].deps == ("ch01.alt_guard",)
    assert steps["ch01.machine_check"].deps == ("ch01.best_pick_apply",)


def test_alt_guard_regenerates_only_candidate_b(book):
    write_chapter_file(book, 1, NORMAL * 5)
    cand = book.path("candidate_alt", nn=1)
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text(mojibake(NORMAL * 5), encoding="utf-8")
    inv = FakeInvoker(book.root, writers={"alt_write": lambda ws: ws.path("candidate_alt", nn=1).write_text(
        NORMAL * 5, encoding="utf-8")})
    step = S.chapter_steps(1, "测试", total=3, prev_last="x", quality=S.PREMIUM)
    alt_guard = next(s for s in step if s.id == "ch01.alt_guard")
    result = C.h_garble_guard_alt(book, alt_guard, book.settings(), invoker=inv)
    assert "通过" in result["summary"]
    assert sum("alt_write" in c for c in inv.calls) == 1
    assert not any("write_chapter" in c for c in inv.calls)  # 正文 A 未被动
    assert not is_garbled(cand.read_text(encoding="utf-8")).garbled


def _write_pick_report(ws, winner):
    atomic_write_json(ws.path("best_pick_report", nn=1), {"winner": winner, "reason": "测试"})


def test_apply_never_picks_garbled_candidate_b(book):
    a = write_chapter_file(book, 1, NORMAL * 5)
    cand = book.path("candidate_alt", nn=1)
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text(mojibake(NORMAL * 5), encoding="utf-8")
    _write_pick_report(book, "B")
    step = next(s for s in S.chapter_steps(1, "测试", total=3, prev_last="x", quality=S.PREMIUM)
                if s.id == "ch01.best_pick_apply")
    before = a.read_text(encoding="utf-8")
    C.h_best_pick_apply(book, step, book.settings())
    assert a.read_text(encoding="utf-8") == before
    assert not is_garbled(a.read_text(encoding="utf-8")).garbled


def test_apply_replaces_garbled_a_with_clean_b(book):
    a = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    cand = book.path("candidate_alt", nn=1)
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text(NORMAL * 5, encoding="utf-8")
    _write_pick_report(book, "A")
    step = next(s for s in S.chapter_steps(1, "测试", total=3, prev_last="x", quality=S.PREMIUM)
                if s.id == "ch01.best_pick_apply")
    C.h_best_pick_apply(book, step, book.settings())
    assert not is_garbled(a.read_text(encoding="utf-8")).garbled


def test_apply_suspends_when_both_candidates_garbled(book):
    write_chapter_file(book, 1, mojibake(NORMAL * 5))
    cand = book.path("candidate_alt", nn=1)
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text(mojibake(NORMAL * 5), encoding="utf-8")
    _write_pick_report(book, "A")
    step = next(s for s in S.chapter_steps(1, "测试", total=3, prev_last="x", quality=S.PREMIUM)
                if s.id == "ch01.best_pick_apply")
    with pytest.raises(NeedsUser):
        C.h_best_pick_apply(book, step, book.settings())


# ── 重新生成换标题 → 多个同章号文件（回归）────────────────────────────────

class _RetitlingInvoker(FakeInvoker):
    """模拟故障：重新生成时智能体每次都换一个新标题写正文，而不覆盖原文件。"""

    def __init__(self, root, *, clean=True, per_call=1, **kw):
        super().__init__(root, **kw)
        self.clean = clean
        self.per_call = per_call    # 每次调用写出几个不同标题的文件
        self.writes = 0

    def _write_chapter(self, ws, n):
        text = NORMAL * 5 if self.clean else mojibake(NORMAL * 5)
        for _ in range(self.per_call):
            self.writes += 1
            path = ws.root / "正文" / f"第{n:02d}章 新标题{self.writes}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def chapter_files(ws, n):
    return sorted(p.name for p in (ws.root / "正文").glob(f"第{n:02d}章*.md"))


def test_regen_with_new_title_replaces_garbled_file_and_leaves_single_chapter(book):
    path = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    inv = _RetitlingInvoker(book.root, clean=True)
    result = C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert "通过" in result["summary"]
    # 正文目录里同一章只剩原文件名，且内容已是新的正常稿
    assert chapter_files(book, 1) == [path.name]
    assert not is_garbled(path.read_text(encoding="utf-8")).garbled
    assert not list((book.root / "正文").glob("第01章 新标题*.md"))


def test_extra_retitled_files_are_stashed_not_deleted(book):
    path = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    inv = _RetitlingInvoker(book.root, clean=True, per_call=3)
    C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert chapter_files(book, 1) == [path.name]
    drafts = list((book.root / "正文" / "_drafts").glob("第01章*重复残留*.md"))
    assert len(drafts) == 2   # 3 个新文件：1 个覆盖原文件，另外 2 个移入 _drafts


def test_regen_with_new_titles_still_garbled_keeps_single_chapter_file(book):
    path = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    inv = _RetitlingInvoker(book.root, clean=False)
    with pytest.raises(NeedsUser):
        C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert chapter_files(book, 1) == [path.name]


def test_regen_prompt_pins_existing_file(book):
    path = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    inv = FakeInvoker(book.root)
    C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    prompt = inv.prompts["ch01.write_chapter"][0]
    assert "覆盖写入该文件" in prompt and path.name in prompt


def test_existing_duplicates_clean_one_replaces_garbled_without_regen(book):
    bad = write_chapter_file(book, 1, mojibake(NORMAL * 5))
    good = book.root / "正文" / "第01章 另一个标题.md"
    good.write_text(NORMAL * 5, encoding="utf-8")
    os.utime(bad, (1_000_000_000, 1_000_000_000))  # 乱码的是旧文件；好稿较新，chapter_file 会选好稿
    inv = FakeInvoker(book.root)
    result = C.h_garble_guard(book, guard_step(1), book.settings(), invoker=inv)
    assert "未发现乱码" in result["summary"]
    assert not inv.calls
    assert len(chapter_files(book, 1)) == 1


def test_chapter_file_prefers_newest_when_duplicates(book):
    a = write_chapter_file(book, 1, NORMAL)
    b = book.root / "正文" / "第01章 另一个标题.md"
    b.write_text(NORMAL, encoding="utf-8")
    os.utime(a, (1_000_000_000, 1_000_000_000))
    assert C.chapter_file(book, 1) == b
