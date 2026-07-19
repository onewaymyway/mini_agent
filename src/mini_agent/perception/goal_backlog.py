"""
perception/goal_backlog.py — Stage 9 跨会话目标层级（Phase H，第六节）

维护 <project_root>/.agent/goals.json，存储两层目标：
  - Goal（目标）：用户或 agent derive 的长期意图
  - Objective（子目标）：可在若干 Task 内完成的具体目标，引用 WorkThread

与 Stage 4.3 work_index.json 的关系：
  Objective 通过 work_thread_ref 字段引用已有 WorkThread，
  复用其 cumulative_progress/next_suggested，不重复维护进展文本。

存储设计：
  - 纯运行时状态，不经过 StateRepo（与 work_index.json 定位一致）
  - 原子写（tmp + os.replace）
  - 跨进程并发：goals.json 是全项目共享的单文件，可能被多个进程（多个
    CLI session / daemon / HTTP API）同时读写。所有会修改内容的方法
    （add_goal / add_objective / set_status / update_progress）都通过
    ``_locked()`` 临界区执行：进入时加进程间独占文件锁并从磁盘重新加载
    最新状态，退出时落盘并释放锁。这样可以避免"A、B 两个进程各自基于
    旧快照修改后写回，后写的把先写的整个覆盖掉"的丢失更新问题。

档位边界（stage9_plan.md 第七节）：
  - passive 档位：AutonomousLoop.tick() 不读取 GoalBacklog 任何方法
  - maintenance 档位：读 has_actionable_work() 和 next_task()，但不 derive 新 Goal
  - autonomous 档位：可 derive 新 Goal/Objective（第十二节，暂不实现）
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths

try:
    import fcntl  # POSIX only（Linux / macOS）
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows 等无 fcntl 平台
    fcntl = None  # type: ignore
    _HAS_FCNTL = False


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class GoalNode:
    """
    统一的目标节点（Goal 或 Objective，用 level 字段区分）。
    与 WorkThread 现有\"一个 dataclass + 枚举字段区分类型\"风格一致。
    """
    id: str
    level: str                      # "goal" | "objective"
    title: str
    source: str                     # "user" | "agent_derived"
    status: str                     # "active" | "paused" | "completed" | "abandoned"
    created_at: float = 0.0
    last_touched_at: float = 0.0
    progress_notes: str = ""
    parent_id: Optional[str] = None
    children_ids: list[str] = field(default_factory=list)
    # 仅 Objective 使用：关联 WorkThread id
    work_thread_ref: Optional[str] = None
    # 优先级权重（数字越大越优先）
    priority: int = 0
    # 标签（用于分类）
    tags: list[str] = field(default_factory=list)
    # [修复] 目标的静态描述（"为什么要做这件事"），与 progress_notes
    # （"做到哪一步了"的动态追踪记录）语义不同，此前没有独立字段，
    # soft_goal_deriver.commit_goals() 一直在调用 add_goal(description=...)
    # 但该关键字参数根本不存在——每次有候选写入时 TypeError，被外层
    # except Exception 静默吞掉，"软目标自动推导"从未真正提交成功过一个
    # 目标节点。见 docs/system-events-bus-guide.md 第7节。
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "last_touched_at": self.last_touched_at,
            "progress_notes": self.progress_notes,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "work_thread_ref": self.work_thread_ref,
            "priority": self.priority,
            "tags": self.tags,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> "GoalNode":
        return GoalNode(
            id=d.get("id", ""),
            level=d.get("level", "goal"),
            title=d.get("title", ""),
            source=d.get("source", "user"),
            status=d.get("status", "active"),
            created_at=d.get("created_at", 0.0),
            last_touched_at=d.get("last_touched_at", 0.0),
            progress_notes=d.get("progress_notes", ""),
            parent_id=d.get("parent_id"),
            children_ids=d.get("children_ids", []),
            work_thread_ref=d.get("work_thread_ref"),
            priority=d.get("priority", 0),
            tags=d.get("tags", []),
            description=d.get("description", ""),
        )

    @property
    def is_goal(self) -> bool:
        return self.level == "goal"

    @property
    def is_objective(self) -> bool:
        return self.level == "objective"

    @property
    def is_active(self) -> bool:
        return self.status == "active"


# ── GoalBacklog 主类 ──────────────────────────────────────────────────────────

class GoalBacklog:
    """
    跨会话目标层级管理器。

    存储路径：<project_root>/.agent/goals.json
    """

    VERSION = 1

    def __init__(self, paths: AgentPaths) -> None:
        self._paths = paths
        self._goals_path = paths.workdir_dir / "goals.json"
        self._nodes: dict[str, GoalNode] = {}  # id -> GoalNode

    # ── 跨进程并发控制 ────────────────────────────────────────────────────────

    @property
    def _lock_path(self) -> Path:
        return self._goals_path.with_suffix(".json.lock")

    @contextlib.contextmanager
    def _locked(self):
        """独占临界区：加进程间文件锁 → 重新加载磁盘最新状态 → yield 给调用方
        做单个修改 → 落盘 → 释放锁。

        重新 load 这一步是关键：不这样做的话，即使加了锁，调用方内存里
        仍然是"进入临界区之前"的旧快照，一样会在 save() 时把锁等待期间
        其他进程写入的改动覆盖掉。加锁只保证互斥，"以最新数据为基础改"
        才能真正避免丢失更新。

        无 fcntl 的平台（如 Windows）退化为不加锁（仅刷新数据），尽力而为。
        """
        self._goals_path.parent.mkdir(parents=True, exist_ok=True)
        lock_f = None
        if _HAS_FCNTL:
            lock_f = open(self._lock_path, "w")
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            self.load()  # 丢弃旧内存状态，换成磁盘上最新的
            yield
            self.save()
        finally:
            if lock_f is not None:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog._locked')
                    pass
                lock_f.close()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """从磁盘加载（不存在时静默忽略）。"""
        if not self._goals_path.exists():
            return
        try:
            data = json.loads(self._goals_path.read_text(encoding="utf-8"))
            goals_list = data.get("goals", [])
            self._nodes = {
                g["id"]: GoalNode.from_dict(g)
                for g in goals_list
                if isinstance(g, dict) and "id" in g
            }
        except Exception as _mini_agent_exc:
            # 读取失败不阻塞 agent 主流程
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog.load')
            self._nodes = {}

    def save(self) -> None:
        """原子写入磁盘。"""
        self._goals_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.VERSION,
            "goals": [n.to_dict() for n in self._nodes.values()],
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(
            dir=str(self._goals_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self._goals_path)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def has_actionable_work(self) -> bool:
        """
        是否存在 status=active 且 level=objective 的节点。
        这是 AutonomousLoop._tick_maintenance() 调用的核心查询。
        passive 档位的 tick() 不调用此方法（边界由 AutonomousLoop 在调用方保证）。
        """
        return any(
            n.is_active and n.is_objective
            for n in self._nodes.values()
        )

    def active_objectives(self) -> list[GoalNode]:
        """返回所有 active objective，按优先级降序。"""
        objs = [
            n for n in self._nodes.values()
            if n.is_active and n.is_objective
        ]
        return sorted(objs, key=lambda n: n.priority, reverse=True)

    def active_goals(self) -> list[GoalNode]:
        """返回所有 active goal，按优先级降序。"""
        goals = [
            n for n in self._nodes.values()
            if n.is_active and n.is_goal
        ]
        return sorted(goals, key=lambda n: n.priority, reverse=True)

    def get(self, node_id: str) -> Optional[GoalNode]:
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[GoalNode]:
        return list(self._nodes.values())

    # ── 写入 ──────────────────────────────────────────────────────────────────

    def add_goal(
        self,
        title: str,
        description: str = "",
        source: str = "user",
        priority: int = 0,
        tags: Optional[list[str]] = None,
    ) -> GoalNode:
        """
        添加 Goal 节点（通常由用户 /agent goals add 触发）。
        source="user" 时对应用户手动添加；
        source="agent_derived" 时由第十二节 autonomous 档位 tick 内部调用。

        内部会先重新加载磁盘最新状态再追加，并立即落盘，
        避免与其他进程并发写入互相覆盖。
        """
        with self._locked():
            node = GoalNode(
                id=f"goal_{uuid.uuid4().hex[:8]}",
                level="goal",
                title=title,
                source=source,
                status="active",
                created_at=time.time(),
                last_touched_at=time.time(),
                priority=priority,
                tags=tags or [],
                description=description,
            )
            self._nodes[node.id] = node
        return node

    def add_objective(
        self,
        title: str,
        parent_id: Optional[str] = None,
        work_thread_ref: Optional[str] = None,
        source: str = "user",
        priority: int = 0,
    ) -> GoalNode:
        """添加 Objective 节点，可关联 WorkThread。

        内部会先重新加载磁盘最新状态再追加，并立即落盘，
        避免与其他进程并发写入互相覆盖。
        """
        with self._locked():
            node = GoalNode(
                id=f"obj_{uuid.uuid4().hex[:8]}",
                level="objective",
                title=title,
                source=source,
                status="active",
                created_at=time.time(),
                last_touched_at=time.time(),
                parent_id=parent_id,
                work_thread_ref=work_thread_ref,
                priority=priority,
            )
            self._nodes[node.id] = node
            # 更新 parent 的 children_ids（用重新加载后的最新 parent 节点）
            if parent_id and parent_id in self._nodes:
                self._nodes[parent_id].children_ids.append(node.id)
        return node

    def update_progress(self, node_id: str, notes: str) -> bool:
        """更新节点进展记录。

        内部会先重新加载磁盘最新状态，在最新数据基础上改这一个字段再落盘，
        避免与其他进程并发写入互相覆盖。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.progress_notes = notes
            node.last_touched_at = time.time()
        return True

    def set_status(self, node_id: str, status: str) -> bool:
        """更新节点状态。

        内部会先重新加载磁盘最新状态，在最新数据基础上改这一个字段再落盘，
        避免与其他进程并发写入互相覆盖。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.status = status
            node.last_touched_at = time.time()
        return True

    def update_fields(self, node_id: str, **fields) -> Optional[GoalNode]:
        """在锁保护下批量更新节点的任意字段（如 status/priority/progress_notes）。

        用于"一次性改好几个字段再存"的场景（例如 accept/PATCH 接口），
        避免每个字段单独调用一次 set_status/update_progress 时中间态被
        其他进程读到，也避免调用方自己直接改 node 属性再手动 save()
        （那样不会重新加载磁盘最新状态，等于绕开了并发保护）。

        内部会先重新加载磁盘最新状态，在最新数据基础上应用这些字段修改再落盘。
        返回更新后的节点；节点不存在时返回 None（不做任何修改）。
        """
        with self._locked():
            node = self._nodes.get(node_id)
            if not node:
                return None
            for key, value in fields.items():
                setattr(node, key, value)
            node.last_touched_at = time.time()
        return self._nodes.get(node_id)

    # ── 从 WorkThread 拆解下一个 Task ─────────────────────────────────────────

    def next_task_description(
        self,
        llm_client=None,
        *,
        workdir_knowledge=None,
    ) -> Optional[tuple[str, str]]:
        """
        从最高优先级 active Objective 拆解出下一个可执行 Task 描述。
        返回 (objective_id, task_description) 或 None。

        拆解逻辑：
        1. 取最高优先级 active Objective
        2. 若有 work_thread_ref，从 WorkThread 的 next_suggested 获取提示
        3. 用轻量 LLM 调用生成具体 Task 描述（若有 llm_client）
           否则直接用 Objective.title 作为 Task 描述
        """
        objectives = self.active_objectives()
        if not objectives:
            return None

        obj = objectives[0]  # 最高优先级

        # 获取 WorkThread 进展提示
        next_suggested = ""
        if obj.work_thread_ref and workdir_knowledge:
            try:
                wt = workdir_knowledge.get_work_thread(obj.work_thread_ref)
                if wt:
                    next_suggested = wt.next_suggested or ""
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog')
                pass

        # 构建 Task 描述
        if next_suggested:
            base_desc = f"{obj.title}\n\n[来自工作线索的提示] {next_suggested}"
        else:
            base_desc = obj.title

        # 有 LLM 时做一次轻量拆解
        if llm_client:
            try:
                task_desc = self._llm_decompose(llm_client, obj, next_suggested)
                if task_desc:
                    return obj.id, task_desc
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog.next_task_description')
                pass  # 降级为直接使用 title

        return obj.id, base_desc

    def _llm_decompose(self, llm_client, obj: GoalNode, next_suggested: str) -> Optional[str]:
        """
        轻量 LLM 调用：将 Objective 拆解为具体可执行的 Task 描述。
        参照 Stage 4.2 timeline.jsonl 反思调用的独立轻量调用模式。
        """
        prompt = f"""将以下目标拆解为一个具体可在单次 Task 中完成、有明确验收标准的任务描述。

目标：{obj.title}
当前进展：{obj.progress_notes or '暂无'}
工作建议：{next_suggested or '暂无'}

要求：
1. 输出一句话的具体任务描述（不超过 100 字）
2. 任务必须可在单次执行中完成
3. 有明确的完成标准

只输出任务描述，不要其他内容。"""

        try:
            msgs = [{"role": "user", "content": prompt}]
            result = llm_client.chat(messages=msgs, max_tokens=200)
            if result:
                text = result.strip()
                if text:
                    return text
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog')
            pass
        return None


# ── 模块级便捷函数 ────────────────────────────────────────────────────────────

def load_goal_backlog(paths: AgentPaths) -> GoalBacklog:
    """加载并返回 GoalBacklog（便捷函数）。"""
    gb = GoalBacklog(paths)
    gb.load()
    return gb


__all__ = [
    "GoalNode",
    "GoalBacklog",
    "load_goal_backlog",
]
