"""
tests/test_entity_summary_truncation.py — 回归测试：`entity_index.py` 里
实体摘要种子截断从硬截断 `text[:200]` 改为句子边界感知截断
`_truncate_at_boundary()`。

背景：goal 模式的"钉住"提醒消息（`prompts/user/goal_context.md`）会随着
每次 compact 被重新附加进对话历史，如果这段文本恰好落在某条记忆文本的
前 200 字符窗口内，硬截断会把它切得残缺不全（比如切在"请始终以此"中间，
后半句"为准）]"直接丢失），实体摘要读起来断句混乱、不知所云。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from mini_agent.perception.entity_index import EntityStore, _truncate_at_boundary


def test_truncate_at_boundary_prefers_sentence_end():
    text = "用户需要创建一个脚本。这是第二句包含更多细节说明和背景信息。这是第三句超出截断长度的部分内容。"
    result = _truncate_at_boundary(text, 30)
    assert result.endswith("。")
    assert len(result) <= 30


def test_truncate_at_boundary_short_text_unchanged():
    text = "短文本不需要截断"
    assert _truncate_at_boundary(text, 200) == text


def test_truncate_at_boundary_no_punctuation_falls_back_with_ellipsis():
    text = "a" * 250  # 没有任何句子边界标点
    result = _truncate_at_boundary(text, 200)
    assert len(result) == 201  # 200 字符 + 省略号
    assert result.endswith("…")


def test_truncate_at_boundary_does_not_mangle_goal_pin_message(tmp_path):
    """复现用户报告的具体场景：goal 模式提醒消息模板被硬截断在
    "请始终以此"中间，产生读不通的半句话。"""
    goal_pin = (
        "用户需要创建一个Python脚本，功能是动态计算并输出100以内的25个质数"
        "（2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, "
        "61, 67, 71, 73, 79, 83, 89, 97），脚本需放置在./temp/目录下。"
        " 周杰伦生日 [Goal 模式 — 目标与验收标准（此消息会在每次压缩历史后"
        "重新附加，请始终以此为准）]\n目标：..."
    )
    result = _truncate_at_boundary(goal_pin, 200)
    # 之前的硬截断会切成 "...请始终以此"（读不通）；修复后应该在句号处截断，
    # 不应该在"请始终以此"这种半句话中间结束。
    assert not result.endswith("请始终以此")
    assert result.endswith("。")


def test_link_entry_seeds_summary_with_boundary_truncation(tmp_path):
    store = EntityStore(tmp_path / "entities.json")
    text = (
        "用户需要创建一个Python脚本，功能是动态计算并输出100以内的25个质数"
        "（2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, "
        "61, 67, 71, 73, 79, 83, 89, 97），脚本需放置在./temp/目录下。"
        " 周杰伦生日 [Goal 模式 — 目标与验收标准（此消息会在每次压缩历史后"
        "重新附加，请始终以此为准）]\n目标：..."
    )
    store.link_entry("entry_1", text, entity_type="concept")
    entities = list(store._entities.values())
    assert len(entities) >= 1
    summary = entities[0].summary
    assert len(summary) <= 200
    assert not summary.endswith("请始终以此")
