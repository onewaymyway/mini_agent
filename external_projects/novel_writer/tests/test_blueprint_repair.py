"""蓝图文件格式错误 → 交给 LLM 修复 → 重试（而不是原地反复失败）。"""

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import chapter_ops as C
from novel_writer import config, repair as RP, state as st, steps as S, tool_api
from novel_writer.workspace import Workspace
from tests.fakes import make_blueprint


class FakeResult:
    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.timed_out = ok, error, False


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("蓝图修复", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    w = Workspace(r["data"]["workspace"])
    BS.write_store(w.path("blueprint_dir"), w.path("blueprint"), make_blueprint(3))
    return w


def _step():
    return S.StepDef(id="ch31.word_quota", title="字数配额", stage="p0", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="word_quota")


def _break_chapter(ws, name="第002章.md"):
    (ws.path("blueprint_dir") / name).write_text("这不是合法的章节文件\n", encoding="utf-8")


def _expected_rel(ws, name="第002章.md"):
    return (ws.path("blueprint_dir") / name).relative_to(ws.root).as_posix()


def test_broken_chapter_raises_repairable_error_naming_the_file(ws):
    _break_chapter(ws)
    with pytest.raises(RP.RepairableDataError) as exc:
        C._blueprint_strict(ws)
    err = exc.value
    assert err.validator == "blueprint"
    assert _expected_rel(ws) in err.files
    assert err.scope == ws.path("blueprint_dir").relative_to(ws.root).as_posix()
    assert err.problems and any("第002章.md" in p for p in err.problems)


def test_validator_reports_problems_then_clears_after_fix(ws):
    _break_chapter(ws)
    assert RP.VALIDATORS["blueprint"](ws)
    BS.write_store(ws.path("blueprint_dir"), ws.path("blueprint"), make_blueprint(3))
    assert RP.VALIDATORS["blueprint"](ws) == []


def test_llm_repair_fixes_blueprint_and_rerun_succeeds(ws):
    _break_chapter(ws)
    with pytest.raises(RP.RepairableDataError) as exc:
        C._blueprint_strict(ws)
    request = {"validator": "blueprint", "files": exc.value.files, "problems": exc.value.problems,
               "scope": exc.value.scope}

    def invoker(**kw):
        # 模拟 LLM：只修改列出的文件，把章节恢复成正确内容
        BS.write_store(ws.path("blueprint_dir"), ws.path("blueprint"), make_blueprint(3))
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=request)
    assert out["fixed"] and out["problems"] == []
    assert C._blueprint_strict(ws)["chapters"]                 # 重跑时蓝图可以正常读取
    assert st.get_step_state(ws, "ch31.word_quota")["repair_attempts"] == 1


def test_missing_chapter_files_stay_a_plain_error(ws):
    for f in ws.path("blueprint_dir").glob("第*章.md"):
        f.unlink()
    with pytest.raises(C.ChapterOpError) as exc:
        C._blueprint_strict(ws)
    assert "没有任何章节文件" in str(exc.value)
    assert not isinstance(exc.value, RP.RepairableDataError)
