"""
role_agents/verdict.py — 判官类 Agent 结构化输出（JudgeVerdict）解析

背景：GoalJudge / TurnJudge 此前要求模型输出一段 Markdown（"**结论**\n...\n
GOAL_STATUS: DONE"），调用方靠正则从自由文本里"抠"出状态关键字
（feedback.py::extract_goal_status / extract_turn_status）。这在模型输出
略有偏差（比如把关键字写进代码块、多写了一行、大小写不一致）时很脆弱，
而且"反馈内容"和"状态关键字"混在同一段文本里，展示层还得再用正则去掰开
（如 agent/role_judge.py 里那段提取"**反馈**"段落的兜底正则）。

本模块的做法：约定判官类 Agent 直接输出一个 JSON 对象（也允许包裹在
```json 代码块或夹杂在少量说明文字里——用 json_repair 兜底容错），
形如：

    {"status": "CONTINUE", "feedback": "...", "checklist": [...]}

`parse_judge_verdict` 负责把这段文本解析成结构化的 `JudgeVerdict`，状态
字段做大小写归一化 + 白名单校验，校验不通过（不是 JSON / 缺 status 字段 /
status 不在允许列表里）一律返回 `parse_ok=False` + 调用方传入的保守兜底
状态，绝不会让解析失败被误判成某个具体的合法状态。

这是一个新增能力，不强制所有判官类型迁移——只有 GoalJudge/TurnJudge 这类
需要用状态机驱动外层循环的判官才需要调用它；evaluator/coach 这类只是把
原始文本转成反馈注入历史，不需要状态机，继续用原来的纯文本输出即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import json_repair


@dataclass
class JudgeVerdict:
    """判官类 Agent 一次结构化判定的解析结果。"""
    status: str                      # 归一化为大写后的状态；解析失败时等于调用方传入的 fallback_status
    feedback: str = ""               # 人类可读的反馈/理由文本
    parse_ok: bool = True            # 是否成功解析出合法 JSON 且 status 在白名单内
    raw: str = ""                    # 原始文本（未经任何处理），供展示层兜底/排查
    extra: dict = field(default_factory=dict)  # status/feedback 之外的其它字段（如 checklist）


def parse_judge_verdict(
    text: str,
    *,
    valid_statuses: list[str],
    fallback_status: str,
    status_key: str = "status",
    feedback_key: str = "feedback",
) -> JudgeVerdict:
    """把判官 Agent 的原始输出解析为结构化的 `JudgeVerdict`。

    Args:
        text: 判官 Agent 的原始输出文本（期望是 JSON，允许有少量包裹文字/代码块围栏）。
        valid_statuses: 合法状态白名单（如 ["DONE", "CONTINUE", "NEED_COMPACT"]）。
        fallback_status: 解析失败（非 JSON / 缺字段 / status 不在白名单）时使用的保守兜底状态，
            调用方应该传入"绝不会被误当成已完成/已放行"的值
            （如 GoalJudge 传 "CONTINUE"，TurnJudge 传 "NEED_USER"）。
        status_key / feedback_key: JSON 对象里状态/反馈字段的键名，默认 "status" / "feedback"，
            自定义判官类型如需不同字段名可覆盖。

    Returns:
        `JudgeVerdict`。`parse_ok=False` 时 `status` 恒等于 `fallback_status`、
        `feedback` 恒为空字符串——调用方不应该在解析失败时信任 feedback 内容，
        应该回退到展示/使用原始 `raw` 文本。
    """
    raw = text or ""

    try:
        result: Any = json_repair.loads(raw)
    except Exception:
        result = None

    if not isinstance(result, dict):
        return JudgeVerdict(status=fallback_status, feedback="", parse_ok=False, raw=raw)

    status_val = result.get(status_key)
    if not isinstance(status_val, str) or not status_val.strip():
        return JudgeVerdict(status=fallback_status, feedback="", parse_ok=False, raw=raw)

    status_norm = status_val.strip().upper()
    valid_upper = {s.upper() for s in valid_statuses}
    if status_norm not in valid_upper:
        return JudgeVerdict(status=fallback_status, feedback="", parse_ok=False, raw=raw)

    feedback_val = result.get(feedback_key, "")
    feedback_str = feedback_val if isinstance(feedback_val, str) else str(feedback_val)

    extra = {k: v for k, v in result.items() if k not in (status_key, feedback_key)}

    return JudgeVerdict(
        status=status_norm, feedback=feedback_str, parse_ok=True, raw=raw, extra=extra,
    )
