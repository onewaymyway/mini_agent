"""
evolution/resource_arbiter.py — Stage 9 资源仲裁（第八节）

实现设计文档 7.5 节三条仲裁规则：
1. 用户优先：自主任务执行期间收到用户消息时，自主任务暂停（PAUSED 状态）
2. 资源锁：提交自主任务前检查用户最近触碰的路径是否重叠
3. 预算硬限制：used_today < daily_token_budget 才允许自主执行

同时管理探索预算（第八节补充）：
  resource_budget 新增 exploration_budget_ratio（默认 10%）
  used_today 拆分为 used_today_goals + used_today_exploration 两个计数器

降级路径：tracing 未开启时，资源锁退化为"保守地一律视为重叠"（宁可错误暂停，
不可错误覆盖用户文件）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from mini_agent.time_utils import ts_to_str

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig


# ── 探索预算扩展字段（补充 Stage 5 ResourceBudget）────────────────────────────

_EXPLORATION_BUDGET_RATIO_DEFAULT = 0.10  # 默认 10%
_RESOURCE_LOCK_WINDOW_MINUTES = 10        # 检查最近 N 分钟内用户触碰的路径
_FRUSTRATION_SNAPSHOT_STALE_MINUTES = 10  # proprioception 快照超过此时长视为过期，不阻塞

_GATING_HISTORY_MAX_ENTRIES = 200  # [P5] 仲裁状态时间线最多保留的条数（追加时裁剪）


def record_gating_transition(paths: "AgentPaths", state: str, reason: str) -> None:
    """[调度统一化 + 看板可观测性改进方案 P5] 记录一次 ResourceArbiter 三态门控
    （full/degraded/blocked）状态变化。

    只在"这次的 state 和历史记录里最后一条不一样"时才追加一行，避免每次
    `/v1/autonomous/status` 被轮询（kanban 顶栏每几秒刷新一次）都写一行——
    那样会让 gating_history.jsonl 变成跟轮询频率挂钩的高频日志，失去
    "状态变化时间线"本身的意义。文件损坏/不存在/写入失败时静默忽略，
    不能因为记历史这种锦上添花的功能影响主状态查询。
    """
    try:
        history_path = paths.gating_history_path
        last_state = None
        if history_path.exists():
            try:
                with history_path.open("r", encoding="utf-8") as f:
                    lines = [ln for ln in f.read().splitlines() if ln.strip()]
                if lines:
                    last_state = json.loads(lines[-1]).get("state")
            except Exception:
                lines = []
        else:
            lines = []

        if last_state == state:
            return  # 状态未变化，不记录

        now = time.time()
        entry = {
            "at": now,
            "at_str": ts_to_str(now),
            "state": state,
            "reason": reason,
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
        if len(lines) > _GATING_HISTORY_MAX_ENTRIES:
            lines = lines[-_GATING_HISTORY_MAX_ENTRIES:]
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.evolution.resource_arbiter.record_gating_transition")


def read_gating_history(paths: "AgentPaths", limit: int = 50) -> list[dict]:
    """[P5] 读取最近 `limit` 条仲裁状态变化记录，按时间正序返回（旧→新）。
    供看板"🗓️ 全局日程" tab 的时间线展示。文件不存在/损坏时返回空列表。
    """
    try:
        history_path = paths.gating_history_path
        if not history_path.exists():
            return []
        lines = [ln for ln in history_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        entries = []
        for ln in lines[-limit:]:
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
        return entries
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.evolution.resource_arbiter.read_gating_history")
        return []


def gating_ratio_summary(paths: "AgentPaths", *, window_days: float = 7.0, limit: int = 200) -> dict:
    """[kanban_perception_gaps_improvement_plan.md 方向 C] 基于
    `read_gating_history()` 的逐条状态变化记录，重建 `window_days` 天窗口内
    full/degraded/blocked 三态各自的累计时长占比。

    纯计算函数，不新增任何落盘文件——所有输入数据 `record_gating_transition()`
    已经持久化在 gating_history.jsonl 里了。

    重建方法：记录只在\"状态变化时\"才追加一条（见 `record_gating_transition()`
    的去重逻辑），所以某个时间点的状态 = 小于等于该时间点的最后一条记录的
    state。用相邻两条记录的时间差做区间累加；窗口起点落在两条记录之间的，
    只计入窗口内的那一段；最后一条记录到\"现在\"的这段区间按当前状态计入。

    返回：
    {
      "window_days": float,
      "ratios": {"full": 0.92, "degraded": 0.06, "blocked": 0.02},  # 占比之和≈1
      "seconds": {"full": ..., "degraded": ..., "blocked": ...},    # 原始秒数，供调试
      "incomplete": bool,   # True 表示历史记录可能因为
                             # _GATING_HISTORY_MAX_ENTRIES 裁剪而缺失窗口最早的一段
                             # （或窗口内压根没有任何记录，也就是"从未变化过"，
                             # 此时 incomplete 仍为 False，因为不是被裁剪掉的）
    }

    窗口内没有任何状态变化记录、也没有任何历史记录时，返回全部为 0 的占比，
    调用方应据此展示"暂无数据"而不是强行凑出 100%。
    """
    now = time.time()
    window_seconds = max(0.0, window_days) * 86400.0
    window_start = now - window_seconds

    seconds = {"full": 0.0, "degraded": 0.0, "blocked": 0.0}
    incomplete = False

    try:
        all_entries = read_gating_history(paths, limit=limit)
    except Exception:
        all_entries = []

    if not all_entries:
        return {
            "window_days": window_days,
            "ratios": {"full": 0.0, "degraded": 0.0, "blocked": 0.0},
            "seconds": seconds,
            "incomplete": False,
        }

    # 记录数达到裁剪上限，且最早一条记录本身就落在窗口内（说明窗口内的
    # 状态变化次数可能超过了 limit，更早的记录已经被裁剪掉，无法准确重建）。
    if len(all_entries) >= min(limit, _GATING_HISTORY_MAX_ENTRIES) and all_entries[0].get("at", now) > window_start:
        incomplete = True

    # 窗口开始时刻的状态：取窗口起点之前（或恰好等于）的最后一条记录的 state；
    # 如果所有记录都晚于窗口起点，说明窗口起点之前的状态未知，退化为把
    # 第一条记录的 state 当作窗口起点状态（保守近似，不因此判定 incomplete，
    # 因为这只是"窗口比数据历史更长"，不是数据被裁剪导致的缺失）。
    prev_state = all_entries[0].get("state", "full")
    prev_at = window_start
    for entry in all_entries:
        at = entry.get("at", now)
        state = entry.get("state", "full")
        if at <= window_start:
            prev_state = state
            continue
        seg_start = max(prev_at, window_start)
        seg_end = min(at, now)
        if seg_end > seg_start and prev_state in seconds:
            seconds[prev_state] += seg_end - seg_start
        prev_state = state
        prev_at = at

    # 最后一条记录到"现在"的这段区间。
    seg_start = max(prev_at, window_start)
    if now > seg_start and prev_state in seconds:
        seconds[prev_state] += now - seg_start

    total = sum(seconds.values())
    if total <= 0:
        ratios = {"full": 0.0, "degraded": 0.0, "blocked": 0.0}
    else:
        ratios = {k: round(v / total, 4) for k, v in seconds.items()}

    return {
        "window_days": window_days,
        "ratios": ratios,
        "seconds": {k: round(v, 1) for k, v in seconds.items()},
        "incomplete": incomplete,
    }


class ResourceArbiter:
    """
    自主任务的资源仲裁器。
    由 AutonomousLoop._tick_maintenance() 在提交任务前调用。
    """

    def __init__(self, paths: "AgentPaths", cfg: "AppConfig") -> None:
        self._paths = paths
        self._cfg = cfg

    # ── 主仲裁入口 ────────────────────────────────────────────────────────────

    def can_run_autonomous(self) -> bool:
        """
        综合判断是否可以提交自主任务。
        向后兼容接口：等价于 gating_state()["state"] != "blocked"。

        [看板与自主性改进方案 Track J] 在引入三态门控之前，规则 4/5 任一
        触发都会让本方法返回 False（对应 AutonomousLoop 里的 pause_all()
        整体停摆）。现在规则 4/5 的"触发"只表示 degraded（收敛，不停摆），
        只有规则 3（预算硬限制）或 frustration 达到更高的 blocked 阈值时
        才会真正返回 False。这是本 Track 的核心行为变化，如实记录在这里
        而不是悄悄改：调用方如果只关心"能不能跑"，语义变得更宽松了；如果
        关心"是否应该降级"，需要改用 gating_state()。
        """
        return self.gating_state()["state"] != "blocked"

    def gating_state(self) -> dict:
        """
        [看板与自主性改进方案 Track J] 三态门控入口，取代
        can_run_autonomous() 内部原来的二元判断。

        返回 {"state": "full"|"degraded"|"blocked", "reason": str}：
        - "blocked"：预算耗尽，或 frustration 达到 frustration_blocked_threshold
          （比原来的 degraded 阈值更严重）——整体停摆，调用方应 pause_all()。
        - "degraded"：frustration 达到（但未超过 blocked 阈值）proprioception
          .frustration_threshold，或用户明显活跃切换——不停摆，但调用方应把
          并发上限临时收紧（见 ObjectiveExecutor.effective_max_concurrent()
          的 external_degraded 参数）。
        - "full"：三条规则都正常，无需收敛。

        `resource_gating_degraded_enabled=False` 时退化为改造前的二元行为
        （degraded 视同 blocked），保证配置未升级的用户行为不变。
        """
        # 规则 3：预算硬限制——保持二元，不属于本 Track 的三态化范围
        # （方案原文明确写的是"第4/5条规则"，预算是硬限制，不应该有中间态：
        # 预算耗尽就是耗尽，没有"打个折继续花"这种语义）。
        if not self._check_budget():
            # [P1] 附带三类消耗的分项数字，方便看板/日志区分这次预算耗尽
            # 主要是被 Goal、cron 还是探索实验哪一部分消耗触发的。
            reason = "预算已耗尽（used_today >= daily_token_budget）" + self._usage_breakdown_str()
            result = {"state": "blocked", "reason": reason}
            self._record_transition(result["state"], result["reason"])
            return result

        gating_cfg = getattr(self._cfg, "autonomy", None)
        degraded_enabled = bool(getattr(gating_cfg, "resource_gating_degraded_enabled", True))

        frustration_level, frustration_reason = self._check_frustration_tri()
        presence_level, presence_reason = self._check_user_presence_tri()

        if not degraded_enabled:
            # 退化路径：degraded 视同 blocked，与改造前行为完全一致。
            if frustration_level != "full":
                result = {"state": "blocked", "reason": frustration_reason}
            elif presence_level != "full":
                result = {"state": "blocked", "reason": presence_reason}
            else:
                result = {"state": "full", "reason": "正常"}
            self._record_transition(result["state"], result["reason"])
            return result

        if frustration_level == "blocked":
            result = {"state": "blocked", "reason": frustration_reason}
        elif frustration_level == "degraded":
            result = {"state": "degraded", "reason": frustration_reason}
        elif presence_level == "degraded":
            result = {"state": "degraded", "reason": presence_reason}
        else:
            result = {"state": "full", "reason": "正常"}

        self._record_transition(result["state"], result["reason"])
        return result

    def _record_transition(self, state: str, reason: str) -> None:
        """[daemon 稳定性与用户体验改进方案 P0-4] 在 gating_state() 计算出
        结果的那一刻主动记录时间线，而不是依赖某个只读接口被外部轮询。
        gating_state() 本身会被 AutonomousLoop 主循环的每个 tick 调用（见
        `autonomous_loop.py` 的调度决策点），与是否有看板客户端在轮询无关，
        因此这里落地即可覆盖"daemon 长时间没有客户端轮询"的可观测性盲区。
        record_gating_transition() 内部已做"状态未变化则不写入"的去重，
        这里重复调用是安全的（幂等）。
        """
        try:
            record_gating_transition(self._paths, state, reason)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.resource_arbiter.ResourceArbiter._record_transition")

    def can_run_exploration(self) -> bool:
        """判断探索预算是否还有余量。"""
        return self._check_exploration_budget()

    def check_path_conflict(self, task_paths: list[str]) -> bool:
        """
        规则 2：检查 task_paths 与最近用户触碰路径是否重叠。
        返回 True 表示有冲突（应暂停/跳过自主任务）。
        降级：tracing 未开启时一律返回 True（保守）。
        """
        recent = self._recent_user_touched_paths()
        if recent is None:
            # tracing 未开启，保守地认为有冲突
            return True
        task_set = {str(Path(p).resolve()) for p in task_paths}
        return bool(task_set & recent)

    # ── 规则实现 ──────────────────────────────────────────────────────────────

    def _check_budget(self) -> bool:
        """规则 3：used_today < daily_token_budget。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if not profile:
                return True  # 读取失败时不阻塞
            rb = profile.resource_budget
            used = rb.used_today
            budget = rb.daily_token_budget
            if budget <= 0:
                return True  # 无限制
            return used < budget
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._check_budget')
            return True

    def _check_frustration(self) -> bool:
        """
        规则 4：读取 agent.py 写入的 proprioception_snapshot.json（B1 → Stage 9
        信号桥接，见 resource_arbiter.py 模块 docstring 之外的设计说明）。

        - 快照不存在 / 读取失败：视为当前没有可用的本体感知信号，不阻塞（与
          规则 3 predicate 一贯的"读取失败不阻塞"风格一致）。
        - 快照过旧（超过 _FRUSTRATION_SNAPSHOT_STALE_MINUTES 分钟没更新，说明
          近期没有活跃 session 在跑）：同样不阻塞，避免用一个过期信号长期卡住
          自主任务。
        - frustration 达到阈值：返回 False（阻塞本次自主任务提交）。
        """
        try:
            snapshot_path = self._paths.proprioception_snapshot
            if not snapshot_path.exists():
                return True
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            updated_at = float(data.get("updated_at", 0))
            if time.time() - updated_at > _FRUSTRATION_SNAPSHOT_STALE_MINUTES * 60:
                return True
            threshold = getattr(
                getattr(self._cfg, "proprioception", None),
                "frustration_threshold",
                0.5,
            )
            frustration = float(data.get("frustration", 0.0))
            return frustration < threshold
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._check_frustration')
            return True

    def _check_user_presence(self) -> bool:
        """
        规则 5（方案二新增）：用户当前明显活跃（近期有应用切换）时，收敛
        自主任务，避免和用户抢资源/写冲突；用户 idle 或信号缺失时不阻塞
        （保守：不确定就不阻断，behavior 采集本身就是可选组件，缺失是
        常态而非异常）。

        双开关哲学，与 affordance.use_behavior_context 保持一致：默认
        autonomy.behavior_gating_enabled=False，关闭时本方法恒真，
        can_run_autonomous() 行为与改动前完全一致。
        """
        gating_cfg = getattr(self._cfg, "autonomy", None)
        if not gating_cfg or not getattr(gating_cfg, "behavior_gating_enabled", False):
            return True
        try:
            from mini_agent.perception.affordance_analyzer import load_behavior_context
            # 短窗口：只关心"刚刚"，与 AffordanceAnalyzer 默认的 30 分钟
            # 观察窗口不同——自主调度门控关心的是"此刻是否该让路"，
            # 用更短的窗口能更快感知到用户已经离开/恢复空闲。
            ctx = load_behavior_context(self._cfg, window_minutes=5)
            if ctx is None:
                return True  # 信号缺失，不阻断
            threshold = getattr(gating_cfg, "behavior_gating_switch_threshold", 3)
            if ctx.is_actively_engaged and ctx.context_switch_count >= threshold:
                return False  # 用户明显在忙碌切换，暂缓自主任务
            return True
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._check_user_presence')
            return True  # 读取失败保守放行

    def _check_frustration_tri(self) -> tuple[str, str]:
        """
        [Track J] 规则 4 的三态版本，逻辑与 _check_frustration() 共享同一份
        快照读取，只是把"过阈值就返回 False"拆成两级阈值：
        - frustration < frustration_threshold          → "full"
        - frustration_threshold <= frustration < blocked_threshold → "degraded"
        - frustration >= frustration_blocked_threshold  → "blocked"
        快照不存在/过期/读取失败时统一返回 "full"（不阻塞/不降级），与
        _check_frustration() 的既有"读取失败不阻塞"风格一致。
        """
        try:
            snapshot_path = self._paths.proprioception_snapshot
            if not snapshot_path.exists():
                return "full", "无本体感知快照"
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            updated_at = float(data.get("updated_at", 0))
            if time.time() - updated_at > _FRUSTRATION_SNAPSHOT_STALE_MINUTES * 60:
                return "full", "本体感知快照已过期"
            degraded_threshold = getattr(
                getattr(self._cfg, "proprioception", None),
                "frustration_threshold",
                0.5,
            )
            blocked_threshold = getattr(
                getattr(self._cfg, "autonomy", None),
                "frustration_blocked_threshold",
                0.85,
            )
            frustration = float(data.get("frustration", 0.0))
            if frustration >= blocked_threshold:
                return "blocked", f"挫败感 {frustration:.2f} 达到硬停摆阈值 {blocked_threshold:.2f}"
            if frustration >= degraded_threshold:
                return "degraded", f"挫败感 {frustration:.2f} 达到降级阈值 {degraded_threshold:.2f}"
            return "full", "正常"
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._check_frustration_tri')
            return "full", "读取异常，不阻塞"

    def _check_user_presence_tri(self) -> tuple[str, str]:
        """
        [Track J] 规则 5 的三态版本：只有 full/degraded 两级，没有 blocked
        （见 config/models.py::AutonomyConfig.resource_gating_degraded_max_concurrent
        字段注释里的说明：用户活跃切换不是"危险"信号，只需要让路，不需要
        整体停摆）。双开关哲学与 _check_user_presence() 保持一致。
        """
        gating_cfg = getattr(self._cfg, "autonomy", None)
        if not gating_cfg or not getattr(gating_cfg, "behavior_gating_enabled", False):
            return "full", "未启用行为门控"
        try:
            from mini_agent.perception.affordance_analyzer import load_behavior_context
            ctx = load_behavior_context(self._cfg, window_minutes=5)
            if ctx is None:
                return "full", "行为信号缺失，不阻断"
            threshold = getattr(gating_cfg, "behavior_gating_switch_threshold", 3)
            if ctx.is_actively_engaged and ctx.context_switch_count >= threshold:
                return "degraded", "检测到用户正在活跃切换应用，收敛并发（让路）"
            return "full", "未启用或用户不活跃"
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._check_user_presence_tri')
            return "full", "读取异常，保守放行"

    def _check_exploration_budget(self) -> bool:
        """探索预算：used_today_exploration < exploration_budget（daily_budget * ratio）。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if not profile:
                return True
            rb = profile.resource_budget
            total = rb.daily_token_budget
            ratio = getattr(rb, "exploration_budget_ratio", _EXPLORATION_BUDGET_RATIO_DEFAULT)
            exploration_budget = int(total * ratio)
            used_exploration = getattr(rb, "used_today_exploration", 0)
            if exploration_budget <= 0:
                return False
            return used_exploration < exploration_budget
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._check_exploration_budget')
            return True

    def diagnose(self) -> dict:
        """[看板诊断改进] 返回四条仲裁规则各自的通过/阻塞状态和具体数值，
        供 `/v1/autonomous/status` 透出给看板展示——用户反馈"目标看板里加了
        目标，也拆出了 Objective，但看不到 agent 去执行，也不知道为什么"，
        can_run_autonomous() 只返回一个布尔值，任何一条规则挡住都表现为
        "什么都没发生"，无法定位是哪一条、具体数值是多少。这里把
        can_run_autonomous() 内部四条规则逐条跑一遍，每条附带
        `passed`/`reason`/关键数值，不影响 can_run_autonomous() 本身的
        行为（各 _check_* 方法本身没有副作用，可以安全重复调用）。
        """
        budget_ok = self._check_budget()
        frustration_ok = self._check_frustration()
        presence_ok = self._check_user_presence()

        budget_detail: dict = {}
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if profile:
                rb = profile.resource_budget
                budget_detail = {"used_today": rb.used_today, "daily_token_budget": rb.daily_token_budget}
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter.diagnose.budget')

        frustration_detail: dict = {}
        try:
            snapshot_path = self._paths.proprioception_snapshot
            if snapshot_path.exists():
                data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                threshold = getattr(
                    getattr(self._cfg, "proprioception", None), "frustration_threshold", 0.5,
                )
                frustration_detail = {
                    "frustration": data.get("frustration", 0.0),
                    "threshold": threshold,
                    "snapshot_age_s": round(time.time() - float(data.get("updated_at", 0)), 1),
                }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter.diagnose.frustration')

        rules = [
            {
                "rule": "budget", "label": "每日 token 预算",
                "passed": budget_ok,
                "reason": "预算已耗尽（used_today >= daily_token_budget）" if not budget_ok else "预算充足",
                **budget_detail,
            },
            {
                "rule": "frustration", "label": "本体感知（挫败感）",
                "passed": frustration_ok,
                "reason": "近期挫败感超过阈值，暂停自主任务" if not frustration_ok else "正常",
                **frustration_detail,
            },
            {
                "rule": "user_presence", "label": "用户在场（行为门控）",
                "passed": presence_ok,
                "reason": "检测到用户正在活跃切换应用，让路给用户" if not presence_ok else "未启用或用户不活跃",
            },
        ]
        state = self.gating_state()
        return {
            "can_run_autonomous": budget_ok and frustration_ok and presence_ok,
            "rules": rules,
            # [Track J] 三态门控结果，供看板区分"整体停摆"和"降级运行"，
            # 不再是只有 can_run_autonomous 这一个布尔值。
            "gating_state": state["state"],
            "gating_reason": state["reason"],
        }

    def _recent_user_touched_paths(
        self,
        window_minutes: float = _RESOURCE_LOCK_WINDOW_MINUTES,
    ) -> Optional[set[str]]:
        """
        从 Stage 6 traces.jsonl 提取最近 window_minutes 内用户触碰的文件路径。
        tracing 未开启时返回 None（调用方应保守处理）。
        """
        try:
            # 查找最近的 session traces
            sessions_dir = self._paths.sessions_dir if hasattr(self._paths, "sessions_dir") else None
            if sessions_dir is None:
                # 尝试从 workdir 目录推断
                sessions_dir = self._paths.workdir_dir / "sessions"

            if not sessions_dir.exists():
                return None

            cutoff = time.time() - window_minutes * 60
            touched: set[str] = set()

            # 遍历 session 目录，查找最近的 traces.jsonl
            for session_dir in sorted(sessions_dir.iterdir(), reverse=True)[:5]:
                traces_file = session_dir / "traces.jsonl"
                if not traces_file.exists():
                    continue
                paths = self._extract_paths_from_traces(traces_file, cutoff, "user")
                touched.update(paths)

            return touched
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._recent_user_touched_paths')
            return None

    def _extract_paths_from_traces(
        self, traces_file: Path, cutoff: float, initiator_filter: str
    ) -> set[str]:
        """从 traces.jsonl 提取指定 initiator 触碰的文件路径。"""
        paths: set[str] = set()
        _PATH_TOOLS = {"read_file", "write_file", "patch_file", "bash", "grep"}
        try:
            with open(traces_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._extract_paths_from_traces')
                        continue
                    if rec.get("ts", 0) < cutoff:
                        continue
                    if rec.get("initiator", "user") != initiator_filter:
                        continue
                    tool = rec.get("tool_name", "")
                    if tool not in _PATH_TOOLS:
                        continue
                    tool_input = rec.get("tool_input", {})
                    if isinstance(tool_input, dict):
                        for key in ("path", "file_path", "filepath"):
                            val = tool_input.get(key)
                            if val and isinstance(val, str):
                                try:
                                    paths.add(str(Path(val).resolve()))
                                except Exception as _mini_agent_exc:
                                    from mini_agent.errors import log_exception
                                    log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._extract_paths_from_traces')
                                    paths.add(val)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
            pass
        return paths

    def record_autonomous_token_usage(self, tokens: int, usage_type: str = "goals") -> None:
        """
        记录自主任务的 token 用量（与 update_token_usage 分开计数）。
        usage_type: "goals"（目标执行）| "exploration"（探索实验）|
        "cron"（[goal_cron_unified_scheduler_improvement_plan.md P1] 普通
        cron job 执行，与 goals/exploration 是同级的第三个分项——cron
        通道跑掉的 token 此前不计入任何计数器，Goal 和 cron 对预算负同等
        责任后，"blocked" 状态才是可解释、可审计的：不再是只有 Goal 才能
        把 arbiter 打满）。
        """
        try:
            from mini_agent.perception.global_knowledge import (
                load_self_profile, save_self_profile,
            )
            profile = load_self_profile(self._paths)
            if not profile:
                return
            rb = profile.resource_budget
            field = {
                "exploration": "used_today_exploration",
                "cron": "used_today_cron",
            }.get(usage_type, "used_today_goals")
            current = getattr(rb, field, 0)
            setattr(rb, field, current + max(0, tokens))
            # 也累加到 used_today（总计数）
            rb.used_today += max(0, tokens)
            save_self_profile(self._paths, profile)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
            pass

    def _usage_breakdown_str(self) -> str:
        """[P1] 返回三类消耗（goals/cron/exploration）的分项数字，供
        gating_state()/diagnose() 的 reason 文案里说明"这次 blocked/degraded
        是被哪部分消耗触发的"，不是只看 used_today 总数。读取失败时返回
        空字符串（调用方拼接时天然降级为不带分项说明）。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if not profile:
                return ""
            rb = profile.resource_budget
            goals = getattr(rb, "used_today_goals", 0)
            cron = getattr(rb, "used_today_cron", 0)
            exploration = getattr(rb, "used_today_exploration", 0)
            return f"（分项：goals={goals}, cron={cron}, exploration={exploration}）"
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.ResourceArbiter._usage_breakdown_str')
            return ""


# ── activity_digest.jsonl 辅助 ────────────────────────────────────────────────

def append_activity_digest(paths: "AgentPaths", record: dict) -> None:
    """
    向 activity_digest.jsonl 追加一条记录。
    与 activity_log.jsonl（Stage 5，粒度=session）不同：
    这里粒度=自主行为（task/proposal/goal）。
    """
    try:
        digest_path = paths.workdir_dir / "activity_digest.jsonl"
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        _now = time.time()
        entry = {"at": _now, "at_str": ts_to_str(_now), **record}
        with open(digest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
        pass


def read_activity_digest(
    paths: "AgentPaths",
    since_ts: Optional[float] = None,
) -> list[dict]:
    """读取 activity_digest.jsonl（可按时间戳过滤）。"""
    digest_path = paths.workdir_dir / "activity_digest.jsonl"
    if not digest_path.exists():
        return []
    records: list[dict] = []
    try:
        with open(digest_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if since_ts is None or rec.get("at", 0) >= since_ts:
                        records.append(rec)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter.read_activity_digest')
                    continue
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
        pass
    return records


def build_digest_summary(records: list[dict]) -> str:
    """
    将 activity_digest 记录分四组展示：

      【Objective 进展】  — objective_started / objective_completed / objective_failed
      【Cron 执行记录】  — cron_run
      【探索实验结果】   — exploration_result
      【Agent 建议目标】 — soft_goal_created（附 accept/reject 快捷指令）
    """
    if not records:
        return "（自上次交互以来无自主活动）"

    import time as _time

    def _ago(ts: float) -> str:
        if not ts:
            return ""
        delta = _time.time() - ts
        if delta < 60:
            return "刚刚"
        if delta < 3600:
            return f"{delta/60:.0f}m前"
        if delta < 86400:
            return f"{delta/3600:.1f}h前"
        return f"{delta/86400:.1f}d前"

    # 分组
    obj_records   = [r for r in records if r.get("type", "").startswith("objective_")]
    cron_records  = [r for r in records if r.get("type") == "cron_run"]
    explore_records = [r for r in records if r.get("type") == "exploration_result"]
    goal_records  = [r for r in records if r.get("type") == "soft_goal_created"]
    evolve_records = [r for r in records if r.get("type") == "evolve_proposal"]
    other_records = [r for r in records if r.get("type", "") not in (
        "objective_started", "objective_completed", "objective_failed",
        "cron_run", "exploration_result", "soft_goal_created", "evolve_proposal",
    )]

    # 将 obj_records 按 objective_id 折叠
    obj_by_id: dict[str, list[dict]] = {}
    for r in obj_records:
        oid = r.get("objective_id") or r.get("execution_id", "?")
        obj_by_id.setdefault(oid, []).append(r)

    total = len(records)
    lines = [f"自上次交互以来的自主活动（{total} 条，最近 24h）："]

    # ── Objective 进展 ─────────────────────────────────────────────────────────
    if obj_by_id:
        lines.append(f"\n【Objective 进展】")
        for oid, recs in list(obj_by_id.items())[:6]:
            # 找最新记录
            latest = max(recs, key=lambda r: r.get("at", 0))
            rtype = latest.get("type", "")
            title = latest.get("title", oid)
            ago = _ago(latest.get("at", 0))

            if rtype == "objective_completed":
                steps = latest.get("steps", "?")
                dur = latest.get("duration", 0)
                dur_str = f"，用时 {dur/60:.0f}m" if dur > 60 else ""
                lines.append(f"  ✅ {title}（{steps} 步完成{dur_str}）[{ago}]")
            elif rtype == "objective_failed":
                reason = latest.get("reason", "")
                lines.append(f"  ✗  {title} — 执行失败：{reason[:60]} [{ago}]")
                lines.append(f"     /goals progress <id> <备注> 后可重新激活")
            elif rtype == "objective_started":
                lines.append(f"  ●  {title} — 已启动 [{ago}]")
            else:
                lines.append(f"  ·  {title} [{ago}]")

    # ── Cron 执行记录 ──────────────────────────────────────────────────────────
    if cron_records:
        lines.append(f"\n【Cron 执行记录】")
        for r in cron_records[-6:]:
            job_id   = r.get("job_id", "?")
            job_name = r.get("job_name", job_id)
            summary  = r.get("summary", "")
            ago      = _ago(r.get("at", 0))
            detail   = f" — {summary[:60]}" if summary and summary != f"Cron job 触发：{job_id}" else ""
            lines.append(f"  ✓ {job_name}{detail} [{ago}]")
        if len(cron_records) > 6:
            lines.append(f"  ... 还有 {len(cron_records)-6} 条")

    # ── 探索实验结果 ───────────────────────────────────────────────────────────
    if explore_records:
        lines.append(f"\n【探索实验结果】")
        for r in explore_records[-4:]:
            ok      = r.get("success", False)
            goal    = r.get("goal", "")[:60]
            finding = r.get("finding", "")[:80]
            tokens  = r.get("tokens_used", 0)
            ago     = _ago(r.get("at", 0))
            icon    = "✅" if ok else "✗ "
            token_str = f"，{tokens} tokens" if tokens else ""
            lines.append(f"  {icon} {goal} [{ago}{token_str}]")
            if finding:
                lines.append(f"     → {finding}")
            skill_id = r.get("proposed_skill_id")
            if skill_id:
                lines.append(f"     → 已生成技能提案：{skill_id}（/evolve review 查看）")

    # ── Agent 建议目标（含 accept/reject 快捷指令）─────────────────────────────
    if goal_records:
        lines.append(f"\n【💡 Agent 建议目标】")
        for r in goal_records[-4:]:
            goal_id = r.get("goal_id", "?")
            title   = r.get("title", goal_id)
            summary = r.get("summary", "")
            ago     = _ago(r.get("at", 0))
            # 来源说明：从 summary 中提取（例如 "来自 capability_map（成功率 28%）"）
            source_note = ""
            if " — " in summary:
                source_note = " — " + summary.split(" — ", 1)[-1]
            lines.append(f"  💡 \"{title}\"{source_note} [{ago}]")
            lines.append(f"     /goals accept {goal_id}  接受  |  /goals reject {goal_id}  拒绝（30天去重）")

    # ── 进化提案 ────────────────────────────────────────────────────────────────
    if evolve_records:
        lines.append(f"\n【进化提案】{len(evolve_records)} 个待审：")
        for r in evolve_records[-3:]:
            lines.append(f"  · {r.get('summary', r.get('branch', ''))}")
        if len(evolve_records) > 3:
            lines.append(f"  ... 还有 {len(evolve_records)-3} 个")
        lines.append("  /evolve review 查看并审核")

    # ── 其余活动 ────────────────────────────────────────────────────────────────
    if other_records:
        lines.append(f"\n【其他活动】{len(other_records)} 条：")
        for r in other_records[-4:]:
            summary = r.get("summary") or r.get("task_desc") or r.get("type", "")
            ago = _ago(r.get("at", 0))
            lines.append(f"  · [{ago}] {summary[:80]}")

    return "\n".join(lines)


__all__ = [
    "ResourceArbiter",
    "append_activity_digest",
    "read_activity_digest",
    "build_digest_summary",
    "record_gating_transition",
    "read_gating_history",
]
