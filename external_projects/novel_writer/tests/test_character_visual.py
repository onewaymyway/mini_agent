"""角色外形结构化字段测试：校验、摘要、缺失项、写作摘要、正文渲染与旧数据兼容。全部不调用真实 LLM。"""

import json
import re

from novel_writer import character_store as CS
from novel_writer import steps as S


def _full_visual():
    return {
        "gender": "female",
        "age_look": "看起来约 25 岁，干练",
        "face": "鹅蛋脸，眉目清冷",
        "eyes": {"color": "琥珀", "shape": "杏眼"},
        "hair": {"style": "高马尾，碎发垂落", "color": "银灰"},
        "build": {"height_cm": 165, "body_type": "利落挺拔"},
        "default_outfit": {"top": "黑色短款机能外套", "bottom": "深灰工装裤", "footwear": "军靴",
                           "outerwear": "无", "palette": ["黑", "灰"], "material": "尼龙", "condition": "干净"},
        "accessories": ["右腕黑色护腕", "腰间工具包"],
        "voice": {"timbre": "低沉", "pace": "快", "accent": "普通话"},
        "signature_visual": "右腕黑色护腕",
        "anchor_en": "25-year-old woman, silver-grey high ponytail, black short tactical jacket, grey cargo pants, black military boots, black wrist guard",
    }


def _rec(**kw):
    rec = {"id": "char_001", "name": "沈岚", "role": "protagonist", "introduced_chapter": 1,
           "identity": {"one_line_essence": "冷静的修复师"}, "current_state": {"as_of_chapter": 0}}
    rec.update(kw)
    return rec


def test_field_order_and_rules_mention_visual():
    assert "visual" in CS.FIELD_ORDER and "visual" in CS.DICT_FIELDS
    assert "visual" in CS.LAYOUT_RULES and "gender" in CS.LAYOUT_RULES
    assert "appearance_change" in CS.LAYOUT_RULES


def test_layout_example_is_valid_json_with_structured_visual():
    # 规则里的示例必须是合法 JSON，否则 LLM 会照着写出无法解析的文件
    m = re.search(r"^visual: (\{.*\})$", CS.LAYOUT_RULES, re.M)
    assert m, "示例中缺少 visual 行"
    example = json.loads(m.group(1))
    assert example["gender"] in CS.GENDER_VALUES
    assert example["hair"]["style"] and example["default_outfit"]["top"] and example["anchor_en"]
    assert CS.record_problems(_rec(visual=example), "示例") == []


def test_valid_visual_passes_validation():
    assert CS.record_problems(_rec(visual=_full_visual()), "x") == []


def test_invalid_gender_and_shapes_are_reported():
    bad = _rec(visual={"gender": "robot", "hair": "长发", "accessories": "手表", "anchor_en": ["x"]})
    problems = CS.record_problems(bad, "x")
    assert any("visual.gender" in p and "robot" in p for p in problems)
    assert any("visual.hair" in p for p in problems)
    assert any("visual.accessories" in p for p in problems)
    assert any("visual.anchor_en" in p for p in problems)
    assert any("visual 应为对象" in p for p in CS.record_problems(_rec(visual="瘦"), "x"))


def test_legacy_record_without_visual_is_still_valid():
    # 旧项目的角色文件没有 visual：必须仍然通过校验，不能让老项目刷新失败
    legacy = _rec(identity={"one_line_essence": "旧角色", "appearance": "身形单薄"})
    assert CS.record_problems(legacy, "x") == []


def test_summary_contains_gender_hair_outfit_and_current_outfit():
    rec = _rec(visual=_full_visual(), current_state={"as_of_chapter": 3, "outfit": "染血的外套"})
    line = CS.visual_summary(rec)
    assert "女" in line and "银灰" in line and "高马尾" in line
    assert "黑色短款机能外套" in line and "军靴" in line
    assert "当前装扮：染血的外套" in line


def test_summary_omits_missing_items_without_inventing():
    assert CS.visual_summary(_rec()) == ""
    assert CS.visual_summary(_rec(visual={"gender": "unknown"})) == ""


def test_gaps_report_what_is_still_missing():
    assert set(CS.visual_gaps(_rec())) == {"性别", "发型", "默认装扮", "英文外形锚点"}
    assert CS.visual_gaps(_rec(visual=_full_visual())) == []


def test_brief_includes_appearance_or_marks_it_unrecorded():
    chars = {"char_001": _rec(visual=_full_visual()), "char_002": _rec(id="char_002", name="路人甲")}
    text = CS.brief_for(chars, ["沈岚", "路人甲"])
    assert "外形：女" in text and "高马尾" in text
    assert "路人甲" in text and "未记录" in text


def test_body_renders_appearance_line():
    body = CS._body(_rec(visual=_full_visual()))
    assert "- 外形：女" in body


def test_build_instruction_requires_structured_visual():
    step = S.build_characters_step(["沈岚"])
    assert "visual" in step.instructions and "gender" in step.instructions
    design = S.design_character_step("沈岚")
    assert "外形规格" in design.instructions and "英文外形锚点" in design.instructions


def test_sync_instruction_keeps_fixed_look_separate_from_outfit_changes():
    text = S._character_sync_instructions(5)
    assert "appearance_change" in text and "current_state.outfit" in text
