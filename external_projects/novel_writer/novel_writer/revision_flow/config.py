"""revision_flow 可配置上限。默认值保守；后续由界面修改，写入 _revision/settings.json。"""
from __future__ import annotations

from typing import Any, Dict

from novel_writer.revision_flow.common import FlowError, load_json, revision_root, save_json

# 评估维度。默认只开客观性强的五类；主观、误报多的三类需作者手动开启（保守的 opt-in 默认）
REVIEW_ALL_DIMENSIONS = ("contradiction", "continuity", "timeline", "character_logic", "foreshadow",
                         "pacing", "redundancy", "style_drift")
REVIEW_DEFAULT_DIMENSIONS = ("contradiction", "continuity", "timeline", "character_logic", "foreshadow")

DEFAULTS: Dict[str, Any] = {
    "max_chapters_per_run": 20,        # 单次运行最多改写的章数，超出则暂停，续跑时继续
    "max_llm_calls_per_run": 60,       # 单次运行最多 LLM 调用次数（含重试）
    "max_attempts_per_chapter": 2,     # 单章最多尝试次数（首次 + 带反馈的重试）
    "length_ratio_min": 0.7,           # 改写后字数 / 原字数 下限
    "length_ratio_max": 1.3,           # 改写后字数 / 原字数 上限
    "stop_on_failure": True,           # 某章失败即停止（保证后续章节不依赖未完成的前文）
    "review_scope": "hits",            # 候选范围：hits=只判定命中定位词的章节；all=全书逐章判定（设定变更，成本高）
    # ── 全书评估（review_*，见 next_doc/novel_writer_post_completion_review_and_outline_regen_plan.md）──
    "review_max_llm_calls": 0,         # 单次评估最多 LLM 调用次数；0 = 不设上限（作者已确认不设上限）
    "review_dimensions": list(REVIEW_DEFAULT_DIMENSIONS),   # 启用的评估维度；节奏冗余 / 文风漂移默认关闭
    "review_window": 5,                # 衔接审阅的窗口章数（相邻窗口重叠 1 章）
    "review_snippet_chars": 300,       # 仲裁时每处原文片段的前后长度（字）
}

# (类型, 最小, 最大)
_BOUNDS: Dict[str, tuple] = {
    "max_chapters_per_run": (1, 200, int),
    "max_llm_calls_per_run": (1, 1000, int),
    "max_attempts_per_chapter": (1, 5, int),
    "length_ratio_min": (0.1, 1.0, float),
    "length_ratio_max": (1.0, 3.0, float),
    "stop_on_failure": (None, None, bool),
    "review_scope": (None, None, str),
    "review_max_llm_calls": (0, 100000, int),
    "review_dimensions": (None, None, list),
    "review_window": (2, 20, int),
    "review_snippet_chars": (50, 2000, int),
}


def _settings_path(ws):
    return revision_root(ws) / "settings.json"


def validate(patch: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise FlowError(f"未知配置项：{key}")
        lo, hi, kind = _BOUNDS[key]
        if kind is bool:
            if not isinstance(value, bool):
                raise FlowError(f"{key} 必须是布尔值")
        elif kind is list:
            if (not isinstance(value, list) or not value
                    or any(d not in REVIEW_ALL_DIMENSIONS for d in value)):
                raise FlowError(f"{key} 必须是非空列表，取值只能来自 {list(REVIEW_ALL_DIMENSIONS)}")
            value = [d for d in REVIEW_ALL_DIMENSIONS if d in value]   # 去重并按固定顺序
        elif kind is str:
            if value not in ("hits", "all"):
                raise FlowError(f"{key} 只能是 hits 或 all")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FlowError(f"{key} 必须是数字")
            if not (lo <= value <= hi):
                raise FlowError(f"{key} 超出范围 [{lo}, {hi}]")
            value = kind(value)
        out[key] = value
    if "length_ratio_min" in out and "length_ratio_max" in out and out["length_ratio_min"] >= out["length_ratio_max"]:
        raise FlowError("length_ratio_min 必须小于 length_ratio_max")
    return out


def load(ws) -> Dict[str, Any]:
    stored = load_json(_settings_path(ws), default={}) or {}
    merged = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return merged


def update(ws, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_json(_settings_path(ws), default={}) or {}
    current.update(validate(patch))
    save_json(_settings_path(ws), current)
    return load(ws)
