"""evolution/focus_research_trigger.py — 焦点驱动调研触发器（阶段二）

见 next_doc/goal_tree_research_and_action_recommendation_plan.md §4.2、
§五 分阶段实施规划第 2 项，以及阶段一实施记录里对 §4.1 的调整说明。

职责：给定树上一个"现阶段焦点"节点（`GoalBacklog.focus_research_nodes()`
返回的集合，阶段一新增），判断是否值得触发一次调研，触发时生成/合并一条
`GrowthCandidate`（`origin="focus_research"`），走现有
`GrowthBacklog.add_or_merge()` 的"生成 → 用户确认"范式——不新开一条候选
队列，复用现有的 accept/dismiss/自动持续调研（`auto_pursue_candidate`）
全套机制。

跟 `GoalTreeDecomposer` 的关系：`GoalTreeDecomposer` 负责"这个节点该不该
拆子节点"（结构性建议），本模块负责"这个节点该不该主动查点相关信息"
（调研候选），两者触发时机不同、各自节奏治理，互不覆盖，都通过
`focus_research_nodes()`/相关性判断做节流，避免同一节点被两套机制同时
高频打扰。

本阶段（阶段二）只接入 CLI 手动触发（`trigger()` 方法可以被任何调用方
直接调用），真正接入"`current_focus_ids` 变化后自动触发"留到阶段四，
那时候会在 `sys:goal_tree_focus_recompute` 巡检发现焦点变化后调用本模块，
这里先把判断/触发逻辑写成独立可测试的方法，阶段四直接复用。

节奏治理（§4.4）：
  - 同一节点两次触发之间至少间隔按层级区分的最小间隔（结构节点
    domain/stage 明显长于叶子 goal，量级参考 §4.4"以周为单位"/
    "以天为单位"）；
  - 候选生成本身复用 `GrowthBacklog.add_or_merge()` 已有的字面去重/
    冷却期/pending 上限逻辑，不重复造一套。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.growth_advisor import GrowthCandidate
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
    from mini_agent.storage.paths import AgentPaths


_STATE_FILENAME = "goal_tree_focus_research_state.json"

# 结构节点（domain/stage）：以周为单位；叶子 Goal：以天为单位（§4.4）。
MIN_INTERVAL_SECONDS_STRUCTURAL = 7 * 86400
MIN_INTERVAL_SECONDS_GOAL = 2 * 86400

# 复用 GrowthAdvisorConfig 的默认节流参数量级作为兜底，cfg 不可用时使用。
DEFAULT_MAX_PENDING = 10
DEFAULT_DISMISSED_COOLDOWN_DAYS = 30

# 焦点触发是"结构性动机"（树告诉系统"现在该关注这里"），不是"外部证据
# 数量"，只要求 1 条占位证据即可——跟 soft_goal_deriver/goal_relevance
# 那种"证据数量门槛"场景的语义不同，不应该用同一套阈值。
FOCUS_EVIDENCE_REF_MIN_COUNT = 1

# 供候选证据引用使用的前缀，跟其它证据引用（memory entry_id）风格区分开，
# 一眼能看出这条候选是"焦点驱动"而不是"信号扫描"产生的。
FOCUS_EVIDENCE_REF_PREFIX = "goal_tree"


def _min_interval_for(node: "GoalNode") -> float:
    return (
        MIN_INTERVAL_SECONDS_STRUCTURAL if node.is_structural
        else MIN_INTERVAL_SECONDS_GOAL
    )


class FocusResearchTrigger:
    def __init__(self, paths: "AgentPaths", backlog: "GoalBacklog") -> None:
        self._paths = paths
        self._backlog = backlog

    # ── 节奏治理状态：上次触发时间 ───────────────────────────────────────────

    @property
    def _state_path(self) -> Path:
        return self._paths.workdir_dir / _STATE_FILENAME

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def last_triggered_at(self, node_id: str) -> float:
        return float(self._load_state().get(node_id, 0.0))

    def record_trigger(self, node_id: str) -> None:
        try:
            data = self._load_state()
            data[node_id] = time.time()
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.focus_research_trigger.record_trigger")

    # ── 节奏治理：是否可以触发 ───────────────────────────────────────────────

    def should_trigger(self, node: "GoalNode") -> Optional[str]:
        """返回 `None` 表示可以触发一次调研；返回字符串是跳过原因（供
        日志/CLI 展示，不是异常）。"""
        last = self.last_triggered_at(node.id)
        min_interval = _min_interval_for(node)
        if last and time.time() - last < min_interval:
            remain_days = round((min_interval - (time.time() - last)) / 86400, 1)
            return f"距上次调研触发不足最小间隔，还需等待约 {remain_days} 天"
        return None

    # ── prompt/rationale 拼装 ────────────────────────────────────────────────

    def _ancestor_titles(self, node: "GoalNode") -> list[str]:
        """从根到该节点（不含自身）的祖先标题链，跟 `GoalTreeDecomposer.
        _ancestor_chain()` 同样的"让下游理解这是在为哪个更大目标服务"的
        动机，存在环（数据异常）时提前截断，避免死循环。"""
        titles: list[str] = []
        seen: set[str] = set()
        cur = node
        while cur.parent_id and cur.parent_id not in seen:
            parent = self._backlog.get(cur.parent_id)
            if parent is None:
                break
            seen.add(cur.parent_id)
            titles.append(parent.title)
            cur = parent
        titles.reverse()
        return titles

    def _build_rationale(self, node: "GoalNode") -> str:
        ancestors = self._ancestor_titles(node)
        context = " → ".join(ancestors + [node.title]) if ancestors else node.title
        level_label = {
            "domain": "领域方向", "stage": "阶段目标",
            "goal": "目标", "objective": "子任务",
        }.get(node.level, node.level)
        return (
            f"「{context}」目前是你的现阶段焦点（{level_label}），"
            "建议主动了解相关信息，帮助想清楚下一步具体该怎么推进。"
        )

    # ── 触发 ────────────────────────────────────────────────────────────────

    def trigger(
        self,
        node_id: str,
        *,
        cfg=None,
        llm_helper: Optional[Callable[[str], str]] = None,
        force: bool = False,
    ) -> Optional["GrowthCandidate"]:
        """针对 `node_id` 触发一次调研候选生成，返回新建/命中合并的
        `GrowthCandidate`；节点不存在、节奏治理跳过、或 `add_or_merge()`
        判定不该生成（比如冷却期内/pending 已满）时返回 `None`，不抛异常。

        `force=True` 跳过 `should_trigger()` 的节奏治理检查（CLI 手动
        触发场景，用于调试）。

        是否记录本次触发时间：只要节点存在就记录（跟
        `GoalTreeDecomposer.decompose()` 一致的"记录 attempt，不等生成
        结果"的语义，避免节奏治理被"总是被 add_or_merge 去重挡掉"的节点
        绕开、导致同一节点被反复高频尝试）。

        `llm_helper` 只在 `add_or_merge()` 内部用于"语义判重"这一步，是
        可选的轻量调用，不传时退化为纯字面去重——跟设计文档 §4.4
        "LLM 调用轻量化、可关闭"原则一致，不是本方法的必需依赖。
        """
        node = self._backlog.get(node_id)
        if node is None:
            return None
        if not force:
            skip_reason = self.should_trigger(node)
            if skip_reason:
                return None

        self.record_trigger(node.id)

        from mini_agent.evolution.growth_advisor import GrowthBacklog

        if cfg is None:
            try:
                from mini_agent.config.models import GrowthAdvisorConfig
                cfg = GrowthAdvisorConfig()
            except Exception:
                cfg = None
        max_pending = getattr(cfg, "max_pending_candidates", DEFAULT_MAX_PENDING)
        dismissed_cooldown_days = getattr(
            cfg, "dismissed_cooldown_days", DEFAULT_DISMISSED_COOLDOWN_DAYS,
        )

        existing_goal_titles = [g.title for g in self._backlog.active_goals()]

        backlog = GrowthBacklog(self._paths)
        return backlog.add_or_merge(
            title=node.title,
            rationale=self._build_rationale(node),
            evidence_refs=[f"{FOCUS_EVIDENCE_REF_PREFIX}:{node.id}"],
            min_evidence_count=FOCUS_EVIDENCE_REF_MIN_COUNT,
            max_pending=max_pending,
            dismissed_cooldown_days=dismissed_cooldown_days,
            origin="focus_research",
            llm_helper=llm_helper,
            existing_goal_titles=existing_goal_titles,
        )


def find_newly_focused_nodes(
    backlog: "GoalBacklog", previous_focus_ids: set,
) -> list["GoalNode"]:
    """[§4.2 触发时机"焦点变化"] 对比上一次巡检记录的焦点节点集合和当前
    `focus_research_nodes()` 的结果，返回"新进入焦点"的节点——这是最该
    触发一次调研的时机（"刚成为现阶段该关注的事"）。

    只读查询，供阶段四接入 `sys:goal_tree_focus_recompute` 巡检后调用；
    `previous_focus_ids` 由调用方持久化维护（阶段四实施时定具体存储
    位置），本函数本身不读写任何状态，行为纯粹是"集合差集"。
    """
    current = backlog.focus_research_nodes()
    return [n for n in current if n.id not in previous_focus_ids]
