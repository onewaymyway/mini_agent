"""gates.py — 挡位（G0–G3）与检查点策略。

挡位只决定“哪些检查点自动通过”，不改变任何生成逻辑。语义见设计方案第 8 节：

  检查点                 G0    G1    G2    G3
  全书素材（文本/定妆）   手动  手动  批量  自动（校验失败才停）
  章节剧本/分镜           手动  手动  自动  自动
  小场景视频（逐镜审核）  手动  手动  手动  自动（校验失败才停）
  章节合成               手动  自动  自动  自动
  补建角色               手动  手动  手动  自动补建，完成后汇总
  全书合成（F-1）        始终手动，任何挡位都不可跳过

G6（全自动）在 G3 的基础上，队列会替用户完成所有「需要人动手」的素材工作：按本章镜头用到的角色/地点/道具
自动补全视觉与声音设定、确认性别、通过设定、生成并通过定妆图；失败的章节自动重试（最多 3 次）；
一章结束自动开始下一章。预算上限与 G3 一样必填。
G6 **不跳过失败的章节**：某章失败后持续重试（间隔逐步拉长），直到成功、用户停止、预算用尽，或达到预算里可选的
「单章最多重试次数」；后两种情况整个队列停下并说明原因，而不是继续做后面的章节。

注意：小场景逐镜审核在 G0–G2 始终保留；G3 只在“视频生成校验通过”时自动通过，
这里的“自动通过”是指由代码依据机械校验结果置为 approved，并记入 review.json。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

GEARS = ("G0", "G1", "G2", "G3", "G4", "G5", "G6")
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
    "G6": {"asset_text": "auto", "asset_portrait": "auto", "chapter_script": "auto",
           "shot_review": "auto", "chapter_compose": "auto", "auto_add": "auto"},
}

# 小场景/场景图审核由代码依据机械校验自动通过的挡位
AUTO_REVIEW_GEARS = ("G3", "G4", "G5", "G6")
# 必须配置预算上限的挡位（付费接口无人值守）
BUDGET_GEARS = ("G3", "G6")
# 队列自动完成全书素材（设定补全、性别、定妆图）的挡位
FULL_AUTO_GEARS = ("G6",)

# 失败恢复的重试次数上限。None = 不设固定上限（只受停止请求、预算与可选的单章重试上限约束）。
# G4/G5 有固定上限，避免在付费接口上无限重试；G6 持续重试、不跳过失败章节，由必填的预算兜底。
RETRY_LIMITS = {"G3": 0, "G4": 1, "G5": 3, "G6": None}
RECOVERY = {
    "G4": "失败后换策略一次：降低口播块长度（重新切分镜头）后重跑该章",
    "G5": "失败后重置该章失败阶段并重试，最多 3 次，仍失败则停止并记录",
    "G6": "失败的章节持续重试、不跳过（直到成功、用户停止或预算用尽）；另外素材（设定、性别、定妆图）由队列自动补全并确认",
}

# 预算里可选的「单章最多重试次数」：仅 G6 使用；缺省或 0 表示不限制次数
CHAPTER_RETRY_KEY = "max_chapter_retries"
# 重试之间的等待秒数：第 n 次重试前等待 min(MAX, BASE * n)，避免在付费接口上连续猛砸
RETRY_BACKOFF_BASE_SEC = 5.0
RETRY_BACKOFF_MAX_SEC = 60.0

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


def retry_limit(gear: str, budget: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """失败恢复的重试上限；None 表示不设固定上限。
    G6 默认不设上限，预算里的 max_chapter_retries（正整数）可以给它加一个上限；其余挡位用 RETRY_LIMITS。"""
    g = normalize_gear(gear)
    limit = RETRY_LIMITS.get(g, 0)
    if limit is None:
        raw = (budget or {}).get(CHAPTER_RETRY_KEY)
        try:
            n = int(raw) if raw not in (None, "") else 0
        except (TypeError, ValueError):
            n = 0
        return n if n > 0 else None
    return limit


def retry_backoff_sec(attempt: int) -> float:
    """第 attempt 次（从 1 开始）重试前的等待秒数。"""
    return min(RETRY_BACKOFF_MAX_SEC, RETRY_BACKOFF_BASE_SEC * max(1, attempt))


def is_auto(gear: str, checkpoint: str) -> bool:
    if checkpoint in ALWAYS_MANUAL:
        return False
    if checkpoint not in _POLICY["G0"]:
        raise GateError(f"未知检查点：{checkpoint}")
    return _POLICY[normalize_gear(gear)][checkpoint] == "auto"


def policy_table() -> Dict[str, Dict[str, str]]:
    return {g: dict(v) for g, v in _POLICY.items()}


def _budget_keys(mode: str) -> tuple:
    """动态漫模式不调用视频接口，只需要图像与耗时上限（见 render_mode.py）。"""
    if mode == "still":
        return ("max_image_calls", "max_minutes")
    return ("max_video_calls", "max_image_calls", "max_minutes")


def require_budget(budget: Dict[str, Any], usage: Dict[str, int], minutes_used: float, gear: str,
                   mode: str = "video") -> None:
    """G3 与 G6 必须配置预算上限；超出上限时抛出 GateError。

    usage: {"video": n, "image": n}；minutes_used: 已用分钟数。mode 为项目的渲染方式。
    """
    if normalize_gear(gear) not in BUDGET_GEARS:
        return
    validate_budget(budget, gear, mode)
    limits = {"max_video_calls": "video", "max_image_calls": "image"}
    for lim, kind in limits.items():
        if lim not in _budget_keys(mode):
            continue
        if usage.get(kind, 0) >= int(budget[lim]):
            raise GateError(f"预算已用尽：{kind} 调用 {usage.get(kind, 0)} / {budget[lim]}")
    if minutes_used >= float(budget["max_minutes"]):
        raise GateError(f"预算已用尽：耗时 {minutes_used:.1f} / {budget['max_minutes']} 分钟")


def validate_budget(budget: Dict[str, Any], gear: str, mode: str = "video") -> None:
    """只检查预算是否“已配置”（G3 必填）；上限为 0 是合法配置，表示立即停止。
    是否超限属于运行时检查，由 require_budget 负责，不在保存设置时拦截。
    动态漫模式不要求视频调用上限。"""
    if normalize_gear(gear) not in BUDGET_GEARS:
        return
    missing = [k for k in _budget_keys(mode) if budget.get(k) in (None, "")]
    if missing:
        raise GateError(f"{normalize_gear(gear)} 挡位必须设置预算上限：" + "、".join(missing))
