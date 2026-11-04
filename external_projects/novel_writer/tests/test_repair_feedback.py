"""数据修复：范围外改动只撤销范围外的文件、范围内的正确修复保留、每次重试带上一次的结果、默认次数 6。"""

import pytest

from novel_writer import config, repair as RP, state as st, steps as S, tool_api
from novel_writer.workspace import Workspace


class FakeResult:
    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.timed_out = ok, error, False


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("修复反馈", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


@pytest.fixture
def toy(ws):
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

    RP.VALIDATORS["toy2"] = validate
    yield base
    RP.VALIDATORS.pop("toy2", None)


def _step():
    return S.StepDef(id="t.fb", title="反馈测试", stage="p0", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="x")


def _request():
    return {"validator": "toy2", "files": ["数据/a.md", "数据/b.md"], "problems": [], "scope": "数据"}


def test_default_attempt_limit_is_six():
    assert RP.max_attempts({}) == 6


def test_out_of_scope_new_file_is_removed_and_in_scope_fix_kept(ws, toy):
    def invoker(**kw):
        (toy / "a.md").write_text("ok\n", encoding="utf-8")
        (ws.root / "多余.md").write_text("新建\n", encoding="utf-8")
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=_request())
    assert out["fixed"] and out["rolled_back"]
    assert not (ws.root / "多余.md").exists()
    assert (toy / "a.md").read_text(encoding="utf-8") == "ok\n"


def test_in_scope_edit_that_adds_problems_is_fully_reverted(ws, toy):
    original = (toy / "a.md").read_text(encoding="utf-8")

    def invoker(**kw):
        (toy / "a.md").write_text("BAD x\nBAD y\nBAD z\n", encoding="utf-8")   # 问题反而变多
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=_request())
    assert out["rolled_back"] and not out["fixed"]
    assert (toy / "a.md").read_text(encoding="utf-8") == original


def test_out_of_scope_change_is_recorded_as_feedback_for_next_attempt(ws, toy):
    def bad_invoker(**kw):
        (ws.root / "别处.md").write_text("越界\n", encoding="utf-8")      # 越界；问题本身没有被修好
        return FakeResult()

    RP.repair_attempt(ws, _step(), invoker=bad_invoker, settings={}, request=_request())
    feedback = st.get_step_state(ws, "t.fb")["repair_feedback"]
    assert "别处.md" in feedback and "已撤销" in feedback

    prompts = []

    def second(**kw):
        prompts.append(kw["prompt"])
        return FakeResult()

    RP.repair_attempt(ws, _step(), invoker=second, settings={}, request=_request())
    assert "上一次修复的结果" in prompts[0] and "别处.md" in prompts[0]


def test_feedback_is_cleared_once_the_problem_is_fixed(ws, toy):
    st.update_step(ws, "t.fb", repair_feedback="旧的反馈")

    def invoker(**kw):
        (toy / "a.md").write_text("ok\n", encoding="utf-8")
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=_request())
    assert out["fixed"]
    assert not st.get_step_state(ws, "t.fb").get("repair_feedback")


def test_generic_repair_is_not_affected_by_feedback_logic(ws):
    (ws.root / "数据").mkdir(exist_ok=True)
    (ws.root / "数据" / "in.md").write_text("内容\n", encoding="utf-8")
    step = S.StepDef(id="t.gen", title="通用", stage="p0", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="x", inputs=("数据/in.md",))
    req = RP.generic_request(ws, step)
    req["problems"] = ["ValueError: 失败"]
    seen = []

    def invoker(**kw):
        seen.append(kw["prompt"])
        return FakeResult()

    out = RP.repair_attempt(ws, step, invoker=invoker, settings={}, request=req)
    assert out["fixed"] is False and "ValueError: 失败" in seen[0]     # 通用修复的结论交给重跑，不因此回滚


def test_windows_style_paths_do_not_make_in_scope_files_out_of_scope(ws, toy):
    """Windows 上请求里的路径可能是 数据\\a.md 这种写法：范围内的正确修复不能被误判为范围外而撤销。"""
    req = {"validator": "toy2", "files": ["数据\\a.md", "数据\\b.md"], "problems": [], "scope": "数据"}

    def invoker(**kw):
        (toy / "a.md").write_text("ok\n", encoding="utf-8")                 # 范围内的正确修复
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=req)
    assert out["fixed"] and out["rolled_back"] is False
    assert (toy / "a.md").read_text(encoding="utf-8") == "ok\n"


def test_norm_unifies_separators():
    assert RP._norm("大纲规划\\蓝图\\第031章.md") == "大纲规划/蓝图/第031章.md"
    assert RP._norm("大纲规划/蓝图/第031章.md") == "大纲规划/蓝图/第031章.md"


def test_new_run_resets_exhausted_repair_budget_for_failed_steps(ws):
    from novel_writer import worker as W
    st.update_step(ws, "t.failed", status=st.ST_FAILED, repair_attempts=RP.MAX_REPAIR_ATTEMPTS)
    st.update_step(ws, "t.passed", status=st.ST_PASSED, repair_attempts=2)
    assert RP.exhausted(ws, "t.failed", {})
    W._reset_repair_budgets(ws)
    assert not RP.exhausted(ws, "t.failed", {})                        # 新一轮可以再修复
    assert int(st.get_step_state(ws, "t.passed").get("repair_attempts") or 0) == 2   # 其它步骤不动
    assert "数据修复次数重新计算" in ws.path("studio_log").read_text(encoding="utf-8")
