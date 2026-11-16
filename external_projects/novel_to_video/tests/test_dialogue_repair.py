"""对白自动修复（方式二）：模型给出的对白不是剧本连续原文时，替换为剧本中最相似的连续原文；
相似度不够的不修复，仍由原有校验与反馈流程处理。"""
from novel_to_video.steps import chapter_shots as CS
from test_shots_autofix import proj  # noqa: F401  (fixture)

LINES = [
    {"speaker": "林昼", "text": "你好，我们走吧。"},
    {"speaker": "阿青", "text": "天上的星星好亮，像你的眼睛。"},
    {"speaker": "林昼", "text": "明天再说吧。"},
]


def _shots(text):
    return [{"id": "s01", "blocks": [{"type": "dialogue", "text": text, "speaker_id": None, "speaker_name": "阿青"}]}]


def test_stitched_text_is_replaced_by_the_verbatim_line():
    raw = _shots("天上的星星好亮你的眼睛")            # 模型漏掉了“像”，把前后拼在一起
    reps = CS.repair_dialogue_texts(raw, LINES)
    assert len(reps) == 1
    assert raw[0]["blocks"][0]["text"] == "天上的星星好亮，像你的眼睛。"   # 剧本里的连续原文
    assert reps[0]["speaker"] == "阿青" and reps[0]["before"] == "天上的星星好亮你的眼睛"


def test_already_verbatim_is_untouched():
    raw = _shots("明天再说吧。")
    assert CS.repair_dialogue_texts(raw, LINES) == []
    assert raw[0]["blocks"][0]["text"] == "明天再说吧。"


def test_dissimilar_text_is_not_repaired():
    raw = _shots("火星殖民计划的预算")
    before = raw[0]["blocks"][0]["text"]
    assert CS.repair_dialogue_texts(raw, LINES) == []
    assert raw[0]["blocks"][0]["text"] == before


def test_narration_blocks_are_never_touched():
    raw = [{"id": "s01", "blocks": [{"type": "narration", "text": "天上的星星好亮像你的眼睛"}]}]
    assert CS.repair_dialogue_texts(raw, LINES) == []
    assert raw[0]["blocks"][0]["text"] == "天上的星星好亮像你的眼睛"


def test_repaired_text_is_always_in_script():
    from novel_to_video.chapter_io import norm
    raw = _shots("你好我们走吧")
    CS.repair_dialogue_texts(raw, LINES)
    script = "\n".join(l["text"] for l in LINES)
    assert norm(raw[0]["blocks"][0]["text"]) in norm(script)


def test_repair_happens_inside_planning_and_skips_retry(proj):
    """接入规划流程：相似的坏对白被修复，直接通过，不触发反馈修正（只调用一次模型）。"""
    from test_shots_autofix import GOOD, Seq, _bad
    from novel_to_video.steps.chapter_shots import run_shots
    llm = Seq(_bad("我们快走。"), GOOD)                        # 与剧本“我们走。”相似
    r = run_shots(proj, 1, llm)
    assert r["skipped"] is False and r["attempts"] == 1 and len(llm.calls) == 1
