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


# ---- 镜头级硬要求：4–12 秒，相邻画面不同 ----
from novel_to_video.steps.chapter_shots import _shot_seconds  # noqa: E402

LONG = "铁城的清晨很安静，穹顶遮住了天空，街道上空无一人，只有监视塔的摄像头不停地转动着，扫过每一扇窗。"  # ≈ 17 秒以上
SHORT = "是的。"  # ≈ 1 秒


def _narr(text, scene=4, zh="中景：林远", en="medium shot: a man"):
    return {"scene_no": scene, "location_id": "loc_04", "character_ids": [],
            "blocks": [{"type": "narration", "text": text, "speaker_id": None, "speaker_name": None}],
            "visual_hint_zh": zh, "visual_hint_en": en}


def test_overlong_shot_is_accepted_without_splitting():
    # 口播时长不做硬性限制：长镜头不报错，合成时画面会按配音时长缩放
    assert not any("秒" in i["message"] for i in collect_shot_issues([_narr(LONG + LONG)], SCRIPT, CHARS))


def test_short_shot_is_accepted():
    issues = collect_shot_issues([_narr(SHORT), _narr(LONG, zh="远景：穹顶", en="wide shot: dome")], SCRIPT, CHARS)
    assert not any("秒" in i["message"] for i in issues)


def test_single_short_shot_chapter_is_accepted():
    assert collect_shot_issues([_narr(SHORT)], SCRIPT, CHARS) == []


def test_adjacent_same_scene_identical_visuals_are_flagged():
    a = _narr(LONG, zh="中景：林远", en="medium shot: a man")
    b = _narr(LONG, zh="中景：林远", en="medium shot: a man")
    msg = "\n".join(i["message"] for i in collect_shot_issues([a, b], SCRIPT, CHARS))
    assert "第 2 个镜头与第 1 个镜头画面描述相同" in msg


def test_identical_visuals_across_different_scenes_are_allowed():
    a = _narr(LONG, scene=1, zh="中景：林远", en="medium shot: a man")
    b = _narr(LONG, scene=2, zh="中景：林远", en="medium shot: a man")
    assert not any("画面描述相同" in i["message"] for i in collect_shot_issues([a, b], SCRIPT, CHARS))


def test_distinct_visuals_pass_duration_checks():
    a = _narr(LONG, zh="远景：穹顶", en="wide shot: dome")
    b = _narr(LONG, zh="特写：摄像头", en="close-up: camera lens")
    assert collect_shot_issues([a, b], SCRIPT, CHARS) == []
