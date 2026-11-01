"""镜头规划与逐镜审核：shots.yaml 的“重复内容”、出镜角色展示与修改、重新规划。

用户现场（第 1 章 shots.yaml）：
  * 出现 `&id001` / `*id001`：拆分镜头时同一个 character_ids 列表被多个镜头共用，YAML 写成锚点与别名；
  * 同一场次的多个镜头画面描述一模一样：模型一个场次只给了 1 个镜头，代码按时长拆成多个；
  * 某镜头 character_ids 里同一角色出现两次（[A, B, A]）：C-5 回填时没有去重；
  * new_speakers 里留着已经补建过的说话人。
"""

import json
from pathlib import Path

import pytest
from novel_to_video.steps.global_visual import StepError
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import prompts, tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.shot_review import edit as edit_shot
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots, validate_and_build

from test_chapter_pipeline import CHAPTER_TEXT, SCRIPT, ChapterLLM

APP = str(Path(__file__).resolve().parent.parent / "app.py")
LONG_NARR = "铁城的清晨很安静。" * 10          # 90 字，会被切成 3 个口播块 → 3 个镜头


class RecLLM:
    name = "rec"

    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
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
    return p


def _chars(p):
    return json.loads((p / "global/characters.json").read_text(encoding="utf-8"))["characters"]


def _cid(p, name):
    return next(c["id"] for c in _chars(p) if name in c["names"])


def _plan(p, raw_shots):
    run_script(p, 1, RecLLM(SCRIPT))
    return run_shots(p, 1, ChapterLLM(shots={"shots": raw_shots}))


def _long_raw(p):
    """模型按规则自行拆好的三个镜头：每个 4–12 秒、画面各不相同。代码不再替它拆分。"""
    lin = [_cid(p, "林远")]
    return [
        {"scene_no": 1, "location_id": "x", "character_ids": lin,
         "blocks": [{"type": "narration", "text": "铁城的清晨很安静，穹顶遮住了天空。林远睁开眼。"}],
         "visual_hint_zh": "远景：穹顶城市的清晨剪影", "visual_hint_en": "wide shot: dome city at dawn"},
        {"scene_no": 1, "location_id": "x", "character_ids": lin,
         "blocks": [{"type": "narration", "text": "他坐到窗边，防弹玻璃外是转动的监视塔。"}],
         "visual_hint_zh": "中景：林远坐在窗边", "visual_hint_en": "medium shot: a man seated by the window"},
        {"scene_no": 1, "location_id": "x", "character_ids": lin,
         "blocks": [{"type": "narration", "text": "摄像头扫过他的脸，他低下头，没有说话。"}],
         "visual_hint_zh": "特写：监视镜头扫过林远的侧脸", "visual_hint_en": "close-up: surveillance lens sweeping a man's profile"},
    ]


# ---------------------------------------------------------------- shots.yaml 的重复

def test_yaml_has_no_anchors_and_shots_do_not_share_lists(proj):
    _plan(proj, _long_raw(proj))
    text = (cio.chapter_dir(proj, 1) / "shots.yaml").read_text(encoding="utf-8")
    assert "&id" not in text and "*id" not in text
    shots = cio.load_shots(proj, 1)["shots"]
    assert len(shots) == 3
    ids = [id(s["character_ids"]) for s in shots]
    assert len(set(ids)) == 3                       # 内存里也各自独立
    shots[0]["character_ids"].append("char_999")    # 改一处不影响其他镜头
    assert "char_999" not in shots[1]["character_ids"]


def test_overlong_shot_is_rejected_not_silently_split(proj):
    """超过 12 秒的镜头不能被代码悄悄拆开（那样会共用同一画面描述），必须反馈给模型自行拆分。"""
    from novel_to_video.steps.chapter_shots import collect_shot_issues
    long_one = _long_raw(proj)[0]
    long_one["blocks"] = [{"type": "narration", "text": LONG_NARR}]
    issues = collect_shot_issues([long_one], SCRIPT, _chars(proj))
    assert any("超过 12 秒上限" in i["message"] and "拆成 2 个或以上" in i["message"] for i in issues)


def test_overlong_shot_never_becomes_several_shots_with_one_hint(proj):
    from novel_to_video.steps.chapter_shots import collect_shot_issues
    long_one = _long_raw(proj)[0]
    long_one["blocks"] = [{"type": "narration", "text": LONG_NARR}]
    with pytest.raises(StepError):
        _plan(proj, [long_one])


def test_model_split_plan_with_distinct_hints_passes(proj):
    doc = cio.load_shots(proj, 1)
    _plan(proj, _long_raw(proj))
    doc = cio.load_shots(proj, 1)
    assert len(doc["shots"]) == 3
    assert len({s["visual_hint_en"] for s in doc["shots"]}) == 3
    assert doc["plan_warnings"] == []


def test_assets_backfill_dedupes_cast_and_prunes_new_speakers(proj):
    lin, ying = _cid(proj, "林远"), _cid(proj, "影子")
    raw = [{"scene_no": 1, "location_id": "x", "character_ids": [],
            "blocks": [{"type": "dialogue", "text": "我们走。", "speaker_id": lin},
                       {"type": "dialogue", "text": "等一下。", "speaker_id": ying},
                       {"type": "dialogue", "text": "我们走。", "speaker_id": lin},
                       {"type": "dialogue", "text": "我也要去。", "speaker_id": None, "speaker_name": "新人"}],
            "visual_hint_zh": "对话", "visual_hint_en": "talk"}]
    run_script(proj, 1, RecLLM(SCRIPT))
    from novel_to_video.steps.chapter_assets import run_assets
    run_shots(proj, 1, ChapterLLM(shots={"shots": raw}))
    run_assets(proj, 1, ChapterLLM(), StubImageProvider())
    doc = cio.load_shots(proj, 1)
    for sh in doc["shots"]:
        assert len(sh["character_ids"]) == len(set(sh["character_ids"])), sh["character_ids"]
    assert doc["new_speakers"] == []                # 已补建，不再挂在待补建名单里


def test_shots_prompt_demands_short_distinct_shots(proj):
    run_script(proj, 1, RecLLM(SCRIPT))
    llm = RecLLM(json.dumps({"shots": _long_raw(proj)}, ensure_ascii=False))
    run_shots(proj, 1, llm)
    p = llm.calls[0]
    assert "4–12 秒" in p and "整章预计约" in p and "硬要求" in p
    assert "相邻镜头的 visual_hint 必须不同" in p and "character_ids 列出该镜头画面中出现的所有角色" in p
    assert "绝不能把整场戏写成一个镜头" in p


# ---------------------------------------------------------------- 逐镜审核里的角色

def test_chapter_view_lists_cast_location_and_speaker_names(proj):
    lin, ying = _cid(proj, "林远"), _cid(proj, "影子")
    raw = [{"scene_no": 1, "location_id": "x", "character_ids": [],
            "blocks": [{"type": "dialogue", "text": "我们走。", "speaker_id": lin},
                       {"type": "dialogue", "text": "等一下。", "speaker_id": ying}],
            "visual_hint_zh": "林远走向门口", "visual_hint_en": "walk to door"}]
    _plan(proj, raw)
    v = cp.chapter_view(proj, 1)
    sh = v["shots"][0]
    assert [c["name"] for c in sh["characters"]] == ["林远", "影子"]
    assert [b["speaker_label"] for b in sh["blocks"]] == ["林远", "影子"]
    assert sh["location_name"]
    assert sh["missing_mentions"] == []
    assert {c["name"] for c in v["all_characters"]} >= {"林远", "影子"}


def test_missing_mentions_flags_character_in_hint_but_not_in_cast(proj):
    raw = [{"scene_no": 1, "location_id": "x", "character_ids": [],
            "blocks": [{"type": "narration", "text": "铁城的清晨很安静。"}],
            "visual_hint_zh": "林远站在电梯里", "visual_hint_en": "elevator"}]
    _plan(proj, raw)
    assert cp.chapter_view(proj, 1)["shots"][0]["missing_mentions"] == ["林远"]


def test_edit_cast_validates_and_keeps_speakers(proj):
    lin, ying = _cid(proj, "林远"), _cid(proj, "影子")
    raw = [{"scene_no": 1, "location_id": "x", "character_ids": [],
            "blocks": [{"type": "dialogue", "text": "我们走。", "speaker_id": lin}],
            "visual_hint_zh": "走", "visual_hint_en": "go"}]
    _plan(proj, raw)
    assert tool_api.edit_shot(str(proj), 1, "s01", character_ids=[lin, ying, ying])["ok"]
    assert cio.load_shots(proj, 1)["shots"][0]["character_ids"] == [lin, ying]       # 去重
    bad = tool_api.edit_shot(str(proj), 1, "s01", character_ids=["char_999"])
    assert not bad["ok"] and "角色不存在" in bad["error"]["message"]
    bad = tool_api.edit_shot(str(proj), 1, "s01", character_ids=[ying])
    assert not bad["ok"] and "说话人必须保留" in bad["error"]["message"]


# ---------------------------------------------------------------- 重新规划

def test_replan_discards_plan_and_next_run_plans_again(proj):
    _plan(proj, _long_raw(proj))
    assert tool_api.replan_shots(str(proj), 1)["ok"]
    doc = cio.load_shots(proj, 1)
    assert doc["shots"] == [] and "plan_warnings" not in doc
    assert cio.load_state(proj, 1)["stages"]["shots"]["status"] == "stale"
    llm = ChapterLLM(shots={"shots": [{"scene_no": 1, "location_id": "x", "blocks": [
        {"type": "narration", "text": "铁城的清晨很安静。"}], "visual_hint_zh": "a", "visual_hint_en": "a"}]})
    r = run_shots(proj, 1, llm)
    assert r["skipped"] is False and r["shots"] == 1


def test_replan_without_shots_fails_cleanly(proj):
    res = tool_api.replan_shots(str(proj), 1)
    assert not res["ok"] and "没有镜头规划" in res["error"]["message"]


# ---------------------------------------------------------------- 界面

def _open(proj):
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("🎬 章节生产").run()
    at.text_input(key="chap_root").input(str(proj.parent)).run()
    assert not at.exception, at.exception
    return at


def test_review_page_shows_cast_warning_and_replan(proj):
    lin = _cid(proj, "林远")
    raw = _long_raw(proj)
    raw[0]["visual_hint_zh"] = "林远和影子站在窗边"          # 提到影子，但出镜角色里没有影子
    _plan(proj, raw)
    at = _open(proj)
    caps = " ".join(c.value for c in at.caption)
    assert "**林远**" in caps
    warns = " ".join(w.value for w in at.warning)
    assert "影子" in warns and "编辑镜头内容" in warns
    # 编辑里有出镜角色多选，且默认值是当前角色
    ms = next(m for m in at.multiselect if (m.key or "").startswith("cast_1_s01"))
    assert ms.value == [lin]
    # 重新规划：未勾选确认前不可点；确认后丢弃镜头
    assert at.button(key="replan_1").disabled
    at.checkbox(key="replan_ok_1").check().run()
    at.button(key="replan_1").click().run()
    assert not at.exception, at.exception
    assert cio.load_shots(proj, 1)["shots"] == []


def test_legacy_shots_yaml_gets_derived_warnings_and_is_rewritten_without_anchors(proj):
    """用户手里的旧文件：旧版代码机械拆出的镜头（共用同一画面描述、带 split_* 字段、带 &id001 锚点）。"""
    _plan(proj, _long_raw(proj))
    path = cio.chapter_dir(proj, 1) / "shots.yaml"
    doc = cio.load_shots(proj, 1)
    doc.pop("plan_warnings")
    for n, s in enumerate(doc["shots"], start=1):
        s["split_from"], s["split_part"], s["split_total"] = 1, n, 3
        s["visual_hint_en"] = "one shared description"
    shared = doc["shots"][0]["character_ids"]
    for s in doc["shots"]:
        s["character_ids"] = shared
    import yaml
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")   # 旧写法：带锚点
    assert "&id001" in path.read_text(encoding="utf-8")

    w = cp.chapter_view(proj, 1)["plan_warnings"]
    assert len(w) == 1 and w[0]["parts"] == 3 and w[0]["scene_no"] == 1
    cio.save_shots(proj, 1, cio.load_shots(proj, 1))                 # 任何一次保存都会去掉锚点
    assert "&id" not in path.read_text(encoding="utf-8")