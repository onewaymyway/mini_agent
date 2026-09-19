"""world_simulator/policy_feedback.py — 用户反馈反哺画像（阶段三十四，
4.5 节第三批）

设计依据：`next_doc/world_simulator_agent_preview_and_adaptive_policy_
plan.md` 第 5 节。自动挡运行中，用户如果觉得代理某一步的选择"不符合
预期"，可以就地写一句理由；这条反馈不会自动改画像（画像永远只能由
用户自己在编辑页手动调整——同父文档"透明度红线"），只是被记下来，供
画像编辑页汇总展示，让用户自己判断要不要据此调整画像。

**与子方案原文的一处出入（实施前发现，如实记录）**：子方案 5.1 节
假设"自动挡运行已有 review_mode（人工复核）机制，用户可以在复核时
标记某次选择'不符合预期'并写理由"，但实际读代码发现，现有
`review_mode` 只是"重大决策时暂停连续推进"，并没有任何"标记这一步
不符合预期 + 写理由"的既成入口。因此本模块落地时，在 `app.py` 时间线
的自动挡步骤上新增了一个轻量入口（一个折叠区 + 一段理由文本框），
而不是"复用"一个原本就不存在的入口——这是子方案范围内最小的必要
补充，不违背"系统只展示/建议，画像调整必须由用户手动操作"这条红线。

存储作用域：`autopilot.py` 4.5 第一批实施时已确认，画像
（`manifest.autopilot`）按"每条分支各自独立"存储（见
`engine/management.py::set_pilot_config()`），因此这里的反馈记录也
按 `sim_id` + `branch` 存取，不做成跨模拟的全局记录。

落盘位置：`data/<sim_id>/policy_feedback.jsonl`，与 `reality_checks.
jsonl` 平级，复用同样的 `atomic_write_jsonl` 降级导入方式。

范围克制（同父文档/子方案）：
- 不做任何自动改画像的逻辑——本模块只负责"记录 + 简单关键词分组
  统计触发一句归纳提示"，`risk_preference`/`conditional_policies`
  的实际取值调整永远只能通过 `autopilot.set_pilot_config()` 由用户
  在编辑页手动保存。
- 归纳提示只用固定关键词分组的命中次数统计生成，不做任何 NLP 语义
  聚类（同 4.4 节 `error_category` 不做自动语义判定的一贯取舍）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mini_agent.utils.atomic_write import atomic_write_jsonl
except ImportError:  # 独立运行、未装 mini_agent 时的降级实现，同 reality_check.py

    def atomic_write_jsonl(path: Path, records: list) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
            + ("\n" if records else ""),
            encoding="utf-8",
        )


class PolicyFeedbackError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class PolicyFeedbackNote:
    """一条"用户觉得代理这一步选择不符合预期"的反馈记录（4.5 节
    第三批，5.2 节字段集）。"""

    id: str
    sim_id: str
    branch: str
    step: int
    user_reason: str
    created_at: str
    acknowledged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sim_id": self.sim_id,
            "branch": self.branch,
            "step": self.step,
            "user_reason": self.user_reason,
            "created_at": self.created_at,
            "acknowledged": self.acknowledged,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicyFeedbackNote":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            sim_id=str(data.get("sim_id") or ""),
            branch=str(data.get("branch") or "main"),
            step=int(data.get("step") or 0),
            user_reason=str(data.get("user_reason") or ""),
            created_at=str(data.get("created_at") or ""),
            acknowledged=bool(data.get("acknowledged", False)),
        )


# ── 落盘路径与读写 ───────────────────────────────────────────────────


def policy_feedback_path(data_dir: Path, sim_id: str) -> Path:
    return Path(data_dir) / sim_id / "policy_feedback.jsonl"


def load_all(data_dir: Path, sim_id: str) -> List[PolicyFeedbackNote]:
    path = policy_feedback_path(data_dir, sim_id)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(PolicyFeedbackNote.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # 单行损坏不应该让整个记录不可用，跳过即可
    return items


def _save_all(data_dir: Path, sim_id: str, items: List[PolicyFeedbackNote]) -> None:
    atomic_write_jsonl(policy_feedback_path(data_dir, sim_id), [it.to_dict() for it in items])


# ── 记录与确认 ───────────────────────────────────────────────────────


def record_feedback(
    data_dir: Path,
    sim_id: str,
    *,
    branch: str,
    step: int,
    user_reason: str,
) -> PolicyFeedbackNote:
    """记录一条"这一步代理的选择不符合预期"反馈（4.5 节 5.2）。

    Raises:
        PolicyFeedbackError: `user_reason` 为空。
    """
    reason = user_reason.strip()
    if not reason:
        raise PolicyFeedbackError("请填写「为什么觉得不符合预期」，不能为空")
    item = PolicyFeedbackNote(
        id=uuid.uuid4().hex[:12],
        sim_id=sim_id,
        branch=branch,
        step=step,
        user_reason=reason,
        created_at=_now_iso(),
        acknowledged=False,
    )
    existing = load_all(data_dir, sim_id)
    existing.append(item)
    _save_all(data_dir, sim_id, existing)
    return item


def acknowledge(data_dir: Path, sim_id: str, note_id: str) -> Optional[PolicyFeedbackNote]:
    """把某条反馈标记为"已阅"（4.5 节 5.3）——只影响展示层是否继续
    提示，不删除记录本身。找不到对应 id 时返回 None，不抛异常（调用方
    通常是点了"已阅"按钮后的幂等操作，重复点击/记录已被其它方式清理
    都不应该让页面报错）。
    """
    items = load_all(data_dir, sim_id)
    updated = None
    for it in items:
        if it.id == note_id:
            it.acknowledged = True
            updated = it
            break
    if updated is not None:
        _save_all(data_dir, sim_id, items)
    return updated


def find_for_step(
    data_dir: Path, sim_id: str, *, branch: str, step: int
) -> List[PolicyFeedbackNote]:
    """返回某个具体 step 已经记录过的反馈，供 `app.py` 判断"这一步是否
    已经反馈过"（同 `reality_check.find_for_step` 的既有模式）。"""
    return [
        it for it in load_all(data_dir, sim_id) if it.branch == branch and it.step == step
    ]


# ── 关键词分组统计 + 归纳提示（4.5 节 5.4） ─────────────────────────

_KEYWORD_GROUPS: Dict[str, List[str]] = {
    "too_conservative": ["太保守", "太谨慎", "太犹豫"],
    "too_aggressive": ["太冒险", "太激进", "不计后果"],
}

_GROUP_SUGGESTIONS: Dict[str, str] = {
    "too_conservative": "要不要调整风险偏好，或者在情境化策略里补一条「倾向更积极」的规则？",
    "too_aggressive": "要不要调整风险偏好，或者在情境化策略里补一条「倾向更谨慎」的规则？",
}

_THRESHOLD = 3
"""同一关键词分组命中次数达到这个阈值才生成归纳提示，避免偶发的一两次
反馈就打扰用户（4.5 节 5.4：\"最简单的规则\"）。"""


def _match_group(user_reason: str) -> Optional[str]:
    for group, keywords in _KEYWORD_GROUPS.items():
        if any(kw in user_reason for kw in keywords):
            return group
    return None


def summarize_feedback(
    items: List[PolicyFeedbackNote], *, branch: Optional[str] = None
) -> Dict[str, Any]:
    """对一组反馈做"未确认列表 + 关键词分组归纳提示"的最小汇总
    （4.5 节 5.3/5.4）。

    `branch` 非 None 时只统计该分支的记录（画像按分支独立存储，见
    模块 docstring）；为 None 时不筛选分支（一般不会这样调用，保留
    是为了方便单测直接传入已经筛好的列表）。

    只做简单的关键词命中次数统计，不做任何语义分析——见模块 docstring
    "范围克制"。

    Returns:
        `{"unacknowledged": [未确认的 PolicyFeedbackNote, 按 created_at
        倒序], "suggestion": 归纳提示文本或 None}`。
    """
    scoped = [it for it in items if branch is None or it.branch == branch]
    unacked = sorted(
        (it for it in scoped if not it.acknowledged),
        key=lambda it: it.created_at,
        reverse=True,
    )

    group_counts: Dict[str, int] = {}
    for it in unacked:
        group = _match_group(it.user_reason)
        if group:
            group_counts[group] = group_counts.get(group, 0) + 1

    suggestion = None
    for group, count in group_counts.items():
        if count >= _THRESHOLD:
            suggestion = (
                f"最近 {count} 次反馈里，你都提到 Agent 的选择"
                f"{'「太保守」' if group == 'too_conservative' else '「太冒险」'}。"
                f"{_GROUP_SUGGESTIONS[group]}"
            )
            break  # 一次只展示一条归纳提示，避免同时命中两个方向时信息过载

    return {"unacknowledged": unacked, "suggestion": suggestion}
