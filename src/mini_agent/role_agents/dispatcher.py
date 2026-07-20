"""
role_agents/dispatcher.py — RoleAgentDispatcher

职责：
  - 管理所有 role_type 不为空的 AgentProfile（角色 Agent 注册表）
  - 根据触发事件（trigger_on）决定调用哪些角色 Agent
  - 协调串行管道模式 和 主从分发模式
  - 把角色 Agent 输出格式化后注入主 Agent 历史

触发时机：
  "output"          → 主 Agent 完成整个 turn 的输出后
  "tool_use:<name>" → 特定工具调用完成后（CoachAgent 常用）
  "turn_end"        → turn 结束时（alias for output）
  "goal_review"      → [判官接线统一 阶段六] Goal 模式每轮结束后，GoalJudge
                        核查是否 DONE/CONTINUE/NEED_COMPACT。与下面的
                        "turn_end_review" 及本文件已有的 "turn_end" 是
                        三个容易混淆但语义完全不同的概念，务必区分：
                          - "turn_end"：主 Agent 输出后、还没决定要不要
                            交还用户之前的质量修订循环（evaluator/coach），
                            是 "output" 的别名，运行时机最早。
                          - hooks 系统的 "TurnEnd" 事件：外部脚本/进程的
                            钩子机制，和这里的 trigger_on 字段是两套不
                            相关的系统，只是恰好同名。
                          - "turn_end_review"（本文件新增）：主 Agent
                            输出 + evaluator/coach 修订都跑完、TurnEnd
                            hook 也没接管之后，TurnJudge 判断"这轮到底
                            要不要交还真人"，运行时机最晚。
  "turn_end_review"  → [判官接线统一 阶段六] TurnJudge 判定，见上条说明。

两种协作模式（profile 的 inject_as 决定）：
  串行管道：evaluator → 反馈注入 → 主 Agent 可以看到并修订
  主从分发：coach 在 tool_use 后注入建议，主 Agent 后续参考

用法：
  dispatcher = RoleAgentDispatcher(cfg, profile_loader)

  # 主 Agent 输出后触发
  feedbacks = dispatcher.trigger_output(
      main_output="...",
      original_request="...",
      inject_into=agent._history,   # 直接注入到主 Agent 历史
  )

  # 工具调用后触发
  feedbacks = dispatcher.trigger_tool_use(
      tool_name="bash",
      tool_input={...},
      tool_output="...",
      context="...",
      inject_into=agent._history,
  )
"""

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

import mini_agent.ui.renderer as R

from .feedback import RoleFeedback, extract_score, build_inject_message

# [Phase 4] auto_quarantine 上报此前在这里用正则识别 "[XxxAgent 运行失败: ...]"
# 字符串来判断成败。现在 evaluator/coach/custom 三类角色 Agent 都经由
# judge_factory.run_judge_turn 运行，该函数在拿到类型化的 JudgeResult.ok 后
# 会自动上报一次 auto_quarantine（见 judge_factory.report_judge_outcome），
# 不再需要在这里做字符串匹配，也就消灭了"约定俗成的失败字符串格式"这个
# 隐性契约——本文件不再需要 _ROLE_FAILURE_RE / _report_role_agent_failure。

if TYPE_CHECKING:
    from pathlib import Path
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfileLoader, AgentProfile


# 全局单例
_dispatcher: Optional["RoleAgentDispatcher"] = None


def get_dispatcher() -> Optional["RoleAgentDispatcher"]:
    return _dispatcher


def init_role_agent_system(
    cfg: "AppConfig",
    profile_loader: "AgentProfileLoader",
) -> "RoleAgentDispatcher":
    """初始化全局 RoleAgentDispatcher，在 app.py 启动时调用。

    如果 cfg.role_agent.agents_dir 指定了自定义目录，则用该目录的 loader
    替换传入的 profile_loader（仅用于 role agent 发现）。
    """
    global _dispatcher

    # 如果指定了自定义 agents_dir，用新 loader 覆盖
    if cfg.role_agent.agents_dir is not None:
        from mini_agent.orchestrator.agent_profiles import AgentProfileLoader
        import mini_agent.ui.renderer as R
        custom_dir = cfg.role_agent.agents_dir
        R.print_info(f"[RoleAgent] 使用自定义目录：{custom_dir}")
        effective_loader = AgentProfileLoader([custom_dir])
    else:
        effective_loader = profile_loader

    _dispatcher = RoleAgentDispatcher(cfg, effective_loader)
    return _dispatcher


class RoleAgentDispatcher:
    """
    角色 Agent 调度器。

    维护两个内部注册表：
      _output_roles    → trigger_on 为 "output"/"turn_end" 的角色
      _tool_roles      → trigger_on 为 "tool_use:<name>" 的角色，
                         key 是工具名，value 是 profile 列表
    """

    def __init__(
        self,
        cfg: "AppConfig",
        profile_loader: "AgentProfileLoader",
    ) -> None:
        self._cfg = cfg
        self._loader = profile_loader
        self._output_roles: list["AgentProfile"] = []
        self._tool_roles: dict[str, list["AgentProfile"]] = {}
        # [判官接线统一 阶段六] goal_judge/turn_judge 的注册表，与
        # _output_roles/_tool_roles 平级，但来源可以是磁盘 profile
        # （用户自定义同名文件）或内建合成 profile（见 builtin_profiles.py）。
        self._goal_review_roles: list["AgentProfile"] = []
        self._turn_end_review_roles: list["AgentProfile"] = []
        self._enabled = True
        self._discover()

    def _discover(self) -> None:
        """发现所有角色 Agent profile 并分类，分两段逻辑：

          1. 磁盘自定义 evaluator/coach/custom profile：仍然完全受
             `role_agent.enabled` 门控——`role_agent.enabled=False` 时不
             加载任何磁盘自定义 evaluator/coach/custom，行为与升级前完全
             一致。
          2. goal_judge/turn_judge（判官）：**不**受 `role_agent.enabled`
             门控，而是分别受各自子系统的开关（`cfg.goal_mode.enabled`/
             `cfg.turn_judge.enabled`）门控——哪怕 `role_agent.enabled` 为
             False，只要 `goal_mode.enabled=True`，goal_judge 也应该正常
             触发（这是 §1.2 发现的兼容性风险，必须保留）。磁盘上如果存在
             同名的自定义 `.agent/agents/goal_judge.md`/`turn_judge.md`，
             会覆盖内建合成 profile（磁盘优先）——这个覆盖能力本身也不
             应该被 `role_agent.enabled` 挡住：判官的"是否生效"由它自己
             的子系统开关决定，"用什么 profile"（内建还是磁盘自定义）是
             另一个独立维度，两者不应该被同一个 `role_agent.enabled`
             耦合在一起。

        allow/block 过滤优先级（对两段逻辑都适用）：
          1. allow 白名单（非空时，只保留名单内的）
          2. block 黑名单（过滤掉名单内的）
          两者均对 profile.name 进行精确匹配。
        """
        ra_cfg = self._cfg.role_agent
        allow_set = set(ra_cfg.allow) if ra_cfg.allow else set()
        block_set = set(ra_cfg.block) if ra_cfg.block else set()

        # 每次重新发现前清空，保证热重载/重复调用时不会重复累加
        self._output_roles = []
        self._tool_roles = {}
        self._goal_review_roles = []
        self._turn_end_review_roles = []

        _JUDGE_NAMES = ("goal_judge", "turn_judge")

        def _passes_allow_block(name: str, label: str) -> bool:
            if allow_set and name not in allow_set:
                R.print_info(f"[RoleAgent] 跳过{label} '{name}'（不在白名单 {sorted(allow_set)} 中）")
                return False
            if name in block_set:
                R.print_info(f"[RoleAgent] 屏蔽{label} '{name}'（在黑名单中）")
                return False
            return True

        def _register_by_trigger(profile: "AgentProfile", name: str, source_label: str) -> None:
            trigger = (profile.trigger_on or "").strip().lower()
            if trigger in ("output", "turn_end", ""):
                self._output_roles.append(profile)
                R.print_info(f"[RoleAgent] 注册 {profile.role_type} '{name}'{source_label} → trigger: output")
            elif trigger.startswith("tool_use:"):
                tool_name = trigger[len("tool_use:"):]
                self._tool_roles.setdefault(tool_name, []).append(profile)
                R.print_info(f"[RoleAgent] 注册 {profile.role_type} '{name}'{source_label} → trigger: tool_use:{tool_name}")
            elif trigger == "goal_review":
                self._goal_review_roles.append(profile)
                R.print_info(f"[RoleAgent] 注册 {profile.role_type} '{name}'{source_label} → trigger: goal_review")
            elif trigger == "turn_end_review":
                self._turn_end_review_roles.append(profile)
                R.print_info(f"[RoleAgent] 注册 {profile.role_type} '{name}'{source_label} → trigger: turn_end_review")

        # ── 1. 磁盘自定义 evaluator/coach/custom：受 role_agent.enabled 门控 ──
        judge_names_on_disk: set[str] = set()
        if ra_cfg.enabled:
            for name in self._loader.available:
                profile = self._loader.get(name)
                if not profile or not profile.role_type:
                    continue
                if name in _JUDGE_NAMES or profile.role_type in _JUDGE_NAMES:
                    # 判官走第 2 段独立处理（不受 role_agent.enabled 门控），
                    # 这里只是发现阶段顺带跳过，避免重复注册。
                    continue
                if not _passes_allow_block(name, ""):
                    continue
                _register_by_trigger(profile, name, "")

        # ── 2. 判官（goal_judge/turn_judge）：不受 role_agent.enabled 门控，
        #        磁盘自定义优先于内建合成 profile ─────────────────────────────
        for judge_name in _JUDGE_NAMES:
            disk_profile = self._loader.get(judge_name) if judge_name in self._loader.available else None
            if disk_profile is not None and disk_profile.role_type == judge_name:
                judge_names_on_disk.add(judge_name)

        from .builtin_profiles import get_builtin_profiles
        builtin_by_name = {p.name: p for p in get_builtin_profiles(self._cfg)}

        for judge_name in _JUDGE_NAMES:
            if judge_name in judge_names_on_disk:
                profile = self._loader.get(judge_name)
                source_label = "（磁盘自定义，覆盖内建）"
            elif judge_name in builtin_by_name:
                profile = builtin_by_name[judge_name]
                source_label = "（内建）"
            else:
                # 既没有磁盘自定义，对应子系统也未开启：不注册
                continue

            if not _passes_allow_block(judge_name, "判官"):
                continue
            _register_by_trigger(profile, judge_name, source_label)

    def rediscover(self, dirs: Optional[list] = None) -> None:
        """[SYS-HOT-RELOAD] 重新执行一次 _discover()。

        与 skill/agent-profile loader 的 rediscover 同名同义，供
        `perception.hot_reload.HotReloader` 在磁盘自定义 profile 文件变化
        后调用（HotReloader 按 `reload_fn(dirs)` 的约定传参，这里的 dirs
        未被使用——dispatcher 的发现逻辑总是读取 self._loader.available，
        接收该参数只是为了匹配调用约定），以刷新 _output_roles/
        _tool_roles/_goal_review_roles/_turn_end_review_roles 四张注册表。
        """
        self._discover()

    @property
    def has_output_roles(self) -> bool:
        return bool(self._output_roles)

    @property
    def has_tool_roles(self) -> bool:
        return bool(self._tool_roles)

    def get_tool_triggers(self) -> set[str]:
        """返回需要监听的工具名集合，供 agent.py 判断是否触发。"""
        return set(self._tool_roles.keys())

    # ── 判官（goal_review / turn_end_review）─────────────────────────────────
    # [判官接线统一 阶段六]

    @property
    def has_goal_review_roles(self) -> bool:
        return bool(self._goal_review_roles)

    @property
    def has_turn_end_review_roles(self) -> bool:
        return bool(self._turn_end_review_roles)

    def get_goal_review_roles(self) -> list["AgentProfile"]:
        """返回当前注册的 goal_review 判官 profile 列表（通常只有一个内建
        goal_judge，但保留多个的可能性，方便未来支持自定义 goal 判官协同）。

        当前调用方（goal_mode/runner.py）只取第一个；多判官协同留作未来
        独立设计课题。
        """
        return list(self._goal_review_roles)

    def get_turn_end_review_roles(self) -> list["AgentProfile"]:
        """返回当前注册的 turn_end_review 判官 profile 列表，语义同上。"""
        return list(self._turn_end_review_roles)

    # ── 串行管道：output 触发 ────────────────────────────────────────────────

    def trigger_output(
        self,
        main_output: str,
        original_request: str,
        inject_into: list,
        *,
        verbose: bool = True,
        parent_session_id: Optional[str] = None,
        parent_session_dir: Optional["Path"] = None,
    ) -> list[RoleFeedback]:
        """
        主 Agent 完成输出后触发所有 output 类角色。
        支持 evaluator 的多轮修订循环。

        inject_into: 主 Agent 的 _history 列表（直接追加消息）
        返回所有角色的 feedback 列表。
        """
        if not self._enabled or not self._output_roles:
            return []

        all_feedbacks: list[RoleFeedback] = []

        for profile in self._output_roles:
            feedbacks = self._run_role_with_retry(
                profile=profile,
                main_output=main_output,
                original_request=original_request,
                inject_into=inject_into,
                verbose=verbose,
                parent_session_id=parent_session_id,
                parent_session_dir=parent_session_dir,
            )
            all_feedbacks.extend(feedbacks)

        return all_feedbacks

    def _run_role_with_retry(
        self,
        profile: "AgentProfile",
        main_output: str,
        original_request: str,
        inject_into: list,
        verbose: bool,
        parent_session_id: Optional[str] = None,
        parent_session_dir: Optional["Path"] = None,
    ) -> list[RoleFeedback]:
        """
        对单个角色 Agent 执行（含多轮修订循环）。
        对 evaluator 类型：运行 → 注入反馈 → 等主 Agent 修订 → 再评估（循环）
        对其他类型：运行一次，注入反馈。
        """
        from .evaluator import run_evaluator
        feedbacks: list[RoleFeedback] = []
        max_iter = profile.max_iterations if profile.role_type == "evaluator" else 1

        current_output = main_output
        for iteration in range(1, max_iter + 1):
            if verbose:
                R.print_info(
                    f"[RoleAgent:{profile.name}] 第 {iteration}/{max_iter} 轮评估..."
                )

            if profile.role_type == "evaluator":
                raw = run_evaluator(
                    profile=profile,
                    base_cfg=self._cfg,
                    original_request=original_request,
                    agent_output=current_output,
                    iteration=iteration,
                    parent_session_id=parent_session_id,
                    parent_session_dir=parent_session_dir,
                )
            else:
                # custom role：把输出直接作为 prompt 传入
                raw = self._run_custom_role(
                    profile, current_output, original_request,
                    parent_session_id=parent_session_id,
                    parent_session_dir=parent_session_dir,
                )

            # [auto_quarantine] evaluator 走 run_evaluator → run_judge_turn，
            # custom role 走 _run_custom_role → run_judge_turn，两者内部已经
            # 自动上报了这次运行的成败（见 judge_factory.report_judge_outcome），
            # 这里不需要再做任何事。

            score = extract_score(raw) if profile.role_type == "evaluator" else None
            passed = (score is not None and score >= profile.pass_threshold) if score is not None else None

            feedback = RoleFeedback(
                role_name=profile.name,
                role_type=profile.role_type,
                raw_output=raw,
                score=score,
                passed=passed,
                inject_as=profile.inject_as,
            )
            feedbacks.append(feedback)

            # 注入到主 Agent 历史
            msg = build_inject_message(feedback)
            inject_into.append(msg)

            if verbose and score is not None:
                score_pct = int(score * 100)
                status = "✅ 通过" if passed else "⚠️ 需修订"
                R.print_info(f"[RoleAgent:{profile.name}] 评分 {score_pct}/100 {status}")

            # 如果通过或已到最后一轮，结束循环
            if passed or iteration >= max_iter:
                break

            # 未通过且还有轮次：给主 Agent 机会修订
            # 注入后我们需要让主 Agent 看到反馈并重新生成输出
            # 这需要 dispatcher 外部的调用方配合（见 agent.py 的集成点）
            # 这里返回 feedbacks，由调用方决定是否重新触发主 Agent
            if iteration < max_iter:
                if verbose:
                    R.print_info(f"[RoleAgent:{profile.name}] 反馈已注入，等待主 Agent 修订...")
                # 标记需要修订（调用方通过 feedback.passed=False 判断）
                break  # 本轮结束，让 agent.py 重新执行并再次调用 trigger_output

        return feedbacks

    def _run_custom_role(
        self,
        profile: "AgentProfile",
        agent_output: str,
        original_request: str,
        parent_session_id: Optional[str] = None,
        parent_session_dir: Optional["Path"] = None,
    ) -> str:
        """运行自定义角色 Agent。

        [Phase 3+4 重构] 样板逻辑收敛到 judge_factory.spawn_judge_agent /
        run_judge_turn；传入 profile_name 后 run_judge_turn 会自动把这次
        运行的成败上报给 auto_quarantine，函数返回值保持完全不变。
        """
        from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

        import os
        from mini_agent.config.models import DEFAULT_AGENT_NAME

        role_agent = spawn_judge_agent(
            profile=profile,
            base_cfg=self._cfg,
            role_cfg_block=None,
            # [行为保持] custom role 此前从未显式设置 agent_name，等价于 load_config 的默认值
            display_name=os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME),
            system_prompt=profile.system_prompt,
            max_turns=3,
            tools_enabled=False,
            parent_session_id=parent_session_id,
            parent_session_dir=parent_session_dir,
        )

        prompt = f"""请对以下 AI 助手的输出进行分析。

**用户请求：**
{original_request}

**AI 助手输出：**
{agent_output}"""

        result = run_judge_turn(
            role_agent, prompt, failure_role_label="自定义角色 Agent", profile_name=profile.name,
        )
        return result.raw_output

    # ── 主从分发：tool_use 触发 ──────────────────────────────────────────────

    def trigger_tool_use(
        self,
        tool_name: str,
        tool_input: dict,
        tool_output: str,
        context: str,
        inject_into: list,
        *,
        verbose: bool = True,
        parent_session_id: Optional[str] = None,
        parent_session_dir: Optional["Path"] = None,
    ) -> list[RoleFeedback]:
        """
        特定工具调用后触发相关角色 Agent（通常是 CoachAgent）。

        inject_into: 主 Agent 的 _history 列表
        """
        if not self._enabled:
            return []

        profiles = self._tool_roles.get(tool_name, [])
        if not profiles:
            return []

        from .coach import run_coach
        feedbacks: list[RoleFeedback] = []

        for profile in profiles:
            if verbose:
                R.print_info(f"[RoleAgent:{profile.name}] tool_use:{tool_name} 触发...")

            if profile.role_type == "coach":
                raw = run_coach(
                    profile=profile,
                    base_cfg=self._cfg,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=tool_output,
                    context=context,
                    parent_session_id=parent_session_id,
                    parent_session_dir=parent_session_dir,
                )
            else:
                raw = self._run_custom_role(
                    profile, tool_output, context,
                    parent_session_id=parent_session_id,
                    parent_session_dir=parent_session_dir,
                )

            # [auto_quarantine] coach 走 run_coach → run_judge_turn，custom role
            # 走 _run_custom_role → run_judge_turn，两者内部已自动上报，这里
            # 同 _run_role_with_retry，不需要再做任何事。

            feedback = RoleFeedback(
                role_name=profile.name,
                role_type=profile.role_type,
                raw_output=raw,
                inject_as=profile.inject_as,
            )
            feedbacks.append(feedback)

            msg = build_inject_message(feedback)
            inject_into.append(msg)

            if verbose:
                R.print_info(f"[RoleAgent:{profile.name}] 建议已注入")

        return feedbacks

    # ── 控制 ────────────────────────────────────────────────────────────────

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @property
    def summary(self) -> str:
        output_names = [p.name for p in self._output_roles]
        tool_names = {k: [p.name for p in v] for k, v in self._tool_roles.items()}
        return (
            f"RoleAgentDispatcher("
            f"output_roles={output_names}, "
            f"tool_roles={tool_names})"
        )