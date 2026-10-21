"""巡检整改项：提取 → 分流 → 采纳 → 注入（上下文包 / 风格提示 / 机器校验 / 收件箱），默认全关。"""

from __future__ import annotations

import json

import pytest

from novel_writer import chapter_ops as C
from novel_writer import inbox
from novel_writer import patrol_findings as PF
from novel_writer import pipeline as P
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.fsutil import atomic_write_json
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _f(scope="forward", **kw):
    base = {"dimension": "伏笔", "severity": "严重", "title": "伏笔过载", "directive": "本章起优先兑现 F03、F11，不再新增伏笔",
            "scope": scope}
    if scope == "style":
        base.update(title="铜钱反应套路化", dimension="人物", avoid_phrases=["铜钱发烫"], directive="换一种铜钱的反应方式")
    if scope == "retro":
        base.update(title="第5章节奏平板", dimension="节奏", chapters=[5], level="L2", directive="压缩第5章前半段铺垫")
    if scope == "blueprint":
        base.update(title="情感峰值分散", dimension="情感", directive="把第31-40章的情感高点集中到两处")
    base.update(kw)
    return base


def _data(*fs):
    return {"findings": list(fs)}


def _apply(ws, n, data, gear="G0"):
    return PF.apply_extract(ws, n, data, gear)


# ── 校验 ───────────────────────────────────────────────────────────────────


def test_valid_findings_pass_and_bad_ones_are_pointed_out():
    assert PF.problems(_data(_f("forward"), _f("style"), _f("retro"), _f("blueprint"))) == []
    assert PF.problems([]) and PF.problems({"findings": "x"})
    assert any("avoid_phrases" in p for p in PF.problems(_data(_f("style", avoid_phrases=[]))))
    assert any("level" in p for p in PF.problems(_data(_f("retro", level="L9"))))
    assert any("chapters" in p for p in PF.problems(_data(_f("retro", chapters=[]))))
    assert any("scope" in p for p in PF.problems(_data({**_f("forward"), "scope": "weird"})))
    assert any("300" in p for p in PF.problems(_data(_f("forward", directive="长" * 301))))


# ── 分流与采纳 ─────────────────────────────────────────────────────────────


def test_default_gear_leaves_everything_proposed(isolated):
    ws = new_book(isolated)
    res = _apply(ws, 10, _data(_f("forward"), _f("style")), gear="G0")
    assert res == {"total": 2, "adopted": 0}
    assert all(f["status"] == PF.S_PROPOSED for f in PF.read_ledger(ws)["findings"])


def test_g3_auto_adopts_forward_and_style_but_never_blueprint_or_retro(isolated):
    ws = new_book(isolated)
    _apply(ws, 10, _data(_f("forward"), _f("style"), _f("blueprint"), _f("retro")), gear="G3")
    by = {f["scope"]: f for f in PF.read_ledger(ws)["findings"]}
    assert by["forward"]["status"] == by["style"]["status"] == PF.S_ADOPTED
    assert by["forward"]["decided_by"] == "auto"
    assert by["blueprint"]["status"] == by["retro"]["status"] == PF.S_PROPOSED


def test_reextract_is_idempotent_and_keeps_author_decisions(isolated):
    ws = new_book(isolated)
    _apply(ws, 10, _data(_f("forward"), _f("retro")))
    PF.set_status(ws, "P10-2", PF.S_DISMISSED)
    _apply(ws, 10, _data(_f("forward"), _f("retro")))   # 同一份结果再提取
    findings = PF.read_ledger(ws)["findings"]
    assert len(findings) == 2
    assert next(f for f in findings if f["scope"] == "retro")["status"] == PF.S_DISMISSED


def test_author_decided_item_survives_when_reextract_drops_it(isolated):
    ws = new_book(isolated)
    _apply(ws, 10, _data(_f("forward"), _f("retro")))
    PF.set_status(ws, "P10-1", PF.S_ADOPTED)
    _apply(ws, 10, _data(_f("retro")))   # 新结果里没有 forward 了
    titles = {f["title"] for f in PF.read_ledger(ws)["findings"]}
    assert "伏笔过载" in titles  # 作者亲自采纳过的不被丢弃


def test_set_status_rejects_unknown(isolated):
    ws = new_book(isolated)
    _apply(ws, 10, _data(_f("forward")))
    r = tool_api.patrol_set_status(str(ws.root), "P99-1", "adopted")
    assert not r["ok"]
    assert not tool_api.patrol_set_status(str(ws.root), "P10-1", "bogus")["ok"]
    assert tool_api.patrol_set_status(str(ws.root), "P10-1", "adopted")["ok"]


# ── 注入 ───────────────────────────────────────────────────────────────────


def _enable(ws, **flags):
    assert tool_api.update_settings(str(ws.root), {"patrol_feedback": flags})["ok"]


def test_forward_directive_only_reaches_later_chapters_and_only_when_enabled(isolated):
    ws = new_book(isolated, total=4)
    _apply(ws, 2, _data(_f("forward")), gear="G3")
    assert "巡检整改指令" not in C.build_context(ws, 3)         # 默认关
    _enable(ws, inject_forward=True)
    assert "优先兑现 F03" in C.build_context(ws, 3)
    assert "巡检整改指令" not in C.build_context(ws, 2)         # 巡检章本身及之前不受影响
    assert "巡检整改指令" not in C.build_context(ws, 1)


def test_proposed_or_dismissed_items_are_not_injected(isolated):
    ws = new_book(isolated, total=4)
    _apply(ws, 2, _data(_f("forward")), gear="G0")
    _enable(ws, inject_forward=True)
    assert "巡检整改指令" not in C.build_context(ws, 3)
    PF.set_status(ws, "P2-1", PF.S_ADOPTED)
    assert "巡检整改指令" in C.build_context(ws, 3)
    PF.set_status(ws, "P2-1", PF.S_DISMISSED)
    assert "巡检整改指令" not in C.build_context(ws, 3)


def test_until_chapter_expires_directive(isolated):
    ws = new_book(isolated, total=6)
    _apply(ws, 2, _data(_f("forward", until_chapter=4)), gear="G3")
    _enable(ws, inject_forward=True)
    assert "巡检整改指令" in C.build_context(ws, 4)
    assert "巡检整改指令" not in C.build_context(ws, 5)


def test_injection_is_capped(isolated):
    ws = new_book(isolated, total=4)
    many = [_f("forward", title=f"问题{i}", directive="指令" * 60) for i in range(12)]
    _apply(ws, 2, _data(*many), gear="G3")
    text = PF.render_directives(PF.active_for_chapter(ws, 3, PF.SCOPE_FORWARD))
    assert len(text) <= PF.MAX_INJECT_CHARS and text.count("\n") + 1 <= PF.MAX_INJECT_ITEMS


def test_style_hint_and_machine_warn_follow_the_switch(isolated):
    ws = new_book(isolated, total=4)
    _apply(ws, 1, _data(_f("style")), gear="G3")
    step = S.StepDef(id="ch02.style_hint", title="t", stage="p2", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="style_hint", chapter=2)
    C.h_style_hint(ws, step, ws.settings())
    assert "铜钱发烫" not in ws.path("style_hint", n=2).read_text(encoding="utf-8")   # 默认关
    _enable(ws, inject_style=True)
    C.h_style_hint(ws, step, ws.settings())
    assert "铜钱发烫" in ws.path("style_hint", n=2).read_text(encoding="utf-8")
    body = ws.path("chapter_text", nn=2, title="测试")
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text("他胸口的铜钱发烫，铜钱发烫。" * 50, encoding="utf-8")
    rep = C.machine_findings(ws, 2, ws.settings())
    assert any("铜钱发烫" in w for w in rep["warn"]) and not any("铜钱发烫" in h for h in rep["hard"])


# ── 步骤接入 ───────────────────────────────────────────────────────────────


def _premium_ids(ws):
    assert tool_api.update_settings(str(ws.root), {"quality": "premium"})["ok"]
    return [s.id for s in P.build_plan(ws)]


def test_plan_unchanged_by_default_and_has_extract_apply_when_enabled(isolated):
    ws = new_book(isolated, total=10)
    ids = _premium_ids(ws)
    assert "patrol:10" in ids and not any(i.startswith("patrol_extract") for i in ids)
    _enable(ws, extract=True)
    plan = P.build_plan(ws)
    ids = [s.id for s in plan]
    i = ids.index("patrol:10")
    assert ids[i:i + 3] == ["patrol:10", "patrol_extract:10", "patrol_apply:10"]
    ext = next(s for s in plan if s.id == "patrol_extract:10")
    assert ext.executor == S.EXECUTOR_SKILL and ext.extra["output_check"] == "patrol_findings"
    assert "不要重读正文" in ext.instructions


def test_apply_handler_reads_sidecar_and_reports(isolated):
    ws = new_book(isolated)
    step = S.patrol_apply_step(10, "patrol_extract:10")
    with pytest.raises(C.ChapterOpError) as e:
        C.h_patrol_apply(ws, step, ws.settings())
    assert "不存在" in str(e.value)
    path = ws.path("patrol_findings", nn=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("```json\n" + json.dumps(_data(_f("forward"), _f("retro")), ensure_ascii=False) + "\n```",
                    encoding="utf-8")
    res = C.h_patrol_apply(ws, step, {**ws.settings(), "gear": "G3"})
    assert "整改项 2 条，自动采纳 1 条" in res["summary"]
    atomic_write_json(path, {"findings": [{"title": "x"}]})
    with pytest.raises(C.ChapterOpError) as e2:
        C.h_patrol_apply(ws, step, ws.settings())
    assert "不合格" in str(e2.value)


def test_output_check_rejects_bad_sidecar(isolated):
    from novel_writer import validators as V
    ws = new_book(isolated)
    step = S.patrol_extract_step(10)
    assert any("不存在" in p for p in V.content_problems(ws, step))
    atomic_write_json(ws.path("patrol_findings", nn=10), {"findings": [{"title": "x"}]})
    assert V.content_problems(ws, step)
    atomic_write_json(ws.path("patrol_findings", nn=10), _data(_f("forward")))
    assert V.content_problems(ws, step) == []


# ── 收件箱 / 设置 ───────────────────────────────────────────────────────────


def test_inbox_shows_pending_items_only_when_enabled(isolated):
    ws = new_book(isolated)
    _apply(ws, 10, _data(_f("retro"), _f("blueprint")))
    assert not any(i["kind"] == inbox.KIND_PATROL for i in inbox.collect(ws)["items"])
    _enable(ws, to_inbox=True)
    items = [i for i in inbox.collect(ws)["items"] if i["kind"] == inbox.KIND_PATROL]
    assert len(items) == 2 and any("建议 L2" in i["message"] for i in items)
    PF.set_status(ws, "P10-1", PF.S_DISMISSED)
    assert len([i for i in inbox.collect(ws)["items"] if i["kind"] == inbox.KIND_PATROL]) == 1


def test_settings_default_off_and_validated(isolated):
    ws = new_book(isolated)
    assert PF.feedback_settings(ws.settings()) == {"extract": False, "inject_forward": False,
                                                    "inject_style": False, "to_inbox": False}
    assert not tool_api.update_settings(str(ws.root), {"patrol_feedback": {"extract": "yes"}})["ok"]
    assert not tool_api.update_settings(str(ws.root), {"patrol_feedback": {"nope": True}})["ok"]
