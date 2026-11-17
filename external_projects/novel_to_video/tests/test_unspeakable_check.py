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


def test_narrator_dialogue_block_becomes_narration():
    raw = [{"scene_no": 1, "blocks": [
        {"type": "dialogue", "speaker_name": "旁白", "text": "他没有回头。"},
        {"type": "dialogue", "speaker_name": "李明", "text": "走。"}]}]
    fixed = sh.normalize_narrator_blocks(raw)
    assert len(fixed) == 1
    assert raw[0]["blocks"][0]["type"] == "narration" and "speaker_name" not in raw[0]["blocks"][0]
    assert raw[0]["blocks"][1]["type"] == "dialogue"


def test_ask_json_feeds_error_back_and_retries(tmp_path):
    from novel_to_video.steps import chapter_assets as ca

    class LLM:
        name = "x"

        def __init__(self):
            self.prompts = []

        def ask(self, prompt, system=""):
            self.prompts.append(prompt)
            return "!" * 500 if len(self.prompts) == 1 else '{"gender": "male"}'
    llm = LLM()
    obj = ca._ask_json(tmp_path, llm, "原提示", system="", label="测试", chapter_no=1)
    assert obj == {"gender": "male"}
    assert len(llm.prompts) == 2 and "上一次输出无效" in llm.prompts[1] and "没有 JSON" in llm.prompts[1]
