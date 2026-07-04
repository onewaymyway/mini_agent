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
    final_report: str = ""           # 结束时的汇报文本
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

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
            final_report=d.get("final_report", ""),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
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
        except Exception:
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


def find_resumable_session(project_root, from_session_id: Optional[str] = None) -> Optional[str]:
    """在 sessions_dir 下扫描，找到最近一个 status=="running" 的 goal_state.json 对应的
    session_id（供启动时的"检测到未完成目标"提示使用）。

    只做粗粒度扫描（按文件 mtime 取最新一个），不做复杂索引——这足够覆盖
    "上次进程被杀掉、重新打开项目"这个核心场景。
    """
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=project_root)
    sessions_dir = paths.sessions_dir
    if not sessions_dir.exists():
        return None

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
