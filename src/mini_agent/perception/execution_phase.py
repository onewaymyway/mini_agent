"""
perception/execution_phase.py — Goal 执行阶段（ExecutionPhaseState）

（next_doc/goal_execution_phase_improvement_plan.md）

在 GoalExecutionSpec（"每轮该产出什么"）之上加一层"现在该怎么干"：
explore（探索）/ converge（收敛）/ stable（稳定）/ tidy（整理）/ auto（自动）。

存储：独立文件 `.agent/goal_execution_phase/<goal_id>.json`，不改动
`goals.json` 的 GoalNode 结构，与 GoalExecutionSpec 的隔离存储方式一致。
文件不存在时视为默认状态（mode="auto", locked=False），行为与引入本机制
之前完全一致。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


VALID_MODES = ("explore", "converge", "running", "tidy", "auto")

# [Stage 8b] 旧数据/旧调用方仍可能写入 "stable"（改名前的规范名），统一在
# 读取/写入入口做别名归一化，避免历史落盘文件在改名后被误判为非法状态。
_LEGACY_MODE_ALIASES = {"stable": "running"}


def _normalize_mode(mode: str) -> str:
    """把可能的旧名（如 "stable"）映射到当前规范名（"running"）。

    未知字符串原样返回，由调用方各自决定是回退默认还是报错——本函数只做
    别名替换，不做值域校验。
    """
    return _LEGACY_MODE_ALIASES.get(mode, mode)


# auto 模式下的默认规则参数（§2）。后续如需可配置，从 AppConfig 读取覆盖。
DEFAULT_EXPLORE_MIN_CYCLES = 3
DEFAULT_SPEC_STABLE_CYCLES = 2
DEFAULT_TIDY_EVERY_N_CYCLES = 0  # 0 = 关闭自动 tidy 插入

# [goal_cron_task_optimization_holistic_plan.md 方向 B] 健康告警的默认阈值。
DEFAULT_STUCK_EXPLORE_CYCLES = 6      # auto 模式下连续判定为 explore 达到此轮数即告警
DEFAULT_FLAP_WINDOW = 8               # 检查 mode_history 最近这么多条 auto 判定记录
DEFAULT_FLAP_THRESHOLD = 3            # 窗口内"回退到 explore/converge"的次数达到此值即告警
DEFAULT_HEALTH_ALERT_COOLDOWN_SECONDS = 3 * 24 * 3600  # 同一种告警的最短复发间隔

# [goal_cron_task_optimization_holistic_plan.md §5 调度联动子项] 各阶段的
# 相对资源倍率——explore/converge 期任务定义/方案本身还在变动，多给一点
# 超时/重试预算换取"别因为节流误伤还在摸索的早期尝试"；running/tidy 期
# 任务已经跑顺，收紧成本控制。数值是启发式初始值（1.0 为改进前的统一
# 基线，未接入本机制时的行为），刻意不做更复杂的模型——与
# `DEFAULT_STUCK_EXPLORE_CYCLES` 等阈值一样，先上线观察，需要调整时改
# 这里的常量即可，不涉及调用方代码变动。
DEFAULT_PHASE_RESOURCE_MULTIPLIERS = {
    "explore": 1.3,
    "converge": 1.15,
    "running": 1.0,
    "tidy": 0.85,
}


@dataclass
class ModeChange:
    at: float
    from_mode: str
    to_mode: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"at": self.at, "from": self.from_mode, "to": self.to_mode, "reason": self.reason}

    @staticmethod
    def from_dict(d: dict) -> "ModeChange":
        return ModeChange(
            at=float(d.get("at", 0.0)),
            from_mode=d.get("from", ""),
            to_mode=d.get("to", ""),
            reason=d.get("reason", ""),
        )


@dataclass
class ExecutionPhaseState:
    version: int = 1
    goal_id: str = ""
    mode: str = "auto"                # explore | converge | running | tidy | auto
    locked: bool = False
    stability_score: float = 0.0
    cycles_in_mode: int = 0
    last_tidy_cycle: Optional[int] = None
    mode_history: list[ModeChange] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    # [goal_cron_task_optimization_holistic_plan.md 方向 B] 上一次健康告警
    # 的时间戳/种类，用于告警冷却——避免同一种健康问题每轮都重复推送。
    # 不参与阶段判定本身，纯粹是通知层的去重状态。
    last_health_alert_at: float = 0.0
    last_health_alert_kind: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode_history"] = [m.to_dict() for m in self.mode_history]
        return d

    @staticmethod
    def from_dict(d: dict) -> "ExecutionPhaseState":
        mode = _normalize_mode(d.get("mode", "auto"))
        if mode not in VALID_MODES:
            mode = "auto"
        return ExecutionPhaseState(
            version=int(d.get("version", 1)),
            goal_id=d.get("goal_id", ""),
            mode=mode,
            locked=bool(d.get("locked", False)),
            stability_score=float(d.get("stability_score", 0.0)),
            cycles_in_mode=int(d.get("cycles_in_mode", 0)),
            last_tidy_cycle=d.get("last_tidy_cycle"),
            mode_history=[ModeChange.from_dict(m) for m in d.get("mode_history", [])],
            updated_at=float(d.get("updated_at", time.time())),
            last_health_alert_at=float(d.get("last_health_alert_at", 0.0)),
            last_health_alert_kind=d.get("last_health_alert_kind", ""),
        )

    def record_transition(self, to_mode: str, reason: str = "") -> None:
        if to_mode == self.mode:
            return
        self.mode_history.append(ModeChange(at=time.time(), from_mode=self.mode, to_mode=to_mode, reason=reason))
        # mode_history 只做诊断展示用，避免无限增长
        if len(self.mode_history) > 50:
            self.mode_history = self.mode_history[-50:]
        self.mode = to_mode
        self.cycles_in_mode = 0


def _phase_dir(paths: "AgentPaths") -> Path:
    return Path(paths.project_root) / ".agent" / "goal_execution_phase"


def _phase_path(paths: "AgentPaths", goal_id: str) -> Path:
    safe_id = goal_id.replace("/", "_")
    return _phase_dir(paths) / f"{safe_id}.json"


def load_phase(paths: "AgentPaths", goal_id: str) -> ExecutionPhaseState:
    """不存在/损坏时返回默认状态（mode="auto", locked=False），不抛异常。"""
    p = _phase_path(paths, goal_id)
    if not p.exists():
        return ExecutionPhaseState(goal_id=goal_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        state = ExecutionPhaseState.from_dict(data)
        state.goal_id = goal_id
        return state
    except Exception:
        return ExecutionPhaseState(goal_id=goal_id)


def save_phase(paths: "AgentPaths", state: ExecutionPhaseState) -> None:
    _phase_dir(paths).mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    atomic_write_json(_phase_path(paths, state.goal_id), state.to_dict())


def set_mode(paths: "AgentPaths", goal_id: str, mode: str, *, lock: Optional[bool] = None,
             reason: str = "user_set") -> ExecutionPhaseState:
    """用户手动切换阶段。mode 必须是 VALID_MODES 之一（"stable" 作为改名前的
    旧别名仍被接受，自动归一化为 "running"，避免存量脚本/文档一夜之间失效）。
    """
    mode = _normalize_mode(mode)
    if mode not in VALID_MODES:
        raise ValueError(f"invalid execution phase mode: {mode!r}, must be one of {VALID_MODES}")
    state = load_phase(paths, goal_id)
    state.record_transition(mode, reason=reason)
    if lock is not None:
        state.locked = lock
    elif mode != "auto":
        # 手动指定非 auto 阶段时，默认视为用户意图明确，隐式锁定，避免
        # 下一轮自动判定立刻把用户刚设置的阶段覆盖掉。
        state.locked = True
    else:
        state.locked = False
    save_phase(paths, state)
    return state


def unlock_mode(paths: "AgentPaths", goal_id: str) -> ExecutionPhaseState:
    state = load_phase(paths, goal_id)
    state.locked = False
    save_phase(paths, state)
    return state


def _llm_judge_progress_trend(notes: list[str], llm_helper) -> Optional[bool]:
    """[goal_stuck_stats_and_llm_progress_judge_plan.md §2] 把最近几轮
    `progress_notes` 原文交给 LLM，判断这几轮是"真的在原地打转"还是
    "内容上有实质推进（哪怕文字表述相似）"或"看起来相似但属于正常重复"
    （比如周期性巡检类 Goal）。

    只要求回答三选一关键词：STUCK / PROGRESSING / UNSURE。解析不到合法
    关键词、响应为空、或调用抛异常，一律返回 None（调用方据此退回 difflib
    结果），不影响 Goal 触发主流程，也不在这里做重试。
    """
    if llm_helper is None or not notes:
        return None
    try:
        notes_block = "\n".join(f"第{i + 1}轮：{n}" for i, n in enumerate(notes))
        prompt = (
            "下面是同一个周期性任务最近几轮的进展记录（每轮任务结束后留下的\n"
            "摘要文字）。请判断这几轮是否属于\"内容上原地打转、没有实质推进\"，\n"
            "还是\"确实有实质推进（哪怕措辞相似）\"，或者\"内容雷同但属于这个\n"
            "任务本身该有的正常重复\"（例如周期性巡检、格式固定的定期汇报）。\n"
            "只回答以下三个词之一，不要输出任何其它内容：\n"
            "STUCK（原地打转，没有实质推进）\n"
            "PROGRESSING（有实质推进，或雷同属于正常重复）\n"
            "UNSURE（无法判断）\n\n"
            f"{notes_block}"
        )
        raw = llm_helper(prompt)
        if not raw or not raw.strip():
            return None
        text = raw.strip().upper()
        if "STUCK" in text and "PROGRESSING" not in text:
            return True
        if "PROGRESSING" in text and "STUCK" not in text:
            return False
        return None
    except Exception:
        return None


def compute_progress_trend_signal(
    goal_backlog, goal_id: str, *, window: int = 3, similarity_threshold: float = 0.85,
    llm_helper=None,
) -> Optional[bool]:
    """[goal_execution_phase_improvement_plan.md Stage D /
    goal_stuck_stats_and_llm_progress_judge_plan.md §2] 跨轮"进展趋势"信号。

    读取这个 Goal 最近已完成的 `window` 个周期子节点（`reaped_cycle_child_ids`
    末尾）的 `progress_notes` 文本。

    `llm_helper` 传入（且配置开启，见 `ExecutionPhaseConfig.
    progress_trend_llm_enabled`）时优先用 LLM 判断（`_llm_judge_progress_trend`）
    ——只看文本相似度分不清"雷同但正常"（比如周期性巡检类 Goal）和"雷同
    且确实卡住"，LLM 能结合语义判断。LLM 不可用/未传入/判断不出结果（返回
    `None`）时，退回原有的 difflib 文本相似度判断（两两比较相邻轮次的
    `progress_notes`，全部达到 `similarity_threshold` 才判定为 True）。

    历史不足 `window` 轮、缺少 `progress_notes`、或任何环节异常，返回
    `None`（不参与判定，等价于该信号关闭）——这一层判定在 LLM 和 difflib
    两条路径下语义一致，不会因为选了哪条路径而改变"信息不足就不判定"的
    保守策略。
    """
    if goal_backlog is None or not goal_id:
        return None
    try:
        goal = goal_backlog.get(goal_id)
        if goal is None:
            return None
        reaped = list(getattr(goal, "reaped_cycle_child_ids", []) or [])
        if len(reaped) < window:
            return None
        recent_ids = reaped[-window:]
        notes: list[str] = []
        for child_id in recent_ids:
            child = goal_backlog.get(child_id)
            note = (getattr(child, "progress_notes", "") or "").strip() if child is not None else ""
            if not note:
                return None  # 缺少足够信息时不判定，避免误判
            notes.append(note)

        if llm_helper is not None:
            llm_result = _llm_judge_progress_trend(notes, llm_helper)
            if llm_result is not None:
                return llm_result
            # LLM 不可用/判断不出结果时，静默退回下面的 difflib 兜底，
            # 不提前 return None（否则等于关闭了这个信号，而不是"降级"）。

        import difflib

        for i in range(1, len(notes)):
            ratio = difflib.SequenceMatcher(None, notes[i - 1], notes[i]).ratio()
            if ratio < similarity_threshold:
                return False
        return True
    except Exception:
        return None


def compute_routine_stability_signal(
    routine_texts: list[str], *, similarity_threshold: float = 0.85, llm_helper=None,
) -> Optional[bool]:
    """[goal_output_directory_and_execution_phase_redesign_plan.md §Stage8b]
    规范层"execution_routine 是否已收敛"的信号——照搬
    `compute_progress_trend_signal()`/`_llm_judge_progress_trend()` 的思路
    （LLM 优先，判断不出来退回 difflib 相邻版本相似度比较），只是比较对象从
    "跨轮进展描述文本"换成"execution_routine 历次版本的序列化文本"。

    调用方（`goal_cron_bridge.py`）负责组装 `routine_texts`：一般取
    `list_spec_history()` 里最近几个历史版本 + 当前版本的
    `execution_routine`，各自序列化成 `"\\n".join(r.step for r in routine)`
    形式的纯文本，按时间顺序传入。

    返回 True 代表"最近几个版本的 routine 基本没变"（规范层已收敛，可以
    支持进入 running）；False 代表"还在变"（规范层仍不稳定）；样本不足
    （少于 2 条）或任何环节异常，返回 None（不参与判定，等价于该信号关闭，
    与 `compute_progress_trend_signal` 的保守策略一致）。

    这是一个独立信号，本阶段（Stage 8b）**尚未接入** `resolve_effective_mode`
    的判定路径——是否要用它来"加速进入 running"还是仅用于展示，需要先观察
    真实 routine 版本演进数据再决定，留给 Stage 8c 及以后。
    """
    if not routine_texts or len(routine_texts) < 2:
        return None
    try:
        texts = [t for t in routine_texts if (t or "").strip()]
        if len(texts) < 2:
            return None

        if llm_helper is not None:
            llm_result = _llm_judge_progress_trend(texts, llm_helper)
            if llm_result is not None:
                # `_llm_judge_progress_trend` 的 True 语义是"STUCK（文本雷同/
                # 没有变化）"——对 execution_routine 而言，"没有变化"正是我们
                # 想要的收敛信号，语义方向恰好一致，直接透传不需要取反。
                return llm_result
            # LLM 不可用/判断不出结果时，静默退回下面的 difflib 兜底。

        import difflib

        for i in range(1, len(texts)):
            ratio = difflib.SequenceMatcher(None, texts[i - 1], texts[i]).ratio()
            if ratio < similarity_threshold:
                return False
        return True
    except Exception:
        return None


def resolve_effective_mode(
    state: ExecutionPhaseState,
    *,
    cycle_no: int,
    spec_confirmed: bool,
    spec_recently_revised: bool,
    miss_streak: int = 0,
    tidy_every_n_cycles: int = DEFAULT_TIDY_EVERY_N_CYCLES,
    progress_trend_stuck: Optional[bool] = None,
    routine_stability: Optional[bool] = None,
) -> tuple[str, ExecutionPhaseState]:
    """计算本轮的"有效阶段"，并按需推进/落盘 mode_history（调用方负责 save）。

    - locked=True 或 mode != "auto"：直接返回用户指定的阶段，不做规则判定。
    - mode == "auto"：按 §2 规则粗略判定：
        cycle_no <= explore_min_cycles                → explore
        spec 未确认 / 最近仍在被 revise / miss_streak 高 → explore（还不稳定）
        spec 已确认且近期未 revise 且 miss_streak 低    → running（跳过 converge，
          第一版不做"候选方案对比"识别，converge 仅作为可手动进入的阶段）
        否则                                            → converge（过渡态）
      [Stage D] progress_trend_stuck=True（跨轮进展文本高度雷同）时，即使
      前面条件满足 running，也降级为 converge——"文件层面看起来收敛了，但
      内容层面可能只是在重复"，用 converge 阶段要求的"方案对比说明"倒逼
      agent 交代清楚，而不是静默判 running。不满足 running 条件时该信号不
      生效（不会把 explore 进一步"加重"，避免同一个粗糙信号被用在两个
      方向上）。**该降级仅在 `new_topic_discovery != "intrinsic"` 时生效**
      ——累积型/双轨型 goal（wiki、股票报告等）内容层天然每轮都不同，
      "跨轮进展文本雷同"这个信号对它们没有意义，调用方（goal_cron_bridge）
      在读取到 spec.new_topic_discovery == "intrinsic" 时应直接不传/传
      `progress_trend_stuck=None`，本函数不重复做这层判断，只负责在拿到
      `True` 时执行降级。
      [Stage 8c] `routine_stability=True`（`execution_routine`
      最近几个版本的历次文本高度相似，见
      `compute_routine_stability_signal()`）时，作用方向与
      `progress_trend_stuck` 相反、层级也不同——`progress_trend_stuck` 是
      "内容层面看起来在重复"，作用于已经判定为 running 的场景，做降级；
      `routine_stability` 是"规范层面的标准动作已经稳定"，只在 target
      已经落在 converge（即 spec 已确认、未被近期 revise，但
      `soft_check_miss_streak==1` 导致粗规则判 converge 兜底）时生效，
      把 converge **提升**为 running——"该走的例程已经稳定复现，个别一次
      软核查未命中不足以打回收敛判定"。target 已经是 explore 或已经是
      running（含被 `progress_trend_stuck` 降级后的 converge）时，该信号
      不生效——不用它去覆盖 explore（信息不足的早期阶段不该被单一信号
      跳过）、也不用它去对抗刚发生的降级（避免两个信号互相拉扯出抖动）。
      `None`/`False` 均不产生任何效果，与其余信号一致的保守风格。
    """
    if state.locked or state.mode != "auto":
        # [Stage B] tidy 阶段是"一次性插入"的维护动作：手动/自动进入 tidy 后，
        # 执行完一轮（cycles_in_mode 已经 >=1，说明已经跑过一次 tidy 提示）
        # 就自动回到 running 并解除锁定，不需要用户手动切回，避免每轮都停在
        # 整理模式不产出正常内容。
        if state.mode == "tidy" and state.cycles_in_mode >= 1:
            state.last_tidy_cycle = cycle_no
            state.record_transition("running", reason="tidy_auto_revert")
            state.locked = False
            return "running", state
        state.cycles_in_mode += 1
        return state.mode, state

    converge_from_miss_streak = False
    if cycle_no <= DEFAULT_EXPLORE_MIN_CYCLES:
        target = "explore"
    elif not spec_confirmed or spec_recently_revised or miss_streak >= 2:
        target = "explore"
    elif spec_confirmed and not spec_recently_revised and miss_streak == 0:
        target = "running"
        if progress_trend_stuck is True:
            target = "converge"
    else:
        target = "converge"
        converge_from_miss_streak = True

    # [Stage 8c] 仅当 converge 是因为 soft_check_miss_streak==1 这种粗规则
    # 兜底（而非刚被 progress_trend_stuck 降级）时，routine_stability=True
    # 才把它提升回 running——理由见函数 docstring。
    if target == "converge" and converge_from_miss_streak and routine_stability is True:
        target = "running"

    # [Stage B §2.4] 稳定期周期性自动插入 tidy：仅当已经判定为 running、
    # 配置了 tidy_every_n_cycles>0、且距上次 tidy 已满足轮次间隔时触发。
    # 触发后本轮 effective mode 直接给 tidy（下一轮由上面的
    # "tidy 一轮后自动回 running"逻辑收尾），不改变 state.mode 本身
    # （仍是 "auto"）。
    if target == "running" and tidy_every_n_cycles and tidy_every_n_cycles > 0:
        last_tidy = state.last_tidy_cycle or 0
        if cycle_no - last_tidy >= tidy_every_n_cycles:
            target = "tidy"
            state.last_tidy_cycle = cycle_no

    # stability_score：粗略地用"是否达到 running 判定条件"映射到 0~1，
    # 仅供展示参考，不参与其他逻辑。
    if target == "running":
        state.stability_score = 1.0
    elif target == "converge":
        state.stability_score = 0.6
    else:
        state.stability_score = min(0.4, cycle_no / max(DEFAULT_EXPLORE_MIN_CYCLES, 1) * 0.4)

    if target != state.mode:
        # auto 模式下的自动切换不改变 state.mode 字段本身（mode 仍保持
        # "auto"，代表"跟随自动判定"），只记一条历史，effective mode 单独
        # 返回。真正把 state.mode 写死为具体阶段的，只有用户手动 set_mode。
        state.mode_history.append(
            ModeChange(at=time.time(), from_mode=f"auto:{state.mode if state.mode != 'auto' else 'auto'}",
                       to_mode=f"auto:{target}", reason="rule_based_auto")
        )
        if len(state.mode_history) > 50:
            state.mode_history = state.mode_history[-50:]
    state.cycles_in_mode += 1
    return target, state


def last_known_effective_mode(state: ExecutionPhaseState) -> str:
    """[goal_cron_task_optimization_holistic_plan.md 方向 A] 不重新计算判定，
    只读取"最近一次已知的有效阶段"，供归档/调度这类不适合在每次调用时都
    重新跑一遍完整规则判定的场景使用。

    - `state.mode != "auto"`：用户手动指定的阶段就是当前有效阶段，直接返回。
    - `state.mode == "auto"`：从 `mode_history` 里找最近一条
      `reason == "rule_based_auto"` 的记录，取其 `to_mode`（形如
      `"auto:running"`）解析出阶段名。
    - 没有任何历史记录（Goal 刚开始跑，或阶段机制还没被真正触发过）时，
      保守返回 `"explore"`——"不确定就当作还在探索期"，避免在阶段信息
      缺失时误判为已收敛而过早归档/放宽资源控制。
    """
    if state.mode != "auto":
        return state.mode
    for m in reversed(state.mode_history):
        if m.reason == "rule_based_auto" and m.to_mode.startswith("auto:"):
            return m.to_mode.split(":", 1)[1]
    return "explore"


def phase_resource_multiplier(
    mode: str,
    *,
    multipliers: Optional[dict] = None,
) -> float:
    """[goal_cron_task_optimization_holistic_plan.md §5 调度联动子项]
    把一个已知的有效阶段名换算成相对资源倍率，供调度层（目前是
    `UnifiedTaskScheduler` 的 Goal 通道只读预览）参考——explore 期给更
    宽松的预算，stable/tidy 期收紧。

    纯函数、零 IO：只做字典查找，`mode` 不在 `multipliers` 里（包括
    未知阶段名、或状态本身还没有任何历史因而无法判定）时保守返回
    `1.0`——即"这个机制引入之前的统一基线"，不因为阶段信息缺失而放大
    或收紧资源估算。`multipliers` 参数留给未来接 `AppConfig` 覆盖默认值
    用，不传时用模块顶部的 `DEFAULT_PHASE_RESOURCE_MULTIPLIERS`。

    本函数只产出一个数字，是否/如何据此调整真实的执行资源分配，仍由
    调用方决定——与 `allocate_weighted_slots()` 一样，是"接管仲裁裁决"
    这条长期路径上的一块纯计算积木，本身不产生任何执行副作用。
    """
    table = multipliers if multipliers is not None else DEFAULT_PHASE_RESOURCE_MULTIPLIERS
    try:
        return float(table.get(mode, 1.0))
    except Exception:
        return 1.0


def check_phase_health(
    state: ExecutionPhaseState,
    effective_mode: str,
    *,
    stuck_explore_cycles: int = DEFAULT_STUCK_EXPLORE_CYCLES,
    flap_window: int = DEFAULT_FLAP_WINDOW,
    flap_threshold: int = DEFAULT_FLAP_THRESHOLD,
    cooldown_seconds: float = DEFAULT_HEALTH_ALERT_COOLDOWN_SECONDS,
) -> Optional[str]:
    """[goal_cron_task_optimization_holistic_plan.md 方向 B] 判断这个 Goal 的
    执行阶段是否出现了值得主动告诉用户的"健康问题"，返回一段中文告警原因；
    没有问题、或命中冷却期内已经告警过同一种问题，返回 None。

    这不是对 agent 行为的调节（那是 resolve_effective_mode 的职责），而是
    把阶段状态转成一个面向用户的信号：用户不需要每天巡检看板，系统主动
    在异常时喊一声。只做规则判定，第一版不引入额外 LLM 调用。

    两类问题：
    1. stuck_explore —— 长期（auto 模式下）判定为 explore 迟迟不收敛，往往
       意味着任务定义本身有问题（目标不清晰/环境不稳定），而不是 agent
       "还需要多试几次"。只在 mode == "auto" 且未被用户手动锁定时判定——
       用户手动锁定在 explore 是明确意图，不应被当成异常。
    2. phase_flapping —— 阶段反复从 running/converge 被打回 explore/converge
       （常见于 Stage D 的"伪进展"降级反复触发），意味着看起来收敛但内容
       层面并不稳定，值得用户介入看看，而不是让系统一直自动降级下去。

    两类问题共享同一个冷却字段（`last_health_alert_kind`/`last_health_alert_at`）
    ——同一种 kind 在 cooldown_seconds 内不重复返回，不同 kind 之间不互相
    抑制（stuck 和 flapping 是不同性质的问题，都值得各自提醒一次）。
    调用方负责在决定"确实要发送通知"后落盘更新这两个字段（本函数只读
    判断，不修改 state，保持与其它只读判定函数一致的风格）。
    """
    try:
        now = time.time()

        def _cooldown_ok(kind: str) -> bool:
            if state.last_health_alert_kind != kind:
                return True
            return (now - state.last_health_alert_at) >= cooldown_seconds

        if (
            state.mode == "auto"
            and not state.locked
            and effective_mode == "explore"
            and state.cycles_in_mode >= stuck_explore_cycles
        ):
            if _cooldown_ok("stuck_explore"):
                return (
                    f"已连续 {state.cycles_in_mode} 轮仍处于探索阶段（explore）未能收敛，"
                    "可能是任务目标不够清晰或执行环境不稳定，建议人工看一下这个 Goal 的"
                    "执行方向是否需要调整。"
                )
            return None

        if flap_threshold > 0 and len(state.mode_history) > 0:
            recent = [m for m in state.mode_history[-flap_window:] if m.reason == "rule_based_auto"]
            regressions = sum(
                1
                for m in recent
                if m.to_mode.startswith("auto:") and m.to_mode.split(":", 1)[1] in ("explore", "converge")
                and m.from_mode.startswith("auto:") and m.from_mode.split(":", 1)[1] in ("running", "converge")
                and m.to_mode.split(":", 1)[1] != m.from_mode.split(":", 1)[1]
            )
            if regressions >= flap_threshold and _cooldown_ok("phase_flapping"):
                return (
                    f"最近 {len(recent)} 次自动阶段判定里，有 {regressions} 次从更靠后的阶段"
                    "被打回更早的阶段（比如 running/converge 被打回 converge/explore），"
                    "说明这个 Goal 表面上看起来收敛了，但内容层面可能反复不稳定，建议人工"
                    "复核最近几轮的实际产出。"
                )
        return None
    except Exception:
        return None
