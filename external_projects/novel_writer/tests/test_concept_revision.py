"""构思修订测试：校验、请求与步骤展开、备份与撤销、新思路注入执行器、作者修改标记、蓝图确认门槛。

全部使用桩执行器（tests/fakes.FakeInvoker），不调用真实 LLM。
"""

import pytest

from novel_writer import concept_revision as CR
from novel_writer import config, state as st
from novel_writer import ondemand as OD
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.pipeline import build_plan
from novel_writer.runner import NeedsUser
from novel_writer.workspace import Workspace

from fakes import FakeInvoker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("构思", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _concept_step(rid, handler="concept"):
    return S.StepDef(id=f"cpt.{rid}", title="构思修订", stage="ondemand", category=S.CAT_REVISE,
                     executor=S.EXECUTOR_PYTHON, handler=handler, chapter=0,
                     extra={"request": f"concept_{rid}", "kind": "concept"})


def _write(ws, key, text, **fmt):
    p = ws.path(key, **fmt)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ----------------------------------------------------------------- 校验与请求

def test_validate_rejects_empty_unknown_and_empty_scope():
    with pytest.raises(CR.ConceptError, match="思路"):
        CR.validate("  ", ["theme"])
    with pytest.raises(CR.ConceptError, match="未知"):
        CR.validate("救赎", ["plot"])
    with pytest.raises(CR.ConceptError, match="至少选择"):
        CR.validate("救赎", [])
    text, chosen = CR.validate(" 救赎 ", ["world", "theme", "world"])
    assert text == "救赎" and chosen == ["theme", "world"]        # 去重并按展示顺序排列


def test_request_appears_as_manual_step_in_plan(tmp_path):
    ws = _ws(tmp_path)
    r = tool_api.create_concept_revision(str(ws.root), "把复仇改为救赎", ["theme", "world"])
    assert r["ok"], r
    rid, step_id = r["data"]["id"], r["data"]["step"]
    assert step_id == f"cpt.{rid}"
    steps = {s.id: s for s in OD.ondemand_steps(ws)}
    assert steps[step_id].handler == "concept" and steps[step_id].extra["optional"] is True
    listing = tool_api.list_concept_revisions(str(ws.root))["data"]["requests"]
    assert listing[0]["direction"] == "把复仇改为救赎"
    assert listing[0]["scope_labels"] == [CR.SCOPE_LABELS["theme"], CR.SCOPE_LABELS["world"]]


def test_api_reports_validation_errors(tmp_path):
    ws = _ws(tmp_path)
    r = tool_api.create_concept_revision(str(ws.root), "", ["theme"])
    assert not r["ok"] and "思路" in r["error"]["message"]


# ----------------------------------------------------------------- 备份与撤销

def test_restore_brings_back_prior_content_and_removes_new_files(tmp_path):
    ws = _ws(tmp_path)
    theme = _write(ws, "theme_design", "原主题：复仇")
    world = _write(ws, "world_bible", '{"name":"旧世界"}')
    card = _write(ws, "character_card", "原人设", name="林远")
    rid = OD.create_request(ws, "concept", direction="救赎", scopes=["theme", "world", "characters", "opening"])["id"]

    CR.backup_scopes(ws, rid, ["theme", "world", "characters", "opening"])
    # 模拟修订：改写已有文件，并新生成一个修订前不存在的文件
    theme.write_text("新主题：救赎", encoding="utf-8")
    world.write_text('{"name":"新世界"}', encoding="utf-8")
    card.write_text("新人设", encoding="utf-8")
    opening = ws.path("opening_design")
    opening.write_text("新开篇", encoding="utf-8")

    res = CR.restore(ws, rid)
    assert theme.read_text(encoding="utf-8") == "原主题：复仇"
    assert world.read_text(encoding="utf-8") == '{"name":"旧世界"}'
    assert card.read_text(encoding="utf-8") == "原人设"
    assert not opening.exists()                      # 修订前不存在，撤销后删除
    assert res["restored_files"] >= 3
    req = OD.load_request(ws, "concept", rid)
    assert req["restored"] is True and "撤销" in req["result"]["outcome"]


def test_restore_without_backup_is_refused(tmp_path):
    ws = _ws(tmp_path)
    rid = OD.create_request(ws, "concept", direction="x", scopes=["theme"])["id"]
    with pytest.raises(CR.ConceptError, match="备份"):
        CR.restore(ws, rid)


# ----------------------------------------------------------------- 执行

def test_theme_revision_injects_direction_and_marks_author_change(tmp_path):
    ws = _ws(tmp_path)
    _write(ws, "theme_design", "原主题")
    rid = OD.create_request(ws, "concept", direction="把复仇改为救赎", scopes=["theme"])["id"]
    # 桩执行器按步骤 id 的子串写出“修订后”的内容（真实执行器由 LLM 改写文件）
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题：救赎", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)

    prompt = fake.prompts[f"cpt.{rid}.theme"][0]
    assert "把复仇改为救赎" in prompt and "构思修订" in prompt        # 新思路进入了执行器的指令
    req = OD.load_request(ws, "concept", rid)
    assert req["result"]["changed"] == ["theme"]
    rel = ws.path("theme_design").relative_to(ws.root).as_posix()
    assert req["result"]["changed_files"] == [rel]
    writers = st.load_state(ws).get("file_writers") or {}
    assert writers.get(rel) == "author"                   # 作者修改：下游会按依赖传播


def test_blueprint_revision_requires_confirmation(tmp_path):
    ws = _ws(tmp_path)
    rid = OD.create_request(ws, "concept", direction="结构改为三幕", scopes=["blueprint", "theme"])["id"]
    fake = FakeInvoker(ws.root)
    with pytest.raises(NeedsUser):
        CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    assert fake.calls == []                              # 未确认前不调用执行器，也不备份


def test_characters_revision_needs_a_blueprint_first(tmp_path):
    ws = _ws(tmp_path)
    rid = OD.create_request(ws, "concept", direction="主角改为女性", scopes=["characters"])["id"]
    with pytest.raises(CR.ConceptError, match="蓝图"):
        CR.h_concept(ws, _concept_step(rid), {}, invoker=FakeInvoker(ws.root))


def test_scope_paths_cover_every_scope(tmp_path):
    ws = _ws(tmp_path)
    for scope in CR.SCOPE_ORDER:
        paths = CR.scope_paths(ws, scope)
        assert paths and all(str(p).startswith(str(ws.root)) for p, _ in paths)
    assert build_plan(ws)                                # 计划可以正常展开（撤销时会用到）


def test_background_task_rows_accept_concept_requests(tmp_path):
    """后台任务面板：构思修订请求必须有标签且不报 KeyError（曾因标签表缺 concept 导致整个面板报错）。"""
    from novel_writer import tasks as TK
    ws = _ws(tmp_path)
    OD.create_request(ws, "concept", direction="要包含 sky 光遇的关键元素", scopes=["blueprint"])
    rows = TK._request_rows(ws, "构思测试")
    assert len(rows) == 1
    assert "构思修订" in rows[0]["detail"]
    assert rows[0]["step_title"].startswith("要包含 sky")


def test_confirmed_blueprint_answer_proceeds_to_execution(tmp_path):
    """作者确认后，同一步骤再次运行必须真正执行（不再要求答复，并调用执行器）。"""
    from novel_writer.runner import _answers_file
    ws = _ws(tmp_path)
    rid = OD.create_request(ws, "concept", direction="结构改为三幕", scopes=["blueprint"])["id"]
    path = _answers_file(ws, f"cpt.{rid}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"answers": {"decision": "确认，改写蓝图"}}', encoding="utf-8")
    fake = FakeInvoker(ws.root)
    err = None
    try:
        CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    except NeedsUser:
        raise AssertionError("确认后仍然要求答复")
    except Exception as exc:   # 桩执行器产出可能不通过蓝图校验；门槛已通过是关键，这里记录原因便于诊断
        err = exc
    assert fake.calls, f"确认后没有调用执行器，异常：{type(err).__name__}: {err}"
    assert fake.calls[0] == f"cpt.{rid}.blueprint"
    assert not path.exists()


def test_revision_writes_step_by_step_progress_to_run_log(tmp_path):
    """回归：修订过程必须写入运行日志（开始、每个子步骤的开始与完成、最终结果），不能只有一行。"""
    from novel_writer import state as st
    ws = _ws(tmp_path)
    _write(ws, "theme_design", "原主题")
    rid = OD.create_request(ws, "concept", direction="把复仇改为救赎", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    log = "\n".join(st.read_log_tail(ws, lines=200))
    assert "构思修订开始" in log and "把复仇改为救赎" in log
    assert "调用模型中" in log and "已完成" in log
    assert "构思修订完成" in log and "实际改动" in log


def test_failed_sub_step_writes_the_reason_to_run_log(tmp_path):
    """子步骤失败时，运行日志必须写出失败原因（之前只有最终的一行摘要）。"""
    from novel_writer import state as st
    from novel_writer.runner import StepFailed
    ws = _ws(tmp_path)
    _write(ws, "theme_design", "原主题")
    rid = OD.create_request(ws, "concept", direction="救赎", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, fail={f"cpt.{rid}.theme"})
    with pytest.raises(StepFailed):
        CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    log = "\n".join(st.read_log_tail(ws, lines=200))
    assert "✘ 主题设计 失败" in log and "workflow 执行未成功" in log


def test_retry_attempt_carries_previous_failure_reason_into_prompt(tmp_path):
    from novel_writer import state as st
    ws = _ws(tmp_path)
    _write(ws, "theme_design", "原主题")
    rid = OD.create_request(ws, "concept", direction="救赎", scopes=["theme"])["id"]
    st.update_step(ws, f"cpt.{rid}", attempts=2, retry_hint="主题产出缺少回执")
    fake = FakeInvoker(ws.root)
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    prompt = fake.prompts[f"cpt.{rid}.theme"][0]
    assert "上一次尝试失败" in prompt and "主题产出缺少回执" in prompt
    log = "\n".join(st.read_log_tail(ws, lines=200))
    assert "这是第 2 次尝试" in log and "主题产出缺少回执" in log


def test_heartbeat_reports_progress_while_waiting_for_model(tmp_path):
    import time as _t
    from novel_writer import state as st
    ws = _ws(tmp_path)
    with CR._heartbeat(ws, "主题设计", every=0.05):
        _t.sleep(0.25)
    log = "\n".join(st.read_log_tail(ws, lines=50))
    assert "仍在调用模型" in log and "主题设计" in log
