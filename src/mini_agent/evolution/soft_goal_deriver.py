"""
evolution/soft_goal_deriver.py — 软目标 derive（autonomous 档位专属）

当 AutonomousLoop 处于 autonomous 档位时，每次 tick 调用 SoftGoalDeriver.derive()：
  1. 读 capability_map  → 找 confidence < CONFIDENCE_LOW 的能力条目
  2. 读 work_index      → 找 next_suggested 非空且 30 天无 active Objective 的 WorkThread
  3. 读 lesson_review   → 找高频触发 (T1+) 的 LessonGroup，尚无对应 Goal 的
  4. 三路合并去重，每次最多输出 MAX_NEW_GOALS 个新 Goal
  5. 直接写入 GoalBacklog（source="agent_derived"，priority 比用户 Goal 低一级）

节奏治理：
  - 通过 phase_g_rhythm.json 记录上次 derive 时间，最少间隔 DERIVE_INTERVAL_SECONDS
  - 若 GoalBacklog 中 agent_derived 类 active Goal 已有 MAX_PENDING_DERIVED 个，跳过

用户体验：
  - derive 出的 Goal 会在 /digest 中以"💡 Agent 建议"形式展示
  - 用户可通过 /goals accept <id> 或 /goals reject <id> 显式处理
  - reject 的 Goal 被标记为 abandoned，30 天内不会再 derive 相同主题
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode


# ── 常量 ──────────────────────────────────────────────────────────────────────

CONFIDENCE_LOW          = 0.35   # capability_map 中低置信度阈值
STALE_WORKTHREAD_DAYS   = 30     # WorkThread next_suggested 超过此天数无进展则触发 derive
T1_THRESHOLD            = 3      # LessonGroup total_occurrence 高频门槛
DERIVE_INTERVAL_SECONDS = 21600  # 最少 6 小时 derive 一次
MAX_NEW_GOALS           = 2      # 每次 derive 最多产生新 Goal 数量
MAX_PENDING_DERIVED     = 5      # GoalBacklog 中 agent_derived active Goal 上限
REJECTED_TTL_SECONDS    = 30 * 86400  # rejected goal 的去重窗口（30 天）


# ── 候选来源 ──────────────────────────────────────────────────────────────────

@dataclass
class _DeriveCandidate:
    title: str
    description: str
    source_tag: str     # "capability" | "workthread" | "lesson"
    priority: int = 20  # agent_derived Goal 的优先级（低于用户 Goal 的默认 50）
    urgency: float = 0.0  # 用于排序，越高越先出

    def dedupe_key(self) -> str:
        """归一化 title 用于去重。"""
        s = self.title.lower().strip()
        s = re.sub(r"[^\w\s]", " ", s)
        return " ".join(sorted(s.split()))


# ── SoftGoalDeriver ───────────────────────────────────────────────────────────

class SoftGoalDeriver:
    """
    Autonomous 档位的软目标 derive 引擎。

    由 AutonomousLoop._tick_autonomous() 调用：
        deriver = SoftGoalDeriver(paths, cfg)
        if deriver.should_derive():
            new_goals = deriver.derive(goal_backlog)
    """

    def __init__(self, paths: "AgentPaths", cfg: "AppConfig") -> None:
        self._paths = paths
        self._cfg = cfg
        self._rhythm_path = paths.workdir_dir / "phase_g_rhythm.json"
        self._rejected_path = paths.workdir_dir / "soft_goal_rejected.json"

    # ── 节奏控制 ──────────────────────────────────────────────────────────────

    def should_derive(self) -> bool:
        """是否满足 derive 条件（时间间隔 + 不超过 pending 上限）。"""
        last = self._last_derive_at()
        if time.time() - last < DERIVE_INTERVAL_SECONDS:
            return False
        return True

    def _last_derive_at(self) -> float:
        try:
            data = json.loads(self._rhythm_path.read_text(encoding="utf-8"))
            return float(data.get("last_soft_goal_derive_at", 0.0))
        except Exception:
            return 0.0

    def _record_derive(self) -> None:
        try:
            data: dict = {}
            if self._rhythm_path.exists():
                data = json.loads(self._rhythm_path.read_text(encoding="utf-8"))
            data["last_soft_goal_derive_at"] = time.time()
            self._rhythm_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ── 已拒绝目标集 ──────────────────────────────────────────────────────────

    def _load_rejected_keys(self) -> set[str]:
        """加载用户已 reject 的 Goal dedupe_key（30 天内有效）。"""
        try:
            data = json.loads(self._rejected_path.read_text(encoding="utf-8"))
            now = time.time()
            return {
                k for k, ts in data.items()
                if now - float(ts) < REJECTED_TTL_SECONDS
            }
        except Exception:
            return set()

    def record_rejected(self, goal_title: str) -> None:
        """用户 reject 一个 Goal 后调用，30 天内不再 derive 相同主题。"""
        key = _DeriveCandidate(title=goal_title, description="", source_tag="").dedupe_key()
        try:
            data: dict = {}
            if self._rejected_path.exists():
                data = json.loads(self._rejected_path.read_text(encoding="utf-8"))
            data[key] = time.time()
            self._rejected_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def derive(self, goal_backlog: "GoalBacklog") -> list["GoalNode"]:
        """
        分析三路信号，生成新 Goal，写入 GoalBacklog，返回新增的 GoalNode 列表。
        调用前应先调用 should_derive() 确认条件满足。
        """
        # 检查 pending derived goal 上限
        existing_derived = [
            g for g in goal_backlog.active_goals()
            if g.source == "agent_derived" and g.status == "active"
        ]
        if len(existing_derived) >= MAX_PENDING_DERIVED:
            return []

        rejected_keys = self._load_rejected_keys()
        existing_titles = {
            _DeriveCandidate(title=g.title, description="", source_tag="").dedupe_key()
            for g in goal_backlog.active_goals()
        }

        candidates: list[_DeriveCandidate] = []

        # 信号 1：capability_map 低置信度
        candidates.extend(self._from_capability_map())

        # 信号 2：WorkThread next_suggested 积压
        candidates.extend(self._from_work_index())

        # 信号 3：高频 lesson 尚无对应目标
        candidates.extend(self._from_lesson_review())

        # 去重 + 过滤已有 / 已拒绝
        seen_keys: set[str] = set()
        filtered: list[_DeriveCandidate] = []
        for c in sorted(candidates, key=lambda x: x.urgency, reverse=True):
            key = c.dedupe_key()
            if key in seen_keys or key in rejected_keys or key in existing_titles:
                continue
            seen_keys.add(key)
            filtered.append(c)

        # 每次最多 MAX_NEW_GOALS 个
        to_create = filtered[:MAX_NEW_GOALS]
        if not to_create:
            self._record_derive()
            return []

        # 写入 GoalBacklog
        new_goals: list["GoalNode"] = []
        for c in to_create:
            goal = goal_backlog.add_goal(
                title=c.title,
                description=c.description,
                source="agent_derived",
                priority=c.priority,
            )
            new_goals.append(goal)

        self._record_derive()
        return new_goals

    # ── 三路信号采集 ──────────────────────────────────────────────────────────

    def _from_capability_map(self) -> list[_DeriveCandidate]:
        """
        信号 1：capability_map 中 confidence < CONFIDENCE_LOW 的条目。
        说明 agent 在该能力上经常失败，有必要主动练习/改进。
        """
        candidates = []
        try:
            from mini_agent.evolution.phase_g import load_capability_map
            entries = load_capability_map(self._paths)
            for entry in entries:
                if entry.confidence >= CONFIDENCE_LOW:
                    continue
                if entry.total_calls < 3:
                    continue  # 样本太少，不可靠
                urgency = (CONFIDENCE_LOW - entry.confidence) * 10 + entry.total_calls * 0.1
                candidates.append(_DeriveCandidate(
                    title=f"改善 {entry.capability_name} 的执行可靠性",
                    description=(
                        f"capability_map 记录：{entry.capability_name} 的成功率仅 "
                        f"{entry.confidence:.0%}（{entry.success_count}/{entry.total_calls} 次）。"
                        f"分析失败原因并改进相关工具使用或前置检查。"
                    ),
                    source_tag="capability",
                    priority=25,
                    urgency=urgency,
                ))
        except Exception:
            pass
        return candidates

    def _from_work_index(self) -> list[_DeriveCandidate]:
        """
        信号 2：WorkThread.next_suggested 非空，但超过 STALE_WORKTHREAD_DAYS 天无推进。
        说明 agent 自己建议的后续工作一直没有跟进。
        """
        candidates = []
        try:
            from mini_agent.perception.workdir_knowledge import load_work_index
            threads = load_work_index(self._paths)
            now = time.time()
            stale_cutoff = now - STALE_WORKTHREAD_DAYS * 86400
            for thread in threads:
                if not thread.next_suggested:
                    continue
                last_activity = getattr(thread, "last_activity_at", 0.0) or 0.0
                if last_activity > stale_cutoff:
                    continue  # 最近有活动，不触发
                stale_days = (now - last_activity) / 86400
                urgency = min(stale_days / STALE_WORKTHREAD_DAYS, 3.0)
                candidates.append(_DeriveCandidate(
                    title=thread.next_suggested[:80],
                    description=(
                        f"WorkThread [{thread.thread_id}] 建议的后续工作已搁置 "
                        f"{stale_days:.0f} 天：{thread.next_suggested}"
                    ),
                    source_tag="workthread",
                    priority=20,
                    urgency=urgency,
                ))
        except Exception:
            pass
        return candidates

    def _from_lesson_review(self) -> list[_DeriveCandidate]:
        """
        信号 3：T1+ 高频 LessonGroup，说明某类错误模式反复出现，
        应当主动 derive 一个改进 Goal。
        """
        candidates = []
        try:
            from mini_agent.perception.lesson_review import scan_lesson_groups
            groups = scan_lesson_groups(self._paths)
            for group in groups:
                if not group.meets_t1_threshold():
                    continue
                count = group.total_occurrence
                # urgency 正比于触发次数，T2/T3 额外加权
                urgency = count * 0.5
                if group.meets_t2_t3_threshold():
                    urgency *= 1.5
                # 从 trigger 文本提取主题关键词作为 Goal 标题
                trigger_sample = group.entries[0].trigger if group.entries else "未知触发"
                title = f"系统性解决：{trigger_sample[:50]}"
                candidates.append(_DeriveCandidate(
                    title=title,
                    description=(
                        f"LessonGroup 高频触发（{count} 次，"
                        f"{len(group.session_ids)} 个 session）。"
                        f"触发样例：{trigger_sample[:100]}。"
                        f"建议分析根因并修复相关工具调用或流程。"
                    ),
                    source_tag="lesson",
                    priority=30,  # lesson 来源比其他信号优先级略高
                    urgency=urgency,
                ))
        except Exception:
            pass
        return candidates


__all__ = ["SoftGoalDeriver"]
