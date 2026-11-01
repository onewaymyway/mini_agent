"""第 1 章剧本失败的回归测试（计划文档 14.13）。

现象：剧本校验在“第 1 行出现在任何场次标题之前：{”失败。
根因：剧本调用沿用了要求“只输出 JSON 对象”的 system 提示，模型把剧本包进 {"script": "..."}。
修复：剧本专用 system 提示；JSON/代码块包装自动解包；原文只发送一次；篇幅要求前置；
      每次文本模型调用的完整输入/输出留痕并在界面展示；校验失败可按选项带反馈重试（默认关闭）。
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import joblog, llm_trace, prompts, tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_script import ScriptError, _clean_script, check_script, run_script

from test_chapter_pipeline import CHAPTER_TEXT, SCRIPT

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class RecLLM:
    """按顺序返回预设输出，并记录每次调用的 prompt 与 system。"""
    name = "rec"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def ask(self, prompt, system=""):
        self.calls.append({"prompt": prompt, "system": system})
        out = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    return p


# ---------------------------------------------------------------- 根因：prompt 与解包

def test_script_call_does_not_use_json_only_system_prompt(proj):
    llm = RecLLM(SCRIPT)
    run_script(proj, 1, llm)
    system = llm.calls[0]["system"]
    assert system == prompts.SYSTEM_SCRIPT
    assert "JSON 对象" not in system and "不要使用 JSON" in system


def test_script_prompt_sends_source_once_and_puts_budget_first():
    text = "独一无二的原文句子。" * 5
    p = prompts.script_prompt(1, "测试", text, 756)
    assert p.count(text) == 1          # 不再在 RULE_INPUT 里重复一份
    assert p.index("篇幅要求") < p.index("剧本格式") < p.index("原文：")
    assert "756" in p and "不要 JSON" in p and "### 场次 1｜" in p


def test_rule_llm_still_works_with_deduplicated_prompt():
    out = RuleTextLLM().ask(prompts.script_prompt(1, "测试", CHAPTER_TEXT, 200), system=prompts.SYSTEM_SCRIPT)
    assert out.startswith("### 场次 1｜")


@pytest.mark.parametrize("wrap", [
    lambda s: json.dumps({"script": s}, ensure_ascii=False),
    lambda s: "```json\n" + json.dumps({"script": s}, ensure_ascii=False) + "\n```",
    lambda s: json.dumps({"剧本": s}, ensure_ascii=False),
    lambda s: json.dumps({"anything": s}, ensure_ascii=False),
    lambda s: json.dumps(s, ensure_ascii=False),
    lambda s: json.dumps(s.splitlines(), ensure_ascii=False),
    lambda s: "```\n" + s + "\n```",
])
def test_clean_script_unwraps_json_and_fences(wrap):
    msgs = []
    out = _clean_script(wrap(SCRIPT), log=msgs.append)
    assert out.startswith("### 场次 1｜") and "旁白：铁城的清晨很安静。" in out
    assert not out.lstrip().startswith(("{", "[", '"', "`"))


def test_clean_script_leaves_plain_text_and_non_json_alone():
    msgs = []
    assert _clean_script(SCRIPT, log=msgs.append) == SCRIPT and msgs == []
    assert _clean_script("{这不是 JSON").startswith("{这不是 JSON")   # 取不出就原样交给校验，由校验报错
    with pytest.raises(ScriptError):
        _clean_script("   ")


def test_json_wrapped_script_now_passes_and_warns(proj):
    """复现用户的现场：模型返回 {"script": "..."}。"""
    llm = RecLLM(json.dumps({"script": SCRIPT}, ensure_ascii=False))
    r = run_script(proj, 1, llm)
    assert r["status"] == "pass", r
    assert (proj / "chapters/001/script.md").read_text(encoding="utf-8").startswith("### 场次 1｜")
    msgs = [x for x in joblog.tail(proj, scopes=["chapter"]) if x["level"] == "warn"]
    assert any("包进了 JSON" in x["message"] for x in msgs)


def test_first_line_error_tells_where_to_look():
    res = check_script("下面是剧本：\n### 场次 1｜a·b（内景）\n旁白：x", "x", [])
    assert res["status"] == "fail"
    assert "模型调用记录" in res["issues"][0]["message"]


def test_failed_stage_message_carries_first_error(proj):
    long = "### 场次 1｜a·b（内景）\n旁白：" + "字" * 3000 + "\n"
    r = run_script(proj, 1, RecLLM(long))
    assert r["status"] == "fail"
    err = json.loads((proj / "chapters/001/state.json").read_text(encoding="utf-8"))["stages"]["script"]["error"]
    assert err.startswith("剧本校验未通过：") and "压缩" in err


# ---------------------------------------------------------------- 留痕

def test_every_text_call_is_traced_with_full_input_and_output(proj):
    llm = RecLLM(json.dumps({"script": SCRIPT}, ensure_ascii=False))
    run_script(proj, 1, llm)
    rows = llm_trace.tail(proj, chapter=1)
    assert len(rows) == 1
    r = rows[0]
    assert r["step"] == "C-1" and r["ok"] is True and r["provider"] == "rec"
    assert r["prompt"] == llm.calls[0]["prompt"] and r["system"] == prompts.SYSTEM_SCRIPT
    assert r["response"].startswith('{"script"')          # 原始输出，未经解包
    # 实时日志里能直接看到返回的开头
    msgs = [x["message"] for x in joblog.tail(proj, scopes=["chapter"])]
    assert any("收到" in m and '返回开头：{"script"' in m for m in msgs), msgs


def test_trace_records_exceptions_and_reraises(proj):
    with pytest.raises(RuntimeError):
        run_script(proj, 1, RecLLM(RuntimeError("接口 401")))
    r = llm_trace.tail(proj, chapter=1)[0]
    assert r["ok"] is False and "401" in r["error"]
    assert any(x["level"] == "error" and "401" in x["message"] for x in joblog.tail(proj, scopes=["chapter"]))


def test_trace_filters_and_tolerates_partial_last_line(proj):
    llm_trace.ask_traced(proj, RecLLM("a"), "p1", scope="assets", label="g2", step="G-2")
    llm_trace.ask_traced(proj, RecLLM("b"), "p2", scope="chapter", label="c1", step="C-1", chapter=1)
    llm_trace.ask_traced(proj, RecLLM("c"), "p3", scope="chapter", label="c1", step="C-1", chapter=2)
    with (proj / llm_trace.TRACE_FILE).open("a", encoding="utf-8") as f:
        f.write('{"ts": "截断的一行')
    # 夹具里的 G-2 补全同样被留痕；本测试新增的三条按新到旧排在最前
    assert [r["prompt"] for r in llm_trace.tail(proj)][:3] == ["p3", "p2", "p1"]
    assert [r["prompt"] for r in llm_trace.tail(proj, chapter=1)] == ["p2"]
    assert llm_trace.tail(proj, scopes=["assets"])[0]["prompt"] == "p1"
    assert {r["step"] for r in llm_trace.tail(proj, scopes=["assets"], limit=50)} == {"G-2"}
    assert tool_api.llm_trace(str(proj), chapter=2)["data"][0]["response"] == "c"


def test_oversized_fields_are_truncated(proj):
    llm_trace.ask_traced(proj, RecLLM("x" * (llm_trace.MAX_FIELD_CHARS + 500)), "p", scope="chapter",
                         label="big", step="C-1", chapter=1)
    resp = llm_trace.tail(proj)[0]["response"]
    assert len(resp) < llm_trace.MAX_FIELD_CHARS + 100 and "已截断" in resp


# ---------------------------------------------------------------- 重试（opt-in）

LONG = "### 场次 1｜a·b（内景）\n旁白：" + "铁城的清晨很安静。" * 400 + "\n"


def test_no_retry_by_default(proj):
    llm = RecLLM(LONG, SCRIPT)
    r = run_script(proj, 1, llm)
    assert r["status"] == "fail" and len(llm.calls) == 1


def test_retry_feeds_back_issues_when_enabled(proj):
    assert tool_api.set_script_retries(str(proj), 1)["ok"]
    llm = RecLLM(LONG, SCRIPT)
    r = run_script(proj, 1, llm)
    assert r["status"] == "pass" and len(llm.calls) == 2
    fix = llm.calls[1]["prompt"]
    assert "TASK=script_fix" in fix and "压缩" in fix and LONG[:30] in fix and "新人从阴影里走出来：“我也要去。”" in fix
    review = (proj / "chapters/001/script_review.yaml").read_text(encoding="utf-8")
    assert "attempts: 2" in review
    assert len(llm_trace.tail(proj, chapter=1)) == 2


def test_retry_stops_at_limit(proj):
    tool_api.set_script_retries(str(proj), 2)
    llm = RecLLM(LONG)
    r = run_script(proj, 1, llm)
    assert r["status"] == "fail" and len(llm.calls) == 3


def test_set_script_retries_validates_range(proj):
    assert not tool_api.set_script_retries(str(proj), 4)["ok"]
    assert not tool_api.set_script_retries(str(proj), -1)["ok"]
    assert tool_api.set_script_retries(str(proj), 0)["ok"]


# ---------------------------------------------------------------- 界面

def test_chapter_page_shows_prompt_response_and_review(proj):
    run_script(proj, 1, RecLLM(json.dumps({"script": LONG}, ensure_ascii=False)))
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("🎬 章节生产").run()
    at.text_input(key="chap_root").input(str(proj.parent)).run()
    assert not at.exception, at.exception
    text = " ".join(str(getattr(e, "value", "")) for e in at.text_area)
    assert '{"script"' in text                       # 原始返回内容
    assert "TASK=script" in text and "篇幅要求" in text   # 发送的提示词
    assert prompts.SYSTEM_SCRIPT in text             # 系统提示
    errs = " ".join(e.value for e in at.error)
    assert "剧本校验" in " ".join(m.value for m in at.markdown) and "未通过" in errs
    assert any("压缩" in str(r) for df in at.dataframe for r in [df.value.to_dict("records")])
