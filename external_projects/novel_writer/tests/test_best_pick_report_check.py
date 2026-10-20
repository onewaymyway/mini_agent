"""择优报告的内容校验与重试（M8）：前一步就要发现 winner 不合法，并让 agent 重写，而不是等到应用步骤才失败。"""

import json

import pytest

from fakes import InvokeResult
from novel_writer import config, steps as S, tool_api, validators as V
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import MAX_RECEIPT_ATTEMPTS, RECEIPT_FILE, run_skill_step
from novel_writer.runner import StepFailed
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path):
    r = tool_api.create_novel("择优校验", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _report_path(ws, n=5):
    return ws.path("best_pick_report", nn=n)


def test_normalize_winner_accepts_common_spellings_and_rejects_unknown():
    assert V.normalize_pick_winner("A") == "A"
    assert V.normalize_pick_winner(" b ") == "B"
    assert V.normalize_pick_winner("候选稿") == "B"
    assert V.normalize_pick_winner("正文") == "A"
    assert V.normalize_pick_winner("C") is None
    assert V.normalize_pick_winner(None) is None
    assert V.normalize_pick_winner(1) is None


def test_valid_report_passes_untouched(tmp_path):
    ws = _ws(tmp_path)
    p = _report_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"winner": "A", "reason": "节奏更好"}, ensure_ascii=False), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    assert V.best_pick_report_problems(p) == []
    assert p.read_text(encoding="utf-8") == before   # 合格时不改写文件


def test_lowercase_winner_is_normalized_and_written_back(tmp_path):
    ws = _ws(tmp_path)
    p = _report_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"winner": "b", "reason": "伏笔更完整"}', encoding="utf-8")
    assert V.best_pick_report_problems(p) == []
    assert json.loads(p.read_text(encoding="utf-8"))["winner"] == "B"


def test_fenced_json_is_accepted_and_rewritten_as_plain_json(tmp_path):
    ws = _ws(tmp_path)
    p = _report_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('```json\n{"winner": "A", "reason": "x"}\n```', encoding="utf-8")
    assert V.best_pick_report_problems(p) == []
    assert json.loads(p.read_text(encoding="utf-8"))["winner"] == "A"


@pytest.mark.parametrize("content, keyword", [
    ('{"winner": "C", "reason": "x"}', "winner"),
    ('{"reason": "没有 winner"}', "winner"),
    ("我选 B 稿，因为更好。", "JSON"),
    ('["A"]', "JSON 对象"),
    ('{"winner": "A", "reason": 3}', "reason"),
])
def test_invalid_reports_produce_actionable_problems(tmp_path, content, keyword):
    ws = _ws(tmp_path)
    p = _report_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    problems = V.best_pick_report_problems(p)
    assert problems and any(keyword in x for x in problems)


def test_best_pick_step_declares_its_content_check():
    step = S.best_pick_step(5, deps=())
    assert step.extra.get("output_check") == "best_pick_report"


class PickInvoker:
    """依次返回预设的择优报告内容；每次调用都写回执，模拟 agent 的一次执行。"""

    def __init__(self, ws, contents):
        self.ws = ws
        self.contents = list(contents)
        self.prompts = []

    def __call__(self, *, skill, prompt, export_dir, step_id, **kw):
        self.prompts.append(prompt)
        content = self.contents[min(len(self.prompts), len(self.contents)) - 1]
        p = _report_path(self.ws)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        export_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(export_dir / RECEIPT_FILE, {"status": "done", "summary": "已择优"})
        return InvokeResult(True, "")


def test_bad_report_is_retried_with_hint_and_then_passes(tmp_path):
    ws = _ws(tmp_path)
    step = S.best_pick_step(5, deps=())
    inv = PickInvoker(ws, ['{"winner": "C", "reason": "x"}', '{"winner": "a", "reason": "好"}'])
    receipt = run_skill_step(ws, step, invoker=inv, settings={}, run_no=1)
    assert receipt["status"] == "done"
    assert len(inv.prompts) == 2
    assert "winner" in inv.prompts[1]                     # 第二次提示里带上了具体问题
    assert json.loads(_report_path(ws).read_text(encoding="utf-8"))["winner"] == "A"   # 已规范化写回


def test_persistently_bad_report_fails_at_the_picking_step_itself(tmp_path):
    ws = _ws(tmp_path)
    step = S.best_pick_step(5, deps=())
    inv = PickInvoker(ws, ["不是 JSON"])
    with pytest.raises(StepFailed) as exc:
        run_skill_step(ws, step, invoker=inv, settings={}, run_no=1)
    assert len(inv.prompts) == MAX_RECEIPT_ATTEMPTS
    assert "择优报告" in str(exc.value)                   # 错误发生在择优步骤，而不是应用步骤
