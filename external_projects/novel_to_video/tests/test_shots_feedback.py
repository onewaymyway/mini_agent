"""C-3 校验反馈必须具体：位置、剧本原句、说话人映射、可直接执行的修正指令。"""
from novel_to_video.steps.chapter_shots import collect_shot_issues

SCRIPT = (
    "### 场次 4｜3号会议室·黄昏（内景）\n"
    "周监察员：因为那些人的存在已经没有意义了。\n"
    "配给员：根据最新政策，所有公民需在每日晨间进行健康自检。\n"
)
CHARS = [
    {"id": "char_004", "name": "周正明", "names": ["周正明"]},
    {"id": "char_022", "name": "配给员", "names": ["配给员"]},
]


def _shot(text, **kw):
    block = {"type": "dialogue", "text": text, "speaker_id": None, "speaker_name": None, **kw}
    return {"scene_no": 4, "location_id": "loc_04", "character_ids": [], "blocks": [block],
            "visual_hint_zh": "", "visual_hint_en": ""}


def test_rewritten_dialogue_points_to_real_script_line():
    issues = collect_shot_issues([_shot("因为那些人已经没有意义了", speaker_id="char_004")], SCRIPT, CHARS)
    msg = issues[0]["message"]
    assert "第 1 个镜头的第 1 个口播块" in msg
    assert "周监察员：因为那些人的存在已经没有意义了。" in msg
    assert "连续原文" in msg


def test_missing_speaker_names_script_speaker_and_matching_id():
    # 剧本说话人“配给员”在角色库中存在，应直接给出 id
    issues = collect_shot_issues(
        [_shot("根据最新政策，所有公民需在每日晨间进行健康自检。")], SCRIPT, CHARS)
    msg = issues[0]["message"]
    assert "缺少说话人" in msg
    assert "剧本里的说话人是“配给员”" in msg
    assert 'speaker_id="char_022"' in msg


def test_unknown_script_speaker_asks_for_speaker_name_and_lists_library():
    # 剧本说话人“周监察员”不在角色库（库里只有“周正明”），应给出两条明确出路
    issues = collect_shot_issues(
        [_shot("因为那些人的存在已经没有意义了。")], SCRIPT, CHARS)
    msg = issues[0]["message"]
    assert "周监察员" in msg
    assert 'speaker_name="周监察员"' in msg
    assert "char_004（周正明）" in msg


def test_valid_plan_has_no_issues():
    ok = _shot("因为那些人的存在已经没有意义了。", speaker_id="char_004")
    assert collect_shot_issues([ok], SCRIPT, CHARS) == []
