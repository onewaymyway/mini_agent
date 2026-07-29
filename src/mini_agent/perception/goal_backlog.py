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
    # "active" | "paused" | "completed" | "abandoned" | "failed" | "cancelled"
    # [看板与自主性改进方案 Track B] 新增 "failed"/"cancelled" 两个取值：
    # - "failed"：由 ObjectiveExecutor 在其对应 execution 判定失败后单向回写，
    #   代表"事实上执行失败了"，区别于用户主动放弃的 "abandoned"。
    # - "cancelled"：用户在看板上主动终止一个仍在运行的 Objective 时使用
    #   （见 objective_executor.cancel()），区别于"从未开始就放弃"的 "abandoned"。
    # 两个新值不影响任何既有读取方——所有既有代码都是把 status 当不透明字符串
    # 比较/展示，没有做枚举校验，看板侧的展示映射见 apps/mini_agent_kanban/app.py
    # 的 GOAL_STATUS_COLUMNS。
    status: str
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

    # [watchlist_notification_goal_design.md §3.5，P5 新增]
    # GoalRelevanceEngine Stage② 判定 relevant=true 时追加的外部信息摘要，
    # 只保留最近 max_keep 条（见 attach_external_context()）。跟
    # progress_notes（"做到哪一步了"）语义不同，这里纯粹是"外部世界发生的
    # 跟这个 Goal 相关的事"，只在处理这个 Goal 自己的任务时被读取（§4.5），
    # 不做全局注入。每项: {"event_id","title","snippet","occurred_at","source_id"}。
    external_context: list = field(default_factory=list)

    # [watchlist_notification_goal_design.md §3.5，P5 新增]
    # 上一次因外部信号被"主动拉起"(advance_goal) 的时间戳，用于 §4.4 的
    # 冷却限流判断。跟 progress_notes 不是一回事。
    last_external_advance_at: float = 0.0

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
            "external_context": self.external_context,
            "last_external_advance_at": self.last_external_advance_at,
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
            external_context=d.get("external_context", []),
            last_external_advance_at=d.get("last_external_advance_at", 0.0),
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


@dataclass
class AdvanceDecision:
    """`try_advance_goal()` 的返回值（P5，§3.5/§4.4）。

    action 取值：
      - "not_found"     — goal_id 不存在，未做任何修改。
      - "cooldown_skip" — 仍在冷却期内，未执行拉起动作（remaining_seconds
                          给出还剩多少秒）。
      - "reactivated"   — Goal 原本非 active，已被 set_status(active)。
      - "enqueue_turn"  — Goal 本来就是 active，调用方需要自己去
                          enqueue_turn（本方法不直接依赖 InputQueue）。
    """
    action: str
    goal_id: str
    remaining_seconds: float = 0.0


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

    def all_nodes(self) -> list[GoalNode]:
        """返回全部节点（不按 status 过滤），按优先级降序。

        供看板等"需要看到完整 goals.json 内容"的场景使用——
        active_goals()/active_objectives() 是给 AutonomousLoop 用的，
        只关心 active 状态；这里是给外部展示用的全量视图，paused /
        completed / abandoned 节点也要能看到，否则看板会显示不出
        goals.json 里实际存在的数据（这几种状态的节点永远不出现）。
        """
        return sorted(self._nodes.values(), key=lambda n: n.priority, reverse=True)

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

    def goals_missing_objective(self) -> list[GoalNode]:
        """返回没有任何 active Objective 子节点的 active Goal（按优先级降序）。

        只读查询，不加锁——调用方（AutonomousLoop）通常紧接着要做一次可能
        较慢的 LLM 拆解，若在锁内做会让持有跨进程文件锁的时间从"毫秒级"
        变成"LLM 请求耗时"，阻塞同一时间其他进程（别的 CLI session / API
        请求）对 goals.json 的读写。所以这里只负责"读出需要处理什么"，
        真正写入用 add_objectives_for_goal()，两者分开、各自最小化持锁/
        无锁窗口。
        """
        self.load()
        result = [
            n for n in self._nodes.values()
            if n.is_active and n.is_goal and not any(
                (child := self._nodes.get(cid)) is not None and child.is_active and child.is_objective
                for cid in n.children_ids
            )
        ]
        return sorted(result, key=lambda n: n.priority, reverse=True)

    def add_objectives_for_goal(self, goal_id: str, titles: list[str]) -> list[GoalNode]:
        """在锁保护下，为指定 Goal 批量创建 Objective 子节点。

        titles 应该是调用方已经在锁外算好的具体标题（无论是 LLM 拆解结果
        还是降级后的镜像标题）——这个方法本身只做纯粹的数据写入，不做
        任何耗时操作，保证锁的持有时间可控。
        """
        with self._locked():
            goal = self._nodes.get(goal_id)
            if not goal or not goal.is_goal:
                return []
            created: list[GoalNode] = []
            for title in titles:
                node = GoalNode(
                    id=f"obj_{uuid.uuid4().hex[:8]}",
                    level="objective",
                    title=title,
                    source="agent_derived",
                    status="active",
                    created_at=time.time(),
                    last_touched_at=time.time(),
                    parent_id=goal_id,
                    priority=goal.priority,
                )
                self._nodes[node.id] = node
                goal.children_ids.append(node.id)
                created.append(node)
        return created

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

    # ── P5：外部信号驱动 Goal 执行 ────────────────────────────────────────────
    # 设计背景见 next_doc/watchlist_notification_goal_design.md §3.5/§4.4。

    def attach_external_context(self, goal_id: str, item: dict, max_keep: int = 20) -> bool:
        """把一条外部事件摘要 append 进 GoalNode.external_context，只保留
        最近 max_keep 条（超出部分从队首丢弃）。不改变 Goal 的
        status/priority，纯粹是"信息至少要能被看到"这一步（§4.4），
        由 GoalRelevanceEngine Stage② 在 relevant=true 时无条件调用。

        走 `_locked()` 临界区（与 set_status/update_progress 同一把锁），
        避免跟看板手动编辑 Goal 的写入路径产生丢失更新（§9.1 #3）。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node:
                return False
            node.external_context.append(item)
            if len(node.external_context) > max_keep:
                node.external_context = node.external_context[-max_keep:]
            node.last_touched_at = time.time()
        return True

    def try_advance_goal(self, goal_id: str, cooldown_seconds: float) -> "AdvanceDecision":
        """在冷却期检查通过后"拉起"一个 Goal（§4.4）：
        - status != active（如 paused）→ set_status(active) + progress_notes
          追加一笔"因外部信号被自动重新激活"的记录；
        - status == active → 不在这里调用 enqueue_turn（那是
          GoalRelevanceEngine 的职责，需要 InputQueue 依赖，本方法只负责
          纯 GoalBacklog 内部状态判断/变更），只返回 action="enqueue_turn"
          让调用方自己去 enqueue。

        无论是否真的执行了"拉起"，只要不在冷却期内，都会更新
        `last_external_advance_at = now`（见 §4.4：\"执行了拉起动作之后
        更新时间戳\"——`enqueue_turn` 分支视为\"提交动作\"本身已经算一次
        拉起，不等 agent 执行完才算数）。

        冷却期内直接返回 action="cooldown_skip"，不做任何写入。
        """
        with self._locked():
            node = self._nodes.get(goal_id)
            if not node:
                return AdvanceDecision(action="not_found", goal_id=goal_id)

            now = time.time()
            if now - node.last_external_advance_at < cooldown_seconds:
                return AdvanceDecision(
                    action="cooldown_skip",
                    goal_id=goal_id,
                    remaining_seconds=cooldown_seconds - (now - node.last_external_advance_at),
                )

            node.last_external_advance_at = now
            if node.status != "active":
                node.status = "active"
                note = f"[{time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}] 因外部信号被自动重新激活"
                node.progress_notes = (
                    f"{node.progress_notes}\n{note}" if node.progress_notes else note
                )
                node.last_touched_at = now
                return AdvanceDecision(action="reactivated", goal_id=goal_id)

            node.last_touched_at = now
            return AdvanceDecision(action="enqueue_turn", goal_id=goal_id)

    # ── 从 WorkThread 拆解下一个 Task ─────────────────────────────────────────

    def next_task_description(
        self,
        llm_helper=None,
        *,
        workdir_knowledge=None,
    ) -> Optional[tuple[str, str]]:
        """
        从最高优先级 active Objective 拆解出下一个可执行 Task 描述。
        返回 (objective_id, task_description) 或 None。

        拆解逻辑：
        1. 取最高优先级 active Objective
        2. 若有 work_thread_ref，从 WorkThread 的 next_suggested 获取提示
        3. 用轻量 LLM 调用生成具体 Task 描述（若有 llm_helper）
           否则直接用 Objective.title 作为 Task 描述

        llm_helper — 需实现 .ask(prompt, ...) -> str，通常传入
        Agent.llm_helper（见 llm/service.py::LLMHelper），
        天然复用主 agent 当前的 provider/model 与统一重试策略。
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
        if llm_helper:
            try:
                task_desc = self._llm_decompose(llm_helper, obj, next_suggested)
                if task_desc:
                    return obj.id, task_desc
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.GoalBacklog.next_task_description')
                pass  # 降级为直接使用 title

        return obj.id, base_desc

    def _llm_decompose(self, llm_helper, obj: GoalNode, next_suggested: str) -> Optional[str]:
        """
        轻量 LLM 调用：将 Objective 拆解为具体可执行的 Task 描述。
        参照 Stage 4.2 timeline.jsonl 反思调用的独立轻量调用模式。

        历史提示：此函数曾直接接收裸 LLMClient 并调用
        `llm_client.chat(messages=msgs, max_tokens=200)`——签名不匹配，
        实际每次都抛 TypeError 被吞掉，此方法一直静默返回 None。
        改用 LLMHelper.ask() 后签名统一、自带重试。
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
            text = llm_helper.ask(prompt).strip()
            if text:
                return text
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog')
            pass
        return None


# ── 模块级便捷函数 ────────────────────────────────────────────────────────────

def default_goal_to_objectives(
    llm_helper,
    title: str,
    description: str = "",
    max_n: int = 3,
) -> list[str]:
    """LLM 拆解：把一个 Goal 拆成多个可独立执行的 Objective 标题。

    与 GoalBacklog._llm_decompose（Objective → 单个 Task）是同一种"轻量
    LLM 调用做结构化拆解"模式，但拆解方向不同：这里是 Goal → 多个
    Objective，每个 Objective 之后还会再被 ObjectiveExecutor 各自拆成
    3-7 个 Step。

    调用方（AutonomousLoop）负责在拿到返回值之后再决定是否要做 1:1 镜像
    降级——这个函数本身失败/无输出时只返回空列表，不做任何降级决定。

    llm_helper — 需实现 .ask(prompt, ...) -> str（同 LLMHelper.ask）。
    """
    prompt = f"""将以下目标拆解为 1~{max_n} 个可以独立执行、彼此边界清晰的子目标。

目标标题：{title}
目标描述：{description or '（无）'}

要求：
1. 每个子目标一行，不要编号、不要多余符号
2. 子目标要具体到"可以单独作为一项工作去推进"，不要重复目标标题本身
3. 如果目标本身已经足够具体、拆不出多个子目标，只输出 1 行也可以
4. 不要输出子目标数量之外的任何说明文字

只输出子目标标题，每行一个。"""

    try:
        text = llm_helper.ask(prompt)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.goal_backlog.default_goal_to_objectives')
        return []

    if not text:
        return []
    lines = [ln.strip(" -•\t") for ln in text.splitlines()]
    titles = [ln for ln in lines if ln]
    return titles[:max_n]


def load_goal_backlog(paths: AgentPaths) -> GoalBacklog:
    """加载并返回 GoalBacklog（便捷函数）。"""
    gb = GoalBacklog(paths)
    gb.load()
    return gb


__all__ = [
    "GoalNode",
    "GoalBacklog",
    "AdvanceDecision",
    "load_goal_backlog",
    "default_goal_to_objectives",
]
