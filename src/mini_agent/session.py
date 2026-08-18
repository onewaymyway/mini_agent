"""
session.py — 会话 Session 持久化管理

每个 Session 保存为独立目录，目录名即 session_id。

目录结构：
  <project_root>/.agent/sessions/<session_id>/
    history.json   — 完整对话历史（messages 数组）
    meta.json      — 元信息（id, title, provider, model, stats, summary）

默认目录：<project_root>/.agent/sessions/
可配置：  SessionConfig.dir 或 --session-dir CLI 参数

向后兼容：
  仍可读取旧格式（单个 .json / .jsonl 文件）。
  新 session 一律写目录格式。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime

from mini_agent.time_utils import now_str
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class SessionMeta:
    """Session 元数据（不含完整历史，用于列表展示）。"""
    id: str
    title: str
    created_at: str
    updated_at: str
    provider: str
    model: str
    turns: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    file_path: str   # 保留字段（兼容旧代码）：目录格式下指向 meta.json
    fmt: str         # "dir" | "json" | "jsonl"
    summary: str = ""
    knowledge_extracted: bool = False
    knowledge_extracted_at: str = ""
    pinned: bool = False

    @property
    def age_str(self) -> str:
        try:
            dt = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
            # _now_iso() 生成的是不带时区信息的本地时间字符串（如 "2026-06-14T02:51:32"），
            # 与 timezone-aware 的 now 相减会抛 TypeError，因此按 dt 是否带 tzinfo 选择基准
            now = datetime.now().astimezone() if dt.tzinfo else datetime.now()
            diff = now - dt
            s = int(diff.total_seconds())
            if s < 0:     return "刚刚"
            if s < 60:    return f"{s}秒前"
            if s < 3600:  return f"{s//60}分钟前"
            if s < 86400: return f"{s//3600}小时前"
            return f"{s//86400}天前"
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.session.SessionMeta.age_str')
            return self.updated_at[:16]


@dataclass
class Session:
    """完整 Session（含历史）。"""
    id: str
    title: str
    created_at: str
    updated_at: str
    provider: str
    model: str
    stats: dict           # turns / input_tokens / output_tokens / tool_calls
    history: list[dict]   # 完整对话历史
    fmt: str = "dir"      # "dir" | "json" | "jsonl"
    file_path: str = ""   # 目录格式下指向 meta.json；旧格式为单文件路径
    summary: str = ""
    summary_at_turns: int = 0  # 上次生成摘要/记忆时的 stats.turns，用于判断是否需要重新生成
    active_persona: Optional[str] = None  # 角色扮演系统：当前激活的 persona name，None=未激活
    # [session 清理功能] 知识抽取状态：save_session() 每次保存时，用
    # history_manager.HistoryManager.is_extraction_caught_up() 的结果刷新，
    # 表示"截至这次保存，抽取游标是否已经追上了当前 session 的 raw_history
    # 末尾"。evolution/session_cleanup.py 清理旧 session 前用它判断是否需要
    # 先补一次离线抽取，避免误删还没提炼出知识的内容。
    # 已知局限：抽取游标是进程内单调计数器，new_session()/load_session()
    # 切换到一个新/其它 session 后，游标不会归零，因此刚创建的小 session
    # 可能被"乐观"地标记为 True——这不会导致误删（因为这类会话轮次很少，
    # 本身也会被 min_turns_for_extraction 判定为"无需抽取"），但不代表这是
    # 一个精确的跨进程/跨 session 的抽取完整性证明。
    knowledge_extracted: bool = False
    knowledge_extracted_at: str = ""     # 打标时间（ISO 字符串），空表示从未打过
    pinned: bool = False                 # 用户手动置顶保护，session cleanup 永不删除

    @property
    def meta(self) -> SessionMeta:
        return SessionMeta(
            id=self.id,
            title=self.title,
            created_at=self.created_at,
            updated_at=self.updated_at,
            provider=self.provider,
            model=self.model,
            turns=self.stats.get("turns", 0),
            input_tokens=self.stats.get("input_tokens", 0),
            output_tokens=self.stats.get("output_tokens", 0),
            tool_calls=self.stats.get("tool_calls", 0),
            file_path=self.file_path,
            fmt=self.fmt,
            summary=self.summary,
            knowledge_extracted=self.knowledge_extracted,
            knowledge_extracted_at=self.knowledge_extracted_at,
            pinned=self.pinned,
        )

    def to_meta_dict(self) -> dict:
        """meta.json 的内容（不含 history）。"""
        d = {
            "id":          self.id,
            "title":       self.title,
            "created_at":  self.created_at,
            "updated_at":  self.updated_at,
            "provider":    self.provider,
            "model":       self.model,
            "stats":       self.stats,
        }
        if self.summary:
            d["summary"] = self.summary
        if self.summary_at_turns:
            d["summary_at_turns"] = self.summary_at_turns
        if self.active_persona:
            d["active_persona"] = self.active_persona
        if self.knowledge_extracted:
            d["knowledge_extracted"] = self.knowledge_extracted
        if self.knowledge_extracted_at:
            d["knowledge_extracted_at"] = self.knowledge_extracted_at
        if self.pinned:
            d["pinned"] = self.pinned
        return d

    def to_dict(self) -> dict:
        """完整字典（含 history），用于旧格式兼容写入。"""
        d = self.to_meta_dict()
        d["history"] = self.history
        return d


# ── session 列表缓存（跨实例，按 session_dir 路径 keyed）────────────────────
#
# 背景：list_sessions_page() 每次都要 iterdir() + 逐个 meta.json 读取解析，
# session 越多越慢；daemon 常驻运行、看板又高频轮询 /v1/sessions，
# 长期下来这个函数本身的耗时会随 session 数量线性增长，哪怕丢进线程池
# 也只是不阻塞事件循环，函数本身还是慢、还是占线程池资源。
#
# 这里加一层进程内缓存：
#   - key 是 session_dir 的绝对路径字符串（多用户模式下每个用户的
#     SessionManager 实例都是新建的，不能用实例属性缓存，必须是模块级/
#     跨实例的）
#   - value 是 (cached_at, all_metas 按 mtime 倒序, total)
#   - TTL 内直接复用缓存，不重新扫盘；TTL 外重新构建
#   - 任何写操作（save/delete/set_pinned/mark_*）主动使该 session_dir
#     对应的缓存失效，保证"自己刚做的修改自己能立刻看到"，不必等 TTL 过期
#
# TTL 设置得比看板轮询间隔（通常几秒一次）稍长，既能大幅减少重复扫盘，
# 又不会让别的进程/CLI 对同一目录的修改被缓存太久看不到。
_METAS_CACHE_TTL = 5.0  # 秒
_metas_cache: dict[str, tuple[float, "list[SessionMeta]", int]] = {}
_metas_cache_lock = threading.Lock()


def _invalidate_metas_cache(session_dir: Path) -> None:
    """使某个 session 目录对应的列表缓存失效（写操作后调用）。"""
    key = str(Path(session_dir).resolve())
    with _metas_cache_lock:
        _metas_cache.pop(key, None)


# ── SessionManager ────────────────────────────────────────────────────────────

class SessionManager:
    """
    管理 Session 的读写、列举和查找。

    新格式：每个 session 是一个目录（session_id 为目录名）：
        <sessions_dir>/<session_id>/
            history.json
            meta.json

    旧格式（只读兼容）：
        <sessions_dir>/<session_id>_<timestamp>.json(l)

    使用方式：
        mgr = SessionManager(session_dir=Path(".agent/sessions"))
        session = mgr.new_session(provider="anthropic", model="claude-opus-4-5")
        mgr.save(session, history=[...], stats={...})
        metas = mgr.list_sessions()
        session = mgr.load("abc12345")
    """

    def __init__(
        self,
        session_dir: Optional[Path] = None,
        fmt: str = "dir",
        project_root: Optional[Path] = None,
    ) -> None:
        # 优先用传入的 session_dir；否则从 project_root 推导；最后 fallback 到 cwd
        if session_dir is not None:
            self.session_dir = Path(session_dir).expanduser()
        else:
            _root = project_root or Path.cwd()
            self.session_dir = AgentPaths(_root).sessions_dir

        self.fmt = fmt
        self.session_dir.mkdir(parents=True, exist_ok=True)

    # ── 新建 ──────────────────────────────────────────────────────────────────

    def new_session(self, provider: str, model: str) -> Session:
        """创建一个空的新 Session（尚未写入磁盘）。"""
        sid = uuid.uuid4().hex[:8]
        now = _now_iso()
        return Session(
            id=sid,
            title="New session",
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            stats={"turns": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
            history=[],
            fmt="dir",
            file_path="",
        )

    # ── 保存 ──────────────────────────────────────────────────────────────────

    def save(
        self,
        session: Session,
        history: list[dict],
        stats: dict,
        raw_history=None,
    ) -> Path:
        """
        将当前对话历史和统计写入 Session 目录。
        首次保存时创建目录，后续更新同一目录下的文件。

        Args:
            raw_history: 可选的 RawHistory 实例，若传入则同步保存 raw_history.json

        Returns:
            meta.json 的路径
        """
        session.updated_at = _now_iso()
        session.history = _serialize_history(history)
        session.stats = dict(stats)

        # 从历史中提取标题（首条真实用户消息）
        if session.title in ("New session", "") and history:
            for msg in history:
                if msg.get("role") == "user":
                    # 优先用 _type=user_input 的消息；向后兼容时检查非前缀
                    _t = msg.get("_type")
                    content = msg.get("content", "")
                    is_real = (
                        (_t == "user_input")
                        or (_t is None and isinstance(content, str)
                            and not content.startswith("<tool_result")
                            and not content.startswith("[Previous")
                            and not content.startswith("[Compressed"))
                    )
                    if is_real and isinstance(content, str) and content.strip():
                        title = content.strip().replace("\n", " ")
                        session.title = title[:40] + ("…" if len(title) > 40 else "")
                        break

        session_dir = self.session_dir / session.id
        session_dir.mkdir(parents=True, exist_ok=True)

        meta_path    = session_dir / "meta.json"
        history_path = session_dir / "history.json"

        # 原子写入 meta.json
        _atomic_write_json(meta_path, session.to_meta_dict())

        # 原子写入 history.json
        _atomic_write_json(history_path, session.history)

        # raw_history：新格式已通过 set_path() 实时写入 raw_history.jsonl，
        # 此处无需再写一次；save_to_file 仅作为旧格式兼容路径的后备。
        if raw_history is not None and raw_history._file is None:
            # 未绑定实时写入路径时（如测试环境），才走批量写入
            raw_path = session_dir / "raw_history.jsonl"
            raw_history.save_to_file(raw_path)

        session.file_path = str(meta_path)
        _invalidate_metas_cache(self.session_dir)
        return meta_path

    # ── 加载 ──────────────────────────────────────────────────────────────────

    def load(self, session_id: str) -> Optional[Session]:
        """
        按 session_id（或其前缀）加载 Session。
        先尝试新格式（目录），再尝试旧格式（单文件）。
        """
        # 新格式：目录
        session_dir = self.session_dir / session_id
        if session_dir.is_dir():
            return self._read_dir(session_dir)

        # 前缀匹配（新格式目录）
        candidates_dir = [
            d for d in self.session_dir.iterdir()
            if d.is_dir() and d.name.startswith(session_id)
        ]
        if candidates_dir:
            latest = max(candidates_dir, key=lambda d: d.stat().st_mtime)
            return self._read_dir(latest)

        # 旧格式：单文件前缀匹配
        candidates_file = self._find_legacy_files(session_id)
        if candidates_file:
            path = max(candidates_file, key=lambda p: p.stat().st_mtime)
            return self._read_legacy_file(path)

        return None

    def load_file(self, path: Path) -> Optional[Session]:
        """直接按路径加载（兼容旧调用方式）。"""
        if path.is_dir():
            return self._read_dir(path)
        return self._read_legacy_file(path)

    # ── 列举 ──────────────────────────────────────────────────────────────────

    def _list_session_entries(self) -> list[tuple[float, Path, str]]:
        """收集所有 session 条目（mtime, path, fmt），按最后修改时间倒序。
        从 list_sessions() 里抽出来，供 list_sessions() 和
        list_sessions_page() 共用，避免重复扫描目录的逻辑漂移。"""
        entries: list[tuple[float, Path, str]] = []  # (mtime, path, fmt)

        # 新格式：目录
        for d in self.session_dir.iterdir():
            if d.is_dir() and (d / "meta.json").exists():
                entries.append((d.stat().st_mtime, d, "dir"))

        # 旧格式：文件（向后兼容）
        for p in self.session_dir.glob("*.json"):
            entries.append((p.stat().st_mtime, p, "file"))
        for p in self.session_dir.glob("*.jsonl"):
            entries.append((p.stat().st_mtime, p, "file"))

        entries.sort(key=lambda x: -x[0])
        return entries

    def _read_metas(self, entries: list[tuple[float, Path, str]]) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for _, path, fmt in entries:
            try:
                meta = self._read_meta(path, fmt)
                if meta:
                    metas.append(meta)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.session')
                pass
        return metas

    def _all_metas_cached(self) -> list[SessionMeta]:
        """返回全部 session 的元数据（按最后修改时间倒序），带进程内缓存。

        TTL 内命中缓存直接返回（浅拷贝，调用方可安全 slice/排序而不污染
        缓存本体）；未命中或过期则重新全量扫描 + 解析，并写回缓存。
        """
        key = str(self.session_dir.resolve())
        now = time.monotonic()

        with _metas_cache_lock:
            cached = _metas_cache.get(key)
        if cached is not None:
            cached_at, metas, _total = cached
            if now - cached_at < _METAS_CACHE_TTL:
                return list(metas)

        # 缓存未命中/过期：重新扫描磁盘（这一步仍然是全量扫描 + 逐个读
        # meta.json，开销没有消失，只是被 TTL 摊薄；调用方（HTTP 路由层）
        # 应该把这条路径整体丢进线程池，避免阻塞事件循环）。
        all_entries = self._list_session_entries()
        metas = self._read_metas(all_entries)

        with _metas_cache_lock:
            _metas_cache[key] = (now, metas, len(metas))
        return list(metas)

    def list_sessions(self, limit: int = 50) -> list[SessionMeta]:
        """
        列出所有 Session 的元数据（按最后修改时间倒序）。
        新格式（目录）和旧格式（文件）同时支持。
        """
        return self._all_metas_cached()[:limit]

    def list_sessions_page(self, limit: int = 50, offset: int = 0) -> tuple[list[SessionMeta], int]:
        """[看板分页改进] 支持 offset 的分页版本，额外返回分页前的总数，
        供前端计算总页数。不改动 list_sessions() 本身，避免影响 CLI/daemon
        等其它调用方。

        [性能改进] 底层走 _all_metas_cached()：TTL 内命中缓存则不重新
        扫盘/解析，避免看板高频轮询时随 session 数量增长而越来越慢。
        """
        all_metas = self._all_metas_cached()
        total = len(all_metas)
        return all_metas[offset:offset + limit], total

    def search(self, query: str, limit: int = 20) -> list[SessionMeta]:
        """在所有 session 的 title + summary 中做关键词搜索。"""
        q = query.lower().split()
        results: list[tuple[int, SessionMeta]] = []
        for meta in self.list_sessions(limit=200):
            score = 0
            text = (meta.title + " " + meta.summary).lower()
            for word in q:
                if word in text:
                    score += 2 if word in meta.title.lower() else 1
            if score:
                results.append((score, meta))
        results.sort(key=lambda x: -x[0])
        return [m for _, m in results[:limit]]

    def set_pinned(self, session_id: str, pinned: bool) -> bool:
        """置顶/取消置顶一个 session（只改 meta.json 的 pinned 字段，不动 history）。

        置顶的 session 在 `/session cleanup` 中永远不会被当作候选删除，
        用于用户手动保护某次重要对话。
        """
        session = self.load(session_id)
        if session is None:
            return False
        session.pinned = pinned
        meta_path = self.session_dir / session.id / "meta.json"
        if not meta_path.parent.is_dir():
            return False
        _atomic_write_json(meta_path, session.to_meta_dict())
        _invalidate_metas_cache(self.session_dir)
        return True

    def mark_knowledge_extracted(self, session_id: str, extracted: bool = True) -> bool:
        """标记某 session 的知识已（离线）抽取完成，只改 meta.json，不动 history。

        由 evolution/session_cleanup.py 在完成一次 --extract-first 离线抽取后调用。
        """
        session = self.load(session_id)
        if session is None:
            return False
        session.knowledge_extracted = extracted
        session.knowledge_extracted_at = _now_iso() if extracted else ""
        meta_path = self.session_dir / session.id / "meta.json"
        if not meta_path.parent.is_dir():
            return False
        _atomic_write_json(meta_path, session.to_meta_dict())
        _invalidate_metas_cache(self.session_dir)
        return True

    def mark_summary_backfilled(self, session_id: str, summary: str, summary_at_turns: int) -> bool:
        """[next_doc/memory_backfill_and_profile_update_plan.md 方向一]
        离线回填摘要成功后，把 summary 写回 meta.json（只改 meta，不动
        history）——对齐 `mark_knowledge_extracted()` / `set_pinned()` 的
        "只写 meta"风格，比走完整的 `save()`（会连带重写 history.json）
        更轻量，也避免触碰 `save()` 里"首条真实用户消息推导标题"等与
        本场景无关的逻辑。

        由 evolution/memory_backfill.py 在离线补生成摘要 + 写入长期记忆
        成功后调用。
        """
        session = self.load(session_id)
        if session is None:
            return False
        session.summary = summary
        session.summary_at_turns = summary_at_turns
        meta_path = self.session_dir / session.id / "meta.json"
        if not meta_path.parent.is_dir():
            return False
        _atomic_write_json(meta_path, session.to_meta_dict())
        _invalidate_metas_cache(self.session_dir)
        return True

    def delete(self, session_id: str) -> bool:
        """删除 Session，返回是否成功。"""
        import shutil

        # 新格式：目录
        session_dir = self.session_dir / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir, ignore_errors=True)
            _invalidate_metas_cache(self.session_dir)
            return True

        # 前缀匹配（新格式）
        candidates_dir = [
            d for d in self.session_dir.iterdir()
            if d.is_dir() and d.name.startswith(session_id)
        ]
        if candidates_dir:
            for d in candidates_dir:
                shutil.rmtree(d, ignore_errors=True)
            _invalidate_metas_cache(self.session_dir)
            return True

        # 旧格式：文件
        candidates_file = self._find_legacy_files(session_id)
        if candidates_file:
            for p in candidates_file:
                p.unlink(missing_ok=True)
            _invalidate_metas_cache(self.session_dir)
            return True

        return False

    # ── 内部：新格式读取 ──────────────────────────────────────────────────────

    @staticmethod
    def _read_dir(session_dir: Path) -> Optional[Session]:
        """读取新格式目录（meta.json + history.json）。"""
        try:
            meta_path    = session_dir / "meta.json"
            history_path = session_dir / "history.json"

            if not meta_path.exists():
                return None

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            history = []
            if history_path.exists():
                raw = json.loads(history_path.read_text(encoding="utf-8"))
                history = raw if isinstance(raw, list) else []

            return Session(
                id=meta.get("id", session_dir.name),
                title=meta.get("title", ""),
                created_at=meta.get("created_at", ""),
                updated_at=meta.get("updated_at", ""),
                provider=meta.get("provider", ""),
                model=meta.get("model", ""),
                stats=meta.get("stats", {}),
                history=history,
                fmt="dir",
                file_path=str(meta_path),
                summary=meta.get("summary", ""),
                summary_at_turns=meta.get("summary_at_turns", 0),
                active_persona=meta.get("active_persona"),
                knowledge_extracted=meta.get("knowledge_extracted", False),
                knowledge_extracted_at=meta.get("knowledge_extracted_at", ""),
                pinned=meta.get("pinned", False),
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.session.SessionManager._read_dir')
            return None

    @staticmethod
    def _read_meta(path: Path, fmt: str) -> Optional[SessionMeta]:
        """快速读取元数据（不加载完整历史）。"""
        try:
            if fmt == "dir":
                meta_path = path / "meta.json"
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                stats = data.get("stats", {})
                return SessionMeta(
                    id=data.get("id", path.name),
                    title=data.get("title", "(untitled)"),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    provider=data.get("provider", ""),
                    model=data.get("model", ""),
                    turns=stats.get("turns", 0),
                    input_tokens=stats.get("input_tokens", 0),
                    output_tokens=stats.get("output_tokens", 0),
                    tool_calls=stats.get("tool_calls", 0),
                    file_path=str(path / "meta.json"),
                    fmt="dir",
                    summary=data.get("summary", ""),
                    knowledge_extracted=data.get("knowledge_extracted", False),
                    knowledge_extracted_at=data.get("knowledge_extracted_at", ""),
                    pinned=data.get("pinned", False),
                )
            else:
                # 旧格式文件
                if path.suffix == ".jsonl":
                    with path.open(encoding="utf-8") as f:
                        data = json.loads(f.readline())
                else:
                    data = json.loads(path.read_text(encoding="utf-8"))
                stats = data.get("stats", {})
                return SessionMeta(
                    id=data.get("id", path.stem[:8]),
                    title=data.get("title", "(untitled)"),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    provider=data.get("provider", ""),
                    model=data.get("model", ""),
                    turns=stats.get("turns", 0),
                    input_tokens=stats.get("input_tokens", 0),
                    output_tokens=stats.get("output_tokens", 0),
                    tool_calls=stats.get("tool_calls", 0),
                    file_path=str(path),
                    fmt=path.suffix.lstrip("."),
                    summary=data.get("summary", ""),
                )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.session.SessionManager._read_meta')
            return None

    # ── 内部：旧格式兼容读取 ──────────────────────────────────────────────────

    @staticmethod
    def _read_legacy_file(path: Path) -> Optional[Session]:
        try:
            if path.suffix == ".jsonl":
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                if not lines:
                    return None
                meta = json.loads(lines[0])
                history = [json.loads(l) for l in lines[1:] if l.strip()]
                fmt = "jsonl"
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data
                history = data.get("history", [])
                fmt = "json"

            return Session(
                id=meta.get("id", ""),
                title=meta.get("title", ""),
                created_at=meta.get("created_at", ""),
                updated_at=meta.get("updated_at", ""),
                provider=meta.get("provider", ""),
                model=meta.get("model", ""),
                stats=meta.get("stats", {}),
                history=history,
                fmt=fmt,
                file_path=str(path),
                summary=meta.get("summary", ""),
                summary_at_turns=meta.get("summary_at_turns", 0),
                active_persona=meta.get("active_persona"),
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.session.SessionManager._read_legacy_file')
            return None

    def _find_legacy_files(self, session_id: str) -> list[Path]:
        """旧格式：按 id 前缀查找 .json/.jsonl 文件。"""
        results: list[Path] = []
        for pattern in ("*.json", "*.jsonl"):
            for f in self.session_dir.glob(pattern):
                if f.name.startswith(session_id):
                    results.append(f)
        return results


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

from mini_agent.utils.atomic_write import atomic_write_json

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

# 保留原有函数名作为别名，避免破坏现有调用
_atomic_write_json = lambda path, data: atomic_write_json(path, data, flock=True)

def _serialize_history(history: list[dict]) -> list[dict]:
    """将 history 序列化为可 JSON 化的格式，处理 SDK content block 对象。
    保留 _type 字段（如有），确保存储后 _type 信息不丢失。
    """
    result = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        _type = msg.get("_type")  # 保留 _type
        if isinstance(content, list):
            serialized_content = []
            for block in content:
                if isinstance(block, dict):
                    serialized_content.append(block)
                else:
                    try:
                        d = {"type": block.type}
                        if hasattr(block, "text"):    d["text"]  = block.text
                        if hasattr(block, "id"):      d["id"]    = block.id
                        if hasattr(block, "name"):    d["name"]  = block.name
                        if hasattr(block, "input"):   d["input"] = block.input
                        serialized_content.append(d)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.session._serialize_history')
                        serialized_content.append({"type": "unknown", "raw": str(block)})
            entry = {"role": role, "content": serialized_content}
        else:
            entry = {"role": role, "content": content}
        if _type is not None:
            entry["_type"] = str(_type)  # HType.__str__ 返回 value（如 "user_input"）
        result.append(entry)
    return result
