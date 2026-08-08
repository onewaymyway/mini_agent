"""[P5-5] config/param_registry.py::load_nested_block() 的类型校验兜底回归测试。

背景：next_doc/growth_advisor_improvement_plan_v3.md P5-5——
`category_notification_frequency`（dict 类型字段）被编辑器错误地当字符串
保存后，此前的通用加载逻辑会"原样透传"，脏值静默流入 GrowthAdvisorConfig，
直到某个随机调用点才报错。这里验证：
1. 类型不匹配时安全回退到默认值，加载不崩溃；
2. 类型匹配（合法 dict）时正常生效，不误伤正常配置；
3. `Optional[str] = None` 这类"合法空值"字段不会被误判为类型不匹配。
"""

from dataclasses import dataclass, field
from typing import Optional

from mini_agent.config.param_registry import load_nested_block


@dataclass
class _DummyConfigWithDict:
    enabled: bool = False
    category_notification_frequency: dict = field(default_factory=dict)
    note: Optional[str] = None


def test_dict_field_wrong_type_falls_back_to_default():
    # 模拟编辑器 bug：dict 字段被错误地存成了字符串
    block = {"enabled": True, "category_notification_frequency": "not_a_dict"}
    cfg = load_nested_block(block, _DummyConfigWithDict)
    assert cfg.enabled is True
    # 回退到 dataclass 默认值（空 dict），不是字符串，也不崩溃
    assert cfg.category_notification_frequency == {}
    assert isinstance(cfg.category_notification_frequency, dict)


def test_dict_field_correct_type_passes_through():
    block = {"category_notification_frequency": {"技术类": "daily"}}
    cfg = load_nested_block(block, _DummyConfigWithDict)
    assert cfg.category_notification_frequency == {"技术类": "daily"}


def test_dict_field_missing_uses_default():
    cfg = load_nested_block({}, _DummyConfigWithDict)
    assert cfg.category_notification_frequency == {}


def test_optional_str_none_not_treated_as_type_mismatch():
    # 显式 null：Optional[str] 字段传 None 是合法值，不应触发任何回退逻辑
    block = {"note": None}
    cfg = load_nested_block(block, _DummyConfigWithDict)
    assert cfg.note is None


def test_dict_field_wrong_type_does_not_crash_whole_block():
    # 一个字段类型错误不应该拖垮同一 block 里其它字段的加载
    block = {"enabled": True, "category_notification_frequency": ["a", "list", "not", "dict"]}
    cfg = load_nested_block(block, _DummyConfigWithDict)
    assert cfg.enabled is True
    assert cfg.category_notification_frequency == {}
