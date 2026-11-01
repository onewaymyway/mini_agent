"""gates.py — 挡位（G0–G3）与检查点策略。

挡位只决定“哪些检查点自动通过”，不改变任何生成逻辑。语义见设计方案第 8 节：

  检查点                 G0    G1    G2    G3
  全书素材（文本/定妆）   手动  手动  批量  自动（校验失败才停）
  章节剧本/分镜           手动  手动  自动  自动
  小场景视频（逐镜审核）  手动  手动  手动  自动（校验失败才停）
  章节合成               手动  自动  自动  自动
  补建角色               手动  手动  手动  自动补建，完成后汇总
  全书合成（F-1）        始终手动，任何挡位都不可跳过

注意：小场景逐镜审核在 G0–G2 始终保留；G3 只在“视频生成校验通过”时自动通过，
这里的“自动通过”是指由代码依据机械校验结果置为 approved，并记入 review.json。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

GEARS = ("G0", "G1", "G2", "G3", "G4", "G5")
DEFAULT_GEAR = "G0"

# 检查点：manual | auto
_POLICY: Dict[str, Dict[str, str]] = {
    "G0": {"asset_text": "manual", "asset_portrait": "manual", "chapter_script": "manual",
           "shot_review": "manual", "chapter_compose": "manual", "auto_add": "manual"},
    "G1": {"asset_text": "manual", "asset_portrait": "manual", "chapter_script": "manual",
           "shot_review": "manual", "chapter_compose": "auto", "auto_add": "manual"},
    "G2": {"asset_text": "manual", "asset_portrait": "manual", "chapter_script": "auto",
           "shot_review": "manual", "chapter_compose": "auto", "auto_add": "manual"},
    "G3": {"asset_text": "auto", "asset_portrait": "auto", "chapter_script": "auto",
           "shot_review": "auto", "chapter_compose": "auto", "auto_add": "auto"},
    "G4": {"asset_text": "auto", "asset_portrait": "auto", "chapter_script": "auto",
           "shot_review": "auto", "chapter_compose": "auto", "auto_add": "auto"},
    "G5": {"asset_text": "auto", "asset_portrait": "auto", "chapter_script": "auto",
           "shot_review": "auto", "chapter_compose": "auto", "auto_add": "auto"},
}

# G4/G5 的失败恢复策略（有上限，避免在付费接口上无限重试）
RETRY_LIMITS = {"G3": 0, "G4": 1, "G5": 3}
RECOVERY = {
    "G4": "失败后换策略一次：降低口播块长度（重新切分镜头）后重跑该章",
    "G5": "失败后重置该章失败阶段并重试，最多 3 次，仍失败则停止并记录",
}

# 任何挡位都不可跳过的检查点
# 任何挡位都不可跳过的检查点：全书合成（F-1 与人工确认）
ALWAYS_MANUAL = ("final_compose",)


class GateError(ValueError):
    pass


def normalize_gear(gear: Optional[str]) -> str:
    g = (gear or DEFAULT_GEAR).strip().upper()
    if g not in GEARS:
        raise GateError(f"未知挡位：{gear}（可选 {', '.join(GEARS)}）")
    return g


def is_auto(gear: str, checkpoint: str) -> bool:
    if checkpoint in ALWAYS_MANUAL:
        return False
    if checkpoint not in _POLICY["G0"]:
        raise GateError(f"未知检查点：{checkpoint}")
    return _POLICY[normalize_gear(gear)][checkpoint] == "auto"


def policy_table() -> Dict[str, Dict[str, str]]:
    return {g: dict(v) for g, v in _POLICY.items()}


def require_budget(budget: Dict[str, Any], usage: Dict[str, int], minutes_used: float, gear: str) -> None:
    """G3 及以上必须配置预算上限；超出上限时抛出 GateError。

    usage: {"video": n, "image": n}；minutes_used: 已用分钟数。
    """
    if normalize_gear(gear) != "G3":
        return
    validate_budget(budget, gear)
    limits = {"max_video_calls": "video", "max_image_calls": "image"}
    for lim, kind in limits.items():
        if usage.get(kind, 0) >= int(budget[lim]):
            raise GateError(f"预算已用尽：{kind} 调用 {usage.get(kind, 0)} / {budget[lim]}")
    if minutes_used >= float(budget["max_minutes"]):
        raise GateError(f"预算已用尽：耗时 {minutes_used:.1f} / {budget['max_minutes']} 分钟")


def validate_budget(budget: Dict[str, Any], gear: str) -> None:
    """只检查预算是否“已配置”（G3 必填）；上限为 0 是合法配置，表示立即停止。
    是否超限属于运行时检查，由 require_budget 负责，不在保存设置时拦截。"""
    if normalize_gear(gear) != "G3":
        return
    missing = [k for k in ("max_video_calls", "max_image_calls", "max_minutes") if budget.get(k) in (None, "")]
    if missing:
        raise GateError("G3 挡位必须设置预算上限：" + "、".join(missing))
