"""整理对话（手动）：范围与检查点、按模块整理、冲突标记与对话处理、Markdown 纪要。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import distill as DS
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "岛")
    w.create("岛")
    C.put_module(w, "premise", "一座会移动的岛")
    return w


def _talk(ws, rounds=3):
    """用桩 agent 产生若干轮对话（作者一句 + 助手一句）。"""
    for i in range(rounds):
        CH.send(ws, f"作者第{i}句", "自由", runner=lambda p, s, i=i: (f"助手第{i}句", None))


def _gen(payload):
    def gen(prompt):
        gen.prompt = prompt
        return json.dumps(payload, ensure_ascii=False)
    return gen


BASE = {"candidates": [
    {"module": "cast", "op": "add", "value": {"name": "岛主", "role": "守岛人"}, "reason": "对话中确定", "source_turns": [0, 1]},
    {"module": "premise", "op": "set", "value": "一座沉睡的岛", "reason": "与现有命题不同", "source_turns": [2]},
    {"module": "world", "op": "add", "target": "rules", "value": {"text": "潮涨时岛会移动", "cost": "失去一个港口"}, "reason": "", "source_turns": [4]},
], "minutes": "## 讨论要点\n- 岛会移动 [0]\n## 已形成的决定\n- 有守岛人 [1]\n## 被否定的方向\n- 无\n## 仍待定的问题\n- 无"}


# ── 范围与检查点 ──

def test_pending_range_then_checkpoint_advances(ws):
    _talk(ws, 3)                                             # 6 条记录
    out = DS.run(ws, generator=_gen(BASE))
    assert out["range"] == [0, 6]
    kinds = {(p["module"], p["op"]) for p in out["proposals"]}
    assert ("cast", "add") in kinds and ("premise", "set") in kinds
    assert all(p["batch"] == out["batch"] and p["status"] == "pending" for p in out["proposals"])
    assert [p["pid"] for p in out["proposals"]]                            # 进入待确认列表
    with pytest.raises(BrainstormError):                                    # 没有新对话
        DS.run(ws, generator=_gen(BASE))
    _talk(ws, 1)
    out2 = DS.run(ws, generator=_gen({"candidates": [], "minutes": "空"}))
    assert out2["range"] == [6, 8]


def test_recent_scope_takes_last_rounds(ws):
    _talk(ws, 4)
    out = DS.run(ws, scope="recent", recent_rounds=1, generator=_gen({"candidates": [], "minutes": ""}))
    assert out["range"] == [6, 8]
    assert DS._checkpoint(ws) == 8


def test_checkpoint_not_advanced_on_failure(ws):
    _talk(ws, 2)
    with pytest.raises(BrainstormError):
        DS.run(ws, generator=lambda p: "没有 JSON")
    assert DS._checkpoint(ws) == 0
    assert DS.select_range(ws)[0] == 0


def test_no_proposals_applied_to_cards(ws):
    _talk(ws, 2)
    before = C.all_modules(ws)
    DS.run(ws, generator=_gen(BASE))
    assert C.all_modules(ws) == before


# ── 按模块整理 ──

def test_module_scope_drops_other_modules(ws):
    _talk(ws, 3)
    out = DS.run(ws, modules=["cast"], generator=_gen(BASE))
    assert {p["module"] for p in out["proposals"]} == {"cast"}
    assert any("模块不在所选范围内：premise" in s for s in out["skipped"])
    prompt = DS.build_prompt(ws, CH.history(ws), 0, ["cast"])
    assert "只提取以下模块：角色" in prompt and "核心命题（模块名" not in prompt


def test_unknown_module_rejected(ws):
    _talk(ws, 1)
    with pytest.raises(BrainstormError):
        DS.run(ws, modules=["magic"], generator=_gen(BASE))


# ── 冲突 ──

def test_conflicts_are_flagged_not_applied(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    premise = next(p for p in out["proposals"] if p["module"] == "premise")
    assert premise["conflict"] and "一座会移动的岛" in premise["conflict"]
    cast = next(p for p in out["proposals"] if p["module"] == "cast")
    assert cast["conflict"] == ""                                          # 新增角色不冲突
    assert C.get_module(ws, "premise") == "一座会移动的岛"


def test_duplicate_idea_is_skipped(ws):
    from novel_writer.brainstorm import ideas as I
    I.add_idea(ws, "潮水带回名字")
    _talk(ws, 1)
    payload = {"candidates": [{"module": "ideas", "op": "add", "value": {"text": "潮水带回名字"}, "source_turns": [0]}],
               "minutes": ""}
    out = DS.run(ws, modules=["ideas"], generator=_gen(payload))
    assert out["proposals"] == [] and any("已存在" in s for s in out["skipped"])


def test_conflict_prompt_carries_both_sides(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    pid = next(p["pid"] for p in out["proposals"] if p["conflict"])
    text = DS.conflict_prompt(ws, pid)
    assert "一座会移动的岛" in text and "一座沉睡的岛" in text and "proposals" in text


def test_conflict_prompt_refuses_non_conflict(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    pid = next(p["pid"] for p in out["proposals"] if not p["conflict"])
    with pytest.raises(BrainstormError):
        DS.conflict_prompt(ws, pid)


def test_conflict_resolved_through_dialogue(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    pid = next(p["pid"] for p in out["proposals"] if p["conflict"])
    msg = DS.conflict_prompt(ws, pid)

    class Resolver:
        def __call__(self, prompt, sid):
            assert "关于整理出的一条冲突" in prompt
            return ('我倾向保留原命题，但可以吸收“沉睡”。\n```proposals\n'
                    '[{"module": "premise", "op": "set", "value": "一座会移动、夜里沉睡的岛", "reason": "合并两者"}]\n```', None)
    CH.send(ws, msg, "自由", runner=Resolver())
    new = [p for p in P.list_items(ws, "pending") if p["module"] == "premise" and p["reason"] == "合并两者"]
    assert len(new) == 1
    P.accept(ws, new[0]["pid"])
    assert C.get_module(ws, "premise") == "一座会移动、夜里沉睡的岛"


# ── 纪要 ──

def test_minutes_written_listed_and_readable(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    assert out["minutes"].startswith("output/纪要/")
    listed = DS.list_minutes(ws)
    assert out["minutes"] in listed
    body = DS.read_minutes(ws, out["minutes"])
    assert body.startswith("# 构思纪要") and "## 讨论要点" in body and "条与现有内容冲突" in body


def test_read_minutes_rejects_outside_paths(ws):
    with pytest.raises(BrainstormError):
        DS.read_minutes(ws, "../meta.json")
    with pytest.raises(BrainstormError):
        DS.read_minutes(ws, "output/纪要/不存在.md")


# ── 接口与页面 ──

def test_api_distill_flow(tmp_path, monkeypatch):
    root = BA.create_project("湾", base=str(tmp_path))["data"]["root"]
    w = BrainstormWorkspace(root)
    _talk(w, 2)
    r = BA.distill_run(root, modules=["cast", "premise"], generator=_gen(BASE))
    assert r["ok"] and r["data"]["minutes"]
    assert BA.distill_minutes(root)["data"]
    assert BA.distill_pending_range(root)["data"] == [4, 4]                  # 检查点已推进到末尾


def test_page_shows_distill_box(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("湾边", base=str(tmp_path))["data"]["root"]
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    assert any("整理对话" in e.label for e in at.expander)


# ── 错误结构：界面的 show_error 必须能读取（回归：曾因 error 为字符串而崩溃） ──

def test_api_errors_follow_project_shape(tmp_path):
    root = BA.create_project("湾口", base=str(tmp_path))["data"]["root"]
    res = BA.distill_run(root, modules=["magic"], generator=_gen(BASE))
    assert res["ok"] is False
    assert isinstance(res["error"], dict) and res["error"]["message"]
    assert res["error_type"] == "BrainstormError"


def test_show_error_accepts_both_shapes(monkeypatch):
    from ui import components

    shown = []
    monkeypatch.setattr(components.st, "error", lambda m: shown.append(m))
    components.show_error({"ok": False, "error": {"type": "X", "message": "甲"}})
    components.show_error({"ok": False, "error": "乙"})
    components.show_error({"ok": False})
    assert shown == ["甲", "乙", "未知错误"]


# ── 整理过程的实时日志 ──

import time  # noqa: E402


def _wait_run(ws, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st_ = DS.run_state(ws, run_id)
        if st_["status"] != "running":
            return st_
        time.sleep(0.02)
    raise AssertionError("整理任务未在时限内结束")


def test_run_emits_step_by_step_log(ws):
    _talk(ws, 3)
    lines = []
    DS.run(ws, generator=_gen(BASE), log=lambda k, t: lines.append((k, t)))
    texts = [t for _, t in lines]
    assert texts[0].startswith("模块：")
    assert any(t.startswith("对话范围：第 0 至 5") for t in texts)
    assert any(t.startswith("提示词：") for t in texts)
    assert any("开始调用模型" in t for t in texts)
    assert any(t.startswith("解析得到 3 条候选") for t in texts)
    assert any("与现有内容冲突" in t for t in texts)                      # 冲突的候选有说明
    assert any(t.startswith("纪要已写入") for t in texts)
    assert texts[-1].startswith("完成：待确认")
    assert any(k == "warning" for k, _ in lines) and any(k == "success" for k, _ in lines)


def test_background_run_records_agent_output_and_result(ws):
    from mini_agent.ui import renderer as RD

    _talk(ws, 2)

    def agent_like(prompt):
        RD.R.print_tool_call("read_file", {"path": "working/premise.md"})
        return json.dumps(BASE, ensure_ascii=False)
    run_id = DS.start_run(ws, generator=agent_like)
    st_ = _wait_run(ws, run_id)
    assert st_["status"] == "done"
    kinds = [e["kind"] for e in st_["events"]]
    assert "tool_call" in kinds                                             # 模型调用过程也记录了
    assert st_["events"][-1]["kind"] == "success"
    assert st_["result"]["proposals"] == 3 and st_["result"]["conflicts"] >= 1


def test_background_run_failure_is_logged(ws):
    _talk(ws, 2)
    run_id = DS.start_run(ws, generator=lambda p: "没有 JSON")
    st_ = _wait_run(ws, run_id)
    assert st_["status"] == "error" and st_["events"][-1]["kind"] == "error"
    assert DS._checkpoint(ws) == 0                                          # 失败不推进检查点


def test_unknown_run_id_is_an_error(ws):
    with pytest.raises(BrainstormError):
        DS.run_state(ws, "d_missing")


def test_api_distill_start_and_state(tmp_path):
    root = BA.create_project("海峡", base=str(tmp_path))["data"]["root"]
    _talk(BrainstormWorkspace(root), 2)
    run_id = BA.distill_start(root, modules=["cast"], generator=_gen(BASE))["data"]["run"]
    st_ = _wait_run(BrainstormWorkspace(root), run_id)
    assert st_["status"] == "done"
    assert BA.distill_state(root, run_id)["ok"]


def test_helper_generator_calls_llm_helper_directly(monkeypatch):
    """整理默认走 LLMHelper.ask（单轮、无工具），而不是 Agent。"""
    import sys
    import types

    from novel_writer import config
    from novel_writer.brainstorm import spark

    seen = {}

    class FakeHelper:
        @classmethod
        def from_config(cls, cfg):
            return cls()

        def ask(self, prompt, **kw):
            seen.update(prompt=prompt, **kw)
            return '{"candidates": [], "minutes": "x"}'

    fake = types.ModuleType("mini_agent.llm.service")
    fake.LLMHelper = FakeHelper
    monkeypatch.setitem(sys.modules, "mini_agent.llm.service", fake)
    monkeypatch.setattr(config, "load_llm_cfg", lambda: object())
    monkeypatch.setattr("novel_writer.runner._ensure_framework_on_path", lambda: None)
    out = spark.helper_generator("提示词", system="S", timeout=10)
    assert out.startswith("{") and seen["prompt"] == "提示词" and seen["system"] == "S"
