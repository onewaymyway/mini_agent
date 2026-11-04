"""通用修复：步骤多次自动重跑仍失败 → 把失败原因和输入文件交给 LLM 修复 → 重跑该步骤。"""

import pytest

from novel_writer import config, repair as RP, runner as R, state as st, steps as S, tool_api
from novel_writer import worker as W
from novel_writer.workspace import Workspace


class FakeResult:
    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.timed_out = ok, error, False


class FakeBudget:
    def __init__(self):
        self.recorded = []

    def record(self, step):
        self.recorded.append(step.id)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("通用修复", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    w = Workspace(r["data"]["workspace"])
    (w.root / "数据").mkdir(exist_ok=True)
    (w.root / "数据" / "a.md").write_text("坏的内容\n", encoding="utf-8")
    return w


def _step(inputs=("数据/a.md",), step_id="t.generic"):
    return S.StepDef(id=step_id, title="通用步骤", stage="p0", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="x", inputs=tuple(inputs))


# ── 范围 ──

def test_generic_scope_is_the_text_inputs(ws):
    req = RP.generic_request(ws, _step())
    assert req["kind"] == RP.GENERIC_KIND and req["files"] == ["数据/a.md"] and req["scope"] is None


def test_no_editable_inputs_means_no_generic_request(ws):
    (ws.root / "数据" / "x.bin").write_bytes(b"\x00\x01")
    assert RP.generic_request(ws, _step(inputs=("数据/x.bin",))) is None       # 非文本
    assert RP.generic_request(ws, _step(inputs=("数据/不存在.md",))) is None  # 不存在
    assert RP.generic_request(ws, _step(inputs=())) is None                   # 没有输入


# ── LLM 修复与范围控制 ──

def test_generic_repair_changes_only_the_inputs_and_counts_attempt(ws):
    step = _step()
    req = RP.generic_request(ws, step)
    req["problems"] = ["ValueError: 内容无法解析"]

    def invoker(**kw):
        (ws.root / "数据" / "a.md").write_text("修好的内容\n", encoding="utf-8")
        return FakeResult()

    out = RP.repair_attempt(ws, step, invoker=invoker, settings={}, request=req)
    assert out["changed"] == ["数据/a.md"] and out["rolled_back"] is False
    assert (ws.root / "数据" / "a.md").read_text(encoding="utf-8") == "修好的内容\n"
    assert st.get_step_state(ws, step.id)["repair_attempts"] == 1


def test_out_of_scope_edit_is_reverted_and_in_scope_edit_kept(ws):
    step = _step()
    req = RP.generic_request(ws, step)
    req["problems"] = ["失败"]

    def invoker(**kw):
        (ws.root / "数据" / "a.md").write_text("改了\n", encoding="utf-8")     # 范围内：保留
        (ws.root / "别处.md").write_text("越界新建\n", encoding="utf-8")      # 范围外：撤销
        return FakeResult()

    out = RP.repair_attempt(ws, step, invoker=invoker, settings={}, request=req)
    assert out["rolled_back"] is True
    assert (ws.root / "数据" / "a.md").read_text(encoding="utf-8") == "改了\n"
    assert not (ws.root / "别处.md").exists()


def test_generic_prompt_carries_failure_reason_not_parse_wording(ws):
    step = _step()
    req = RP.generic_request(ws, step)
    prompt = RP._build_prompt(step.id, req, {"数据/a.md": "坏的内容"}, ["ValueError: 关键字段缺失"])
    assert "ValueError: 关键字段缺失" in prompt and "失败原因" in prompt
    assert "无法被程序读取" not in prompt
    assert "如果失败原因与这些文件无关" in prompt                    # 允许 LLM 判定问题不在文件里


# ── 工作流接入 ──

def _patch_rerun(monkeypatch, status=st.ST_PASSED):
    calls = []

    def fake_run_step(ws, step, **kw):
        calls.append(step.id)
        return status

    monkeypatch.setattr(R, "run_step", fake_run_step)
    return calls


def test_worker_repairs_then_reruns_the_failed_step(ws, monkeypatch):
    step = _step()
    st.update_step(ws, step.id, status=st.ST_FAILED, error="ValueError: 解析失败")
    rerun = _patch_rerun(monkeypatch)
    prompts = []

    def invoker(**kw):
        prompts.append(kw["prompt"])
        (ws.root / "数据" / "a.md").write_text("好的\n", encoding="utf-8")
        return FakeResult()

    status = W._generic_repair_and_rerun(ws, step, invoker=invoker, settings={}, handlers=None, budget=FakeBudget())
    assert status == st.ST_PASSED and rerun == [step.id]
    assert "ValueError: 解析失败" in prompts[0]                     # 失败原因交给了 LLM
    assert (ws.root / "数据" / "a.md").read_text(encoding="utf-8") == "好的\n"


@pytest.mark.parametrize("case", ["no_invoker", "disabled", "exhausted", "no_retry", "no_inputs"])
def test_generic_repair_is_skipped_when_not_applicable(ws, monkeypatch, case):
    step = _step(inputs=() if case == "no_inputs" else ("数据/a.md",))
    st.update_step(ws, step.id, status=st.ST_FAILED, error="失败")
    rerun = _patch_rerun(monkeypatch)
    settings = {"auto_repair": False} if case == "disabled" else {}
    if case == "exhausted":
        st.update_step(ws, step.id, repair_attempts=RP.MAX_REPAIR_ATTEMPTS)
    if case == "no_retry":
        st.update_step(ws, step.id, no_retry=True)
    invoker = None if case == "no_invoker" else (lambda **kw: FakeResult())
    out = W._generic_repair_and_rerun(ws, step, invoker=invoker, settings=settings, handlers=None,
                                      budget=FakeBudget())
    assert out is None and rerun == []
