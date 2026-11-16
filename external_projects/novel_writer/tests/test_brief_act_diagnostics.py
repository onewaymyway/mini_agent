"""章节简述（brief_draft）的诊断：执行器结果被记录；没写出文件时给出具体原因与处理建议；超时不重试；能补救时不失败。"""
from types import SimpleNamespace

import pytest

from novel_writer import brief as B
from novel_writer import blueprint_store as BS
from novel_writer.runner import InvokeResult


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(BS, "load_for_ws", lambda ws, strict=False: {"chapters": [{"id": 1, "title": "开端"}]})
    monkeypatch.setattr(B, "known_names", lambda ws: [])
    monkeypatch.setattr(B, "known_foreshadow", lambda ws: [])
    monkeypatch.setattr(B, "load_brief", lambda ws, n: None)
    monkeypatch.setattr(B, "save_brief", lambda ws, item: None)
    monkeypatch.setattr(B, "_write_index", lambda ws: None)
    return ws


class Inv:
    def __init__(self, *results):
        self.results, self.prompts = list(results), []

    def __call__(self, **kw):
        self.prompts.append(kw["prompt"])
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return r


def test_failed_invoker_reason_is_reported_and_fed_back(env):
    inv = Inv(InvokeResult(False, "模型接口 503"), InvokeResult(False, "模型接口 503"))
    with pytest.raises(RuntimeError) as ei:
        B.generate_act(env, 1, 1, 1, inv)
    msg = str(ei.value)
    assert "执行器返回失败：模型接口 503" in msg and "已尝试 2 次" in msg and "处理：" in msg
    assert "模型接口 503" in inv.prompts[1] and "写文件工具" in inv.prompts[1]   # 下一次提示词带上原因与做法


def test_no_file_and_no_output_explains_directory_and_steps(env):
    inv = Inv(InvokeResult(True), InvokeResult(True))
    with pytest.raises(RuntimeError) as ei:
        B.generate_act(env, 1, 1, 1, inv)
    msg = str(ei.value)
    assert "目录内容：空" in msg and "没有调用写文件" in msg and "invoke.json" in msg
    assert "brief_act_01.json" in inv.prompts[1] and "不要只在回复中输出" in inv.prompts[1]


def test_timeout_stops_retrying_immediately(env):
    inv = Inv(InvokeResult(False, "已超过步骤时限", timed_out=True))
    with pytest.raises(RuntimeError, match="步骤时限已到"):
        B.generate_act(env, 1, 1, 1, inv)
    assert len(inv.prompts) == 1


def test_invoke_record_is_written_for_each_attempt(env):
    inv = Inv(InvokeResult(False, "boom"), InvokeResult(False, "boom"))
    with pytest.raises(RuntimeError):
        B.generate_act(env, 1, 1, 1, inv)
    for n in (1, 2):
        rec = (env.root / "_studio" / "brief_runs" / "act01" / f"attempt{n:02d}" / "invoke.json")
        assert rec.is_file() and '"error": "boom"' in rec.read_text(encoding="utf-8")


def test_json_in_reply_is_salvaged_when_file_was_not_written(env, tmp_path):
    out = tmp_path / "reply.txt"
    out.write_text('好的，结果如下：{"chapters": [{"chapter": 1}]} 完成。', encoding="utf-8")
    inv = Inv(InvokeResult(True, output_path=str(out)))
    try:
        B.generate_act(env, 1, 1, 1, inv)
    except RuntimeError as exc:
        # 补救已写出文件；内容是否合格由后续校验决定，但不应再报「没有写出」
        assert "没有写出简述文件" not in str(exc)
    saved = list((env.root / "_studio" / "brief_runs" / "act01" / "attempt01").glob("brief_act_01.json"))
    assert saved and '"chapters"' in saved[0].read_text(encoding="utf-8")
