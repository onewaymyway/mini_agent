"""render_mode.py — 渲染方式（视频 / 动态漫）的字段、阶段表与门禁。

- video：每个镜头由「参考图 + 配音」生成小场景视频（C-8），现有流程，默认值。
- still：每个镜头生成场景图，再与配音、字幕合成为带动效的片段（动态漫模式，见
  next_doc/novel_to_video_still_comic_mode_improvement_plan.md）。

渲染方式在项目创建时确定，之后只读：本模块不提供修改接口。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

VIDEO = "video"
STILL = "still"
RENDER_MODES = (VIDEO, STILL)
DEFAULT_RENDER_MODE = VIDEO

# 各模式的章节流水线阶段（顺序即执行顺序）
STAGES: Dict[str, List[str]] = {
    VIDEO: ["script", "shots", "assets", "audio", "prompts", "clips", "compose"],
    STILL: ["script", "shots", "assets", "audio", "prompts", "stills", "segments", "compose"],
}

# 动态漫模式中已经实现的阶段。S3 增加 stills，S4 增加 segments；全部实现后整章可以运行。
STILL_IMPLEMENTED: frozenset = frozenset(STAGES[STILL])

# 动态漫模式的设置（项目 still 节）。transition 与 transition_sec 在 S6/S7 使用。
STILL_DEFAULTS: Dict[str, Any] = {
    "motion": "auto_alternate",      # 镜内动效：auto_alternate / push_in / pull_out / none（S5 追加更多）
    "motion_strength": "slow",       # slow / medium
    "transition": "fade",            # 镜间转场：fade / cut（S7 追加 dissolve）
    "transition_sec": 0.4,
    "silent_shot_sec": 3,            # 没有配音的镜头使用的时长（秒）
}


TRANSITIONS = ("fade", "cut")
MAX_TRANSITION_SEC = 1.0


def still_settings(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """动态漫设置：项目里保存的值覆盖默认值；旧项目没有该节时全部取默认。"""
    merged = dict(STILL_DEFAULTS)
    merged.update((meta or {}).get("still") or {})
    return merged


def validate_transition(cfg: Dict[str, Any]) -> None:
    """镜间转场设置必须合法：fade 时长在 (0, 1] 秒之间；cut 不需要时长。"""
    if cfg.get("transition") not in TRANSITIONS:
        raise ValueError(f"镜间转场必须是 {' / '.join(TRANSITIONS)} 之一，收到：{cfg.get('transition')!r}")
    if cfg["transition"] == "fade":
        try:
            sec = float(cfg.get("transition_sec"))
        except (TypeError, ValueError):
            raise ValueError("淡化时长必须是数字（秒）") from None
        if not 0 < sec <= MAX_TRANSITION_SEC:
            raise ValueError(f"淡化时长必须在 0 到 {MAX_TRANSITION_SEC} 秒之间，收到：{sec}")


def normalize(value: Any) -> str:
    """空值视为默认（视频）；其余必须是 RENDER_MODES 之一，否则 ValueError。"""
    if value is None or value == "":
        return DEFAULT_RENDER_MODE
    if value not in RENDER_MODES:
        raise ValueError(f"渲染方式必须是 {' / '.join(RENDER_MODES)} 之一，收到：{value!r}")
    return value


def mode_of(project: Optional[Dict[str, Any]]) -> str:
    """读取项目的渲染方式。旧项目没有该字段，按视频模式处理。"""
    return normalize((project or {}).get("render_mode"))


def stages_for(mode: str) -> List[str]:
    return list(STAGES[normalize(mode)])


def unsupported_reason(mode: str) -> Optional[str]:
    """该模式当前是否能运行整章。返回 None 表示可以运行，否则为给作者看的原因。"""
    mode = normalize(mode)
    if mode == VIDEO:
        return None
    missing = [s for s in STAGES[STILL] if s not in STILL_IMPLEMENTED]
    if missing:
        return "动态漫模式尚未实现（待实现阶段：" + "、".join(missing) + "），请先使用视频模式"
    return None
