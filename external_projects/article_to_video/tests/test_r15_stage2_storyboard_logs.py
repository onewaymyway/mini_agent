"""R15 第二阶段：分镜过程日志（调用、耗时、全部校验错误、缓存决策、结果）；插图可无条目。"""
import json

import pytest

from article_to_video import joblog
from article_to_video.planning.errors import PlanError
from article_to_video.visual import storyboard as sbm
from helpers_p4 import scripted_project
from test_p4_sharing_llm import StoryLLM, _beats_from_prompt, _good_plan


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, spine = scripted_project(tmp_path)
    return pdir


def _msgs(pdir):
    return [r["message"] for r in joblog.tail(pdir, 500, ["storyboard"])]


def _run(pdir, pid, llm, **kw):
    with joblog.bind(pdir, "storyboard"):
        return sbm.plan_storyboard(pdir, pid, engine="llm", llm=llm, **kw)


def test_illustration_without_items_is_accepted(ready):
    def no_text_illustration(prompt, n=None):
        data = json.loads(_good_plan(prompt, n))
        for s in data["scenes"]:
            if s["beat"] == "P01.b02":
                s["type"], s["items"], s["image_subject"] = "illustration", [], "蒸汽机与齿轮"
        return json.dumps(data, ensure_ascii=False)
    sb = _run(ready, "P01", StoryLLM(no_text_illustration))
    assert sb["errors"] == []
    ill = [s for s in sb["scenes"] if s["type"] == "illustration"]
    assert ill and ill[0]["items"] == []


def test_keypoint_without_items_is_still_rejected(ready):
    def empty_keypoint(prompt, n=None):
        data = json.loads(_good_plan(prompt, n))
        for s in data["scenes"]:
            if s["type"] == "keypoint" and s["beat"] == "P01.b01":
                s["items"] = []
        return json.dumps(data, ensure_ascii=False)
    llm = StoryLLM(lambda p, n=None: empty_keypoint(p, n) if llm.calls == 1 else _good_plan(p, n))
    _run(ready, "P01", llm)
    msgs = _msgs(ready)
    assert any("keypoint 缺少条目" in m for m in msgs)
    assert llm.calls == 2


def test_each_call_logs_prompt_size_time_and_outcome(ready):
    llm = StoryLLM(_good_plan)
    _run(ready, "P02", llm)
    msgs = _msgs(ready)
    assert any(m.startswith("P02：输入 ") and "句" in m and "字" in m for m in msgs)
    assert any("可选画面" in m for m in msgs)
    assert any("调用模型设计分镜（第 1/3 次，提示词" in m for m in msgs)
    assert any("模型返回" in m and "用时" in m for m in msgs)
    assert any("模型分镜通过校验：" in m and "×" in m for m in msgs)
    assert any("分镜已写入" in m and "校验通过" in m for m in msgs)


def test_retry_logs_every_validation_error_not_just_the_first(ready):
    def broken(prompt, n=None):
        data = json.loads(_good_plan(prompt, n))
        for s in data["scenes"]:
            s["type"], s["items"] = "keypoint", ["999 编造"]   # 每段都坏（数字不在原文），应记录多条
        return json.dumps(data, ensure_ascii=False)
    llm = StoryLLM(lambda p, n=None: broken(p, n) if llm.calls == 1 else _good_plan(p, n))
    _run(ready, "P03", llm)
    msgs = _msgs(ready)
    bullets = [m for m in msgs if m.startswith("  · ")]
    assert len(bullets) >= 2                    # 多于一条错误都被记录
    assert any("分镜校验发现" in m and "将重试" in m for m in msgs)
    assert any("第 2/3 次" in m and "上次问题" in m for m in msgs)


def test_invalid_json_is_logged_with_reason(ready):
    llm = StoryLLM(lambda p, n=None: "不是 JSON" if llm.calls == 1 else _good_plan(p, n))
    _run(ready, "P01", llm)
    msgs = _msgs(ready)
    assert any("不是有效 JSON" in m and "将重试" in m for m in msgs)


def test_persistent_failure_logs_final_error(ready):
    llm = StoryLLM(lambda p, n=None: "坏输出")
    with pytest.raises(PlanError):
        _run(ready, "P01", llm)
    msgs = _msgs(ready)
    assert any("仍不合格，本章未写入结果" in m for m in msgs)
    assert sbm.load_storyboard(ready, "P01") is None


def test_cache_decisions_are_logged(ready):
    _run(ready, "P01", StoryLLM(_good_plan))                  # 首次：尚无分镜
    assert any("尚无分镜，开始生成" in m for m in _msgs(ready))
    before = len(_msgs(ready))
    llm = StoryLLM(_good_plan)
    _run(ready, "P01", llm)                                    # 输入未变：复用，不调用模型
    assert llm.calls == 0
    assert any("输入未变" in m and "不调用模型" in m for m in _msgs(ready)[before:])
    with joblog.bind(ready, "storyboard"):
        sbm.plan_storyboard(ready, "P01", engine="rules")      # 引擎变化：旧分镜作废
    assert any("输入已变化" in m and "重新生成" in m for m in _msgs(ready))
    _run(ready, "P01", StoryLLM(_good_plan), force=True)       # 强制
    assert any("强制重新生成" in m for m in _msgs(ready))


# ---------- 第三轮修正：公式符号可用、不可用的画面类型改为卡片 ----------

import re


def _formula_beat(prompt):
    """返回含原文公式的段落 ID 与公式文本（参考文章中存在）。"""
    for bid in _beats_from_prompt(prompt):
        block = prompt.split(f"[{bid}]", 1)[1].split("\n[", 1)[0]
        m = re.search(r"原文公式：(.+)", block)
        if m:
            return bid, m.group(1).strip()
    return None, None


def test_formula_explanation_may_use_symbols_from_the_formula(ready):
    seen = {}

    def responder(prompt, n=None):
        bid, formula = _formula_beat(prompt)
        seen["bid"] = bid
        data = json.loads(_good_plan(prompt, n))
        word = re.search(r"[A-Za-z]{4,}", formula or "")
        for s in data["scenes"]:
            if s["beat"] == bid:
                s["type"], s["formula_explanation"] = "formula", [word.group(0) if word else "增长"]
                s.pop("items", None)
        return json.dumps(data, ensure_ascii=False)

    llm = StoryLLM(responder)
    sb = _run(ready, "P04", llm)
    assert seen.get("bid"), "参考文章的 P04 应含原文公式"
    assert sb["errors"] == []
    assert llm.calls == 1                       # 公式符号来自原文，不应被当作编造而重试


def test_disallowed_type_falls_back_to_keypoint_without_retry(ready):
    def responder(prompt, n=None):
        data = json.loads(_good_plan(prompt, n))
        for s in data["scenes"]:
            if s["beat"] == "P03.b04":
                s["type"], s["items"] = "diagram", []     # 本段只允许 keypoint / illustration
        return json.dumps(data, ensure_ascii=False)

    llm = StoryLLM(responder)
    sb = _run(ready, "P03", llm)
    scene = next(s for s in sb["scenes"] if s["beat"] == "P03.b04")
    assert scene["type"] == "keypoint" and scene["fallback_from"] == "diagram"
    assert scene["items"] and all(item.strip() for item in scene["items"])
    assert llm.calls == 1
    assert any("已改为卡片（keypoint）" in m for m in _msgs(ready))
