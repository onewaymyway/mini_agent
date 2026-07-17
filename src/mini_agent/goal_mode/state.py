"""
goal_mode/state.py — GoalState 落盘 / 恢复

设计目标：进程被意外杀死（kill -9 / 崩溃 / 断电）后，重新打开时能从上一个
已完成的轮次边界继续，而不是从头再来，也不会因为写入过程中被中断而损坏状态文件。

落盘时机（只在轮次边界写，不在轮次内部频繁写）：
  1. GoalSpec 确认冻结时 → 写一次（status=running, round=0）
  2. 每轮 run_turn 完成 + Judge 判定完成后 → 更新一次
  3. compact 完成后 → 更新一次（compacts_done+1）
  4. 结束时（DONE / 安全阀触发 / 用户取消）→ 写终态

最坏情况：只丢失"正在进行中的那一轮"，不会丢整个 goal 或损坏历史。

依赖：复用已有的 session 持久化机制（agent.session_id / agent.load_session），
本模块只存"指向 session 的引用 + goal 专属元数据"，不重复保存对话历史。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from .spec import GoalSpec


@dataclass
class GoalState:
    """Goal 模式的运行状态快照。"""
    status: str = "running"          # running | done | cancelled | failed
    session_id: str = ""             # 主 Agent 的 session_id（历史通过它恢复，不重复存）
    goal_spec: dict = field(default_factory=dict)   # 冻结后的 GoalSpec.to_dict()
    round: int = 0
    last_judge_feedback: str = ""
    last_judge_status: str = ""
    compacts_done: int = 0
    consecutive_same_feedback: int = 0
    stuck_recoveries_used: int = 0   # 已用掉几次"卡住→compact→再给一次机会"的恢复额度
    final_report: str = ""           # 结束时的汇报文本
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── [next_doc/goal_mode_completion_improvement_plan.md 改造项三] ─────────
    # 验收标准逐条状态追踪：每项 {"index": int, "text": str, "passed": bool,
    # "evidence": str, "last_updated_round": int}。只在
    # cfg.goal_mode.criteria_tracking_enabled=True 且 GoalJudge 按扩展 schema
    # 输出了 checklist 字段时才会被更新；功能关闭或解析失败时保持初始状态
    # （全部 passed=False），不影响原有判定流程。
    criteria_status: list = field(default_factory=list)

    # ── [改造项二] 最近几轮 GoalJudge 给出的 progress/progress_reason 记录，
    # 每项 {"round": int, "progress": str, "reason": str}，供卡住恢复时拼装
    # "已尝试路径清单"提示使用。只保留最近若干条（由 GoalRunner 控制上限，
    # 落盘时已经是裁剪后的列表）。
    recent_progress_reasons: list = field(default_factory=list)

    # ── [goal_mode_stuck_compact_plan.md §1.2] Dead-end 持久清单：只增不减
    # （去重后）的"已验证无效路径"记录，不随 recent_progress_reasons 的滚动
    # 窗口被冲掉。每项 {"round": int, "reason": str, "progress": str}。
    dead_ends: list = field(default_factory=list)

    # ── [goal_mode_stuck_compact_plan.md §3.1] 进展分数：last_passed_count
    # 记录上一轮 checklist 通过条数（用于算增量），progress_scores 记录最近
    # 若干轮的分数序列（供未来 §3.2 伪进展趋势识别复用，本次改造只落地
    # 分数计算与记录，不改变 stuck 判定逻辑本身）。
    last_passed_count: int = 0
    progress_scores: list = field(default_factory=list)

    # ── [goal_mode_stuck_compact_plan.md §5] Goal 重规划提议：仅在
    # cfg.goal_mode.replan_proposal_mode != "off" 且主 Agent 在最后一次卡住
    # 恢复机会里给出过非空提议时才非空，格式为
    # {"suggested_split": [...], "suggested_criteria_changes": [...],
    # "reason": str}。落盘后即使进程重启，`/goal revise` 也能读到上次终止时
    # 的提议作为修订起点，不需要用户凭记忆重新描述问题。
    replan_proposal: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "GoalState":
        return GoalState(
            status=d.get("status", "running"),
            session_id=d.get("session_id", ""),
            goal_spec=d.get("goal_spec", {}) or {},
            round=int(d.get("round", 0)),
            last_judge_feedback=d.get("last_judge_feedback", ""),
            last_judge_status=d.get("last_judge_status", ""),
            compacts_done=int(d.get("compacts_done", 0)),
            consecutive_same_feedback=int(d.get("consecutive_same_feedback", 0)),
            stuck_recoveries_used=int(d.get("stuck_recoveries_used", 0)),
            final_report=d.get("final_report", ""),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
            criteria_status=list(d.get("criteria_status", []) or []),
            recent_progress_reasons=list(d.get("recent_progress_reasons", []) or []),
            dead_ends=list(d.get("dead_ends", []) or []),
            last_passed_count=int(d.get("last_passed_count", 0)),
            progress_scores=list(d.get("progress_scores", []) or []),
            replan_proposal=dict(d.get("replan_proposal", {}) or {}),
        )


class GoalStateStore:
    """负责 GoalState 的原子写入 / 读取 / 清理。

    原子写：先写临时文件再 os.replace()，防止写入过程中被 kill 导致
    goal_state.json 本身损坏（半截 JSON）。
    """

    def __init__(self, paths: "AgentPaths", session_id: str) -> None:
        self._paths = paths
        self._session_id = session_id

    @property
    def _path(self) -> Path:
        return self._paths.session_goal_state(self._session_id)

    def save(self, state: GoalState) -> None:
        state.updated_at = time.time()
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        data = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # 原子替换，rename 在同一文件系统内是原子操作

    def load(self) -> Optional[GoalState]:
        path = self._path
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return GoalState.from_dict(data)
        except Exception:
            # 状态文件本身损坏：不静默用空状态继续，返回 None 让调用方明确处理
            return None

    def clear(self) -> None:
        path = self._path
        try:
            if path.exists():
                path.unlink()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.goal_mode.state')
            pass

    def exists(self) -> bool:
        return self._path.exists()


def scan_goal_states(project_root) -> list[dict]:
    """扫描 sessions_dir 下所有 goal_state.json，返回诊断信息列表（不做任何过滤）。

    用于 `/goal resume` 在找不到可恢复目标时给出具体原因（比如"确实没有任何
    goal_state.json"，还是"有，但状态都不是 running"，还是"文件损坏"），
    而不是只回一句"没找到"让用户无从排查。
    """
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=project_root)
    sessions_dir = paths.sessions_dir
    results: list[dict] = []
    if not sessions_dir.exists():
        return results

    try:
        entries = list(sessions_dir.iterdir())
    except Exception as e:
        return [{"session_id": None, "error": f"无法列出 sessions_dir：{e}"}]

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except Exception:
            continue
        gs_path = entry / "goal_state.json"
        if not gs_path.exists():
            continue
        try:
            with open(gs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append({
                "session_id": entry.name,
                "status": data.get("status"),
                "round": data.get("round"),
                "updated_at": data.get("updated_at"),
                "error": None,
            })
        except Exception as e:
            results.append({"session_id": entry.name, "error": f"goal_state.json 解析失败：{e}"})

    return results


def list_resumable_sessions(project_root, include_stuck: bool = False) -> list[dict]:
    """扫描 sessions_dir 下所有 status=="running" 的 goal_state.json，按更新时间倒序返回。

    与 find_resumable_session() 的区别：后者只返回"最近一个"（供启动提示用一行话
    简短提醒），这里返回全部——用于 `/goal list`，避免"多个进程各自 /goal 了不同
    目标、都被杀死后，重启只能看到最近一个，其余的就像丢了"这种情况（其实文件都还
    在，只是没有入口能看到）。

    include_stuck=True 时，额外把 status=="stuck" 的会话也一起收进结果（用
    "status" 字段区分，"running" 会话不显式带这个字段以保持向后兼容）。
    [BUGFIX] 此前 stuck 终止的 goal 一旦落盘就彻底从 /goal list 里消失，用户
    完全不知道还能用 `/goal resume <sid> --force` 把它捞回来继续——这里让它
    至少"可见"，恢复动作本身仍然需要显式 --force，不会误导成可以无脑续跑。
    """
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=project_root)
    sessions_dir = paths.sessions_dir
    if not sessions_dir.exists():
        return []

    wanted_statuses = {"running", "stuck"} if include_stuck else {"running"}

    candidates = []
    for entry in sessions_dir.iterdir():
        if not entry.is_dir():
            continue
        gs_path = entry / "goal_state.json"
        if not gs_path.exists():
            continue
        try:
            with open(gs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        status = data.get("status")
        if status in wanted_statuses:
            goal_spec = data.get("goal_spec") or {}
            goal_text = (goal_spec.get("goal_text") or "").strip()
            entry_dict = {
                "session_id": entry.name,
                "round": data.get("round"),
                "updated_at": data.get("updated_at"),
                "goal_text": goal_text,
                "mtime": gs_path.stat().st_mtime,
            }
            if status != "running":
                entry_dict["status"] = status
                entry_dict["final_report"] = (data.get("final_report") or "").strip()
            candidates.append(entry_dict)
    candidates.sort(key=lambda x: x["mtime"], reverse=True)
    for c in candidates:
        c.pop("mtime", None)
    return candidates


def find_resumable_session(project_root, from_session_id: Optional[str] = None) -> Optional[str]:
    """在 sessions_dir 下扫描，找到可恢复的 goal_state.json 对应的 session_id。

    [BUGFIX] 此前 from_session_id 参数虽然存在，但函数体里完全没有用到它——
    导致 `/goal resume`（不带参数）无论当前在哪个 session 里，永远是"全局按
    文件 mtime 找最近一个 running 的 goal"，而不是"优先继续本 session 自己
    的 goal"。典型翻车场景：session A 的 goal 还在 running，但 session B
    之前跑过 goal 且 mtime 更新（哪怕早就结束/取消，只要文件被后续操作碰过
    导致 mtime 更晚），在 session A 里执行 `/goal resume` 就会被错误地
    恢复成 session B 的 goal。

    现在的优先级：
      1. 若传入 from_session_id，且该 session 自己的 goal_state.json 存在
         且 status == "running"，直接返回 from_session_id（不管其他 session
         里有没有更新的 goal）——这是"默认继续本 session 的 goal"这个直觉
         预期。
      2. 否则退化为原有行为：全局扫描所有 session，按 goal_state.json 的
         mtime 取最新一个 status == "running" 的（供启动时的"检测到未完成
         目标"提示、或本 session 确实没有可恢复目标时的兜底使用）。

    只做粗粒度扫描（按文件 mtime 取最新一个），不做复杂索引——这足够覆盖
    "上次进程被杀掉、重新打开项目"这个核心场景。
    """
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=project_root)
    sessions_dir = paths.sessions_dir
    if not sessions_dir.exists():
        return None

    # 1. 优先检查当前 session 自己是否有可恢复的 goal
    if from_session_id:
        own_gs_path = sessions_dir / from_session_id / "goal_state.json"
        if own_gs_path.exists():
            try:
                with open(own_gs_path, "r", encoding="utf-8") as f:
                    own_data = json.load(f)
                if own_data.get("status") == "running":
                    return from_session_id
            except Exception:
                pass  # 本 session 的记录读取失败，落到下面的全局兜底扫描

    # 2. 全局兜底：按 mtime 取最新一个 running 的 goal_state.json
    candidates = []
    for entry in sessions_dir.iterdir():
        if not entry.is_dir():
            continue
        gs_path = entry / "goal_state.json"
        if not gs_path.exists():
            continue
        try:
            with open(gs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("status") == "running":
            candidates.append((gs_path.stat().st_mtime, entry.name))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
