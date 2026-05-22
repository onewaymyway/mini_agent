"""
session.py — 会话 Session 持久化管理

每个 Session 保存为一个独立文件，格式可选 JSON 或 JSONL。

文件命名：<session_id>_<YYYYMMDD_HHMMSS>.json(l)
默认目录：<project_root>/sessions/
可配置：  SESSION_DIR 环境变量 或 --session-dir CLI 参数

Session 文件内容（JSON 格式）：
{
  "id":          "abc12345",           # 8位随机 hex
  "title":       "写质数计算脚本",      # 首条用户消息前40字
  "created_at":  "2025-01-01T12:00:00",
  "updated_at":  "2025-01-01T12:05:00",
  "provider":    "anthropic",
  "model":       "claude-opus-4-5",
  "stats": {
    "turns":         5,
    "input_tokens":  1200,
    "output_tokens": 800,
    "tool_calls":    3
  },
  "history": [                         # 完整对话历史
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": [...]}
  ]
}

JSONL 格式：每轮对话一行，首行为元数据。
"""

from __future__ import annotations

import json
import os
import uuid
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


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
    file_path: str
    fmt: str  # "json" | "jsonl"

    @property
    def age_str(self) -> str:
        """距今多长时间（如 '2小时前'）。"""
        try:
            dt = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - dt
            s = int(diff.total_seconds())
            if s < 60:       return f"{s}秒前"
            if s < 3600:     return f"{s//60}分钟前"
            if s < 86400:    return f"{s//3600}小时前"
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
    fmt: str = "json"     # "json" | "jsonl"
    file_path: str = ""

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
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provider": self.provider,
            "model": self.model,
            "stats": self.stats,
            "history": self.history,
        }


# ── SessionManager ────────────────────────────────────────────────────────────

class SessionManager:
    """
    管理 Session 文件的读写、列举和查找。

    使用方式：
        mgr = SessionManager(session_dir=Path("./sessions"))

        # 新建 session
        session = mgr.new_session(provider="anthropic", model="claude-opus-4-5")

        # 保存（随时调用，覆盖同名文件）
        mgr.save(session, history=[...], stats={...})

        # 列出所有 session
        metas = mgr.list_sessions()

        # 按 id 前缀加载
        session = mgr.load("abc12345")
        session = mgr.load("abc1")     # 前缀匹配
    """

    def __init__(
        self,
        session_dir: Optional[Path] = None,
        fmt: str = "json",
    ) -> None:
        self.session_dir = (session_dir or Path("sessions")).expanduser()
        self.fmt = fmt if fmt in ("json", "jsonl") else "json"
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
            fmt=self.fmt,
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
        将当前对话历史和统计写入 Session 文件。
        首次保存时生成文件名，后续更新同一文件。

        Args:
            session: 当前 Session 对象（会被就地修改）
            history: agent._history 的当前值
            stats:   {"turns":..., "input_tokens":..., ...}

        Returns:
            写入的文件路径
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

        # 确定文件路径（首次保存时生成）
        if not session.file_path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = ".jsonl" if session.fmt == "jsonl" else ".json"
            fname = f"{session.id}_{ts}{ext}"
            session.file_path = str(self.session_dir / fname)

        path = Path(session.file_path)

        if session.fmt == "jsonl":
            self._write_jsonl(session, path)
        else:
            self._write_json(session, path)

        return path

    # ── 加载 ──────────────────────────────────────────────────────────────────

    def load(self, session_id: str) -> Optional[Session]:
        """
        按 session_id（或其前缀）加载 Session。
        返回 None 如果未找到。
        """
        candidates = self._find_files(session_id)
        if not candidates:
            return None
        # 取最新修改的文件
        path = max(candidates, key=lambda p: p.stat().st_mtime)
        return self._read_file(path)

    def load_file(self, path: Path) -> Optional[Session]:
        """直接按文件路径加载。"""
        return self._read_file(path)

    # ── 列举 ──────────────────────────────────────────────────────────────────

    def list_sessions(self, limit: int = 50) -> list[SessionMeta]:
        """
        列出所有 Session 的元数据（按最后修改时间倒序）。
        只读取文件头部，不加载完整历史，速度快。
        """
        files = sorted(
            list(self.session_dir.glob("*.json")) +
            list(self.session_dir.glob("*.jsonl")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]

        metas: list[SessionMeta] = []
        for f in files:
            try:
                meta = self._read_meta(f)
                if meta:
                    metas.append(meta)
            except Exception:
                pass
        return metas

    def delete(self, session_id: str) -> bool:
        """删除 Session 文件，返回是否成功。"""
        candidates = self._find_files(session_id)
        if not candidates:
            return False
        for p in candidates:
            p.unlink(missing_ok=True)
        return True

    # ── 内部：文件写入 ────────────────────────────────────────────────────────

    @staticmethod
    def _write_json(session: Session, path: Path) -> None:
        data = session.to_dict()
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_jsonl(session: Session, path: Path) -> None:
        """
        JSONL 格式：
        - 第1行：元数据（不含 history）
        - 后续行：每个 history 条目一行
        """
        lines: list[str] = []
        meta = {k: v for k, v in session.to_dict().items() if k != "history"}
        lines.append(json.dumps(meta, ensure_ascii=False))
        for msg in session.history:
            lines.append(json.dumps(msg, ensure_ascii=False))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── 内部：文件读取 ────────────────────────────────────────────────────────

    def _read_file(self, path: Path) -> Optional[Session]:
        try:
            if path.suffix == ".jsonl":
                return self._read_jsonl(path)
            else:
                return self._read_json(path)
        except Exception:
            return None

    @staticmethod
    def _read_json(path: Path) -> Optional[Session]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session(
            id=data.get("id", ""),
            title=data.get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            stats=data.get("stats", {}),
            history=data.get("history", []),
            fmt="json",
            file_path=str(path),
        )

    @staticmethod
    def _read_jsonl(path: Path) -> Optional[Session]:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None
        meta = json.loads(lines[0])
        history = [json.loads(l) for l in lines[1:] if l.strip()]
        return Session(
            id=meta.get("id", ""),
            title=meta.get("title", ""),
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
            provider=meta.get("provider", ""),
            model=meta.get("model", ""),
            stats=meta.get("stats", {}),
            history=history,
            fmt="jsonl",
            file_path=str(path),
        )

    def _read_meta(self, path: Path) -> Optional[SessionMeta]:
        """快速读取元数据（JSON: 解析整个文件；JSONL: 只读第一行）。"""
        try:
            if path.suffix == ".jsonl":
                first_line = ""
                with path.open(encoding="utf-8") as f:
                    first_line = f.readline()
                data = json.loads(first_line)
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
            )
        except Exception:
            return None

    def _find_files(self, session_id: str) -> list[Path]:
        """按 id 前缀查找匹配的 Session 文件。"""
        results: list[Path] = []
        for pattern in ("*.json", "*.jsonl"):
            for f in self.session_dir.glob(pattern):
                if f.name.startswith(session_id):
                    results.append(f)
        return results


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _serialize_history(history: list[dict]) -> list[dict]:
    """
    将 history 序列化为可 JSON 化的格式。
    处理 Anthropic SDK 的 content block 对象（非 dict）。
    """
    result = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # content 可能是 SDK 对象，转为 dict
            serialized_content = []
            for block in content:
                if isinstance(block, dict):
                    serialized_content.append(block)
                else:
                    # SDK 对象（如 anthropic ContentBlock）
                    try:
                        d = {"type": block.type}
                        if hasattr(block, "text"):
                            d["text"] = block.text
                        if hasattr(block, "id"):
                            d["id"] = block.id
                        if hasattr(block, "name"):
                            d["name"] = block.name
                        if hasattr(block, "input"):
                            d["input"] = block.input
                        serialized_content.append(d)
                    except Exception:
                        serialized_content.append({"type": "unknown", "raw": str(block)})
            result.append({"role": role, "content": serialized_content})
        else:
            result.append({"role": role, "content": content})
    return result
