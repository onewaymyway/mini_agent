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
import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

    @property
    def age_str(self) -> str:
        try:
            dt = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
            # _now_iso() 生成的是不带时区信息的 UTC 时间字符串（如 "2026-06-14T02:51:32"），
            # 与 timezone-aware 的 now 相减会抛 TypeError，因此按 dt 是否带 tzinfo 选择基准
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now(timezone.utc).replace(tzinfo=None)
            diff = now - dt
            s = int(diff.total_seconds())
            if s < 0:     return "刚刚"
            if s < 60:    return f"{s}秒前"
            if s < 3600:  return f"{s//60}分钟前"
            if s < 86400: return f"{s//3600}小时前"
            return f"{s//86400}天前"
        except Exception:
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
        return d

    def to_dict(self) -> dict:
        """完整字典（含 history），用于旧格式兼容写入。"""
        d = self.to_meta_dict()
        d["history"] = self.history
        return d


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
    ) -> Path:
        """
        将当前对话历史和统计写入 Session 目录。
        首次保存时创建目录，后续更新同一目录下的两个文件。

        Returns:
            meta.json 的路径
        """
        session.updated_at = _now_iso()
        session.history = _serialize_history(history)
        session.stats = dict(stats)

        # 从历史中提取标题（首条用户消息）
        if session.title in ("New session", "") and history:
            for msg in history:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
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

        session.file_path = str(meta_path)
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

    def list_sessions(self, limit: int = 50) -> list[SessionMeta]:
        """
        列出所有 Session 的元数据（按最后修改时间倒序）。
        新格式（目录）和旧格式（文件）同时支持。
        """
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
        entries = entries[:limit]

        metas: list[SessionMeta] = []
        for _, path, fmt in entries:
            try:
                meta = self._read_meta(path, fmt)
                if meta:
                    metas.append(meta)
            except Exception:
                pass
        return metas

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

    def delete(self, session_id: str) -> bool:
        """删除 Session，返回是否成功。"""
        import shutil

        # 新格式：目录
        session_dir = self.session_dir / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir, ignore_errors=True)
            return True

        # 前缀匹配（新格式）
        candidates_dir = [
            d for d in self.session_dir.iterdir()
            if d.is_dir() and d.name.startswith(session_id)
        ]
        if candidates_dir:
            for d in candidates_dir:
                shutil.rmtree(d, ignore_errors=True)
            return True

        # 旧格式：文件
        candidates_file = self._find_legacy_files(session_id)
        if candidates_file:
            for p in candidates_file:
                p.unlink(missing_ok=True)
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
            )
        except Exception:
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
        except Exception:
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
            )
        except Exception:
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

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write_json(path: Path, data: object) -> None:
    """原子写入 JSON 文件（tmp + rename，保证读端不见到半截 JSON）。"""
    import tempfile
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _flock(f)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        os.unlink(tmp)
        raise
    os.replace(tmp, path)


def _serialize_history(history: list[dict]) -> list[dict]:
    """将 history 序列化为可 JSON 化的格式，处理 SDK content block 对象。"""
    result = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
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
                    except Exception:
                        serialized_content.append({"type": "unknown", "raw": str(block)})
            result.append({"role": role, "content": serialized_content})
        else:
            result.append({"role": role, "content": content})
    return result


def _flock(f) -> None:
    """跨平台文件锁（尽力而为）。"""
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return
    except ImportError:
        pass
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 512)
    except Exception:
        pass
