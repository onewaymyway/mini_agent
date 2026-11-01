"""C-3 镜头规划校验失败时自动修正，而不是直接让整章失败。

现场：模型把剧本里一句长台词删减、拼接后当成对白，校验报
“对白未在剧本中找到（疑似改写）：盖亚提醒您：今日空气质量指数正常，配给配额已调整，请前往配给……”，整章 failed。
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import joblog, llm_trace, prompts, tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import collect_shot_issues, run_shots
from novel_to_video.steps.global_visual import StepError

from test_chapter_pipeline import CHAPTER_TEXT, SCRIPT, SHOTS

APP = str(Path(__file__).resolve().parent.parent / "app.py")

GOOD = json.dumps(SHOTS, ensure_ascii=False)


def _bad(text="我们快走。"):
    """对白不是剧本里的连续原文（删减过）。"""
    return json.dumps({"shots": [{"scene_no": 1, "location_id": "loc_01", "character_ids": [],
        "blocks": [{"type": "narration", "text": "铁城的清晨很安静，穹顶遮住了天空，街道上空无一人。"},
                   {"type": "dialogue", "text": text, "speaker_id": None, "speaker_name": "林远"}],
        "visual_hint_zh": "街道", "visual_hint_en": "street"}]}, ensure_ascii=False)


class Seq:
    """按顺序返回预设输出（最后一个重复使用），记录每次的提示词。"""
    name = "seq"

    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
        if "TASK=entity" in prompt:
            return json.dumps({"gender": "male", "age_range": "youth", "description_zh": "新出现的年轻人",
                               "visual_anchor_en": "young adult man, dark jacket, [test]",
                               "voice_profile": "低沉，语速慢"}, ensure_ascii=False)
        if "TASK=script" in prompt and "TASK=shots" not in prompt:
            return SCRIPT
        return self.outs[min(len(self.calls) - 1, len(self.outs) - 1)]


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    run_script(p, 1, Seq())
    return p


def _chars(p):
    return json.loads((p / "global/characters.json").read_text(encoding="utf-8"))["characters"]


def _review(p):
    import yaml
    return yaml.safe_load((cio.chapter_dir(p, 1) / "shots_review.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 自动修正

def test_bad_dialogue_is_fixed_automatically(proj):
    llm = Seq(_bad(), GOOD)
    r = run_shots(proj, 1, llm)
    assert r["skipped"] is False and r["attempts"] == 2 and len(llm.calls) == 2
    assert cio.load_shots(proj, 1)["attempts"] == 2
    rv = _review(proj)
    assert rv["status"] == "pass" and rv["attempts"] == 2
    fix = llm.calls[1]
    assert "TASK=shots_fix" in fix
    assert "不是剧本中的连续原文" in fix and "我们快走。" in fix
    assert "剧本里最接近的原句是“林远：我们走。”" in fix            # 告诉模型该照抄哪里
    assert "上一次的输出" in fix and '"shots"' in fix                  # 带上它自己上次的输出
    assert cio.chapter_dir(proj, 1).joinpath("shots.yaml").is_file()


def test_every_attempt_is_traced_and_logged_in_full(proj):
    run_shots(proj, 1, Seq(_bad(), GOOD))
    rows = llm_trace.tail(proj, chapter=1, steps=["C-3"])
    assert len(rows) == 2 and "第 1 次修正" in rows[0]["label"]
    warn = [x["message"] for x in joblog.tail(proj, scopes=["chapter"]) if x["level"] == "warn"]
    assert any("自动把问题反馈给模型修正（第 1/2 次）" in m and "我们快走。" in m for m in warn), warn


def test_gives_up_after_retries_and_reports_all_issues(proj):
    llm = Seq(_bad())
    with pytest.raises(StepError) as e:
        run_shots(proj, 1, llm)
    assert len(llm.calls) == 3                                          # 1 次规划 + 2 次修正
    assert "已自动修正 2 次仍未解决" in str(e.value) and "我们快走。" in str(e.value)
    rv = _review(proj)
    assert rv["status"] == "fail" and rv["attempts"] == 3 and rv["issues"]
    assert not cio.load_shots(proj, 1).get("shots")                     # 失败不留半成品


def test_can_be_turned_off(proj):
    assert tool_api.set_shots_retries(str(proj), 0)["ok"]
    llm = Seq(_bad(), GOOD)
    with pytest.raises(StepError) as e:
        run_shots(proj, 1, llm)
    assert len(llm.calls) == 1 and "自动修正" not in str(e.value)


def test_legacy_project_uses_default(proj):
    p = proj / "project.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.pop("shots_max_retries", None)
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    assert run_shots(proj, 1, Seq(_bad(), GOOD))["attempts"] == 2


def test_invalid_json_is_also_retried(proj):
    llm = Seq("好的，下面是镜头规划：", GOOD)
    r = run_shots(proj, 1, llm)
    assert r["attempts"] == 2
    assert "没有 JSON 对象" in llm.calls[1] or "JSON" in llm.calls[1]


def test_pipeline_continues_past_c3_after_autofix(proj):
    r = cp.run_chapter(proj, 1, text_llm=Seq(_bad(), GOOD), image=StubImageProvider(),
                       video=StubVideoProvider(), tts=SilenceTTS())
    assert r["status"] == "waiting_assets", r                           # 越过 C-3，停在补建角色确认
    msgs = [x["message"] for x in joblog.tail(proj, scopes=["chapter"])]
    assert any("C-3 校验未通过" in m for m in msgs) and any("C-3 调用文本模型规划镜头（第 1 次修正）" in m for m in msgs)


# ---------------------------------------------------------------- 问题收集

def test_collect_all_issues_not_just_first(proj):
    raw = [{"blocks": [{"type": "dialogue", "text": "我们快走。", "speaker_name": "林远"},
                       {"type": "dialogue", "text": "完全无关的话。", "speaker_name": "影子"},
                       {"type": "dialogue", "text": "等一下。"},                     # 缺说话人
                       {"type": "monologue", "text": "x"}]}]                          # 非法类型
    issues = [i["message"] for i in collect_shot_issues(raw, SCRIPT, _chars(proj))]
    assert len(issues) == 4
    assert "最接近的原句是“林远：我们走。”" in issues[0]
    assert "最接近" not in issues[1]                                                  # 完全无关则不乱给建议
    assert "缺少说话人" in issues[2] and "非法" in issues[3]
    assert collect_shot_issues([], SCRIPT, []) == [{"message": "没有返回任何镜头（shots 为空）"}]
    assert collect_shot_issues(json.loads(GOOD)["shots"], SCRIPT, _chars(proj)) == []


def test_prompt_demands_contiguous_original_text(proj):
    llm = Seq(GOOD)
    run_shots(proj, 1, llm)
    p = llm.calls[0]
    assert "连续原文" in p and "不能删减中间的字" in p and "拆成多个镜头" in p


# ---------------------------------------------------------------- 界面

def _open(proj, page="🎬 章节生产", root_key="chap_root"):
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value(page).run()
    at.text_input(key=root_key).input(str(proj.parent)).run()
    assert not at.exception, at.exception
    return at


def test_chapter_page_shows_unresolved_issues(proj):
    with pytest.raises(StepError):
        run_shots(proj, 1, Seq(_bad()))
    at = _open(proj)
    assert any("镜头规划校验" in m.value for m in at.markdown)
    assert any("未通过" in e.value and "3 次" in e.value for e in at.error)
    rows = [r for df in at.dataframe for r in df.value.to_dict("records")]
    assert any("我们快走。" in r.get("问题", "") for r in rows)


def test_chapter_page_notes_when_it_passed_after_autofix(proj):
    run_shots(proj, 1, Seq(_bad(), GOOD))
    at = _open(proj)
    assert any("自动修正 1 次后通过" in s.value for s in at.success)


def test_gear_page_sets_both_retry_counts(proj):
    at = _open(proj, "🚦 挡位与队列", "gear_root")
    at.number_input(key="gear_script_retry").set_value(1).run()
    at.number_input(key="gear_shots_retry").set_value(3).run()
    at.button(key="gear_retry_save").click().run()
    assert not at.exception, at.exception
    cfg = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    assert cfg["script_max_retries"] == 1 and cfg["shots_max_retries"] == 3
