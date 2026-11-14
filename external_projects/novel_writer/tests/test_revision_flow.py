"""完稿后修改流程（revision_flow）的测试。全部使用桩执行器，不调用真实 LLM。"""

import json
import re
from pathlib import Path

import pytest

from novel_writer import config, state as st, tool_api
from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import derived, executor, feedback
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


TEXTS = {
    1: "李凌云握着玉佩走进山门。玉佩在掌心发烫，他想起师父的话。",
    2: "夜里，李凌云把玉佩放在桌上，反复端详。窗外有风。",
    3: "清晨的雾很大。李凌云没有提起玉佩，只是低头赶路。",
    4: "王道长召见李凌云，问起那枚玉佩的来历。",
}


def _novel(tmp_path, total=4, passed=True):
    r = tool_api.create_novel("完稿修改测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=total)
    ws = Workspace(r["data"]["workspace"])
    for n in range(1, total + 1):
        p = ws.chapter_path(n, f"标题{n}", total)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# 第{n}章 标题{n}\n\n" + TEXTS.get(n, "平常的一天。") + "\n", encoding="utf-8")
    if passed:
        st.update_step(ws, "final_review", status=st.ST_PASSED)
    return ws


def _chapter_text(ws, vid, n):
    ch = next(c for c in Workspace(V.version_dir(ws, vid)).list_chapters() if c.number == n)
    return ch.path.read_text(encoding="utf-8")


# ─────────────────────────── 桩执行器 ───────────────────────────

_ORIG = re.compile(r"【原文路径】(.+?)（")
_PREV = re.compile(r"【前一章最新文本】(.+?)（")


class RewriteInvoker:
    """按提示里的原文路径读取章节，把 before 替换为 after 写到 export_dir。
    fail_attempts：前 N 次返回未改的原文（用于测试校验与重试）；always_bad：一直不合格。"""

    def __init__(self, before, after, *, fail_attempts=0, always_bad=False):
        self.before, self.after = before, after
        self.fail_attempts, self.always_bad = fail_attempts, always_bad
        self.calls = []
        self.prev_seen = []

    def __call__(self, *, skill, prompt, export_dir, step_id):
        assert skill == "revision-editor"
        src = Path(_ORIG.search(prompt).group(1).strip())
        self.calls.append(src.name)
        m = _PREV.search(prompt)
        self.prev_seen.append(Path(m.group(1).strip()).exists() if m else None)
        text = src.read_text(encoding="utf-8")
        if self.always_bad or len(self.calls) <= self.fail_attempts:
            new = text
        else:
            new = text.replace(self.before, self.after)
        Path(export_dir).mkdir(parents=True, exist_ok=True)
        (Path(export_dir) / src.name).write_text(new, encoding="utf-8")


class TriageInvoker:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def __call__(self, *, skill, prompt, export_dir, step_id):
        out = re.search(r"写入文件：\s*(\S+)", prompt).group(1)
        payload = self.payloads.pop(0)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(payload, encoding="utf-8")


def _setting(**over):
    raw = {"kind": "setting", "title": "玉佩换成银钥匙", "before": "玉佩", "after": "银钥匙",
           "reason": "作者改设定", "terms": ["玉佩"], "mode": "retcon"}
    raw.update(over)
    return raw


def _ready_preview(ws, raw=None):
    c = CH.save_change(ws, raw or _setting())
    pv = CH.build_preview(ws, "v001" if V.is_baseline_ready(ws) else _baseline(ws), [c["id"]])
    CH.confirm_preview(ws, pv["id"])
    return pv


def _baseline(ws):
    V.init_baseline(ws)
    return "v001"


# ─────────────────────────── P0 版本基线 ───────────────────────────

def test_baseline_requires_final_review(tmp_path):
    ws = _novel(tmp_path, passed=False)
    with pytest.raises(FlowError, match="终审"):
        V.init_baseline(ws)


def test_baseline_is_readonly_and_verifiable(tmp_path):
    ws = _novel(tmp_path)
    entry = V.init_baseline(ws)
    assert entry["id"] == "v001" and entry["kind"] == "baseline"
    assert V.verify(ws, "v001") == []
    with pytest.raises(FlowError, match="已存在"):
        V.init_baseline(ws)
    target = next(V.version_dir(ws, "v001").rglob("*.md"))
    assert not (target.stat().st_mode & 0o200)  # 只读
    target.chmod(0o644)
    target.write_text("被改动", encoding="utf-8")
    assert any("内容已变化" in p for p in V.verify(ws, "v001"))


def test_original_workspace_is_not_touched_by_revision(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    before = {p: p.read_text(encoding="utf-8") for p in ws.root.joinpath("正文").iterdir() if p.is_file()}
    pv = _ready_preview(ws)
    executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    after = {p: p.read_text(encoding="utf-8") for p in ws.root.joinpath("正文").iterdir() if p.is_file()}
    assert before == after


# ─────────────────────────── 配置 ───────────────────────────

def test_settings_defaults_validation_and_persistence(tmp_path):
    ws = _novel(tmp_path)
    assert CFG.load(ws)["max_chapters_per_run"] == 20
    assert CFG.update(ws, {"max_chapters_per_run": 5})["max_chapters_per_run"] == 5
    assert CFG.load(ws)["max_chapters_per_run"] == 5
    with pytest.raises(FlowError, match="超出范围"):
        CFG.update(ws, {"max_llm_calls_per_run": 0})
    with pytest.raises(FlowError, match="未知"):
        CFG.update(ws, {"nope": 1})
    with pytest.raises(FlowError, match="超出范围"):
        CFG.update(ws, {"length_ratio_min": 1.2})
    with pytest.raises(FlowError, match="必须小于"):
        CFG.update(ws, {"length_ratio_min": 1.0, "length_ratio_max": 1.0})


# ─────────────────────────── P1 变更与预览 ───────────────────────────

def test_change_validation():
    with pytest.raises(FlowError, match="mode"):
        CH.normalize(_setting(mode=None))
    with pytest.raises(FlowError, match="effective_from"):
        CH.normalize(_setting(mode="from_chapter"))
    with pytest.raises(FlowError, match="terms"):
        CH.normalize(_setting(terms=[]))
    with pytest.raises(FlowError, match="instruction"):
        CH.normalize({"kind": "chapter", "title": "x", "chapters": [1]})
    ok = CH.normalize(_setting(mode="from_chapter", effective_from=3))
    assert ok["effective_from"] == 3


def test_affected_retcon_vs_from_chapter():
    chapters = [{"number": n, "title": f"t{n}", "text": TEXTS[n]} for n in TEXTS]
    retcon = CH.normalize(_setting())
    got = CH.affected_for_change(retcon, chapters)["chapters"]
    assert sorted(got) == [1, 2, 3, 4]  # 四章都提到“玉佩”
    late = CH.normalize(_setting(mode="from_chapter", effective_from=2, after_terms=["银钥匙"]))
    res = CH.affected_for_change(late, chapters)
    assert sorted(res["chapters"]) == [2, 3, 4]
    early = CH.normalize(_setting(mode="from_chapter", effective_from=3, after_terms=["玉佩"]))
    warn = CH.affected_for_change(early, chapters)["warnings"]
    assert any("第1章" in w or "第2章" in w for w in warn)


def test_preview_must_be_confirmed_before_run(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    c = CH.save_change(ws, _setting())
    pv = CH.build_preview(ws, "v001", [c["id"]])
    assert pv["confirmed"] is False and len(pv["chapters"]) == 4
    with pytest.raises(FlowError, match="未确认"):
        executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    CH.confirm_preview(ws, pv["id"])
    assert CH.load_change(ws, c["id"])["status"] == "confirmed"


# ─────────────────────────── P2 执行 ───────────────────────────

def test_retcon_run_rewrites_all_affected_in_new_version(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws)
    inv = RewriteInvoker("玉佩", "银钥匙")
    run = executor.start(ws, pv["id"], inv)
    assert run["status"] == "done" and run["target_version"] == "v002"
    assert run["done"] == [1, 2, 3, 4]
    assert inv.calls == ["第01章 标题1.md", "第02章 标题2.md", "第03章 标题3.md", "第04章 标题4.md"]
    assert inv.prev_seen == [None, True, True, True]  # 滚动：后续章节能看到前一章
    assert "银钥匙" in _chapter_text(ws, "v002", 1) and "玉佩" not in _chapter_text(ws, "v002", 1)
    assert "玉佩" in _chapter_text(ws, "v001", 1)  # 原稿不变
    assert "玉佩" not in _chapter_text(ws, "v002", 4) and "银钥匙" in _chapter_text(ws, "v002", 4)
    assert V.verify(ws, "v002") == []
    assert V.load_index(ws)["head"] == "v002"
    assert CH.load_change(ws, pv["change_ids"][0])["status"] == "applied"
    assert any(m["item"] == "人物索引" for m in run["derived_marks"])


def test_from_chapter_leaves_earlier_chapters_alone(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws, _setting(mode="from_chapter", effective_from=2, after_terms=["银钥匙"]))
    run = executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    assert run["status"] == "done" and run["done"] == [2, 3, 4]
    assert _chapter_text(ws, "v002", 1) == _chapter_text(ws, "v001", 1)
    assert "银钥匙" in _chapter_text(ws, "v002", 2)


def test_failed_check_retries_with_feedback_then_succeeds(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws, _setting(terms=["玉佩"]))
    inv = RewriteInvoker("玉佩", "银钥匙", fail_attempts=1)
    run = executor.start(ws, pv["id"], inv)
    assert run["status"] == "done"
    assert run["llm_calls"] == 5  # 第1章第1次失败重试一次（共2次），第2–4章各一次
    assert any("仍含旧设定" in line for line in run["log"]) or any("未通过" in line for line in run["log"])


def test_stop_on_failure_keeps_head_and_discard_works(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws)
    run = executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙", always_bad=True))
    assert run["status"] == "failed" and run["failed"]["chapter"] == 1
    assert V.load_index(ws)["head"] == "v001"  # 未定稿，不成为 head
    V.discard(ws, run["target_version"])
    assert [v["id"] for v in V.list_versions(ws)] == ["v001"]
    with pytest.raises(FlowError, match="基线"):
        V.discard(ws, "v001")


def test_limit_pauses_and_resume_completes(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    CFG.update(ws, {"max_chapters_per_run": 1})
    pv = _ready_preview(ws)
    inv = RewriteInvoker("玉佩", "银钥匙")
    run = executor.start(ws, pv["id"], inv)
    assert run["status"] == "paused_limit" and run["done"] == [1]
    run = executor.resume(ws, run["id"], inv)
    assert run["status"] == "paused_limit" and run["done"] == [1, 2]
    run = executor.resume(ws, run["id"], inv)
    assert run["status"] == "paused_limit" and run["done"] == [1, 2, 3]
    run = executor.resume(ws, run["id"], inv)
    assert run["status"] == "done" and run["done"] == [1, 2, 3, 4]
    with pytest.raises(FlowError, match="只有"):
        executor.resume(ws, run["id"], inv)


def test_second_version_derives_from_head_and_undo(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws)
    executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    c2 = CH.save_change(ws, _setting(title="改成玉镯", before="银钥匙", after="玉镯", terms=["银钥匙"]))
    pv2 = CH.build_preview(ws, "v002", [c2["id"]])
    CH.confirm_preview(ws, pv2["id"])
    run2 = executor.start(ws, pv2["id"], RewriteInvoker("银钥匙", "玉镯"))
    assert run2["target_version"] == "v003" and run2["base_version"] == "v002"
    assert V.undo_head(ws) == "v002"
    assert [v["id"] for v in V.list_versions(ws)] == ["v001", "v002"]


# ─────────────────────────── P3 分诊 ───────────────────────────

def test_triage_setting_and_accept_requires_mode(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    payload = json.dumps({"kind": "setting", "reason": "涉及信物设定",
                          "change": {"title": "信物", "before": "玉佩", "after": "银钥匙",
                                     "reason": "作者意见", "terms": ["玉佩"], "after_terms": ["银钥匙"]}},
                         ensure_ascii=False)
    rec = feedback.triage(ws, "v001", 2, "第二章的玉佩应该是银钥匙", TriageInvoker([payload]))
    assert rec["status"] == "pending" and rec["result"]["kind"] == "setting"
    with pytest.raises(FlowError, match="mode"):
        feedback.accept(ws, rec["id"])
    created = feedback.accept(ws, rec["id"], mode="from_chapter", effective_from=2)
    assert created["kind"] == "setting" and created["effective_from"] == 2
    assert feedback.load_triage(ws, rec["id"])["status"] == "accepted"


def test_triage_retries_on_bad_json_and_chapter_kind_becomes_local_change(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    good = json.dumps({"kind": "chapter", "reason": "节奏拖沓", "instruction": "压缩开头的描写"}, ensure_ascii=False)
    rec = feedback.triage(ws, "v001", 1, "开头太慢", TriageInvoker(["不是json", good]))
    assert rec["status"] == "pending" and rec["result"]["kind"] == "chapter"
    created = feedback.accept(ws, rec["id"])
    assert created["kind"] == "chapter" and created["chapters"] == [1]
    pv = CH.build_preview(ws, "v001", [created["id"]])
    assert [row["number"] for row in pv["chapters"]] == [1]


def test_triage_failure_is_recorded_not_accepted(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    rec = feedback.triage(ws, "v001", 1, "x", TriageInvoker(["坏", "还是坏"]))
    assert rec["status"] == "failed"
    with pytest.raises(FlowError, match="不能采纳"):
        feedback.accept(ws, rec["id"], mode="retcon")


# ─────────────────────────── P4 差异与导出 ───────────────────────────

def test_diff_versions_reports_changed_chapters(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws)
    executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    rows = {r["path"]: r for r in derived.diff_versions(ws, "v001", "v002")}
    changed = [r for r in rows.values() if r["status"] == "changed" and "正文" in r["path"]]
    assert len(changed) == 4
    same = [r for r in rows.values() if r["status"] == "same" and "正文" in r["path"]]
    assert same == []  # 所有含“玉佩”的章节都已改写
    assert any(r["path"].startswith("修订记录/") and r["status"] == "added" for r in rows.values())


def test_export_version_writes_inside_version(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    result = derived.export_version(ws, "v001")
    assert result is not None


# ─────────────────────────── P1+ LLM 语义判定与排除 ───────────────────────────

class ReviewInvoker:
    """按章节号给出判定：verdicts = {章号: True/False}；缺省视为涉及。bad：返回不合法 JSON 的章号集合。"""

    def __init__(self, verdicts=None, bad=()):
        self.verdicts = verdicts or {}
        self.bad = set(bad)
        self.calls = 0

    def __call__(self, *, skill, prompt, export_dir, step_id):
        self.calls += 1
        out = re.search(r"写入文件：\s*(\S+)", prompt).group(1)
        num = int(re.search(r"第(\d+)章", prompt).group(1))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        if num in self.bad:
            Path(out).write_text("乱写", encoding="utf-8")
        else:
            Path(out).write_text(json.dumps({"affected": self.verdicts.get(num, True), "reason": "测试"},
                                            ensure_ascii=False), encoding="utf-8")


def test_semantic_review_excludes_unaffected_by_default(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    c = CH.save_change(ws, _setting())
    pv = CH.build_preview(ws, "v001", [c["id"]], invoker=ReviewInvoker({3: False}))
    rows = {r["number"]: r for r in pv["chapters"]}
    assert rows[3]["excluded"] is True and rows[3]["review"]["affected"] is False
    assert rows[1]["excluded"] is False
    assert pv["review"]["calls"] == 4 and pv["review"]["done"] is True
    CH.confirm_preview(ws, pv["id"])
    run = executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    assert run["queue"] == [1, 2, 4] and run["done"] == [1, 2, 4]
    assert "玉佩" in _chapter_text(ws, "v002", 3)  # 被排除的章节保持原文


def test_author_can_include_excluded_chapter_before_confirm(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    c = CH.save_change(ws, _setting())
    pv = CH.build_preview(ws, "v001", [c["id"]], invoker=ReviewInvoker({3: False}))
    pv = CH.set_excluded(ws, pv["id"], 3, False)
    assert pv["estimated_llm_calls"] == 8  # 4 章改写 + 4 次语义判定
    pv = CH.set_excluded(ws, pv["id"], 2, True)
    assert [r["number"] for r in pv["chapters"] if not r["excluded"]] == [1, 3, 4]
    CH.confirm_preview(ws, pv["id"])
    with pytest.raises(FlowError, match="已确认"):
        CH.set_excluded(ws, pv["id"], 1, True)


def test_review_failure_keeps_chapter_and_warns(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    c = CH.save_change(ws, _setting())
    pv = CH.build_preview(ws, "v001", [c["id"]], invoker=ReviewInvoker(bad={2}))
    row = next(r for r in pv["chapters"] if r["number"] == 2)
    assert row["review"] is None and row["excluded"] is False
    assert any("第2章语义判定失败" in w for w in pv["warnings"])
    assert pv["review"]["failed"] == 1


def test_preview_without_invoker_keeps_all_chapters(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    c = CH.save_change(ws, _setting())
    pv = CH.build_preview(ws, "v001", [c["id"]])
    assert pv["review"]["done"] is False
    assert all(not r["excluded"] for r in pv["chapters"])


# ─────────────────────────── 候选范围 all 与运行中停止 ───────────────────────────

def test_review_scope_all_finds_indirect_reference(tmp_path):
    ws = _novel(tmp_path, total=5)
    # 第5章只用指代，不含定位词（须在建立基线前写入）
    ch5 = next(c for c in ws.list_chapters() if c.number == 5)
    ch5.path.write_text("# 第5章\n\n他把那枚信物收进怀里，什么也没说。\n", encoding="utf-8")
    _baseline(ws)
    c = CH.save_change(ws, _setting())
    hits_only = CH.build_preview(ws, "v001", [c["id"]], invoker=ReviewInvoker())
    assert 5 not in [r["number"] for r in hits_only["chapters"]]
    CFG.update(ws, {"review_scope": "all"})
    inv = ReviewInvoker({5: True})
    pv = CH.build_preview(ws, "v001", [c["id"]], invoker=inv)
    rows = {r["number"]: r for r in pv["chapters"]}
    assert 5 in rows and rows[5]["hits"] == [] and rows[5]["excluded"] is False
    assert pv["review"]["calls"] == 5


def test_review_scope_all_respects_from_chapter(tmp_path):
    ws = _novel(tmp_path, total=5)
    _baseline(ws)
    CFG.update(ws, {"review_scope": "all"})
    c = CH.save_change(ws, _setting(mode="from_chapter", effective_from=4, after_terms=["银钥匙"]))
    pv = CH.build_preview(ws, "v001", [c["id"]], invoker=ReviewInvoker())
    assert sorted(r["number"] for r in pv["chapters"]) == [4, 5]


def test_review_scope_rejects_unknown_value(tmp_path):
    ws = _novel(tmp_path)
    with pytest.raises(FlowError, match="hits 或 all"):
        CFG.update(ws, {"review_scope": "some"})


class StopAfterFirst(RewriteInvoker):
    def __call__(self, **kw):
        super().__call__(**kw)
        if len(self.calls) == 1:
            executor.request_stop(self.ws, executor.list_runs(self.ws)[-1]["id"])


def test_stop_request_pauses_between_chapters_and_resume_completes(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws)
    inv = StopAfterFirst("玉佩", "银钥匙")
    inv.ws = ws
    run = executor.start(ws, pv["id"], inv)
    assert run["status"] == "stopped" and run["done"] == [1]
    run = executor.resume(ws, run["id"], RewriteInvoker("玉佩", "银钥匙"))
    assert run["status"] == "done" and run["done"] == [1, 2, 3, 4]


def test_stop_only_for_running(tmp_path):
    ws = _novel(tmp_path)
    _baseline(ws)
    pv = _ready_preview(ws)
    run = executor.start(ws, pv["id"], RewriteInvoker("玉佩", "银钥匙"))
    with pytest.raises(FlowError, match="运行中"):
        executor.request_stop(ws, run["id"])
