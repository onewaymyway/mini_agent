import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import shot_review as sr
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_script import check_script, parse_script

CHAPTER_TEXT = (
    "# 第1章 测试章1\n\n"
    "铁城的清晨很安静。\n\n"
    "林远低声说：“我们走。”\n\n"
    "影子回答：“等一下。”\n\n"
    "新人从阴影里走出来：“我也要去。”\n"
)

SCRIPT = """### 场次 1｜铁城·清晨（外景）
旁白：铁城的清晨很安静。
林远：我们走。
影子：等一下。
新人：我也要去。
"""

SHOTS = {"shots": [
    {"scene_no": 1, "location_id": "loc_01", "character_ids": [],
     "blocks": [
         {"type": "narration", "text": "铁城的清晨很安静，穹顶遮住了天空，街道上空无一人。", "speaker_id": None, "speaker_name": None},
         {"type": "dialogue", "text": "我们走。", "speaker_id": None, "speaker_name": "林远"},
     ],
     "visual_hint_zh": "清晨街道", "visual_hint_en": "dawn street, wide shot"},
    {"scene_no": 1, "location_id": "loc_01", "character_ids": [],
     "blocks": [
         {"type": "narration", "text": "一个陌生的身影从巷口的阴影里走了出来，停在他们面前。", "speaker_id": None, "speaker_name": None},
         {"type": "dialogue", "text": "我也要去。", "speaker_id": None, "speaker_name": "新人"},
     ],
     "visual_hint_zh": "新人走出阴影", "visual_hint_en": "figure steps out of shadow, medium shot"},
]}


class ChapterLLM:
    """按 TASK 分派的假 LLM：剧本、镜头、补建角色。"""
    name = "fake"

    def __init__(self, shots=None, script=SCRIPT):
        self.shots = shots if shots is not None else SHOTS
        self.script = script

    def ask(self, prompt, system=""):
        if "TASK=script" in prompt:
            return self.script
        if "TASK=shots" in prompt:
            return json.dumps(self.shots, ensure_ascii=False)
        if "TASK=entity" in prompt:
            return json.dumps({"gender": "male", "age_range": "youth",
                               "description_zh": "新出现的年轻人",
                               "visual_anchor_en": "young adult man, dark jacket, [test]",
                               "voice_profile": "低沉，语速慢"}, ensure_ascii=False)
        return RuleTextLLM().ask(prompt, system)


@pytest.fixture
def project(tmp_path):
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    proj = Path(res["data"]["project_dir"])
    # 先完成全书级素材（G-2 规则兜底 + G-3 占位定妆图）
    gv.visual_text_pass(proj, RuleTextLLM())
    gv.portrait_pass(proj, StubImageProvider())
    return proj


def _run(proj, llm):
    return cp.run_chapter(proj, 1, text_llm=llm, image=StubImageProvider(),
                          video=StubVideoProvider(), tts=SilenceTTS())


def test_parse_and_verbatim_check():
    lines = parse_script(SCRIPT)
    assert [l["type"] for l in lines[0]["lines"]] == ["narration", "dialogue", "dialogue", "dialogue"]
    ok = check_script(SCRIPT, CHAPTER_TEXT, known_names=["林远", "影子"])
    assert ok["status"] == "pass"
    assert any("新人" in i["message"] for i in ok["issues"])  # 库中没有 → 提示，不失败
    fake = SCRIPT.replace("我们走", "我们快走")
    bad = check_script(fake, CHAPTER_TEXT, known_names=[])
    assert bad["status"] == "fail" and any("未在原文中找到" in i["message"] for i in bad["issues"])


def test_length_budget_check():
    long_script = "### 场次 1｜a·b（内景）\n" + "旁白：" + "字" * 1000 + "\n"
    res = check_script(long_script, "字" * 1000, [], target_chars=300)
    assert res["status"] == "fail" and any("压缩" in i["message"] for i in res["issues"])


def test_full_chapter_flow_with_new_speaker_gate(project):
    # 第一次运行：镜头里的“新人”被补建，等待文本确认
    r = _run(project, ChapterLLM())
    assert r["status"] == "waiting_assets", r
    chars = json.loads((project / "global/characters.json").read_text(encoding="utf-8"))["characters"]
    new = next(c for c in chars if c.get("auto_added_chapter") == 1)
    assert new["names"] == ["新人"] and new["source"] == "auto_added"
    assert new["gender"] == "male" and new["visual_anchor_source"] == "fake"

    gv.approve(project, "character", new["id"], "text")
    r = _run(project, ChapterLLM())  # 生成补建角色定妆图，等待其通过
    assert r["status"] == "waiting_assets", r
    gv.approve(project, "character", new["id"], "portrait")

    r = _run(project, ChapterLLM())
    assert r["status"] == "waiting_review", r
    view = cp.chapter_view(project, 1)
    assert view["counts"]["pending"] == 2 and view["script"]

    # 补建角色的台词已回填到对应 id
    shots = cio.load_shots(project, 1)
    dlg = [b for sh in shots["shots"] for b in sh["blocks"] if b["type"] == "dialogue"]
    assert all(b["speaker_id"] for b in dlg)

    for sh in shots["shots"]:
        sr.approve(project, 1, sh["id"])
    r = _run(project, ChapterLLM())
    assert r["status"] == "done", r
    out = project / "chapters/001/chapter_001.mp4"
    assert out.is_file() and r["duration"] > 4


def test_reject_regenerates_and_edit_makes_approval_stale(project):
    # 只含已有角色的镜头，不触发补建
    simple = {"shots": [SHOTS["shots"][0]]}
    r = _run(project, ChapterLLM(shots=simple))
    assert r["status"] == "waiting_review", r
    sr.approve(project, 1, "s01")
    assert cp.chapter_view(project, 1)["shots"][0]["state"] == "approved"

    sr.edit(project, 1, "s01", visual_hint_en="dawn street, close-up, empty")
    r = _run(project, ChapterLLM(shots=simple))
    assert r["status"] == "waiting_review"
    assert cp.chapter_view(project, 1)["shots"][0]["state"] == "stale"  # 已通过但内容变了，需重新审核

    sr.reject(project, 1, "s01", "镜头太远")
    assert cp.chapter_view(project, 1)["shots"][0]["state"] == "missing"
    r = _run(project, ChapterLLM(shots=simple))
    assert cp.chapter_view(project, 1)["shots"][0]["state"] == "pending"


def test_dialogue_edit_must_stay_verbatim(project):
    simple = {"shots": [SHOTS["shots"][0]]}
    _run(project, ChapterLLM(shots=simple))
    with pytest.raises(ValueError):
        sr.edit(project, 1, "s01", block_texts={1: "我们快走吧。"})
    sr.edit(project, 1, "s01", block_texts={0: "铁城的清晨很安静。"})  # 旁白可改


def test_fabricated_dialogue_fails_chapter(project):
    bad = {"shots": [{"scene_no": 1, "location_id": "loc_01", "character_ids": [], "blocks": [
        {"type": "dialogue", "text": "这句话原文没有", "speaker_id": "char_002", "speaker_name": None}],
        "visual_hint_zh": "x", "visual_hint_en": "x"}]}
    r = _run(project, ChapterLLM(shots=bad))
    # 模型始终编造对白：自动修正用尽后仍然失败，并说明修正过几次
    assert r["status"] == "failed" and "不是剧本中的连续原文" in r["message"] and "已自动修正 5 次" in r["message"]


def test_missing_location_portrait_blocks_clips(project):
    loc = json.loads((project / "global/locations.json").read_text(encoding="utf-8"))
    loc["locations"][0]["asset_path"] = None
    (project / "global/locations.json").write_text(json.dumps(loc, ensure_ascii=False), encoding="utf-8")
    simple = {"shots": [SHOTS["shots"][0]]}
    r = _run(project, ChapterLLM(shots=simple))
    assert r["status"] == "waiting_assets" and "地点 loc_01" in r["message"]


def test_rerun_is_idempotent_for_stages(project):
    simple = {"shots": [SHOTS["shots"][0]]}
    _run(project, ChapterLLM(shots=simple))
    state = cio.load_state(project, 1)["stages"]
    assert state["script"]["status"] == "done" and state["clips"]["status"] == "done"
    clip = project / "chapters/001/clips/s01.mp4"
    mtime = clip.stat().st_mtime_ns
    _run(project, ChapterLLM(shots=simple))
    assert clip.stat().st_mtime_ns == mtime  # 未变化则不重新生成


def test_tool_api_chapter_overview(project, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    r = tool_api.chapters_overview(str(project))
    assert r["ok"] and r["data"][0]["no"] == 1
