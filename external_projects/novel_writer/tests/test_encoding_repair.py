"""文本编码损坏：收集错误信息，交给修复流程（Python 步骤也一样），而不是盲目重跑。

复现的损坏与线上报错一致：UTF-8 文本中某个中文标点（E3 80 xx）的末字节被替换成 '?'（3F），
整个文件既不是合法 UTF-8，也解不开 GB18030。
"""

from pathlib import Path

import pytest

from novel_writer import config, recovery  # noqa: F401  (确保模块可导入)
from novel_writer import repair as RP
from novel_writer import runner as R
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.runner import InvokeResult
from novel_writer.textio import TextEncodingError, read_text_utf8
from novel_writer.worker import Budget, _repair_and_rerun
from novel_writer.workspace import Workspace

CORRUPT = ("眼中".encode("utf-8") + b"\xe3\x80\x3f" + "\r\n\r\n核对完成".encode("utf-8")
           + b"\x81\x20\xfe")   # 末尾非法序列：UTF-8 与 GB18030 都解不开（同线上报错）


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("编码修复", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G5", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    report = ws.path("review_report", nn=1)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(CORRUPT)
    return ws


def _python_step(rel: str) -> S.StepDef:
    return S.StepDef(id="ch01.revise_loop", title="第1章：修改循环", stage="p2", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_PYTHON, handler="read_report", chapter=1, inputs=(rel,))


def _handlers(ws):
    def read_report(ws_, step, settings, invoker=None):
        text = read_text_utf8(ws.path("review_report", nn=1))   # 与真实的 revise_loop 一样读取报告
        return {"summary": f"读取 {len(text)} 字"}
    return {"read_report": read_report}


def _fixing_invoker(ws, *, fix: bool):
    """修复调用：fix=True 时把损坏的报告改写为合法 UTF-8（模拟 LLM 修复）；记录收到的提示词。"""
    seen = []

    def invoker(*, skill, prompt, export_dir, step_id, max_turns=None, timeout=None, allowed_tools=None):
        seen.append(prompt)
        if fix:
            ws.path("review_report", nn=1).write_text("# 一致性核查\n\n眼中。\n\n核对完成", encoding="utf-8")
        return InvokeResult(ok=True)

    invoker.prompts = seen
    return invoker


def test_text_encoding_error_carries_the_file_path(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(TextEncodingError) as info:
        read_text_utf8(ws.path("review_report", nn=1))
    assert info.value.path == ws.path("review_report", nn=1)


def test_python_step_failure_becomes_a_repair_request(tmp_path):
    ws = _ws(tmp_path)
    rel = ws.path("review_report", nn=1).relative_to(ws.root).as_posix()
    step = _python_step(rel)
    status = R.run_step(ws, step, invoker=_fixing_invoker(ws, fix=False), settings=ws.settings(),
                        handlers=_handlers(ws))
    assert status == st.ST_FAILED
    repair = st.get_step_state(ws, step.id)["repair"]
    assert repair["validator"] == "text_encoding"
    assert repair["files"] == [rel]
    assert "无法读取为文本" in repair["problems"][0]
    assert RP.request_of(ws, step.id) is not None      # 修复流程能识别这个请求


def test_repair_fixes_the_file_and_the_python_step_passes(tmp_path):
    ws = _ws(tmp_path)
    rel = ws.path("review_report", nn=1).relative_to(ws.root).as_posix()
    step = _python_step(rel)
    handlers = _handlers(ws)
    assert R.run_step(ws, step, invoker=_fixing_invoker(ws, fix=False), settings=ws.settings(),
                      handlers=handlers) == st.ST_FAILED

    invoker = _fixing_invoker(ws, fix=True)
    status = _repair_and_rerun(ws, step, invoker=invoker, settings=ws.settings(), handlers=handlers,
                               budget=Budget(ws.settings()))
    assert status == st.ST_PASSED, st.get_step_state(ws, step.id)
    assert len(invoker.prompts) == 1                     # 修复只调用了一次（成功后计数会清零）
    assert read_text_utf8(ws.path("review_report", nn=1)).startswith("# 一致性核查")


def test_repair_prompt_tells_the_model_about_unreadable_bytes(tmp_path):
    ws = _ws(tmp_path)
    rel = ws.path("review_report", nn=1).relative_to(ws.root).as_posix()
    step = _python_step(rel)
    handlers = _handlers(ws)
    R.run_step(ws, step, invoker=_fixing_invoker(ws, fix=False), settings=ws.settings(), handlers=handlers)
    invoker = _fixing_invoker(ws, fix=True)
    _repair_and_rerun(ws, step, invoker=invoker, settings=ws.settings(), handlers=handlers,
                      budget=Budget(ws.settings()))
    prompt = invoker.prompts[0]
    assert "�" in prompt                                  # 损坏的字节以替换字符展示给 LLM
    assert "无法识别的字节" in prompt                      # 并告诉它怎么处理


def test_generic_repair_request_does_not_crash_on_corrupt_inputs(tmp_path):
    ws = _ws(tmp_path)
    rel = ws.path("review_report", nn=1).relative_to(ws.root).as_posix()
    req = RP.generic_request(ws, _python_step(rel))
    assert req is not None and req["files"] == [rel]


def test_fixing_is_not_faked_when_the_model_does_not_fix(tmp_path):
    """修复没有真正修好文件时，步骤不能通过。"""
    ws = _ws(tmp_path)
    rel = ws.path("review_report", nn=1).relative_to(ws.root).as_posix()
    step = _python_step(rel)
    handlers = _handlers(ws)
    R.run_step(ws, step, invoker=_fixing_invoker(ws, fix=False), settings=ws.settings(), handlers=handlers)
    status = _repair_and_rerun(ws, step, invoker=_fixing_invoker(ws, fix=False), settings=ws.settings(),
                               handlers=handlers, budget=Budget(ws.settings()))
    assert status == st.ST_FAILED
