"""剧本/分镜阶段拦截没有可朗读文字的口播（只有标点/符号/空），避免配音 NoAudioReceived。"""
from novel_to_video.steps import chapter_shots as sh
from novel_to_video.steps.chapter_script import check_script

SCRIPT = "### 场次 1｜城门\n旁白：天亮了。\n李明：……\n旁白：他走了。"


def test_script_symbol_only_line_is_error():
    r = check_script(SCRIPT, "天亮了。李明：……他走了。", ["李明"])
    msgs = [i["message"] for i in r["issues"] if i["level"] == "error"]
    assert r["status"] == "fail" and any("没有可朗读的文字" in m and "第 3 行" in m for m in msgs)


def test_script_normal_passes():
    r = check_script("### 场次 1｜城门\n旁白：天亮了。", "天亮了。", [])
    assert r["status"] == "pass"


def _shots(*texts):
    return [{"scene_no": 1, "blocks": [{"type": "narration", "text": t} for t in texts]}]


def test_drop_unspeakable_blocks():
    raw = _shots("天亮了。", "……", "？！", "他走了。")
    dropped = sh.drop_unspeakable_blocks(raw)
    assert [d["text"] for d in dropped] == ["……", "？！"]
    assert [b["text"] for b in raw[0]["blocks"]] == ["天亮了。", "他走了。"]


def test_collect_issues_flags_symbol_block_and_empty_shot():
    msgs = [i["message"] for i in sh.collect_shot_issues(_shots("……"), "### 场次 1｜城门\n旁白：……", [])]
    assert any("没有可朗读的文字" in m for m in msgs)
    empty = [{"scene_no": 1, "blocks": []}]
    msgs = [i["message"] for i in sh.collect_shot_issues(empty, "### 场次 1｜城门\n旁白：天亮了。", [])]
    assert any("没有可朗读的口播块" in m for m in msgs)
