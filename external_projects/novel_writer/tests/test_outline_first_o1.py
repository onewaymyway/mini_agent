"""大纲先行 O1：简述的确定性校验、JSON 解析、按幕生成（桩执行器）与计划顺序。"""

import json
from pathlib import Path

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import brief as BR
from novel_writer import config, pipeline, state as st, tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def good(n, **over):
    b = {"chapter": n, "title": f"章{n}", "narrative": "甲" * 350,
         "key": {"must_events": ["李凌云踏入山门"], "must_states": [{"who": "李凌云", "change": "得知真相"}],
                 "must_foreshadow": [], "must_hook": "门外有人"}}
    b.update(over)
    return b


def ok_errs(b, names=("李凌云",), fs=("F1",)):
    return BR.validate_brief(b, chapter=b.get("chapter", 1), title="章", names=list(names), foreshadow=list(fs))


# ─────────────────────────── 校验 ───────────────────────────

def test_valid_brief_passes():
    assert ok_errs(good(1)) == []


@pytest.mark.parametrize("n_chars,expect", [(299, True), (300, False), (600, False), (601, True)])
def test_narrative_length_bounds(n_chars, expect):
    errs = ok_errs(good(1, narrative="甲" * n_chars))
    assert any("叙述简述" in e for e in errs) == expect


def test_key_content_must_be_present():
    b = good(1)
    b["key"]["must_events"] = []
    b["key"]["must_hook"] = " "
    b["key"]["must_states"] = []
    errs = ok_errs(b)
    assert any("must_events" in e for e in errs)
    assert any("must_hook" in e for e in errs)
    assert any("must_states" in e for e in errs)


def test_unknown_character_and_foreshadow_are_rejected():
    b = good(1)
    b["key"]["must_states"] = [{"who": "陌生人", "change": "x"}]
    b["key"]["must_foreshadow"] = ["F9", "bad"]
    errs = ok_errs(b, names=("李凌云",), fs=("F1",))
    assert any("陌生人" in e for e in errs)
    assert any("F9" in e and "不在伏笔账本" in e for e in errs)
    assert any("bad" in e and "格式" in e for e in errs)


def test_empty_name_and_ledger_skip_their_checks():
    b = good(1)
    b["key"]["must_states"] = [{"who": "谁都行", "change": "x"}]
    b["key"]["must_foreshadow"] = ["F42"]
    assert ok_errs(b, names=(), fs=()) == []


def test_generated_brief_must_not_carry_deviation_records():
    b = good(1, deviations=[{"where": "x", "original": "a", "change": "b", "reason": "c"}])
    assert any("偏离记录" in e for e in ok_errs(b))


# ─────────────────────────── 解析整幕输出 ───────────────────────────

def _act_json(numbers, **over):
    return json.dumps({"chapters": [good(n, **over) for n in numbers]}, ensure_ascii=False)


def test_parse_act_output_requires_exact_chapter_set():
    titles = {1: "章1", 2: "章2", 3: "章3"}
    items, errs = BR.parse_act_output(_act_json([1, 2]), [1, 2, 3], titles, ["李凌云"], ["F1"], 1)
    assert items == [] and any("缺少章节" in e for e in errs)
    items, errs = BR.parse_act_output(_act_json([1, 2, 3, 4]), [1, 2, 3], titles, ["李凌云"], ["F1"], 1)
    assert items == [] and any("不属于" in e for e in errs)
    items, errs = BR.parse_act_output(_act_json([1, 2, 3]), [1, 2, 3], titles, ["李凌云"], ["F1"], 1)
    assert errs == [] and [b["chapter"] for b in items] == [1, 2, 3] and all(b["act"] == 1 for b in items)


def test_parse_act_output_rejects_bad_json():
    items, errs = BR.parse_act_output("not json", [1], {1: "a"}, [], [], 1)
    assert items == [] and "合法 JSON" in errs[0]


# ─────────────────────────── 按幕生成（桩执行器） ───────────────────────────

class ActInvoker:
    """按提示里的输出路径写 JSON。plan：每次调用返回的内容列表（用完后重复最后一个）。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.prompts = []

    def __call__(self, *, skill, prompt, export_dir, step_id):
        import re
        self.calls += 1
        self.prompts.append(prompt)
        out = re.search(r"写入文件：(\S+)", prompt).group(1)
        text = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")


BLUEPRINT = {"chapters": [{"id": n, "title": f"第{n}章 标题{n}", "act": 1, "function": "推进"} for n in (1, 2, 3)]}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    r = tool_api.create_novel("简述测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3,
                              writing_mode="outline_first")
    w = Workspace(r["data"]["workspace"])
    monkeypatch.setattr(BS, "load_for_ws", lambda _ws, strict=False: BLUEPRINT)
    monkeypatch.setattr(BR, "known_names", lambda _ws: ["李凌云"])
    monkeypatch.setattr(BR, "known_foreshadow", lambda _ws: ["F1"])
    return w


def test_draft_generates_and_saves_all_chapters_of_the_act(ws):
    inv = ActInvoker([_act_json([1, 2, 3])])
    step = BR.brief_draft_step(1, 1, 3, ())
    out = BR.h_brief_draft(ws, step, ws.settings(), invoker=inv)
    assert out["chapters"] == [1, 2, 3] and inv.calls == 1
    for n in (1, 2, 3):
        b = BR.load_brief(ws, n)
        assert b and b["chapter"] == n and b["act"] == 1
        assert BR.brief_md_path(ws, n).exists()
        assert "## 关键内容" in BR.brief_md_path(ws, n).read_text(encoding="utf-8")
    index = (BR.brief_dir(ws) / "_index.md").read_text(encoding="utf-8")
    assert "第3章" in index


def test_invalid_output_is_retried_once_with_errors(ws):
    bad = json.loads(_act_json([1, 2, 3]))
    bad["chapters"][0]["narrative"] = "太短"
    inv = ActInvoker([json.dumps(bad, ensure_ascii=False), _act_json([1, 2, 3])])
    BR.h_brief_draft(ws, BR.brief_draft_step(1, 1, 3, ()), ws.settings(), invoker=inv)
    assert inv.calls == 2
    assert "未通过校验" not in inv.prompts[0]
    assert "叙述简述 2 字" in inv.prompts[1]  # 第二次提示带上了上一轮的错误


def test_two_failures_raise_and_write_nothing(ws):
    inv = ActInvoker(["坏", "还是坏"])
    with pytest.raises(RuntimeError, match="未通过校验"):
        BR.h_brief_draft(ws, BR.brief_draft_step(1, 1, 3, ()), ws.settings(), invoker=inv)
    assert inv.calls == 2
    assert not BR.brief_json_path(ws, 1).exists()


def test_blueprint_missing_entries_is_a_clear_error(ws, monkeypatch):
    monkeypatch.setattr(BS, "load_for_ws", lambda _ws, strict=False: {"chapters": [{"id": 1, "title": "a"}]})
    with pytest.raises(RuntimeError, match="缺少章节条目"):
        BR.h_brief_draft(ws, BR.brief_draft_step(1, 1, 3, ()), ws.settings(), invoker=ActInvoker([""]))


def test_no_invoker_is_an_error(ws):
    with pytest.raises(RuntimeError, match="invoker"):
        BR.h_brief_draft(ws, BR.brief_draft_step(1, 1, 3, ()), ws.settings(), invoker=None)


def test_continuity_uses_previous_chapter_hook(ws):
    prev = good(1)
    prev["key"]["must_hook"] = "玉佩突然碎裂"
    BR.save_brief(ws, {**prev, "act": 0, "deviations": []})
    inv = ActInvoker([_act_json([2, 3])])
    BR.h_brief_draft(ws, BR.brief_draft_step(1, 2, 3, ()), ws.settings(), invoker=inv)
    assert "玉佩突然碎裂" in inv.prompts[0]


# ─────────────────────────── 计划顺序 ───────────────────────────

def test_outline_plan_order_briefs_before_any_chapter(ws):
    plan = pipeline.build_plan(ws)
    ids = [s.id for s in plan]
    drafts = [i for i, s in enumerate(plan) if s.id.startswith("brief_draft:")]
    review = ids.index(BR.BRIEF_REVIEW_ID)
    first_ch = next(i for i, s in enumerate(plan) if s.chapter)
    assert drafts and max(drafts) < review < first_ch
    # 每幕的简述步骤串成一条依赖链
    for a, b in zip(drafts, drafts[1:]):
        assert plan[b].deps == (plan[a].id,)
    assert all(s.handler == BR.BRIEF_DRAFT_ID for s in (plan[i] for i in drafts))


def test_direct_plan_has_no_brief_steps_at_all(tmp_path):
    r = tool_api.create_novel("直写", novels_dir=str(tmp_path / "n2"), index_file=str(tmp_path / "s2.json"),
                              gear="G3", total_chapters_planned=3)
    w = Workspace(r["data"]["workspace"])
    assert not [s for s in pipeline.build_plan(w) if s.id.startswith("brief_")]


def test_missing_act_entries_are_completed_before_briefs(tmp_path, monkeypatch):
    r = tool_api.create_novel("分幕测试", novels_dir=str(tmp_path / "n3"), index_file=str(tmp_path / "s3.json"),
                              gear="G3", total_chapters_planned=6, writing_mode="outline_first")
    w = Workspace(r["data"]["workspace"])
    bp = {"structure": {"total_chapters": 6, "acts": [
              {"act": 1, "name": "起", "chapter_range": [1, 3], "word_budget": 9000},
              {"act": 2, "name": "承", "chapter_range": [4, 6], "word_budget": 9000}]},
          "chapters": [{"id": n, "title": f"章{n}", "act": 1} for n in (1, 2, 3)]}
    monkeypatch.setattr(BS, "load_for_ws", lambda _ws, strict=False: bp)
    plan = pipeline.build_plan(w)
    ids = [s.id for s in plan]
    assert "plan_act:2" in ids and "blueprint_extend:2" in ids
    assert ids.index("blueprint_extend:2") < ids.index("brief_draft:2") < ids.index(BR.BRIEF_REVIEW_ID)
    assert ids.index("brief_draft:1") < ids.index("brief_draft:2")
    # 第 2 幕补全条目只规划一次（不在章节循环里重复出现）
    assert ids.count("plan_act:2") == 1
