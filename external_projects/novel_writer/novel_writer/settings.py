"""novel_writer/settings.py — 挡位 / 质量档 / 预算 / 步骤覆盖（`_studio/settings.json`）。

方案第 6 节：控制挡位（人参与多少）与质量档位（每章做多细）是两个正交旋钮；
最终落到"步骤 × 闸门"矩阵上（`step_overrides`，M1 起由 gates.py 解析）。
**保守 opt-in 默认**：默认 G0 手动挡 + 标准档，预算有限，不自动豁免任何检查点。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

from novel_writer.fsutil import atomic_write_json, read_json

# 控制挡位：id -> 展示名
GEARS: Dict[str, str] = {
    "G0": "手动挡",
    "G1": "引导挡",
    "G2": "半自动挡",
    "G3": "全自动挡",
}
# 质量档位：id -> 展示名
QUALITIES: Dict[str, str] = {
    "draft": "草稿档",
    "standard": "标准档",
    "premium": "精品档",
}
# 步骤闸门取值
GATES = ("manual", "review", "auto", "skip")

SCHEMA_VERSION = 1

DEFAULT_SETTINGS: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "gear": "G0",
    "quality": "standard",
    # 步骤 id -> 闸门（与挡位预设不同的单步覆盖），M1 起生效
    "step_overrides": {},
    # 按阶段覆盖挡位，如 {"planning": "G1", "writing": "G3"}，M5 起生效
    "stage_gear_overrides": {},
    # 预算（达到上限 = 暂停，不是失败）
    "budget": {
        "max_chapters_per_run": 3,
        "max_llm_calls_per_run": 60,
        "max_wall_minutes_per_run": 120,
    },
    # 强制检查点（方案 6.3）；blueprint 可由用户显式豁免，其余不可关闭
    "checkpoints": {
        "waive_blueprint_confirm": False,
    },
    # 步骤 id/类别 -> 模型覆盖，如 {"review": "某个模型名"}；空 = 继承主项目配置
    "model_overrides": {},
    # 步骤 id/类别 -> max_turns 覆盖
    "max_turns_overrides": {},
    # 机器校验用的禁用词表
    "banned_words": [],
    # 半自动挡的批大小
    "semi_auto_batch_chapters": 3,
}


class SettingsError(ValueError):
    pass


def validate_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """校验并返回规整后的设置（缺字段补默认；非法取值抛 SettingsError）。"""
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for key, value in (data or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    if merged["gear"] not in GEARS:
        raise SettingsError(f"未知挡位 {merged['gear']!r}，可选：{list(GEARS)}")
    if merged["quality"] not in QUALITIES:
        raise SettingsError(f"未知质量档 {merged['quality']!r}，可选：{list(QUALITIES)}")
    for step_id, gate in merged["step_overrides"].items():
        if gate not in GATES:
            raise SettingsError(f"步骤 {step_id!r} 的闸门 {gate!r} 非法，可选：{list(GATES)}")
    for stage, gear in merged["stage_gear_overrides"].items():
        if gear not in GEARS:
            raise SettingsError(f"阶段 {stage!r} 的挡位 {gear!r} 非法，可选：{list(GEARS)}")
    for name, value in merged["budget"].items():
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            raise SettingsError(f"预算 {name!r} 必须是正整数或 null（不限），当前 {value!r}")
    if not isinstance(merged["banned_words"], list):
        raise SettingsError("banned_words 必须是列表")
    batch = merged["semi_auto_batch_chapters"]
    if not isinstance(batch, int) or isinstance(batch, bool) or batch < 1:
        raise SettingsError("semi_auto_batch_chapters 必须是正整数")
    return merged


def load_settings(settings_file: Path) -> Dict[str, Any]:
    """读取设置；文件不存在或损坏时退回默认值（不抛异常）。"""
    raw = read_json(settings_file, default=None)
    try:
        return validate_settings(raw if isinstance(raw, dict) else {})
    except SettingsError:
        return copy.deepcopy(DEFAULT_SETTINGS)


def save_settings(settings_file: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = validate_settings(data)
    atomic_write_json(settings_file, cleaned)
    return cleaned
