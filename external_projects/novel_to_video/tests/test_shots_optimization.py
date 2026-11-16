"""分镜生产优化：部分修正、段级检查点、有限并发、失败升级拆分、角色过滤。"""
import copy
import json

import pytest

from novel_to_video import chapter_io as cio
from novel_to_video.steps import chapter_shots as CS
from novel_to_video.steps import chapter_scenes as SC
from novel_to_video.steps.global_visual import StepError
from test_chapter_pipeline import SHOTS, project  # noqa: F401  (fixture)
from test_shots_autofix import GOOD, Seq, proj  # noqa: F401  (fixture)


def _first_dialogue_shot(shots_doc):
    for i, sh in enumerate(shots_doc["shots"], start=1):
        for b in sh["blocks"]:
            if b["type"] == "dialogue":
                return i, sh
    raise AssertionError("no dialogue shot")


def test_partial_fix_rewrites_only_the_bad_shot(proj, monkeypatch):
    monkeypatch.setattr(CS, "DIALOGUE_REPAIR_MIN_SIMILARITY", 1.01)   # 让坏对白走反馈路径
    monkeypatch.setattr(CS, "PARTIAL_FIX_ENABLED", True)
    good = json.loads(GOOD)
    bad = copy.deepcopy(good)
    k, sh = _first_dialogue_shot(bad)
    for b in sh["blocks"]:
        if b["type"] == "dialogue":
            b["text"] = "火星殖民计划的预算。"                       # 与剧本无关，无法自动修复
    fixed = {"fixed": [{"index": k, "shot": json.loads(json.dumps(sh, ensure_ascii=False))}]}
    for b in fixed["fixed"][0]["shot"]["blocks"]:
        if b["type"] == "dialogue":
            b["text"] = "我们走。"
    llm = Seq(json.dumps(bad, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False))
    r = CS.run_shots(proj, 1, llm, force=True)
    assert r["attempts"] == 2
    assert len(llm.calls) == 2
    assert "TASK=shots_partial_fix" in llm.calls[1]           # 第二次是部分修正，不是整段重写
    assert "火星殖民计划" in llm.calls[1]                      # 只带上坏镜头
    assert "我们走。" in json.dumps(cio.load_shots(proj, 1), ensure_ascii=False)


def test_checkpoint_reuses_passed_segments(proj):
    llm = Seq(GOOD)
    CS.run_shots(proj, 1, llm)
    assert len(llm.calls) == 1
    # 删除镜头产物，强制重新进入规划；检查点命中时不应再调用模型
    (cio.chapter_dir(proj, 1) / "shots.yaml").unlink()
    llm2 = Seq(GOOD)
    r = CS.run_shots(proj, 1, llm2)
    assert r["skipped"] is False and len(llm2.calls) == 0
    assert list((cio.chapter_dir(proj, 1) / "shots_parts").glob("*.json"))


def test_force_bypasses_checkpoints(proj):
    CS.run_shots(proj, 1, Seq(GOOD))
    llm = Seq(GOOD)
    CS.run_shots(proj, 1, llm, force=True)
    assert len(llm.calls) == 1


def _script(n_scenes):
    return "".join(f"### 场次 {i}｜地点{i}（外景）\n旁白：第{i}场。\n" for i in range(1, n_scenes + 1))


def test_split_segments_by_level():
    script = _script(3)
    segs = [("大场景 1", script, [{"big_no": 1, "title_zh": "A", "from_scene": 1, "to_scene": 3}], 1)]
    assert CS.split_segments(segs, 0) == segs
    halves = CS.split_segments(segs, 1)
    assert [h[0] for h in halves] == ["大场景 1（拆分 1–1）", "大场景 1（拆分 2–3）"]
    assert halves[1][2][0]["from_scene"] == 2 and halves[1][2][0]["to_scene"] == 3
    singles = CS.split_segments(segs, 2)
    assert len(singles) == 3
    assert all(len(SC.parse_script_scenes(s[1])) == 1 for s in singles)


def test_single_scene_segment_is_not_split():
    segs = [("大场景 1", _script(1), None, 1)]
    assert CS.split_segments(segs, 2) == segs


def test_run_segments_keeps_order_and_raises_after_all_finish(tmp_path):
    assert CS.run_segments(tmp_path, [1, 2, 3], lambda j: j * 10, 2) == [10, 20, 30]

    seen = []

    def fn(j):
        seen.append(j)
        if j == 2:
            raise StepError("段 2 失败")
        return j

    with pytest.raises(StepError, match="段 2 失败"):
        CS.run_segments(tmp_path, [1, 2, 3], fn, 2)
    assert sorted(seen) == [1, 2, 3]          # 失败的段不影响其它段执行


def test_relevant_characters_keeps_only_mentioned_names():
    chars = [{"id": "c1", "names": ["林远"]}, {"id": "c2", "names": ["影子"]}, {"id": "c3", "names": []}]
    kept = [c["id"] for c in CS.relevant_characters(chars, "林远：我们走。")]
    assert kept == ["c1", "c3"]                # 无名字的保留；未出现的 c2 被过滤
    assert [c["id"] for c in CS.relevant_characters(chars, "旁白：天很蓝。")] == ["c3"]   # 只剩无名字的
    only_named = [{"id": "c2", "names": ["影子"]}]
    assert CS.relevant_characters(only_named, "旁白：天很蓝。") == only_named          # 全部过滤时退回完整列表


def test_issue_indices_only_when_every_issue_is_shot_specific():
    assert CS._issue_shot_indices([{"message": "第 2 个镜头的第 1 个口播块非法"}]) == {2}
    assert CS._issue_shot_indices([{"message": "第 2 个镜头的对白"}, {"message": "场次 3 没有镜头"}]) is None
    assert CS._issue_shot_indices([]) is None


def test_apply_partial_fix_validates_indices():
    base = {"shots": [{"a": 1}, {"a": 2}, {"a": 3}]}
    out = CS._apply_partial_fix(base, {"fixed": [{"index": 2, "shot": {"a": 9}}]}, {2})
    assert out["shots"] == [{"a": 1}, {"a": 9}, {"a": 3}] and base["shots"][1] == {"a": 2}
    with pytest.raises(StepError):
        CS._apply_partial_fix(base, {"fixed": [{"index": 1, "shot": {"a": 9}}]}, {2})   # 序号不对
    with pytest.raises(StepError):
        CS._apply_partial_fix(base, {"fixed": []}, {2})                                  # 缺项
