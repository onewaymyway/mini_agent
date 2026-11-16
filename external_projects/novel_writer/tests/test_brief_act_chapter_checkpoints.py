"""章节简述的逐章检查点：通过的章节保存，重试只重做未通过的章节；伏笔编号按账本原样（fs_001）通过。"""
import json
from types import SimpleNamespace

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import brief as B
from novel_writer.runner import InvokeResult


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(BS, "load_for_ws", lambda ws, strict=False: {"chapters": [{"id": 1, "title": "甲"}, {"id": 2, "title": "乙"}]})
    monkeypatch.setattr(B, "known_names", lambda ws: [])
    monkeypatch.setattr(B, "known_foreshadow", lambda ws: ["fs_001"])
    monkeypatch.setattr(B, "load_brief", lambda ws, n: None)
    saved = []
    monkeypatch.setattr(B, "save_brief", lambda ws, item: saved.append(item["chapter"]))
    monkeypatch.setattr(B, "_write_index", lambda ws: None)
    ws.saved = saved
    return ws


def chapter(n, words, foreshadow=("fs_001",)):
    return {"chapter": n, "title": f"章{n}", "narrative": "甲" * words,
            "key": {"must_events": ["事件"], "must_states": [{"who": "甲", "change": "变化"}], "must_foreshadow": list(foreshadow), "must_hook": "钩子"}}


class Scripted:
    """每次调用按脚本写出文件（模拟执行器）；记录提示词。"""
    def __init__(self, *outputs):
        self.outputs, self.prompts = list(outputs), []

    def __call__(self, **kw):
        self.prompts.append(kw["prompt"])
        out_file = kw["export_dir"] / "brief_act_01.json"
        payload = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        out_file.write_text(json.dumps({"chapters": payload}, ensure_ascii=False), encoding="utf-8")
        return InvokeResult(True)


def test_only_failing_chapter_is_redone_and_ledger_ids_pass(env):
    inv = Scripted(
        [chapter(1, 450), chapter(2, 450, ("fs_999",))],   # 第 2 章伏笔编号不在账本中
        [chapter(2, 450)],                           # 只需重做第 2 章
    )
    out = B.generate_act(env, 1, 1, 2, inv)
    assert out["attempts"] == 2 and env.saved == [1, 2] and out["length_notes"] == {}
    assert "本次只需写出下列章节（其余章节已通过校验，不要输出）：2。" in inv.prompts[1]
    assert not (env.root / "_studio" / "brief_runs" / "act01" / "accepted").exists()   # 成功后清理检查点


def test_long_narrative_is_accepted_with_a_note(env):
    inv = Scripted([chapter(1, 450), chapter(2, 900)])
    out = B.generate_act(env, 1, 1, 2, inv)
    assert out["attempts"] == 1 and env.saved == [1, 2]                  # 超长不阻断
    assert out["length_notes"] == {2: "叙述约 900 字，超出建议的 300–600 字（仅供参考，已接受）"}
    assert "字数提示" in out["summary"]


def test_foreshadow_must_be_from_ledger_exact_form(env):
    assert B.validate_brief(chapter(1, 450, ("fs_001",)), chapter=1, title="甲", names=[], foreshadow=["fs_001"]) == []
    errs = B.validate_brief(chapter(1, 450, ("F1",)), chapter=1, title="甲", names=[], foreshadow=["fs_001"])
    assert any("不在伏笔账本中" in e and "fs_001" in e for e in errs)


def test_passed_chapters_survive_a_failed_run(env):
    bad2 = chapter(2, 450, ("fs_999",))            # 伏笔编号不在账本中：硬性失败
    inv = Scripted([chapter(1, 450), bad2], [bad2], [bad2])
    with pytest.raises(RuntimeError, match="已通过 1/2 章"):
        B.generate_act(env, 1, 1, 2, inv)
    kept = list((env.root / "_studio" / "brief_runs" / "act01" / "accepted").glob("ch001.json"))
    assert kept, "第 1 章通过后应保存为检查点"
    assert env.saved == []                                  # 整幕未通过，不落盘
