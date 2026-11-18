import json
import re

import pytest

from article_to_video import cancel
from article_to_video.planning.errors import PlanError
from article_to_video.script import writer as w
from article_to_video.script.lexicon import save_lexicon
from helpers_p2 import FILLER, ScriptLLM, good_script, ready_project, script_with


def test_write_part_success_saves_and_updates_index(tmp_path):
    pdir = ready_project(tmp_path)
    llm = ScriptLLM(good_script)
    s = w.write_part(pdir, "P01", llm)
    assert s["part"] == "P01" and s["beats"]
    assert s["beats"][0]["id"] == "P01.b01"
    assert (pdir / "parts" / "P01" / "script.json").is_file()
    index = json.loads((pdir / "parts" / "index.json").read_text(encoding="utf-8"))
    assert index["P01"]["chars"] >= index["P01"]["budget_chars"] * 0.85
    assert s["confirmed"] is False


def test_resume_skips_when_input_unchanged(tmp_path):
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    llm = ScriptLLM(good_script)
    w.write_part(pdir, "P01", llm)
    assert llm.calls == 0


def test_lexicon_is_applied_to_tts(tmp_path):
    pdir = ready_project(tmp_path)
    save_lexicon(pdir, {"康波": "康波 K B"})

    def responder(prompt):
        _p, refs, budget, _s = (None, None, None, None)
        n = max(1, 252 // len(FILLER)) + 1
        sents = [{"text": "康波很重要。"}] + [{"text": FILLER} for _ in range(n)]
        return json.dumps({"beats": [{"source_refs": ["s01"], "sentences": sents}]}, ensure_ascii=False)

    s = w.write_part(pdir, "P01", ScriptLLM(responder))
    first = s["beats"][0]["sentences"][0]
    assert first["text"] == "康波很重要。"          # 字幕仍显示原写法
    assert first["tts"] == "康波 K B很重要。"        # 配音使用读法
    assert s["beats"][0]["sentences"][1]["tts"] is None  # 没有替换的句子不留 tts


def test_fabricated_number_is_repaired_locally_without_rewrite(tmp_path):
    """R11：只有一个句子含编造的数字时，由模型局部修正该句，不整章重写。"""
    pdir = ready_project(tmp_path)
    state = {"n": 0}

    def responder(prompt):
        state["n"] += 1
        if state["n"] == 1:
            obj = json.loads(good_script(prompt))           # 完整的稿件，只有一句含编造的数字
            obj["beats"][0]["sentences"][0]["text"] = "投入达到9999亿元。"
            return json.dumps(obj, ensure_ascii=False)
        assert "【口播修正任务】" in prompt       # 第二次是局部修正，而不是整章重写
        return _repair_all(prompt)

    llm = ScriptLLM(responder)
    saved = w.write_part(pdir, "P01", llm)
    assert llm.calls == 2
    text = json.dumps(saved, ensure_ascii=False)
    assert "9999" not in text  # 错误的句子已被修正，没有进入结果


def _repair_all(prompt):
    """测试用修正模型：把所有需要修正的句子替换为一句通用中文。"""
    ids = re.findall(r"- id: (\S+)", prompt)
    return json.dumps({"fixes": [{"id": i, "text": "这是口播中的一句话，用来说明本章的要点内容。"} for i in ids]},
                      ensure_ascii=False)


def test_permanent_fabrication_fails_without_writing(tmp_path):
    pdir = ready_project(tmp_path)
    bad = json.dumps({"beats": [{"source_refs": ["s01"], "sentences": [{"text": "编造一个9999年。"}]}]}, ensure_ascii=False)
    llm = ScriptLLM(lambda p: bad)
    with pytest.raises(PlanError) as exc:
        w.write_part(pdir, "P01", llm)
    # 每次整章写稿后，局部修正返回的不是有效结构，随即回到整章重写：3 次整章 + 3 次修正
    assert llm.calls == 2 * w.MAX_ATTEMPTS
    assert "9999" in str(exc.value)
    assert not (pdir / "parts" / "P01" / "script.json").exists()
    assert (pdir / "parts" / "P01" / "raw_last.txt").is_file()


def test_json_failure_on_last_attempt_does_not_reuse_old_verdict(tmp_path):
    """回归：第一次只有节奏问题（无硬错误），之后两次非 JSON。
    旧实现会沿用第一次的 verdict 把稿子保存下来；现在必须报错且不写入。"""
    pdir = ready_project(tmp_path)
    state = {"n": 0}

    def responder(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps({"beats": [{"source_refs": ["s01"], "sentences": [{"text": "很短。"}]}]},
                              ensure_ascii=False)
        return "不是 JSON"

    with pytest.raises(PlanError):
        w.write_part(pdir, "P01", ScriptLLM(responder))
    assert state["n"] == w.MAX_ATTEMPTS
    assert not (pdir / "parts" / "P01" / "script.json").exists()


def test_persistent_pacing_problem_is_accepted_with_warning(tmp_path):
    pdir = ready_project(tmp_path)
    short = json.dumps({"beats": [{"source_refs": ["s01"], "sentences": [{"text": "很短。"}]}]}, ensure_ascii=False)
    llm = ScriptLLM(lambda p: short)
    s = w.write_part(pdir, "P01", llm)
    assert llm.calls == w.MAX_ATTEMPTS
    assert any("偏离预算" in x for x in s["warnings"])
    assert (pdir / "parts" / "P01" / "script.json").is_file()


def test_write_requires_confirmed_outline(tmp_path):
    from article_to_video.planning.runner import plan_outline
    from helpers_p1 import make_project
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    with pytest.raises(PlanError):
        w.write_part(pdir, "P01", ScriptLLM(good_script))


def test_save_rejects_invalid_and_unconfirms_on_valid_edit(tmp_path):
    pdir = ready_project(tmp_path)
    s = w.write_part(pdir, "P01", ScriptLLM(good_script))
    s["confirmed"] = True
    bad = json.loads(json.dumps(s))
    bad["beats"][0]["sentences"][0]["text"] = "**加粗**不允许"
    with pytest.raises(PlanError):
        w.save_script(pdir, "P01", bad)
    ok = json.loads(json.dumps(s))
    ok["beats"][0]["sentences"][0]["text"] = "这是一句新的口播。"
    saved = w.save_script(pdir, "P01", ok)
    assert saved["confirmed"] is False


def test_confirm_marks_part_and_blocks_on_errors(tmp_path):
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    s = w.confirm_script(pdir, "P01")
    assert s["confirmed"] is True
    broken = json.loads((pdir / "parts" / "P01" / "script.json").read_text(encoding="utf-8"))
    broken["beats"][0]["sentences"][0]["text"] = "数字9999不在原文"
    (pdir / "parts" / "P01" / "script.json").write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PlanError):
        w.confirm_script(pdir, "P01")


def test_stop_during_model_call_writes_nothing(tmp_path):
    pdir = ready_project(tmp_path)
    cancel.bind(cancel.begin(pdir))

    class StopLLM:
        def ask(self, prompt, system=""):
            cancel.request(pdir)
            return good_script(prompt)

    with pytest.raises(cancel.Cancelled):
        w.write_part(pdir, "P01", StopLLM())
    assert not (pdir / "parts" / "P01" / "script.json").exists()


def test_call_returns_quickly_when_stop_requested(tmp_path):
    import threading
    import time
    pdir = tmp_path
    ev = cancel.begin(pdir)
    cancel.bind(ev)
    threading.Timer(0.3, lambda: cancel.request(pdir)).start()
    t0 = time.time()
    with pytest.raises(cancel.Cancelled):
        cancel.call(time.sleep, 10)
    assert time.time() - t0 < 2.0
