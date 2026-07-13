"""
perception/self_model.py — AgentSelfModel（具身改进 v3 C1）

## 问题

代码库中存在三个命名相近但职责完全不同的"profile"概念：

  UserProfile (profile.py)
    → 主语：用户，跨项目，LLM 自动生成技术栈/习惯画像
    → 路径：~/.agent/users/<user_id>/profile.json

  RoleProfileManager (api/user_store.py)
    → 主语：多用户角色，项目级，记录关系/信任等级/社交画像
    → 路径：<project>/.agent/users/<user_id>/profile.json

  AgentProfile (orchestrator/agent_profiles.py)
    → 主语：SubAgent 角色定义，描述工具集/模型/系统提示
    → 与前两者完全不同，是"配置模板"而非"画像记录"

此外，`global_knowledge.SelfProfile` / `SelfAssessment` 记录的是
跨 session、慢变化的历史评估（"过去我在哪些领域强/弱"），没有
反映"这个 session / 这一轮，我现在感觉如何"的实时维度。

## 解法

不做破坏性重命名，而是引入 `AgentSelfModel` 作为聚合视图：

  - 从 SelfAssessment（global_knowledge.py）读取跨 session 历史评估
    → 即 `## Self-assessment (across past sessions)` 块，已由
      `_build_global_knowledge_block()` 注入。AgentSelfModel 不再
      重复注入这部分，只引用其中 agent 本轮还不知道的"当前 session
      实时状态"部分。

  - 从 capability_map（phase_g.py build_capability_map）读取
    当前 workdir 的技术领域置信度 → 这是"此刻 workdir 视角"的能力
    分布，与 SelfAssessment.confidence_by_domain（全局历史汇总）互补。

  - 从 ProprioceptionModule 读取最新一次 sense() 快照 → "此刻
    内部感受"（认知负荷、挫败感、剩余预算）

  - 从 AffordanceMap（affordance_analyzer.py，B4）读取当前 session
    构建好的余裕地图 → "此刻环境对我意味着什么"

生命周期：session 级别。Agent 在 session 初始化完成后（_inject 方法
被 SessionAgentPool 调用后）构建 AgentSelfModel 一次，之后在每轮
turn 开始时更新 `internal_state`（只更新这一个快变量，其余慢变量
不重新计算）。

注入方式：通过 ContextBuilder 新增的 `self_model_getter` callable，
与 `profile_text_getter` 同构，每次 build() 调用时读取最新状态。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.perception.proprioception import AgentInternalState
    from mini_agent.perception.affordance_analyzer import AffordanceMap, BehaviorContext
    from mini_agent.storage.paths import AgentPaths


@dataclass
class AgentSelfModel:
    """
    Agent 自身状态的 session 级聚合视图。

    与现有三个 profile 的关系（语义澄清）：

      UserProfile (profile.py)
        → 用户维度：记录用户的技术栈 / 偏好 / 工作习惯
        → AgentSelfModel 不替代它，而是补充"Agent 自身维度"

      RoleProfileManager (api/user_store.py)
        → 社交维度：记录多用户的角色 / 信任等级
        → 与 AgentSelfModel 正交，不重叠

      AgentProfile (orchestrator/agent_profiles.py)
        → 配置维度：SubAgent 的工具集 / 模型 / 提示模板
        → 是"角色定义"，不是"运行时状态"

      SelfAssessment (global_knowledge.py)
        → 历史评估维度：跨 session 的能力强弱总结，慢变量
        → 已由 _build_global_knowledge_block() 注入 system prompt
        → AgentSelfModel 不重复注入这部分，只增量补充实时状态

    字段说明：
      capability_snapshot  — 当前 workdir 的领域置信度（来自 phase_g），
                             只在 session 开始时构建一次（慢变量）
      affordance_summary   — 当前环境行动可能性摘要（来自 AffordanceMap，B4），
                             session 开始时构建一次（慢变量）
      internal_state       — 最新一次 ProprioceptionModule.sense() 快照
                             （来自 B1），每轮 turn 更新（快变量）
      active_skill_count   — 当前 session 加载的 skill 数量，供 LLM 感知
                             自身工具丰富程度
      session_start_at     — 当前 session 启动时间（Unix timestamp）
    """

    # 慢变量（session 开始时建立，不逐 turn 更新）
    capability_snapshot: dict[str, float] = field(default_factory=dict)
    affordance_summary: str = ""
    active_skill_count: int = 0
    session_start_at: float = field(default_factory=time.time)
    # [打通具身感知与行为感知] AffordanceAnalyzer 内部已经算好的 BehaviorContext
    # （用户近期 git/terminal 活动、是否在场），之前只被揉进
    # affordance_summary/system prompt 的文本片段里，下游想程序化读取（而不是
    # 靠模型自己从 prompt 文本里理解）做不到。由 inject_affordance_map() 在
    # session 开始时连同 AffordanceMap 一起写回，None 表示该输入源缺失（behavior
    # 感知未启用，或 affordance.use_behavior_context 关闭）。与 AffordanceMap
    # 本身同一"session 开始时构建一次"的慢变量粒度对齐，不逐 turn 刷新。
    user_presence: Optional["BehaviorContext"] = None

    # 快变量（每轮 turn 开始时由 _update_internal_state 更新）
    internal_state: Optional["AgentInternalState"] = None

    def update_internal_state(self, state: "AgentInternalState") -> None:
        """每轮 turn 开始时由 Agent 调用，更新实时感知快照。"""
        self.internal_state = state

    def is_user_actively_engaged(self) -> Optional[bool]:
        """
        程序化读取"用户当前是否在场/专注"的结构化访问入口，代理
        user_presence.is_actively_engaged。返回 None 表示信号缺失（行为感知
        未启用，或本 session 观察窗口内没有采集到足够信号做判断）——调用方
        应把 None 当作"不知道"处理，不要当作 False。
        """
        if self.user_presence is None:
            return None
        return self.user_presence.is_actively_engaged

    def to_system_prompt_fragment(self) -> str:
        """
        格式化为注入 system prompt 的文本块。

        只呈现 SelfAssessment 块里没有的内容（当前 session 实时维度），
        避免与 _build_global_knowledge_block() 的输出重复：
          ✅ 当前 workdir 领域置信度（workdir 粒度，≠ global 汇总）
          ✅ 当前内部感受（认知负荷、挫败感、剩余预算）
          ✅ 当前余裕地图摘要（top opportunities）
          ❌ 不重复 SelfAssessment.strengths / weak_areas / pending_branches
        """
        parts: list[str] = []

        # ── 当前 workdir 能力置信度（与 global SelfAssessment 区分：这是
        #    当前项目的实测数据，不是跨项目全局汇总）────────────────────────
        if self.capability_snapshot:
            sorted_caps = sorted(
                self.capability_snapshot.items(), key=lambda kv: -kv[1]
            )
            strong = [(d, c) for d, c in sorted_caps if c >= 0.7][:3]
            weak   = [(d, c) for d, c in sorted_caps if c < 0.5][:2]

            cap_lines: list[str] = ["## 当前项目能力分布（本 workdir 实测）"]
            if strong:
                cap_lines.append(
                    "- 高置信度：" + "、".join(f"{d}({c:.0%})" for d, c in strong)
                )
            if weak:
                cap_lines.append(
                    "- 待加强：" + "、".join(f"{d}({c:.0%})" for d, c in weak)
                )
            if strong or weak:
                parts.append("\n".join(cap_lines))

        # ── 当前余裕地图摘要（来自 B4 AffordanceMap）───────────────────────
        if self.affordance_summary:
            parts.append(self.affordance_summary)

        # ── 当前内部感受（来自 B1 ProprioceptionModule）─────────────────────
        if self.internal_state is not None:
            st = self.internal_state
            mood_lines: list[str] = ["## 当前内部状态（本轮实时）"]

            # 只在状态值显著时才注入，避免每轮都塞一堆"0.00"占 context
            if st.cognitive_load > 0.6:
                mood_lines.append(f"- 认知负荷：{st.cognitive_load:.0%}（偏高，必要时可主动压缩历史）")
            if st.frustration > 0.3:
                mood_lines.append(f"- 挫败感：{st.frustration:.0%}（连续失败时建议停下来汇报困境）")
            if st.energy_budget_ratio < 0.3:
                mood_lines.append(f"- 剩余 turn 预算：{st.energy_budget_ratio:.0%}（偏低，优先聚焦核心目标）")
            if st.uncertainty > 0.4:
                mood_lines.append(f"- 不确定性：{st.uncertainty:.0%}（建议先确认意图再动手）")

            if len(mood_lines) > 1:   # 有内容时才追加（不只是标题）
                parts.append("\n".join(mood_lines))

        # ── skill 丰富度（轻量感知，仅在没有 skill 时提示）─────────────────
        if self.active_skill_count == 0:
            parts.append("## 当前 session 无激活 skill（纯基础能力运行）")

        return "\n\n".join(parts)

    def is_empty(self) -> bool:
        return (
            not self.capability_snapshot
            and not self.affordance_summary
            and self.internal_state is None
            and self.active_skill_count > 0
        )

    def recent_negative_outcome_domains(self, *, paths: "AgentPaths") -> list[str]:
        """[方案四新增] 桥接 outcome_tracker.get_revert_candidates()，转换成
        domain 字符串列表供 SoftGoalDeriver 做子串匹配降权。

        TrackedCommit 本身没有独立的 domain 字段，这里复用
        affordance_calibration.py::calibrate() 已经验证过的同一套关联方式：
        优先取 commit_summary（人类可读摘要），缺失时退回
        trigger_lesson_group_id，再用 phase_g._infer_domain() 做规则式推断
        （与 soft_goal_deriver 里其余候选的 domain 归类同一套逻辑，不引入
        第二套规则）。

        只读、不缓存（调用频率低——只在 derive_candidates() 里用一次），
        失败返回空列表。"""
        try:
            from mini_agent.evolution.outcome_tracker import get_revert_candidates
            from mini_agent.evolution.phase_g import _infer_domain

            candidates = get_revert_candidates(paths)
            domains: list[str] = []
            for c in candidates:
                text = getattr(c, "commit_summary", "") or getattr(c, "trigger_lesson_group_id", "")
                if not text:
                    continue
                domain = _infer_domain(text)
                if domain and domain != "general":
                    domains.append(domain)
            return domains
        except Exception:
            return []


class AgentSelfModelBuilder:
    """
    在 session 初始化完成后（Agent 构造完毕，AffordanceMap 已注入后）
    构建一次 AgentSelfModel 慢变量部分，之后在每轮 turn 由 Agent 调用
    `model.update_internal_state()` 更新快变量。

    不做任何写入，不调用 LLM——纯读取 + 组装。
    """

    def build(
        self,
        *,
        project_root,
        affordance_map: Optional["AffordanceMap"] = None,
        active_skill_count: int = 0,
        use_capability_map: bool = True,
    ) -> AgentSelfModel:
        """
        Args:
            project_root: Path 对象，用于定位当前 workdir 的 sessions/
            affordance_map: B4 已构建好的 AffordanceMap 实例（由调用方传入，
                不重复构建），None 表示 B4 未启用或构建失败
            active_skill_count: 当前 session 加载的 skill 数量
            use_capability_map: 是否尝试从 phase_g 读取能力地图
        """
        cap_snapshot: dict[str, float] = {}
        if use_capability_map:
            cap_snapshot = self._load_capability_snapshot(project_root)

        affordance_summary = ""
        if affordance_map is not None:
            # 只取 top_opportunities + unexplored_areas 的极简摘要，
            # 完整块已由 session_pool._inject_affordance_map 写入 system_extra，
            # 这里只保留最精华的"行动可能性"提示，避免重复占 context。
            opp = affordance_map.top_opportunities[:2]
            if opp:
                affordance_summary = "当前最值得关注：" + "；".join(opp)

        return AgentSelfModel(
            capability_snapshot=cap_snapshot,
            affordance_summary=affordance_summary,
            active_skill_count=active_skill_count,
            session_start_at=time.time(),
            internal_state=None,
        )

    @staticmethod
    def _load_capability_snapshot(project_root) -> dict[str, float]:
        """
        从 phase_g.build_capability_map() 读取当前 workdir 的领域置信度。
        失败时静默返回空字典——capability_map 是增量积累的数据，
        新项目初始为空是正常状态，不应阻断 session 启动。
        """
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.evolution.phase_g import build_capability_map

            paths = AgentPaths(project_root)
            entries = build_capability_map(paths, None)  # None=只读，不写回
            return {e.domain: e.confidence for e in entries}
        except Exception:
            return {}


__all__ = ["AgentSelfModel", "AgentSelfModelBuilder"]
