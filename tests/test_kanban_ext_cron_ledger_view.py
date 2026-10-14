"""
看板 Cron 详情：外部项目（ext:*）任务的执行记录读取项目账本。

对应 next_doc/kanban_ext_cron_job_run_records_fix.md。现象：ext:* job 详情里
「累计运行次数」不为 0，但「执行记录」什么都没有。根因是这类 job 的 entrypoint
由 external_projects.scheduler._run_entrypoint() 执行，记录写进外部项目自己的
`.agent/run_status.jsonl` 账本，而不是 Cron job workspace 的 runs/。
"""
from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

_APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"


@pytest.fixture(scope="module")
def app():
    sys.path.insert(0, str(_APP_DIR))
    spec = importlib.util.spec_from_file_location("kanban_app_ext_ledger_test", _APP_DIR / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kanban_app_ext_ledger_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeSt:
    """只记录输出文本的假 streamlit，足够验证展示逻辑。"""

    def __init__(self):
        self.out: list[tuple[str, str]] = []

    def _rec(self, kind, text="", *a, **k):
        self.out.append((kind, str(text)))

    def caption(self, t, *a, **k): self._rec("caption", t)
    def markdown(self, t, *a, **k): self._rec("markdown", t)
    def error(self, t, *a, **k): self._rec("error", t)
    def code(self, t, *a, **k): self._rec("code", t)
    def divider(self, *a, **k): self._rec("divider")

    @contextlib.contextmanager
    def expander(self, label, *a, **k):
        self._rec("expander", label)
        yield

    def text_of(self, kind=None) -> str:
        return "\n".join(t for k, t in self.out if kind is None or k == kind)


class _FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def external_project_ledger(self, name, limit=20):
        self.calls.append((name, limit))
        return self.resp


JOB = {"id": "ext:stock_watch:telegraph_scan", "external_project": "stock_watch",
       "external_entrypoint": "telegraph_scan", "run_count": 19}


def _rec(ep, code=0, trig="daemon", start="2026-10-14T10:00:00+08:00",
         end="2026-10-14T10:00:30+08:00", err=None, detail=None):
    return {"entrypoint": ep, "exit_code": code, "trigger": trig, "started_at": start,
            "finished_at": end, "error_summary": err, "detail": detail}


def _run(app, monkeypatch, resp, job=JOB):
    fake = _FakeSt()
    monkeypatch.setattr(app, "st", fake)
    client = _FakeClient(resp)
    app._render_external_job_ledger(client, job)
    return fake, client


def test_reads_project_ledger_filtered_by_entrypoint_newest_first(app, monkeypatch):
    records = [
        _rec("telegraph_scan", start="2026-10-14T09:00:00+08:00", end="2026-10-14T09:00:10+08:00"),
        _rec("other_entry", code=1, err="other failed"),
        _rec("telegraph_scan", code=1, err="entrypoint exited with code 1",
             detail="Traceback: boom", start="2026-10-14T11:00:00+08:00",
             end="2026-10-14T11:00:20+08:00"),
    ]
    fake, client = _run(app, monkeypatch, {"records": records})
    assert client.calls and client.calls[0][0] == "stock_watch"
    md = fake.text_of("markdown")
    assert "11:00:00" in md and "09:00:00" in md
    assert md.index("11:00:00") < md.index("09:00:00")     # 最新在前
    assert "other failed" not in fake.text_of()            # 其它 entrypoint 被过滤
    assert "entrypoint exited with code 1" in fake.text_of("error")
    assert "Traceback: boom" in fake.text_of("code")
    assert "✅ 成功" in md and "❌ 失败" in md
    assert "耗时 20.0s" in md


def test_empty_ledger_explains_run_count_is_dispatch_count(app, monkeypatch):
    fake, _ = _run(app, monkeypatch, {"records": []})
    cap = fake.text_of("caption")
    assert "暂无该 entrypoint 的执行记录" in cap or "暂无" in cap
    assert "成功派发" in cap and "排队" in cap


def test_ledger_error_is_reported_not_raised(app, monkeypatch):
    fake, _ = _run(app, monkeypatch, {"_error": "HTTP 404"})
    assert "HTTP 404" in fake.text_of("caption")


def test_run_count_larger_than_ledger_is_explained(app, monkeypatch):
    fake, _ = _run(app, monkeypatch, {"records": [_rec("telegraph_scan")]})
    assert "累计运行次数 19 大于账本记录数 1" in fake.text_of("caption")


def test_success_only_has_no_error_blocks(app, monkeypatch):
    fake, _ = _run(app, monkeypatch, {"records": [_rec("telegraph_scan")]})
    assert fake.text_of("error") == "" and fake.text_of("code") == ""


def test_duration_helper(app):
    f = app._ext_ledger_duration_str
    assert f("2026-10-14T10:00:00+08:00", "2026-10-14T10:00:30+08:00") == "，耗时 30.0s"
    assert f("2026-10-14T10:00:00+08:00", "2026-10-14T10:05:00+08:00") == "，耗时 5.0 分钟"
    assert f("", "") == ""
    assert f("2026-10-14T10:00:00", "2026-10-14T10:00:30+08:00") == ""   # 时区不一致不硬算
    assert f("2026-10-14T10:00:30+08:00", "2026-10-14T10:00:00+08:00") == ""


def test_no_record_note_by_channel(app, monkeypatch):
    fake = _FakeSt()
    monkeypatch.setattr(app, "st", fake)
    app._render_cron_no_record_note("local_handler")
    app._render_cron_no_record_note("unknown_channel")
    cap = fake.text_of("caption")
    assert "本地回调" in cap and "触发过至少一次后" in cap
