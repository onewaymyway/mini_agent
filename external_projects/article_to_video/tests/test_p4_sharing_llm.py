"""P4 补充：一图多用的自动分配、模型版分镜（来源校验与重试）、分镜服务的引擎选择与重新分配。"""
import json

import pytest

from article_to_video.planning.errors import PlanError
from article_to_video.project_store import write_json_atomic
from article_to_video.providers.image import StubImageProvider
from article_to_video.render.compose_part import render_part
from article_to_video.validators.visual import validate_storyboard
from article_to_video.visual import storyboard as sbm
from article_to_video.visual.config import normalize_visual, save_visual_config
from article_to_video.visual.director_llm import LLM_PROMPT_VERSION, beat_context
from article_to_video.visual.sharing import allocate_shared_images, count_groups, leader_subjects
from helpers_p4 import scripted_project, voiced_project


# ---------- 一图多用：纯分配 ----------

def _ill(i):
    return {"id": f"b{i:02d}", "type": "illustration", "image": {"subject": f"主题{i}", "focus_x": 0.5}}


def test_allocation_round_robin_spreads_group_members_apart():
    scenes = [_ill(i) for i in range(6)]
    groups = allocate_shared_images(scenes, 2)
    assert groups == ["G01", "G02", "G03"]
    g1 = [s["id"] for s in scenes if s["image"]["group"] == "G01"]
    assert g1 == ["b00", "b03"]                       # 同组镜头相隔 3 段
    assert [s["image"]["leader"] for s in scenes if s["image"]["group"] == "G01"] == [True, False]


def test_allocation_spreads_focus_across_group_members():
    scenes = [_ill(i) for i in range(4)]
    allocate_shared_images(scenes, 2)
    focus = {s["id"]: s["image"]["focus_x"] for s in scenes}
    for gid in ("G01", "G02"):
        xs = sorted(focus[s["id"]] for s in scenes if s["image"]["group"] == gid)
        assert xs[0] < 0.5 < xs[1]                      # 左右取景互异


def test_allocation_never_exceeds_shots_or_group_count():
    for n in range(1, 13):
        for shots in (1, 2, 3):
            scenes = [_ill(i) for i in range(n)]
            allocate_shared_images(scenes, shots)
            assert count_groups(scenes) == -(-n // shots)
            per = {}
            for s in scenes:
                per[s["image"]["group"]] = per.get(s["image"]["group"], 0) + 1
            assert max(per.values()) <= shots
            assert sum(1 for s in scenes if s["image"]["leader"]) == len(per)


def test_one_shot_per_image_keeps_one_image_per_scene():
    scenes = [_ill(i) for i in range(3)]
    allocate_shared_images(scenes, 1)
    assert count_groups(scenes) == 3


def test_leader_subjects_map_group_to_leader_subject():
    scenes = [_ill(i) for i in range(4)]
    allocate_shared_images(scenes, 2)
    assert leader_subjects(scenes) == {"G01": "主题0", "G02": "主题1"}


def _interleaved(n_ill):
    """n_ill 个插图，中间用卡片隔开，避免连续同类型。"""
    scenes = []
    for i in range(n_ill):
        scenes.append({"id": f"b{2 * i:02d}", "type": "illustration", "items": ["条目"],
                       "image": {"subject": f"主题{i}", "focus_x": 0.5}})
        scenes.append({"id": f"b{2 * i + 1:02d}", "type": "keypoint", "items": ["条目"], "image": None})
    for s_ in scenes:
        s_["beat"] = s_["id"]
        s_["reveal"] = [{"step": 0, "at_sentence": 0}]
    return scenes


def _script_for(scenes):
    return {"beats": [{"id": s_["id"], "role": "claim", "sentences": [{"text": "句"}]} for s_ in scenes]}


def test_v5_counts_groups_not_scenes():
    scenes = _interleaved(4)                 # 4 个插图镜头
    allocate_shared_images(scenes, 2)        # 分成 2 张图
    base = dict(allowance=2, style_anchor="无文字", images_enabled=True, shots_per_image=2)
    v = validate_storyboard({"scenes": scenes, "source_structure": []}, _script_for(scenes), **base)
    assert v["errors"] == []
    tight = dict(base, allowance=1)
    v2 = validate_storyboard({"scenes": scenes, "source_structure": []}, _script_for(scenes), **tight)
    assert any("额度" in e for e in v2["errors"])


def test_v5_rejects_group_without_single_leader():
    scenes = _interleaved(2)
    allocate_shared_images(scenes, 2)
    ills = [x for x in scenes if x["type"] == "illustration"]
    ills[1]["image"]["leader"] = True        # 同组两个主图
    v = validate_storyboard({"scenes": scenes, "source_structure": []}, _script_for(scenes), allowance=2,
                            style_anchor="无文字", images_enabled=True, shots_per_image=2)
    assert any("主图" in e for e in v["errors"])


def test_config_clamps_shots_per_image():
    cfg, _ = normalize_visual({"images": {"shots_per_image": 9}})
    assert cfg["images"]["shots_per_image"] == 3
    cfg, _ = normalize_visual({"images": {"shots_per_image": 0}})
    assert cfg["images"]["shots_per_image"] == 1
    cfg, _ = normalize_visual({})
    assert cfg["images"]["shots_per_image"] == 2


# ---------- 模型版分镜 ----------

class StoryLLM:
    """responder(prompt, n) -> str；记录调用次数。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0
        self.prompts = []

    def ask(self, prompt, system=""):
        self.calls += 1
        self.prompts.append(prompt)
        return self.responder(prompt, self.calls)


def _beats_from_prompt(prompt):
    import re
    return re.findall(r"^\[(P\d+\.b\d+)\]", prompt, flags=re.M)


def _good_plan(prompt, _n=None):
    """每段：金句段用 quote，其余用 keypoint；章中一个插图；条目只用本段口播中的字。"""
    scenes = []
    for k, bid in enumerate(_beats_from_prompt(prompt)):
        block = prompt.split(f"[{bid}]", 1)[1].split("\n[", 1)[0]
        texts = [line.strip()[2:].replace("（金句）", "") for line in block.splitlines() if line.strip().startswith("· ")]
        first = texts[0][:20] if texts else "要点"
        if "（金句）" in block:
            scenes.append({"beat": bid, "type": "quote"})
        elif "原文流程" in block:
            scenes.append({"beat": bid, "type": "diagram"})
        elif "原文公式" in block:
            scenes.append({"beat": bid, "type": "formula", "formula_explanation": [first]})
        elif "原文表格" in block:
            scenes.append({"beat": bid, "type": "table"})
        elif "原文列表" in block:
            scenes.append({"beat": bid, "type": "list"})
        elif k % 3 == 2 and "illustration" in block:
            scenes.append({"beat": bid, "type": "illustration", "items": [first], "image_subject": "蒸汽与齿轮"})
        else:
            scenes.append({"beat": bid, "type": "keypoint", "items": [first]})
    # 保证没有连续三段同类型：把第三个相同的改为 keypoint（仅 keypoint 可安全替换）
    for i in range(2, len(scenes)):
        if scenes[i]["type"] == scenes[i - 1]["type"] == scenes[i - 2]["type"] and scenes[i]["type"] != "keypoint":
            scenes[i] = {"beat": scenes[i]["beat"], "type": "keypoint", "items": ["要点"]}
    return json.dumps({"scenes": scenes}, ensure_ascii=False)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, spine = scripted_project(tmp_path)
    return pdir, spine


def _part(spine, pid):
    return next(p for p in spine["parts"] if p["id"] == pid)


def test_llm_plan_builds_valid_storyboard_and_uses_source_structure(ready):
    pdir, spine = ready
    llm = StoryLLM(_good_plan)
    sb = sbm.plan_storyboard(pdir, "P01", engine="llm", llm=llm)
    assert sb["engine"] == "llm" and sb["director"] == LLM_PROMPT_VERSION
    assert sb["errors"] == []
    table = next((s for s in sb["scenes"] if s["type"] == "table"), None)
    if table:  # 表格内容必须来自原文
        assert table["header"] and table["rows"]


def test_llm_structure_is_taken_from_source_not_model(ready):
    pdir, spine = ready
    def hallucinate(prompt, n):
        data = json.loads(_good_plan(prompt, n))
        for s in data["scenes"]:
            if s["type"] == "keypoint":
                s["items"] = ["编造的结构"]
        return json.dumps(data, ensure_ascii=False)
    sb = sbm.plan_storyboard(pdir, "P02", engine="llm", llm=StoryLLM(hallucinate))
    diagrams = [s for s in sb["scenes"] if s["type"] == "diagram"]
    for d in diagrams:
        assert "编造的结构" not in d["items"] and len(d["items"]) >= 2


def test_llm_items_with_numbers_not_in_source_are_rejected_then_retried(ready):
    pdir, _ = ready
    def first_bad(prompt, n):
        data = json.loads(_good_plan(prompt, n))
        if n == 1:
            for s in data["scenes"]:
                if s["type"] == "keypoint":
                    s["items"] = ["增长了 999 倍"]
                    break
        return json.dumps(data, ensure_ascii=False)
    llm = StoryLLM(first_bad)
    sb = sbm.plan_storyboard(pdir, "P01", engine="llm", llm=llm)
    assert llm.calls == 2 and sb["errors"] == []
    assert "999" in llm.prompts[1]          # 第二次提示词带上了问题清单


def test_llm_persistent_failure_raises_and_writes_nothing(ready):
    pdir, _ = ready
    llm = StoryLLM(lambda prompt, n: "这不是 JSON")
    with pytest.raises(PlanError) as err:
        sbm.plan_storyboard(pdir, "P01", engine="llm", llm=llm)
    assert llm.calls == 3 and err.value.raw == "这不是 JSON"
    assert sbm.load_storyboard(pdir, "P01") is None


def test_llm_missing_beat_is_reported(ready):
    pdir, _ = ready
    def drop_first(prompt, n):
        data = json.loads(_good_plan(prompt, n))
        data["scenes"] = data["scenes"][1:]
        return json.dumps(data, ensure_ascii=False)
    with pytest.raises(PlanError, match="缺少段落"):
        sbm.plan_storyboard(pdir, "P01", engine="llm", llm=StoryLLM(drop_first))


def test_llm_engine_requires_model_object(ready):
    pdir, _ = ready
    with pytest.raises(PlanError, match="文本模型"):
        sbm.plan_storyboard(pdir, "P01", engine="llm", llm=None)
    with pytest.raises(PlanError):
        sbm.plan_storyboard(pdir, "P01", engine="bogus")


def test_llm_storyboard_stales_when_engine_recorded_differs(ready):
    pdir, _ = ready
    sbm.plan_storyboard(pdir, "P01", engine="llm", llm=StoryLLM(_good_plan))
    sb = sbm.load_storyboard(pdir, "P01")
    assert sbm.storyboard_status(pdir, "P01")["stale"] is False
    sb["engine"] = "rules"  # 模拟记录的引擎与指纹不符
    write_json_atomic(pdir / "parts" / "P01" / "storyboard.json", sb)
    assert sbm.storyboard_status(pdir, "P01")["stale"] is True


def test_beat_context_only_offers_types_the_source_supports(ready):
    pdir, spine = ready
    script = sbm.load_script(pdir, "P02") if hasattr(sbm, "load_script") else None
    from article_to_video.script.writer import load_script
    from article_to_video.visual.storyboard import _blocks
    script = load_script(pdir, "P02")
    blocks = _blocks(pdir)
    for beat in script["beats"]:
        ctx = beat_context(beat, blocks)
        assert "keypoint" in ctx["allowed"] and "illustration" in ctx["allowed"]
        if "diagram" in ctx["allowed"]:
            assert any("原文流程" in n for n in ctx["notes"])


# ---------- 分镜服务：重新分配与一图多用渲染 ----------

def test_reallocate_images_changes_groups_without_touching_text(ready):
    pdir, _ = ready
    sb = sbm.plan_storyboard(pdir, "P02")
    save_visual_config(pdir, {"images": {"shots_per_image": 1}})
    sb2 = sbm.reallocate_images(pdir, "P02")
    ill = [x for x in sb2["scenes"] if x["type"] == "illustration"]
    assert all(x["image"]["group"] for x in ill)
    assert count_groups(sb2["scenes"]) == len(ill)            # 每图 1 镜头
    assert [x["items"] for x in sb2["scenes"]] == [x["items"] for x in sb["scenes"]]
    assert sb2["confirmed"] is False


def test_storyboard_default_shares_images_and_stays_within_allowance(ready):
    pdir, spine = ready
    for p in spine["parts"]:
        sb = sbm.plan_storyboard(pdir, p["id"])
        assert sb["errors"] == []
        assert sb["images_planned"] <= sb["image_allowance"]
        assert sb["shots_per_image"] == 2


@pytest.fixture
def voiced(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    sbm.plan_storyboard(pdir, "P02")
    sbm.confirm_storyboard(pdir, "P02")
    return pdir


def test_render_generates_once_per_group_and_followers_reuse_leader(voiced):
    provider = StubImageProvider()
    m = render_part(voiced, "P02", provider=provider)
    sb = sbm.load_storyboard(voiced, "P02")
    groups = {x["image"]["group"] for x in sb["scenes"] if x["type"] == "illustration"}
    assert len(provider.calls) == len(groups) == m["counts"]["groups"]
    followers = [x for x in m["scenes"] if x.get("image_group") and not x.get("image_leader")]
    assert followers, "默认每图 2 镜头，应有复用的镜头"
    leaders = {x["image_group"]: x["background_src"] for x in m["scenes"] if x.get("image_leader")}
    for f in followers:
        assert f["background_src"] == leaders[f["image_group"]]


def test_follower_is_a_different_crop_of_the_leader_image(voiced):
    from PIL import Image, ImageChops
    m = render_part(voiced, "P02", provider=StubImageProvider())
    f = next(x for x in m["scenes"] if x.get("image_group") and not x.get("image_leader"))
    leader = next(x for x in m["scenes"] if x.get("image_group") == f["image_group"] and x.get("image_leader"))
    a = Image.open(voiced / "parts" / "P02" / leader["background"]).convert("RGB")
    b = Image.open(voiced / "parts" / "P02" / f["background"]).convert("RGB")
    assert a.size == b.size and ImageChops.difference(a, b).getbbox() is not None


def test_changing_leader_subject_regenerates_all_group_members(voiced):
    first = render_part(voiced, "P02", provider=StubImageProvider())
    raw = sbm.load_storyboard(voiced, "P02")
    lead = next(x for x in raw["scenes"] if x["type"] == "illustration" and x["image"]["leader"])
    group = lead["image"]["group"]
    edited = {"scenes": [dict(x, image=dict(x["image"]) if x.get("image") else None) for x in raw["scenes"]]}
    next(x for x in edited["scenes"] if x["id"] == lead["id"])["image"]["subject"] = "全新的画面主题"
    sbm.save_storyboard(voiced, "P02", edited)
    sbm.confirm_storyboard(voiced, "P02")
    second = render_part(voiced, "P02", provider=StubImageProvider())
    before = {x["id"]: x["sha"] for x in first["scenes"]}
    members = [x for x in second["scenes"] if x.get("image_group") == group]
    assert members and all(x["sha"] != before[x["id"]] for x in members)
