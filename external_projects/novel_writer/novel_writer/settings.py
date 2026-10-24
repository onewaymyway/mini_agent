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
    "G4": "托管挡",
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
    # 步骤边界守卫：越界改动了后续章节文件时，由框架自动还原（agent 手里没有改前内容，无法自己还原）。
    # 设为 false 则只报告并要求 agent 处理（旧行为）。
    "step_guard_auto_restore": True,
    # 步骤失败后的自动重跑策略。max_retries / backoff_sec 为 null 时用 worker 内置默认（3 次，5/15/30 秒）；
    # overrides：步骤 id 或类别 -> 最大重跑次数（0 = 该步骤失败后不自动重跑），步骤 id 优先于类别。
    "step_retry": {"max_retries": None, "backoff_sec": None, "overrides": {}},
    # 巡检整改项反馈（默认全开，全自动，不需要作者操作；可单项关闭）：extract 提取并分流；
    # auto_adopt 自动采纳「约束后续章节」与「避免套路」；inject_forward 把已采纳的后续约束注入上下文包；
    # inject_style 注入风格提示与机器校验提示（不阻断）；to_inbox 把蓝图待办/回改建议放进收件箱（不阻塞流水线）。
    "patrol_feedback": {"extract": True, "auto_adopt": True, "inject_forward": True, "inject_style": True,
                        "to_inbox": True},
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
    pf = merged["patrol_feedback"]
    if not isinstance(pf, dict) or any(k not in DEFAULT_SETTINGS["patrol_feedback"] for k in pf):
        raise SettingsError("patrol_feedback 必须是对象，且只能包含 extract / auto_adopt / inject_forward / inject_style / to_inbox")
    for k, v in pf.items():
        if not isinstance(v, bool):
            raise SettingsError(f"patrol_feedback.{k} 必须是 true/false")
    sr = merged["step_retry"]
    if not isinstance(sr, dict) or any(k not in DEFAULT_SETTINGS["step_retry"] for k in sr):
        raise SettingsError("step_retry 必须是对象，且只能包含 max_retries / backoff_sec / overrides")

    def _nonneg_int(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0

    if sr["max_retries"] is not None and not _nonneg_int(sr["max_retries"]):
        raise SettingsError("step_retry.max_retries 必须是非负整数或 null")
    bo = sr["backoff_sec"]
    if bo is not None and (not isinstance(bo, list) or not bo or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or x < 0 for x in bo)):
        raise SettingsError("step_retry.backoff_sec 必须是非空的非负数字列表或 null")
    if not isinstance(sr["overrides"], dict) or any(not _nonneg_int(v) for v in sr["overrides"].values()):
        raise SettingsError("step_retry.overrides 必须是 {步骤id或类别: 非负整数}")
    if not isinstance(merged["step_guard_auto_restore"], bool):
        raise SettingsError("step_guard_auto_restore 必须是 true/false")
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
