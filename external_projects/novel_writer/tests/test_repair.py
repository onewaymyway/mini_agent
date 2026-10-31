"""数据文件 LLM 自动修复：修好、修坏回滚、范围外改动还原、次数用尽、真实人物档案链路。"""

from pathlib import Path

import pytest

from novel_writer import chapter_ops as C
from novel_writer import character_store as CS
from novel_writer import config, repair as RP, runner as R, state as st, steps as S, tool_api
from novel_writer.kvfile import encode as kv_encode
from novel_writer.workspace import Workspace


class FakeResult:
    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.timed_out = ok, error, False


def _step(step_id="t.step"):
    return S.StepDef(id=step_id, title="测试步骤", stage="p0", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="read_chars")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("修复测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


# ── 通用机制（自定义校验器）──────────────────────────────────────────────────

@pytest.fixture
def toy(ws):
    """一个只看「文件里有没有 BAD 行」的玩具校验器，便于精确控制修复结果。"""
    base = ws.root / "数据"
    base.mkdir(parents=True, exist_ok=True)
    (base / "a.md").write_text("ok\nBAD one\n", encoding="utf-8")
    (base / "b.md").write_text("ok\n", encoding="utf-8")

    def validate(_ws):
        out = []
        for name in ("a.md", "b.md"):
            text = (base / name).read_text(encoding="utf-8")
            out += [f"{name} 第 {i} 行是 BAD" for i, line in enumerate(text.splitlines(), 1) if line.startswith("BAD")]
        return out

    RP.VALIDATORS["toy"] = validate
    yield base
    RP.VALIDATORS.pop("toy", None)


def _request(base_rel="数据"):
    return {"validator": "toy", "files": [f"{base_rel}/a.md", f"{base_rel}/b.md"],
            "problems": [], "scope": base_rel}


def test_successful_repair_fixes_and_counts(ws, toy):
    def invoker(**kw):
        (toy / "a.md").write_text("ok\nfixed\n", encoding="utf-8")
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=_request())
    assert out["fixed"] and out["problems"] == [] and out["changed"] == ["数据/a.md"]
    assert st.get_step_state(ws, "t.step")["repair_attempts"] == 1


def test_worse_result_is_rolled_back(ws, toy):
    before = (toy / "a.md").read_bytes()

    def invoker(**kw):
        (toy / "a.md").write_text("BAD x\nBAD y\n", encoding="utf-8")   # 把问题改得更多
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=_request())
    assert not out["fixed"] and out["rolled_back"]
    assert (toy / "a.md").read_bytes() == before
    assert len(out["problems"]) == 1


def test_out_of_scope_edit_is_rolled_back_even_if_problem_fixed(ws, toy):
    (ws.root / "其他").mkdir()
    other = ws.root / "其他" / "keep.md"
    other.write_text("原样\n", encoding="utf-8")

    def invoker(**kw):
        (toy / "a.md").write_text("ok\n", encoding="utf-8")       # 修好了
        other.write_text("被改了\n", encoding="utf-8")            # 但动了范围外的文件
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=_request())
    assert out["rolled_back"] and not out["fixed"]
    assert other.read_text(encoding="utf-8") == "原样\n"
    assert "BAD" in (toy / "a.md").read_text(encoding="utf-8")


def test_already_passing_validator_does_not_call_llm_but_still_counts(ws, toy):
    (toy / "a.md").write_text("ok\n", encoding="utf-8")
    calls = []
    out = RP.repair_attempt(ws, _step(), invoker=lambda **kw: calls.append(kw) or FakeResult(),
                            settings={}, request=_request())
    assert out["fixed"] and calls == []
    assert st.get_step_state(ws, "t.step")["repair_attempts"] == 1   # 计数，避免无限循环


def test_exhausted_after_max_attempts(ws, toy):
    settings = {"repair_max_attempts": 2}
    step = _step()
    assert not RP.exhausted(ws, step.id, settings)
    for _ in range(2):
        RP.repair_attempt(ws, step, invoker=lambda **kw: FakeResult(), settings=settings, request=_request())
    assert RP.exhausted(ws, step.id, settings)


# ── 真实链路：人物档案 ─────────────────────────────────────────────────────

def _char_record(cid="char_021", name="林远"):
    return {"id": cid, "name": name, "role": "主角",
            "state_log": [{"chapter": 30, "status": "首次激活", "key_change": "林远在服务器机房"}]}


def test_characters_failure_is_repairable_and_retry_passes(ws):
    base = ws.path("characters_dir")
    base.mkdir(parents=True, exist_ok=True)
    CS.write_store(base, ws.path("characters"), {"char_021": _char_record()})   # 建立档案库（有索引文件）
    good = kv_encode(_char_record(), CS.FIELD_ORDER)
    # 模拟 LLM 生成的坏文件：state_log 的字符串里混入了未转义的英文双引号
    broken = good.replace("首次激活", "首次\"激活\"")
    f = base / "char_021.md"
    f.write_text(broken, encoding="utf-8")

    def read_chars(ws_, step, settings, invoker=None):
        return {"summary": str(len(C._characters_or_raise(ws_) or {}))}

    handlers = {"read_chars": read_chars}
    step = _step()
    status = R.run_step(ws, step, invoker=None, settings={}, handlers=handlers)
    assert status == st.ST_FAILED
    req = st.get_step_state(ws, step.id)["repair"]
    assert req["validator"] == "characters" and req["files"][0].endswith("char_021.md")
    assert any("state_log" in p for p in req["problems"])

    def invoker(**kw):   # LLM 修复：只改这个文件，把引号换成「」
        f.write_text(good.replace("首次激活", "首次「激活」"), encoding="utf-8")
        return FakeResult()

    request = dict(req, scope=req["scope"])
    out = RP.repair_attempt(ws, step, invoker=invoker, settings={}, request=request)
    assert out["fixed"], out
    assert R.run_step(ws, step, invoker=None, settings={}, handlers=handlers) == st.ST_PASSED
    after = st.get_step_state(ws, step.id)
    assert after.get("repair") is None and after.get("repair_attempts") == 0


def test_worker_branch_repairs_then_reruns_step(ws):
    """worker 的重跑分支：步骤失败 → _repair_and_rerun 调用 LLM 修复 → 重跑通过。"""
    from novel_writer import worker as W

    base = ws.path("characters_dir")
    base.mkdir(parents=True, exist_ok=True)
    CS.write_store(base, ws.path("characters"), {"char_021": _char_record()})
    good = kv_encode(_char_record(), CS.FIELD_ORDER)
    f = base / "char_021.md"
    f.write_text(good.replace("首次激活", "首次\"激活\""), encoding="utf-8")

    def read_chars(ws_, step, settings, invoker=None):
        return {"summary": str(len(C._characters_or_raise(ws_) or {}))}

    handlers = {"read_chars": read_chars}
    step = _step()
    assert R.run_step(ws, step, invoker=None, settings={}, handlers=handlers) == st.ST_FAILED

    class Budget:
        def record(self, _):
            pass

    calls = []

    def invoker(**kw):
        calls.append(kw["skill"])
        f.write_text(good.replace("首次激活", "首次「激活」"), encoding="utf-8")
        return FakeResult()

    status = W._repair_and_rerun(ws, step, invoker=invoker, settings={}, handlers=handlers, budget=Budget())
    assert status == st.ST_PASSED
    assert calls == [RP.REPAIR_SKILL]


def test_worker_branch_does_nothing_without_request_or_when_exhausted(ws):
    from novel_writer import worker as W

    class Budget:
        def record(self, _):
            pass

    step = _step("t.none")
    assert W._repair_and_rerun(ws, step, invoker=lambda **kw: FakeResult(), settings={},
                               handlers={}, budget=Budget()) is None          # 没有修复请求
    st.update_step(ws, step.id, repair={"validator": "toy", "files": [], "problems": [], "scope": None},
                   repair_attempts=3)
    assert W._repair_and_rerun(ws, step, invoker=lambda **kw: FakeResult(), settings={},
                               handlers={}, budget=Budget()) is None          # 次数已用尽
