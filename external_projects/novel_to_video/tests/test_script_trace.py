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
    # 实时日志打印完整的输入与输出，不截断
    msgs = [x["message"] for x in joblog.tail(proj, scopes=["chapter"])]
    assert any("发送给模型的完整内容" in m and llm.calls[0]["prompt"] in m
               and prompts.SYSTEM_SCRIPT in m for m in msgs), msgs
    assert any("模型返回的完整内容" in m and r["response"] in m for m in msgs), msgs


def test_live_log_prints_long_io_without_truncation(proj):
    big = "### 场次 1｜a·b（内景）\n" + "旁白：" + "长" * 5000 + "\n"
    llm_trace.ask_traced(proj, RecLLM(big), "问" * 4000, system="s", scope="chapter", label="长调用",
                         step="C-1", chapter=1)
    rows = joblog.tail(proj, scopes=["chapter"])
    sent = next(r for r in rows if "发送给模型的完整内容" in r["message"])
    got = next(r for r in rows if "模型返回的完整内容" in r["message"])
    assert "问" * 4000 in sent["message"] and big in got["message"]
    shown = joblog.format_row(got)
    assert "长" * 5000 in shown and "    │ ### 场次 1｜" in shown   # 续行加前缀，整段都在


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


# ---------------------------------------------------------------- 校验失败后的自动修正

LONG = "### 场次 1｜a·b（内景）\n旁白：" + "铁城的清晨很安静。" * 400 + "\n"


def test_auto_fix_is_on_by_default_and_feeds_back_issues(proj):
    llm = RecLLM(LONG, SCRIPT)
    r = run_script(proj, 1, llm)
    assert r["status"] == "pass" and len(llm.calls) == 2
    fix = llm.calls[1]["prompt"]
    assert "TASK=script_fix" in fix and "压缩" in fix and LONG[:30] in fix and "新人从阴影里走出来：“我也要去。”" in fix
    review = (proj / "chapters/001/script_review.yaml").read_text(encoding="utf-8")
    assert "attempts: 2" in review
    assert len(llm_trace.tail(proj, chapter=1)) == 2
    msgs = [x["message"] for x in joblog.tail(proj, scopes=["chapter"]) if x["level"] == "warn"]
    assert any("自动把问题反馈给模型修正（第 1/5 次）" in m and "压缩" in m for m in msgs)


def test_auto_fix_can_be_turned_off(proj):
    assert tool_api.set_script_retries(str(proj), 0)["ok"]
    llm = RecLLM(LONG, SCRIPT)
    r = run_script(proj, 1, llm)
    assert r["status"] == "fail" and len(llm.calls) == 1


def test_legacy_project_without_the_option_uses_default(proj):
    p = proj / "project.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.pop("script_max_retries", None)
    cfg.pop("shots_max_retries", None)
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    llm = RecLLM(LONG, SCRIPT)
    assert run_script(proj, 1, llm)["status"] == "pass" and len(llm.calls) == 2


def test_auto_fix_stops_at_limit(proj):
    llm = RecLLM(LONG)
    r = run_script(proj, 1, llm)
    assert r["status"] == "fail" and len(llm.calls) == 6          # 1 次生成 + 默认 5 次修正
    tool_api.set_script_retries(str(proj), 1)
    llm = RecLLM(LONG)
    run_script(proj, 1, llm, force=True)
    assert len(llm.calls) == 2


def test_set_retries_validates_range(proj):
    for fn in (tool_api.set_script_retries, tool_api.set_shots_retries):
        assert not fn(str(proj), 6)["ok"] and not fn(str(proj), -1)["ok"]
        assert fn(str(proj), 0)["ok"]


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


# ---------------------------------------------------------------- 补建角色：提示合并，并在章节页直接操作

DUP_SCRIPT = """### 场次 1｜铁城·清晨（外景）
旁白：铁城的清晨很安静。
新人：我也要去。
新人：我也要去。
路人：等一下。
"""
DUP_SOURCE = "铁城的清晨很安静。新人：“我也要去。”路人：“等一下。”"


def test_unknown_speaker_warning_is_merged_and_says_what_to_do():
    res = check_script(DUP_SCRIPT, DUP_SOURCE, ["林远"])
    warns = [i["message"] for i in res["issues"] if i["level"] == "warning"]
    assert len(warns) == 2                       # 新人、路人各一条，不再每句一条
    new = next(w for w in warns if "新人" in w)
    assert "2 处" in new and "首见第 3 行" in new and "镜头规划" in new and "补建角色" in new
    assert res["status"] == "pass"               # 仍然只是提示


def test_unknown_speakers_helper():
    from novel_to_video.steps.chapter_script import unknown_speakers
    assert unknown_speakers(DUP_SCRIPT, ["林远"]) == [{"name": "新人", "lines": 2}, {"name": "路人", "lines": 1}]
    assert unknown_speakers(DUP_SCRIPT, ["新人", "路人"]) == []
    assert unknown_speakers("不是剧本", []) == []


def _open_chapter_page(proj):
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("🎬 章节生产").run()
    at.text_input(key="chap_root").input(str(proj.parent)).run()
    assert not at.exception, at.exception
    return at


def test_chapter_page_guides_unknown_speakers_then_confirms_in_place(proj):
    from novel_to_video import chapter_pipeline as cp
    from novel_to_video import shot_review  # noqa: F401
    from novel_to_video.providers.tts import SilenceTTS
    from novel_to_video.providers.video import StubVideoProvider
    from test_chapter_pipeline import ChapterLLM

    # 1) 只有剧本、说话人还没起草：页面说明下一步，不给空按钮
    run_script(proj, 1, RecLLM(SCRIPT))
    at = _open_chapter_page(proj)
    info = " ".join(i.value for i in at.info)
    assert "运行第 1 章流水线" in info and "自动起草" in info
    rows = [r for df in at.dataframe for r in df.value.to_dict("records")]
    assert any(r.get("说话人") == "新人" and "还没做镜头规划" in r.get("处理结果", "") for r in rows)

    # 2) 流水线跑到 C-5：补建角色已起草，章节等待确认；页面直接给出操作按钮
    r = cp.run_chapter(proj, 1, text_llm=ChapterLLM(), image=StubImageProvider(),
                       video=StubVideoProvider(), tts=SilenceTTS())
    assert r["status"] == "waiting_assets", r
    at = _open_chapter_page(proj)
    assert any("等待补建角色确认" in w.value for w in at.warning)
    keys = [b.key for b in at.button if b.key]
    ok_text = next(k for k in keys if k.startswith("ok_t_"))
    assert any(k.startswith("ok_p_") for k in keys) and any(k.startswith("save_") for k in keys)

    # 3) 模型起草的性别不算确认：「通过设定」是灰的，页面必须说明原因和做法
    assert at.button(key=ok_text).disabled
    warn = " ".join(w.value for w in at.warning)
    assert "通过设定」暂时点不了" in warn and "gender" in warn and "保存修改" in warn

    # 4) 在本页选性别并保存 → 「通过设定」可点 → 点击生效 → 出现「生成定妆图」，全程无需跳页
    next(sb for sb in at.selectbox if sb.label == "性别").set_value("male").run()
    at.button(key=next(k for k in keys if k.startswith("save_"))).click().run()
    assert not at.exception, at.exception
    at.button(key=ok_text).click().run()
    assert not at.exception, at.exception
    item = tool_api.auto_added_queue(str(proj))["data"][0]
    assert item["text_status"] == "approved"
    assert any((b.key or "").startswith("gen_") for b in at.button)


def test_auto_added_page_has_action_buttons(proj):
    from novel_to_video import chapter_pipeline as cp
    from novel_to_video.providers.tts import SilenceTTS
    from novel_to_video.providers.video import StubVideoProvider
    from test_chapter_pipeline import ChapterLLM
    cp.run_chapter(proj, 1, text_llm=ChapterLLM(), image=StubImageProvider(),
                   video=StubVideoProvider(), tts=SilenceTTS())
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("🧩 补建角色审核").run()
    at.text_input(key="auto_root").input(str(proj.parent)).run()
    assert not at.exception, at.exception
    assert any((b.key or "").startswith("ok_t_") for b in at.button)


# ---------------------------------------------------------------- 说话人处理结果以镜头规划为准

MAP_SCRIPT = """### 场次 1｜铁城·清晨（外景）
旁白：铁城的清晨很安静。
影子：等一下。
新人：我也要去。
"""


def _chars(proj):
    return json.loads((proj / "global/characters.json").read_text(encoding="utf-8"))["characters"]


def test_speaker_resolution_follows_shot_plan_not_exact_names(proj):
    """复现现场：剧本里有两个库外说话人，但镜头规划把其中一个归入了已有角色，只需补建另一个。"""
    from novel_to_video.steps.chapter_script import speaker_resolution
    chars = _chars(proj)
    shots = {"shots": [{"blocks": [
        {"type": "dialogue", "text": "等一下。", "speaker_id": chars[1]["id"], "speaker_name": None},
        {"type": "dialogue", "text": "我也要去。", "speaker_id": None, "speaker_name": "新人"}]}]}
    res = {r["name"]: r for r in speaker_resolution(MAP_SCRIPT, shots, chars)}
    assert res["影子"]["status"] == "library"
    # 脚本里叫“新人”的人，台词被镜头规划归给了 pending 的新人
    assert res["新人"]["status"] == "pending"
    # 没有镜头规划 → unplanned
    assert {r["status"] for r in speaker_resolution(MAP_SCRIPT, None, chars[:1])} == {"unplanned"}
    # 台词归入已有角色 → mapped，并给出角色名
    shots2 = {"shots": [{"blocks": [
        {"type": "dialogue", "text": "我也要去。", "speaker_id": chars[0]["id"], "speaker_name": None}]}]}
    r2 = {r["name"]: r for r in speaker_resolution(MAP_SCRIPT, shots2, chars[:1])}
    assert r2["新人"]["status"] == "mapped" and r2["新人"]["target"] == chars[0]["names"][0]


def test_chapter_page_says_mapped_speaker_needs_no_review(proj):
    from novel_to_video.steps.chapter_shots import run_shots
    from test_chapter_pipeline import ChapterLLM
    chars = _chars(proj)
    run_script(proj, 1, RecLLM(SCRIPT))            # SCRIPT 里有 林远/影子/新人
    shots = {"shots": [{"scene_no": 1, "location_id": "x", "character_ids": [],
                        "blocks": [{"type": "narration", "text": "铁城的清晨很安静。"},
                                   {"type": "dialogue", "text": "我们走。", "speaker_id": chars[0]["id"]},
                                   {"type": "dialogue", "text": "等一下。", "speaker_id": chars[1]["id"]},
                                   # 模型把“新人”归入了已有角色（真实运行中“盖亚播报”就是这样）
                                   {"type": "dialogue", "text": "我也要去。", "speaker_id": chars[0]["id"],
                                    "speaker_name": "新人"}],
                        "visual_hint_zh": "街道", "visual_hint_en": "street"}]}
    run_shots(proj, 1, ChapterLLM(shots=shots))
    assert tool_api.auto_added_queue(str(proj))["data"] == []         # 没有需要审核的补建角色
    at = _open_chapter_page(proj)
    rows = [r for df in at.dataframe for r in df.value.to_dict("records")]
    row = next(r for r in rows if r.get("说话人") == "新人")
    assert "已归入已有角色" in row["处理结果"] and chars[0]["names"][0] in row["处理结果"]
    assert any("没有需要确认的补建角色" in s.value for s in at.success)
    assert not any("运行第 1 章流水线" in i.value and "自动起草" in i.value for i in at.info)
