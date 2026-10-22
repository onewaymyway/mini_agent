"""步骤边界守卫：防止 agent 越过当前步骤（见 novel_writer/step_guard.py）。"""

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import character_store as CS
from novel_writer import config, state as ST, steps as S, tool_api, validators as V
from novel_writer import pipeline as P
from novel_writer import step_guard as SG
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path):
    r = tool_api.create_novel("守卫", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _plan(ws):
    return P.build_plan(ws)


def _find(plan, step_id):
    return next(s for s in plan if s.id == step_id)


def _all_pending(plan):
    return {s.id: ST.ST_PENDING for s in plan}


# ── 1. 自检只允许检查当前步骤 ─────────────────────────────────────────────

def test_check_unrestricted_when_no_step_running(tmp_path):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    statuses = _all_pending(plan)
    later = next(s for s in plan if s.chapter == 2 and s.id.endswith(".write_chapter"))
    assert SG.step_guard_problem(ws, plan, statuses, later.id) is None


def test_check_rejects_other_step_while_bound(tmp_path):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    statuses = _all_pending(plan)
    frontier = P.frontier(plan, statuses, ws.settings())
    SG.bind_current_step(ws, frontier.id, 1)
    try:
        other = plan[-1]
        msg = SG.step_guard_problem(ws, plan, statuses, other.id)
        assert msg and frontier.id in msg
        assert SG.step_guard_problem(ws, plan, statuses, frontier.id) is None
    finally:
        SG.unbind_current_step(ws)
    assert SG.bound_step_id(ws) is None


def test_finished_step_may_be_rechecked(tmp_path):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    statuses = _all_pending(plan)
    first = plan[0]
    statuses[first.id] = next(iter(ST.FINISHED_STATUSES))
    SG.bind_current_step(ws, plan[1].id, 1)
    try:
        assert SG.step_guard_problem(ws, plan, statuses, first.id) is None
    finally:
        SG.unbind_current_step(ws)


# ── 2. 后续章节文件不属于本步骤 ───────────────────────────────────────────

def _concrete(spec: str) -> str:
    """把产出模式变成一个具体文件名（用于在测试里模拟后续章节文件）。"""
    return SG._pattern(spec).replace("*", "样例")


def test_forbidden_includes_later_chapter_outputs_not_shared(tmp_path):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    sync1 = next(s for s in plan if s.chapter == 1 and s.id.endswith(".sync"))
    write2 = next(s for s in plan if s.chapter == 2 and s.id.endswith(".write_chapter"))
    later_file = ws.root / _concrete(write2.outputs[0])
    later_file.parent.mkdir(parents=True, exist_ok=True)
    later_file.write_text("第2章正文", encoding="utf-8")
    forbidden = SG.forbidden_rels(ws, plan, sync1)
    assert later_file.relative_to(ws.root).as_posix() in forbidden
    # 跨章共享的人物目录 glob 不应被当作越界
    shared_char = SG._existing(ws, [SG._pattern(x) for x in sync1.outputs])
    assert not (shared_char & forbidden)


def test_boundary_detects_write_to_later_chapter(tmp_path):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    sync1 = next(s for s in plan if s.chapter == 1 and s.id.endswith(".sync"))
    before = SG.snapshot(ws, SG.forbidden_rels(ws, plan, sync1))
    assert SG.boundary_violations(ws, plan, sync1, before) == []
    write2 = next(s for s in plan if s.chapter == 2 and s.id.endswith(".write_chapter"))
    target = ws.root / _concrete(write2.outputs[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("越界内容", encoding="utf-8")
    assert target.relative_to(ws.root).as_posix() in SG.boundary_violations(ws, plan, sync1, before)


def test_global_steps_are_not_bounded(tmp_path):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    global_step = next(s for s in plan if not s.chapter)
    assert SG.forbidden_rels(ws, plan, global_step) == set()


# ── 3. 章后人物更新：主要角色必须记录本章状态 ─────────────────────────────

def _patch_blueprint_and_characters(monkeypatch, present, records):
    bp = {"chapters": [{"id": 1, "title": "t", "function": "f", "characters_present": present}]}
    monkeypatch.setattr(BS, "load_for_ws", lambda ws, strict: bp)
    monkeypatch.setattr(CS, "load_records", lambda base: (records, []))


def test_sync_requires_main_character_updated_for_chapter(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    sync1 = next(s for s in plan if s.chapter == 1 and s.id.endswith(".sync"))
    records = {"c1": {"id": "c1", "name": "李凌云", "role": "protagonist",
                      "current_state": {"as_of_chapter": 0}, "state_log": [{"chapter": 0}]}}
    _patch_blueprint_and_characters(monkeypatch, ["李凌云"], records)
    probs = V._sync_presence_problems(ws, sync1)
    assert probs and "李凌云" in probs[0]


def test_sync_passes_when_main_character_has_chapter_entry(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    sync1 = next(s for s in plan if s.chapter == 1 and s.id.endswith(".sync"))
    records = {"c1": {"id": "c1", "name": "李凌云", "role": "protagonist",
                      "current_state": {"as_of_chapter": 1}, "state_log": [{"chapter": 1}]}}
    _patch_blueprint_and_characters(monkeypatch, ["李凌云"], records)
    assert V._sync_presence_problems(ws, sync1) == []


def test_sync_ignores_minor_characters(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    plan = _plan(ws)
    sync1 = next(s for s in plan if s.chapter == 1 and s.id.endswith(".sync"))
    records = {"c2": {"id": "c2", "name": "路人甲", "role": "minor",
                      "current_state": {"as_of_chapter": 0}, "state_log": []}}
    _patch_blueprint_and_characters(monkeypatch, ["路人甲"], records)
    assert V._sync_presence_problems(ws, sync1) == []


# ── 4. 运行器集成：越界修改会被拦截并要求还原，且执行期间步骤已绑定 ─────────

def test_runner_blocks_out_of_scope_write_and_retries(tmp_path):
    import json as _json
    from novel_writer import runner as R
    from novel_writer.runner import InvokeResult

    ws = _ws(tmp_path)
    plan = _plan(ws)
    sync1 = next(s for s in plan if s.chapter == 1 and s.id.endswith(".sync"))
    write2 = next(s for s in plan if s.chapter == 2 and s.id.endswith(".write_chapter"))
    later_file = ws.root / _concrete(write2.outputs[0])
    seen_bind = []
    prompts = []

    def invoker(*, skill, prompt, export_dir, step_id, max_turns=None, timeout=None):
        prompts.append(prompt)
        seen_bind.append(SG.bound_step_id(ws))
        if len(prompts) == 1:
            later_file.parent.mkdir(parents=True, exist_ok=True)
            later_file.write_text("越界", encoding="utf-8")       # 第一次：越界写后续章节
        else:
            later_file.unlink(missing_ok=True)                    # 第二次：按提示还原
        (export_dir / R.RECEIPT_FILE).write_text(_json.dumps({"status": "done", "summary": "ok", "outputs": []}),
                                                 encoding="utf-8")
        return InvokeResult(True)

    step = S.StepDef(id=sync1.id, title="t", stage=sync1.stage, category=sync1.category,
                     executor=S.EXECUTOR_SKILL, skill="character-manager", chapter=1)
    receipt = R.run_skill_step(ws, step, invoker=invoker, settings=ws.settings(), run_no=1)
    assert receipt["status"] == "done"
    assert len(prompts) == 2
    assert "越界修改了不属于本步骤的文件" in prompts[1]
    assert seen_bind == [sync1.id, sync1.id]          # 执行期间绑定为当前步骤
    assert SG.bound_step_id(ws) is None               # 结束后解绑
