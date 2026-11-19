"""R15 第二阶段：分镜过程日志（调用、耗时、全部校验错误、缓存决策、结果）；插图可无条目。

第一期改造后协议为：章级策划 1 次 + 分窗落镜（每窗 1 次，出错只重做该窗口）。
"""
import re
from collections import Counter

import pytest

from article_to_video import joblog
from article_to_video.planning.errors import PlanError
from article_to_video.visual import storyboard as sbm
from helpers_p4 import scripted_project
from helpers_shots import ShotLLM, default_shots, shot, shots_json


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


def _last_sid(block, bid):
    return re.findall(r"\[(" + re.escape(bid) + r"\.s\d+)\]", block)[-1]


def _default_json(prompt):
    return shots_json(default_shots(prompt))


def test_illustration_without_items_is_accepted(ready):
    def no_text_illustration(prompt, wid, n):
        def special(bid, block):
            if bid != "P01.b02":
                return None
            return [shot(f"{bid}.s01", _last_sid(block, bid), "illustration", image_subject="蒸汽机与齿轮")]
        return shots_json(default_shots(prompt, special))

    sb = _run(ready, "P01", ShotLLM(win_fn=no_text_illustration))
    assert sb["errors"] == []
    ill = [s for s in sb["scenes"] if s["type"] == "illustration"]
    assert ill and ill[0]["items"] == []


def test_keypoint_without_items_is_still_rejected(ready):
    def empty_keypoint(prompt, wid, n):
        def special(bid, block):
            if bid != "P01.b01" or n > 1:
                return None
            return [shot(f"{bid}.s01", _last_sid(block, bid), "keypoint", items=[])]
        return shots_json(default_shots(prompt, special))

    llm = ShotLLM(win_fn=empty_keypoint)
    _run(ready, "P01", llm)
    msgs = _msgs(ready)
    assert any("keypoint 缺少条目" in m for m in msgs)
    assert llm.win_calls.count("W01") == 2


def test_each_call_logs_prompt_size_time_and_outcome(ready):
    _run(ready, "P02", ShotLLM())
    msgs = _msgs(ready)
    assert any(m.startswith("P02：输入 ") and "句" in m and "字" in m and "窗" in m for m in msgs)
    assert any("可选画面" in m for m in msgs)
    assert any("章级策划第 1/3 次" in m for m in msgs)
    assert any("调用模型落镜第 1/3 次，提示词" in m for m in msgs)
    assert any("模型返回" in m and "用时" in m for m in msgs)
    assert any("模型分镜通过校验：" in m and "×" in m and "模型调用" in m for m in msgs)
    assert any("分镜已写入" in m and "校验通过" in m for m in msgs)


def test_retry_logs_every_validation_error_not_just_the_first(ready):
    def broken(prompt, wid, n):
        if n > 1:
            return _default_json(prompt)

        def special(bid, block):
            # 每段都坏（数字不在原文），应记录多条
            return [shot(sid, sid, "keypoint", items=["999 编造"])
                    for sid in re.findall(r"\[(" + re.escape(bid) + r"\.s\d+)\]", block)]
        return shots_json(default_shots(prompt, special))

    _run(ready, "P03", ShotLLM(win_fn=broken))
    msgs = _msgs(ready)
    bullets = [m for m in msgs if m.startswith("  · ")]
    assert len(bullets) >= 2                    # 多于一条错误都被记录
    assert any("校验发现" in m and "只重做本窗口" in m for m in msgs)
    assert any("第 2/3 次" in m and "上次问题" in m for m in msgs)


def test_invalid_json_is_logged_with_reason(ready):
    llm = ShotLLM(win_fn=lambda p, wid, n: "不是 JSON" if n == 1 else _default_json(p))
    _run(ready, "P01", llm)
    msgs = _msgs(ready)
    assert any("不是有效 JSON" in m and "只重做本窗口" in m for m in msgs)


def test_persistent_failure_logs_final_error(ready):
    llm = ShotLLM(win_fn=lambda p, wid, n: "坏输出")
    with pytest.raises(PlanError):
        _run(ready, "P01", llm)
    msgs = _msgs(ready)
    assert any("仍不合格" in m for m in msgs)
    assert sbm.load_storyboard(ready, "P01") is None


def test_cache_decisions_are_logged(ready):
    _run(ready, "P01", ShotLLM())                              # 首次：尚无分镜
    assert any("尚无分镜，开始生成" in m for m in _msgs(ready))
    before = len(_msgs(ready))
    llm = ShotLLM()
    _run(ready, "P01", llm)                                    # 输入未变：复用，不调用模型
    assert llm.calls == 0
    assert any("输入未变" in m and "不调用模型" in m for m in _msgs(ready)[before:])
    with joblog.bind(ready, "storyboard"):
        sbm.plan_storyboard(ready, "P01", engine="rules")      # 引擎变化：旧分镜作废
    assert any("输入已变化" in m and "重新生成" in m for m in _msgs(ready))
    _run(ready, "P01", ShotLLM(), force=True)                  # 强制
    assert any("强制重新生成" in m for m in _msgs(ready))


# ---------- 第三轮修正：公式符号可用、不可用的画面类型改为卡片 ----------

def test_formula_explanation_may_use_symbols_from_the_formula(ready):
    seen = {}

    def responder(prompt, wid, n):
        def special(bid, block):
            m = re.search(r"原文公式：(.+)", block)
            if not m:
                return None
            seen["bid"] = bid
            word = re.search(r"[A-Za-z]{4,}", m.group(1))
            return [shot(f"{bid}.s01", _last_sid(block, bid), "formula",
                         formula_explanation=[word.group(0) if word else "增长"])]
        return shots_json(default_shots(prompt, special))

    llm = ShotLLM(win_fn=responder)
    sb = _run(ready, "P04", llm)
    assert seen.get("bid"), "参考文章的 P04 应含原文公式"
    assert sb["errors"] == []
    assert max(Counter(llm.win_calls).values()) == 1   # 公式符号来自原文，不应被当作编造而重试


def test_disallowed_type_falls_back_to_keypoint_without_retry(ready):
    def responder(prompt, wid, n):
        def special(bid, block):
            if bid != "P03.b04":
                return None
            return [shot(f"{bid}.s01", _last_sid(block, bid), "flow")]   # 本段不允许流程
        return shots_json(default_shots(prompt, special))

    llm = ShotLLM(win_fn=responder)
    sb = _run(ready, "P03", llm)
    scene = next(s for s in sb["scenes"] if s["beat"] == "P03.b04")
    assert scene["type"] == "keypoint" and scene["fallback_from"] == "flow"
    assert scene["items"] and all(item.strip() for item in scene["items"])
    assert max(Counter(llm.win_calls).values()) == 1
    assert any("已改为卡片" in m for m in _msgs(ready))
