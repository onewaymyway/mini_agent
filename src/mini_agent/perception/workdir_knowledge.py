"""
perception/workdir_knowledge.py — Workdir 知识层（W2，对应设计文档 8.2 节 /
self_evolution_stage4plus_plan.md Stage 4）

在 `.agent/`（workdir 根）下维护四个结构化文件 + 1 个 Markdown，填补"跨 session
项目知识"的空白：

  - project.json      — 项目身份证（4.1）
  - timeline.jsonl     — session 时序骨架，append-only（4.2）
  - work_index.json    — 跨 session WorkThread 聚合（4.3，本 Stage 价值最高的一项）
  - open_threads.json  — 跨 session 待处理线索池（4.4）
  - knowledge.md       — 项目软知识（4.5，T1，走 StateRepo.apply()，写入不在本
                          模块内，见 tools/workdir_knowledge.py 的
                          update_knowledge 工具；本模块提供检索/读取侧：
                          search_knowledge_index() 按关键词检索摘要，
                          read_knowledge_section() 按标题取出完整正文）

设计取舍：
  - 全部纯写入操作（除 knowledge.md 外）不经过 StateRepo——这四个文件是
    "观察性数据"而非"自我修改的产物"，不需要 git 历史与 tier 校验，
    与 task_manifest.json / plan_snapshot.json（W1）的定位一致
  - 写入使用 tmp + os.replace 原子写，避免并发/中断导致半截 JSON
  - 本模块只负责"数据结构 + 读写"，不负责"什么时候调用"——调用时机
    （SessionEnd hook / 工具主动写 / context 注入）由 agent.py、
    tools/workdir_knowledge.py、context_builder.py 各自负责
  - search_knowledge_index() 复用 perception/memory_store.py 的
    _tokenize()（中英混合 TF-IDF 分词，含中文 n-gram），不重新发明一套
    分词逻辑——knowledge_index.json 条目数量级（几十到几百条）远小于
    long-term memory，用同一套轻量级检索就足够，不需要向量数据库
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.time_utils import ts_to_str


# ── 原子写入辅助（JSON / JSONL 追加）─────────────────────────────────────────

def _atomic_write_text(path: Path, text: str) -> None:
    """原子写入文本文件（tmp + rename），避免读端看到半截内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
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
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: object) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _append_jsonl(path: Path, record: dict) -> None:
    """追加一行 JSONL。append 本身不需要原子性保证（单进程顺序写入场景），
    但确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def _read_json(path: Path, default: object) -> object:
    """读取 JSON 文件；不存在或解析失败时返回 default（不抛异常——这是
    观察性数据，读取失败不应阻塞 agent 主流程）。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge._read_json')
        return default


def _read_jsonl(path: Path, limit: Optional[int] = None) -> list[dict]:
    """读取 JSONL 文件的全部（或最近 limit 条）记录，跳过无法解析的行。"""
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge._read_jsonl')
                continue
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge._read_jsonl')
        return []
    if limit is not None and limit > 0:
        return records[-limit:]
    return records


def capture_environment_fingerprint(project_root: Path) -> dict:
    """
    采集当前运行环境指纹（12.2 节横向加固，挂靠在 project.json 里）：
    `python_version` / `key_deps`（项目自身 pyproject.toml 里声明的依赖的已安装
    版本）/ `os` / `captured_at`。

    "key_deps" 只取项目 pyproject.toml `[project].dependencies` 里列出的包名
    （若存在），用 importlib.metadata 查询已安装版本——不遍历整个环境的全部
    已安装包，那样的"指纹"噪音太大，真正有诊断价值的是"项目显式依赖的关键包
    版本是否变了"。pyproject.toml 不存在或解析失败时 key_deps 为空字典，
    不阻塞整体采集。
    """
    import platform
    import sys

    fingerprint = {
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "key_deps": {},
        "captured_at": time.time(),
    }

    try:
        import importlib.metadata as _ilm
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge.capture_environment_fingerprint')
        _ilm = None

    pyproject = project_root / "pyproject.toml"
    if _ilm is not None and pyproject.is_file():
        try:
            dep_names = _extract_dependency_names(pyproject)
            key_deps = {}
            for name in dep_names:
                try:
                    key_deps[name] = _ilm.version(name)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge.capture_environment_fingerprint')
                    continue
            fingerprint["key_deps"] = key_deps
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge')
            pass

    return fingerprint


def _extract_dependency_names(pyproject_path: Path) -> list[str]:
    """从 pyproject.toml 的 `[project].dependencies` 数组里提取裸包名
    （去掉版本约束符号），不引入 tomllib/tomli 依赖——用简单的逐行正则提取，
    足够覆盖标准 PEP 621 格式；解析失败时返回空列表。"""
    import re

    text = pyproject_path.read_text(encoding="utf-8")
    # 定位 dependencies = [ ... ] 数组块（非贪婪匹配到第一个闭合中括号）
    match = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if not match:
        return []
    block = match.group(1)
    names: list[str] = []
    for line in block.splitlines():
        line = line.strip().strip(",")
        m = re.match(r'^["\']([A-Za-z0-9_.\-]+)', line)
        if m:
            names.append(m.group(1))
    return names


def detect_environment_drift(old_fp: dict, new_fp: dict) -> list[str]:
    """
    比较两份 environment_fingerprint，返回发生变化的字段描述列表（人类可读，
    供调用方决定要不要标记相关 lesson/skill 为 validation_required —— 本 Stage
    只做"检测并报告"，4.1 节范围内不做后续的 lesson/skill 置信度联动，那部分
    属于设计文档 12.2 节"扫描 memory.jsonl / skills/"的消费逻辑，留给有需要时
    再接（Stage 4+ 计划文档把它标注为"中等价值，可独立排期"，不在本 Stage 强行
    捆绑实现）。
    """
    if not old_fp:
        return []
    changes: list[str] = []
    if old_fp.get("python_version") != new_fp.get("python_version"):
        changes.append(
            f"python_version: {old_fp.get('python_version')} -> {new_fp.get('python_version')}"
        )
    if old_fp.get("os") != new_fp.get("os"):
        changes.append(f"os: {old_fp.get('os')} -> {new_fp.get('os')}")
    old_deps = old_fp.get("key_deps", {}) or {}
    new_deps = new_fp.get("key_deps", {}) or {}
    for name in sorted(set(old_deps) | set(new_deps)):
        old_v = old_deps.get(name)
        new_v = new_deps.get(name)
        if old_v != new_v:
            changes.append(f"{name}: {old_v} -> {new_v}")
    return changes


# ════════════════════════════════════════════════════════════════════════════
# 4.1 project.json — 项目身份证
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ProjectMeta:
    """对应设计文档 8.2 节 project.json schema。"""
    name: str = ""
    description: str = ""
    root_language: str = ""
    entry_points: list[str] = field(default_factory=list)
    key_modules: dict = field(default_factory=dict)
    environment_fingerprint: dict = field(default_factory=dict)  # 12.2 节横向加固
    created_at: float = 0.0
    last_active: float = 0.0
    total_sessions: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "root_language": self.root_language,
            "entry_points": list(self.entry_points),
            "key_modules": dict(self.key_modules),
            "environment_fingerprint": dict(self.environment_fingerprint),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "total_sessions": self.total_sessions,
        }

    @staticmethod
    def from_dict(d: dict) -> "ProjectMeta":
        return ProjectMeta(
            name=d.get("name", ""),
            description=d.get("description", ""),
            root_language=d.get("root_language", ""),
            entry_points=list(d.get("entry_points", []) or []),
            key_modules=dict(d.get("key_modules", {}) or {}),
            environment_fingerprint=dict(d.get("environment_fingerprint", {}) or {}),
            created_at=float(d.get("created_at", 0.0) or 0.0),
            last_active=float(d.get("last_active", 0.0) or 0.0),
            total_sessions=int(d.get("total_sessions", 0) or 0),
        )

    def to_prompt_block(self) -> str:
        """注入 system prompt 的精简文本块（context_builder.py 8.4 节 always-on）。"""
        lines = ["## Project identity"]
        if self.name:
            lines.append(f"- Name: {self.name}")
        if self.description:
            lines.append(f"- Description: {self.description}")
        if self.key_modules:
            mods = "; ".join(f"{k}: {v}" for k, v in list(self.key_modules.items())[:8])
            lines.append(f"- Key modules: {mods}")
        if self.total_sessions:
            lines.append(f"- Sessions worked on this project so far: {self.total_sessions}")
        return "\n".join(lines)


def load_project_meta(paths: AgentPaths) -> Optional[ProjectMeta]:
    """读取 project.json；不存在时返回 None（由调用方决定是否调用
    ensure_project_meta 创建初始版本）。"""
    raw = _read_json(paths.workdir_project_meta, None)
    if raw is None or not isinstance(raw, dict):
        return None
    return ProjectMeta.from_dict(raw)


def ensure_project_meta(
    paths: AgentPaths,
    project_root: Path,
    fallback_name: Optional[str] = None,
) -> ProjectMeta:
    """
    session 启动时调用：若 project.json 不存在则创建（4.1）；存在则更新
    last_active / total_sessions 并立即落盘。

    root_language / key_files（entry_points 的数据来源）尽量复用
    ProjectScanner.scan() 的产出，避免重复扫描逻辑；name 取目录名兜底，
    description / key_modules 没有现成数据来源时留空（不强行拼造）。

    【横向加固 12.2】每次调用都重新采集 environment_fingerprint 并与已有的
    比较；变化时记录到返回值无法表达的"漂移详情"——这里只更新 fingerprint
    本身并返回 meta，漂移详情通过 detect_environment_drift() 单独获取
    （调用方如需要"环境变了，去检查哪些 lesson/skill 该重新验证"的下游联动，
    自行在调用处比较新旧 fingerprint，本函数职责只到"采集 + 持久化"）。
    """
    now = time.time()
    new_fp = capture_environment_fingerprint(project_root)
    existing = load_project_meta(paths)
    if existing is not None:
        existing.last_active = now
        existing.total_sessions += 1
        existing.environment_fingerprint = new_fp
        _atomic_write_json(paths.workdir_project_meta, existing.to_dict())
        return existing

    # 首次创建：尽量复用 ProjectScanner 的产出（root_language / entry_points）
    root_language = ""
    entry_points: list[str] = []
    try:
        from mini_agent.perception.project_scanner import ProjectScanner
        snap = ProjectScanner().scan(project_root)
        if snap.languages:
            root_language = snap.languages[0]
        entry_points = list(snap.key_files[:5])
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge')
        pass

    meta = ProjectMeta(
        name=fallback_name or project_root.name,
        description="",
        root_language=root_language,
        entry_points=entry_points,
        key_modules={},
        environment_fingerprint=new_fp,
        created_at=now,
        last_active=now,
        total_sessions=1,
    )
    _atomic_write_json(paths.workdir_project_meta, meta.to_dict())
    return meta


# ════════════════════════════════════════════════════════════════════════════
# 4.2 timeline.jsonl — session 时序骨架
# ════════════════════════════════════════════════════════════════════════════

def append_timeline_entry(
    paths: AgentPaths,
    session_id: str,
    duration_min: float,
    theme: str,
    key_outcomes: list[str],
    task_count: int,
    status: str = "done",
) -> None:
    """SessionEnd hook 调用：追加一行精简的 session 概览（设计文档 8.2 节 schema）。"""
    record = {
        "sid": session_id,
        "at": time.time(),
        "at_str": ts_to_str(time.time()),
        "duration_min": round(duration_min, 1),
        "theme": theme,
        "key_outcomes": list(key_outcomes),
        "task_count": task_count,
        "status": status,
    }
    _append_jsonl(paths.workdir_timeline, record)


def load_recent_timeline(paths: AgentPaths, limit: int = 10) -> list[dict]:
    """读取最近 limit 条 timeline 记录（按写入顺序，最新的在末尾）。"""
    return _read_jsonl(paths.workdir_timeline, limit=limit)


# ════════════════════════════════════════════════════════════════════════════
# 4.3 work_index.json — WorkThread 聚合
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class WorkThread:
    """对应设计文档 8.2 节 WorkThread schema。"""
    id: str
    title: str
    status: str = "active"          # "active" | "done" | "paused"
    started_at: float = field(default_factory=time.time)
    related_sessions: list[str] = field(default_factory=list)
    cumulative_progress: str = ""
    open_questions: list[str] = field(default_factory=list)
    next_suggested: str = ""
    related_goal_id: Optional[str] = None
    # 真正的"最后活跃时间"字段（system-events-bus-guide.md 第8节遗留项）。
    # started_at 在 thread 持续被推进时不会变，不能代表 staleness；
    # last_activity_at 在每次 upsert_work_thread()/关联新 session 时更新。
    # 向后兼容：老数据没有该字段时，from_dict 回退到 started_at。
    last_activity_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "started_at": self.started_at,
            "related_sessions": list(self.related_sessions),
            "cumulative_progress": self.cumulative_progress,
            "open_questions": list(self.open_questions),
            "next_suggested": self.next_suggested,
            "related_goal_id": self.related_goal_id,
            "last_activity_at": self.last_activity_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "WorkThread":
        started_at = float(d.get("started_at", 0.0) or 0.0)
        return WorkThread(
            id=d.get("id", ""),
            title=d.get("title", ""),
            status=d.get("status", "active"),
            started_at=started_at,
            related_sessions=list(d.get("related_sessions", []) or []),
            cumulative_progress=d.get("cumulative_progress", ""),
            open_questions=list(d.get("open_questions", []) or []),
            next_suggested=d.get("next_suggested", ""),
            related_goal_id=d.get("related_goal_id"),
            last_activity_at=float(d.get("last_activity_at", started_at) or started_at),
        )


def load_work_index(paths: AgentPaths) -> list[WorkThread]:
    raw = _read_json(paths.workdir_work_index, {"work_threads": []})
    if not isinstance(raw, dict):
        return []
    items = raw.get("work_threads", []) or []
    return [WorkThread.from_dict(d) for d in items if isinstance(d, dict)]


def save_work_index(paths: AgentPaths, threads: list[WorkThread]) -> None:
    data = {
        "last_updated": time.time(),
        "work_threads": [t.to_dict() for t in threads],
    }
    _atomic_write_json(paths.workdir_work_index, data)


def get_active_work_threads(paths: AgentPaths) -> list[WorkThread]:
    return [t for t in load_work_index(paths) if t.status == "active"]


def find_work_thread(paths: AgentPaths, thread_id: str) -> Optional[WorkThread]:
    for t in load_work_index(paths):
        if t.id == thread_id:
            return t
    return None


def upsert_work_thread(paths: AgentPaths, thread: WorkThread) -> None:
    """新建或覆盖更新一个 WorkThread（按 id 匹配）。

    每次 upsert 都视为一次活跃边沿，刷新 last_activity_at（调用方不需要
    手动维护这个字段——除非显式设置了非默认值，这里统一用当前时间覆盖，
    保证语义始终是"这个 thread 最近一次被真正更新是什么时候"）。
    """
    thread.last_activity_at = time.time()
    threads = load_work_index(paths)
    for i, t in enumerate(threads):
        if t.id == thread.id:
            threads[i] = thread
            save_work_index(paths, threads)
            return
    threads.append(thread)
    save_work_index(paths, threads)


def relate_session_to_work_thread(
    paths: AgentPaths,
    session_id: str,
    session_goal: str,
    relation_days: float = 7.0,
) -> Optional[WorkThread]:
    """
    SessionEnd hook 调用的轻量启发式（4.3 节"最简版本"）：

    若存在一个 status=active 的 WorkThread，其 related_sessions 最近一条
    记录的时间距今小于 relation_days 天，或本次 session 的目标文本与该
    WorkThread 的 title 有明显的字面重叠，则认为本次 session 延续了该
    WorkThread，追加 related_sessions 并返回；否则返回 None（不强行
    创建新 WorkThread——新建是 agent 主动 update_work_thread 或 evolution-
    agent 周期扫描的职责，纯启发式不应该自由发明新工作线）。

    注：这里只做"关联到已有 active WorkThread"，不创建新 WorkThread，
    避免启发式误判导致 work_index.json 里出现大量噪音条目。
    """
    threads = load_work_index(paths)
    active = [t for t in threads if t.status == "active"]
    if not active:
        return None

    now = time.time()
    relation_secs = relation_days * 86400

    # work_index.json 整体的 last_updated 作为"最近是否被推进过"的近似判据
    # （单个 WorkThread 没有独立的 last_touched 字段，用全局 last_updated
    # 近似——本 Stage 选择最简版本，足够支撑"轻量自动关联"这个粒度的判断；
    # 更精确的"按 WorkThread 各自记录最近触达时间"留给 Stage 8 evolution-agent
    # 周期性扫描时按需引入）。
    raw = _read_json(paths.workdir_work_index, {})
    last_updated = float(raw.get("last_updated", 0.0) or 0.0) if isinstance(raw, dict) else 0.0
    recently_touched = last_updated > 0 and (now - last_updated) <= relation_secs

    best: Optional[WorkThread] = None
    for t in active:
        # 语义重叠判据：goal 文本中出现 title 的关键词（中文按字符、英文按词）
        title_hit = bool(t.title) and (
            t.title in session_goal or session_goal in t.title
        )
        if title_hit or recently_touched:
            best = t
            break

    if best is None:
        return None

    if session_id not in best.related_sessions:
        best.related_sessions.append(session_id)
    upsert_work_thread(paths, best)
    return best


# ════════════════════════════════════════════════════════════════════════════
# 4.4 open_threads.json — 跨 session 待处理线索池
# ════════════════════════════════════════════════════════════════════════════

VALID_OPEN_THREAD_TYPES = ("bug", "tech_debt", "feature", "question", "blocker")
VALID_OPEN_THREAD_PRIORITIES = ("low", "medium", "high")


@dataclass
class OpenThread:
    """对应设计文档 8.2 节 open_threads.json item schema。"""
    id: str
    title: str
    discovered_in: str
    type: str = "question"
    priority: str = "medium"
    description: str = ""
    work_thread_ref: Optional[str] = None
    status: str = "open"        # "open" | "resolved"
    resolved_in: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "discovered_in": self.discovered_in,
            "type": self.type,
            "priority": self.priority,
            "description": self.description,
            "work_thread_ref": self.work_thread_ref,
            "status": self.status,
            "resolved_in": self.resolved_in,
        }

    @staticmethod
    def from_dict(d: dict) -> "OpenThread":
        return OpenThread(
            id=d.get("id", ""),
            title=d.get("title", ""),
            discovered_in=d.get("discovered_in", ""),
            type=d.get("type", "question"),
            priority=d.get("priority", "medium"),
            description=d.get("description", ""),
            work_thread_ref=d.get("work_thread_ref"),
            status=d.get("status", "open"),
            resolved_in=d.get("resolved_in"),
        )


def load_open_threads(paths: AgentPaths) -> list[OpenThread]:
    raw = _read_json(paths.workdir_open_threads, {"items": []})
    if not isinstance(raw, dict):
        return []
    items = raw.get("items", []) or []
    return [OpenThread.from_dict(d) for d in items if isinstance(d, dict)]


def save_open_threads(paths: AgentPaths, items: list[OpenThread]) -> None:
    _atomic_write_json(paths.workdir_open_threads, {"items": [t.to_dict() for t in items]})


def _next_open_thread_id(items: list[OpenThread]) -> str:
    n = 1 + sum(1 for t in items if t.id.startswith("ot_"))
    candidate = f"ot_{n:03d}"
    existing_ids = {t.id for t in items}
    while candidate in existing_ids:
        n += 1
        candidate = f"ot_{n:03d}"
    return candidate


def add_open_thread(
    paths: AgentPaths,
    title: str,
    discovered_in: str,
    type: str = "question",
    priority: str = "medium",
    description: str = "",
    work_thread_ref: Optional[str] = None,
) -> OpenThread:
    """add_open_thread 工具的内部实现（供 tools/workdir_knowledge.py 调用）。"""
    items = load_open_threads(paths)
    item = OpenThread(
        id=_next_open_thread_id(items),
        title=title,
        discovered_in=discovered_in,
        type=type if type in VALID_OPEN_THREAD_TYPES else "question",
        priority=priority if priority in VALID_OPEN_THREAD_PRIORITIES else "medium",
        description=description,
        work_thread_ref=work_thread_ref,
    )
    items.append(item)
    save_open_threads(paths, items)
    return item


def import_unresolved_from_manifest(
    paths: AgentPaths,
    session_id: str,
    unresolved: list[str],
) -> list[OpenThread]:
    """
    SessionEnd hook 调用：把 task_manifest.outcome.unresolved 里的条目自动
    推进 open_threads.json（设计文档 8.2 节"SessionEnd hook 自动推进"）。

    W1 已经产出 unresolved 字段，这里只负责"读取并转换格式"——type 统一
    标记为 "tech_debt"（unresolved 条目通常是"还差一点没做完"，不是明确
    分类的 bug/feature；若后续需要更精细分类，应由 agent 主动调用
    add_open_thread 而不是在这个自动化路径上猜测），priority 统一 "medium"
    （不主观放大优先级；高优先级应该是人或 agent 显式判断的结果）。
    """
    if not unresolved:
        return []
    items = load_open_threads(paths)
    created: list[OpenThread] = []
    for desc in unresolved:
        desc = (desc or "").strip()
        if not desc:
            continue
        item = OpenThread(
            id=_next_open_thread_id(items),
            title=desc[:80],
            discovered_in=session_id,
            type="tech_debt",
            priority="medium",
            description=desc,
        )
        items.append(item)
        created.append(item)
    if created:
        save_open_threads(paths, items)
    return created


def get_high_priority_open_threads(paths: AgentPaths, limit: int = 5) -> list[OpenThread]:
    """context 注入用（8.4 节）：priority=high 且 status=open 的条目，限制条数。"""
    items = [
        t for t in load_open_threads(paths)
        if t.priority == "high" and t.status == "open"
    ]
    return items[:limit] if limit > 0 else items


# ════════════════════════════════════════════════════════════════════════════
# 14.1 knowledge_index.json — knowledge.md 的结构化索引（横向加固，与 4.5 同批）
# ════════════════════════════════════════════════════════════════════════════
#
# 设计文档 14.1 节原本设想由"evolution-agent 定期从 Markdown 里解析生成"，
# 但 evolution-agent 周期调度（巩固循环）要等到 Stage 8 才存在。Stage 4+ 计划
# 文档建议提前到 Stage 4 一并完成的做法是：在 update_knowledge() 工具写
# Markdown 的同一次调用里，直接更新索引里对应的条目——不等待一个尚不存在的
# 后台调度器。索引条目的 topic/decision_type/affected_modules 没有强行用
# 启发式从文本猜测（容易产生噪音），而是作为 update_knowledge() 工具的可选
# 参数由调用方（agent）显式提供；省略时这些字段留空，索引仍然可用（按
# heading/summary 检索），只是分类维度不全。

@dataclass
class KnowledgeIndexEntry:
    """对应设计文档 14.1 节 knowledge_index.json entries[] schema。"""
    id: str
    heading: str
    summary: str = ""
    topic: str = ""
    decision_type: str = ""
    affected_modules: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "heading": self.heading,
            "summary": self.summary,
            "topic": self.topic,
            "decision_type": self.decision_type,
            "affected_modules": list(self.affected_modules),
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "KnowledgeIndexEntry":
        return KnowledgeIndexEntry(
            id=d.get("id", ""),
            heading=d.get("heading", ""),
            summary=d.get("summary", ""),
            topic=d.get("topic", ""),
            decision_type=d.get("decision_type", ""),
            affected_modules=list(d.get("affected_modules", []) or []),
            created_at=float(d.get("created_at", 0.0) or 0.0),
        )


def load_knowledge_index(paths: AgentPaths) -> list[KnowledgeIndexEntry]:
    raw = _read_json(paths.workdir_knowledge_index, {"entries": []})
    if not isinstance(raw, dict):
        return []
    items = raw.get("entries", []) or []
    return [KnowledgeIndexEntry.from_dict(d) for d in items if isinstance(d, dict)]


def save_knowledge_index(paths: AgentPaths, entries: list[KnowledgeIndexEntry]) -> None:
    data = {
        "last_indexed": time.time(),
        "entries": [e.to_dict() for e in entries],
    }
    _atomic_write_json(paths.workdir_knowledge_index, data)


def _next_knowledge_index_id(entries: list[KnowledgeIndexEntry]) -> str:
    n = 1 + sum(1 for e in entries if e.id.startswith("kn_"))
    candidate = f"kn_{n:03d}"
    existing_ids = {e.id for e in entries}
    while candidate in existing_ids:
        n += 1
        candidate = f"kn_{n:03d}"
    return candidate


def upsert_knowledge_index_entry(
    paths: AgentPaths,
    heading: str,
    summary: str = "",
    topic: str = "",
    decision_type: str = "",
    affected_modules: Optional[list[str]] = None,
) -> KnowledgeIndexEntry:
    """
    update_knowledge() 工具调用：按 heading 匹配已有索引条目并覆盖更新，
    不存在则新建——与 knowledge.md 本身"按标题替换/追加"的语义保持一致
    （同一个 section 标题始终对应同一个索引条目，不会因为反复更新同一节
    而在索引里堆积重复记录）。
    """
    entries = load_knowledge_index(paths)
    for i, e in enumerate(entries):
        if e.heading == heading:
            updated = KnowledgeIndexEntry(
                id=e.id,
                heading=heading,
                summary=summary or e.summary,
                topic=topic or e.topic,
                decision_type=decision_type or e.decision_type,
                affected_modules=list(affected_modules) if affected_modules is not None else e.affected_modules,
                created_at=e.created_at,
            )
            entries[i] = updated
            save_knowledge_index(paths, entries)
            return updated

    new_entry = KnowledgeIndexEntry(
        id=_next_knowledge_index_id(entries),
        heading=heading,
        summary=summary,
        topic=topic,
        decision_type=decision_type,
        affected_modules=list(affected_modules or []),
        created_at=time.time(),
    )
    entries.append(new_entry)
    save_knowledge_index(paths, entries)
    return new_entry


# ════════════════════════════════════════════════════════════════════════════
# knowledge.md 检索侧（补全设计文档 8.4 节"按本次 session 意图检索后注入"）
# ════════════════════════════════════════════════════════════════════════════
#
# 现状（改动前）：update_knowledge() 工具只负责写入 knowledge.md + 维护
# knowledge_index.json，但索引建好之后从未被读出来过——context_builder.py
# 的 always-on 注入只覆盖 project.json / WorkThread / open_threads 三类，
# 完全不涉及 knowledge.md；agent 唯一能看到 knowledge.md 内容的方式是自己
# 用文件读取工具去翻整份 Markdown，没有任何按相关性筛选的手段。
#
# 这里补的是检索侧：
#   search_knowledge_index() — 对 knowledge_index.json 的 heading/summary/
#       topic/decision_type/affected_modules 做 TF-IDF 关键词检索，返回
#       按相关度排序的索引条目（只是摘要，不含 Markdown 正文）。
#   read_knowledge_section() — 给定一个 heading，从 knowledge.md 里把那一节
#       的完整正文抠出来（与 tools/workdir_knowledge.py 里写入侧的
#       _upsert_markdown_section() 共享同一套"## 标题"边界识别逻辑，
#       但那个函数是私有的、和写入逻辑耦合在一起，这里独立实现一个只读版本，
#       避免 perception 层反向 import tools 层）。
# 二者配合，对应 tools/workdir_knowledge.py 里新增的 search_knowledge 工具：
# 先用 search_knowledge_index() 找到候选 heading，再按需用
# read_knowledge_section() 取出对应正文（节省 token——大多数检索场景下，
# 摘要已经够用，不需要把整节正文都塞进结果）。

def _knowledge_entry_search_text(entry: KnowledgeIndexEntry) -> str:
    """把一条索引条目的可检索字段拼成一段文本，供 TF-IDF 分词打分。"""
    parts = [entry.heading, entry.summary, entry.topic, entry.decision_type]
    parts.extend(entry.affected_modules)
    return " ".join(p for p in parts if p)


def search_knowledge_index(
    paths: AgentPaths,
    query: str,
    k: int = 5,
    topic: Optional[str] = None,
) -> list[tuple[KnowledgeIndexEntry, float]]:
    """
    对 knowledge_index.json 做关键词检索，返回 (entry, score) 按 score 降序
    排列的 top-k 列表（score<=0 的条目被过滤掉，与 memory_store.search() 的
    "不返回毫不相关结果"原则一致）。

    评分复用 perception/memory_store.py 的 _tokenize()（中英混合分词 +
    中文 n-gram），用标准 TF-IDF（不含 memory 那边的时间衰减——knowledge.md
    是"沉淀下来的认知"，不应该因为写入时间久就被判定为不相关；新旧本身不是
    knowledge 的相关性信号，这点区别于 memory 的"最近发生的事更重要"）。

    query 为空或全是停用词时返回 []（而不是退化成"返回全部条目"——
    调用方应该用 load_knowledge_index() 取全量列表，search 只负责"有
    query 时排序"，语义保持单一）。

    topic 非空时先按精确匹配过滤候选范围，再在过滤后的子集里跑 TF-IDF——
    例如 search_knowledge(query="鉴权失败排查", topic="auth") 只在
    topic="auth" 的条目里找最相关的，避免跨主题的同名词干扰排序
    （N 越小，IDF 越准确地反映"在这个主题范围内多稀有"，而不是被全量
    knowledge base 的整体词频分布带偏）。
    """
    from mini_agent.perception.memory_store import _tokenize

    entries = load_knowledge_index(paths)
    if topic:
        entries = [e for e in entries if e.topic == topic]
    if not entries:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    doc_texts = [_tokenize(_knowledge_entry_search_text(e)) for e in entries]
    N = len(entries)

    scored: list[tuple[KnowledgeIndexEntry, float]] = []
    for entry, tokens in zip(entries, doc_texts):
        if not tokens:
            scored.append((entry, 0.0))
            continue
        score = 0.0
        for qt in query_tokens:
            tf = tokens.count(qt) / len(tokens)
            df = sum(1 for t in doc_texts if qt in t)
            idf = math.log((N + 1) / (df + 1)) + 1
            score += tf * idf
        scored.append((entry, score))

    ranked = sorted(scored, key=lambda x: -x[1])
    return [(e, s) for e, s in ranked[:k] if s > 0]


def read_knowledge_section(paths: AgentPaths, heading: str) -> Optional[str]:
    """
    从 knowledge.md 里按 "## <heading>" 标题取出该节的完整正文（不含标题行
    本身），找不到对应标题或文件不存在时返回 None。

    边界识别逻辑（标题匹配到下一个同级或更高级标题为止）与写入侧
    tools/workdir_knowledge.py 的 _upsert_markdown_section() 保持一致，
    但这里是独立实现的只读版本——perception 层不应该反向 import tools
    层（tools 依赖 perception 是允许的方向，反过来会形成循环依赖）。
    """
    path = paths.workdir_knowledge_md
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.workdir_knowledge.read_knowledge_section')
        return None

    target = f"## {heading}".strip()
    lines = text.splitlines()

    start_idx: Optional[int] = None
    end_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip() == target:
            start_idx = i + 1
            continue
        if start_idx is not None and end_idx is None:
            stripped = line.strip()
            if stripped.startswith("# ") or stripped.startswith("## "):
                end_idx = i
                break
    if start_idx is None:
        return None
    if end_idx is None:
        end_idx = len(lines)

    return "\n".join(lines[start_idx:end_idx]).strip()


__all__ = [
    "ProjectMeta",
    "load_project_meta",
    "ensure_project_meta",
    "capture_environment_fingerprint",
    "detect_environment_drift",
    "append_timeline_entry",
    "load_recent_timeline",
    "WorkThread",
    "load_work_index",
    "save_work_index",
    "get_active_work_threads",
    "find_work_thread",
    "upsert_work_thread",
    "relate_session_to_work_thread",
    "OpenThread",
    "VALID_OPEN_THREAD_TYPES",
    "VALID_OPEN_THREAD_PRIORITIES",
    "load_open_threads",
    "save_open_threads",
    "add_open_thread",
    "import_unresolved_from_manifest",
    "get_high_priority_open_threads",
    "KnowledgeIndexEntry",
    "load_knowledge_index",
    "save_knowledge_index",
    "upsert_knowledge_index_entry",
    "search_knowledge_index",
    "read_knowledge_section",
]
