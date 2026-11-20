"""第五阶段：两种方案的客观对比指标（用已知答案的数据验证计算是否正确）。"""
from article_to_video.deck.evaluate import _aligned, classic_metrics, deck_metrics


def _classic_fixture(aligned_steps):
    script = {"beats": [{"id": "P01.b01", "sentences": [{"text": "大脑需要消耗能量。"},
                                                       {"text": "视觉是一个很好的例子。"}]}]}
    storyboard = {"scenes": [{"beat": "P01.b01", "items": ["消耗能量", "视觉"],
                              "reveal": [{"step": 0, "at_sentence": 0}, {"step": 1, "at_sentence": 1}]}]}
    if not aligned_steps:
        storyboard["scenes"][0]["reveal"] = [{"step": 0, "at_sentence": 1}, {"step": 1, "at_sentence": 0}]
    timeline = {"total_sec": 60.0, "sentences": [{"id": "P01.b01.s01", "start": 0, "end": 5},
                                                 {"id": "P01.b01.s02", "start": 5, "end": 10}]}
    return script, storyboard, timeline


def test_alignment_ignores_punctuation_and_spacing():
    assert _aligned("消耗能量", "大脑需要消耗能量。")
    assert _aligned("大 脑", "大脑需要")
    assert not _aligned("不在句中", "大脑需要消耗能量。")


def test_classic_sync_rate_counts_matching_reveals():
    s, sb, tl = _classic_fixture(aligned_steps=True)
    m = classic_metrics(s, sb, tl)
    assert m["reveals"] == 2 and m["sync_rate"] == 1.0
    s, sb, tl = _classic_fixture(aligned_steps=False)
    assert classic_metrics(s, sb, tl)["sync_rate"] == 0.0


def test_classic_density_and_rhythm():
    s, sb, tl = _classic_fixture(aligned_steps=True)
    m = classic_metrics(s, sb, tl)
    assert m["visuals"] == 1 and m["visuals_per_min"] == 1.0
    assert m["items_per_visual"] == 2.0 and m["mean_hold_sec"] == 60.0


def _deck_fixture(anchor_time):
    deck = {"slides": [{"id": "P01.S01", "title": "页", "elements": [
        {"kind": "keypoint", "items": ["消耗能量"]},
        {"kind": "formula", "text": "智能 ≈ 压缩"}]}]}
    script = {"slides": [{"id": "P01.S01", "narration": "大脑需要消耗能量。压缩的能力很重要，共 2025 年。"}]}
    timeline = {"total_sec": 30.0, "pages": [{"id": "P01.S01", "sentences": [
        {"id": "a", "text": "大脑需要消耗能量。", "start": 0.0, "end": 5.0},
        {"id": "b", "text": "压缩的能力很重要，共 2025 年。", "start": 5.0, "end": 10.0}],
        "reveals": [{"element": 0, "at_sec": anchor_time}, {"element": 1, "at_sec": 6.0}]}]}
    return deck, script, timeline


def test_deck_sync_checks_the_sentence_playing_at_reveal_time():
    deck, script, tl = _deck_fixture(anchor_time=1.0)     # 第一个元素出现在“消耗能量”所在句
    m = deck_metrics(deck, script, tl)
    assert m["reveals"] == 2 and m["sync_rate"] == 0.5    # 公式不是关键词，未对齐
    deck, script, tl = _deck_fixture(anchor_time=7.0)     # 出现在别的句子
    assert deck_metrics(deck, script, tl)["sync_rate"] == 0.0


def test_deck_counts_numbers_that_are_not_in_the_source():
    deck, script, tl = _deck_fixture(anchor_time=1.0)
    m = deck_metrics(deck, script, tl)
    assert m["numbers_outside_source"] == 1               # 2025 不在页面中
    assert m["scheme"] == "deck"


def test_deck_visual_rhythm_uses_pages():
    deck, script, tl = _deck_fixture(anchor_time=1.0)
    m = deck_metrics(deck, script, tl)
    assert m["visuals"] == 1 and m["duration_sec"] == 30.0
