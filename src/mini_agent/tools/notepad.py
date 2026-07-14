"""
tools/notepad.py — 记事本工具集

Agent 在执行任务过程中，可以把关键信息、关键结果或注意事项记录到记事本。
记事本内容会常驻 system prompt（见 prompts/system/notepad.md + context_builder.py），
不受 history compact 影响；持久化到对应 session 目录下的 notepad.json。

工具列表：
  notepad_add(content, tag=None)                        — 新增一条记事
  notepad_update(id, content)                            — 修改已有条目
  notepad_remove(id)                                     — 删除一条
  notepad_list()                                         — 列出全部条目（含 id，便于引用）
  notepad_summarize(replace_ids, new_content, tag=None)  — 合并多条为一条（瘦身/总结用）

配置：
  configure_notepad_store(paths, session_id_getter) 由 agent 生命周期在初始化时调用，
  注入当前 project 的 AgentPaths 和 session_id 懒引用，工具据此定位到正确的
  <project_root>/.agent/sessions/<session_id>/notepad.json。

关于 compact 联动：
  记事本本身不参与 history compact 的输入/输出，但当记事本总字数超过阈值
  （见 history/compression 或 agent/compaction.py 中的 NOTEPAD_COMPACT_HINT_THRESHOLD）时，
  compact 流程会在发给模型的 compact 提示中追加一句提示，建议模型调用
  notepad_summarize 合并冗余/过时条目。这不是自动截断——内容取舍始终由 agent 决定。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import tool


# ── 数据模型 ─────────────────────────────────────────────────────────────────

@dataclass
class NotepadEntry:
    id: str
    content: str
    tag: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "tag": self.tag,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NotepadEntry":
        return cls(
            id=d["id"],
            content=d.get("content", ""),
            tag=d.get("tag"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class NotepadStore:
    """
    单个 session 的记事本，负责内存状态 + 原子落盘。

    与 orchestrator/plan.py 的 ExecutionPlan 风格保持一致：
    纯数据 + 简单方法，不依赖框架其他部分。
    """

    VERSION = 1

    def __init__(self, path: Path, session_id: str = "") -> None:
        self.path = path
        self.session_id = session_id
        self.entries: dict[str, NotepadEntry] = {}
        self._order: list[str] = []   # 保持插入顺序，展示时按此顺序
        self._lock = threading.Lock()
        self._load()

    # ── 加载 / 保存 ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries_list = data.get("entries", [])
            self.entries = {}
            self._order = []
            for e in entries_list:
                if isinstance(e, dict) and "id" in e:
                    entry = NotepadEntry.from_dict(e)
                    self.entries[entry.id] = entry
                    self._order.append(entry.id)
        except Exception:
            # 读取失败不阻塞 agent 主流程，视为空记事本
            self.entries = {}
            self._order = []

    def save(self) -> None:
        """原子写入磁盘（.tmp + os.replace），与项目其他持久化模块保持一致。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.VERSION,
            "session_id": self.session_id,
            "entries": [self.entries[i].to_dict() for i in self._order if i in self.entries],
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
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
        os.replace(tmp, self.path)

    # ── 增删改查 ─────────────────────────────────────────────────────────────

    def add(self, content: str, tag: Optional[str] = None) -> NotepadEntry:
        with self._lock:
            entry_id = uuid.uuid4().hex[:6]
            while entry_id in self.entries:  # 极小概率碰撞兜底
                entry_id = uuid.uuid4().hex[:6]
            now = _now()
            entry = NotepadEntry(id=entry_id, content=content, tag=tag, created_at=now, updated_at=now)
            self.entries[entry_id] = entry
            self._order.append(entry_id)
            self.save()
            return entry

    def update(self, entry_id: str, content: str) -> Optional[NotepadEntry]:
        with self._lock:
            entry = self.entries.get(entry_id)
            if entry is None:
                return None
            entry.content = content
            entry.updated_at = _now()
            self.save()
            return entry

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            if entry_id not in self.entries:
                return False
            del self.entries[entry_id]
            self._order = [i for i in self._order if i != entry_id]
            self.save()
            return True

    def clear(self) -> int:
        with self._lock:
            n = len(self.entries)
            self.entries = {}
            self._order = []
            self.save()
            return n

    def summarize(self, replace_ids: list[str], new_content: str, tag: Optional[str] = None) -> NotepadEntry:
        """用一条新条目替换多条旧条目（供瘦身/总结使用）。不存在的 id 会被忽略。"""
        with self._lock:
            for rid in replace_ids:
                if rid in self.entries:
                    del self.entries[rid]
            self._order = [i for i in self._order if i in self.entries]
            entry_id = uuid.uuid4().hex[:6]
            while entry_id in self.entries:
                entry_id = uuid.uuid4().hex[:6]
            now = _now()
            entry = NotepadEntry(id=entry_id, content=new_content, tag=tag, created_at=now, updated_at=now)
            self.entries[entry_id] = entry
            self._order.append(entry_id)
            self.save()
            return entry

    # ── 展示 ─────────────────────────────────────────────────────────────────

    def total_chars(self) -> int:
        return sum(len(e.content) for e in self.entries.values())

    def is_empty(self) -> bool:
        return len(self.entries) == 0

    def to_list(self) -> list[dict]:
        return [self.entries[i].to_dict() for i in self._order if i in self.entries]

    def render(self) -> str:
        """渲染为供 system prompt 注入的文本块（不含标题，由模板负责标题/说明）。"""
        if not self.entries:
            return "(empty — nothing recorded yet)"
        lines = []
        for i in self._order:
            entry = self.entries.get(i)
            if entry is None:
                continue
            tag_part = f"[{entry.tag}] " if entry.tag else ""
            lines.append(f"- ({entry.id}) {tag_part}{entry.content}")
        return "\n".join(lines)


# ── 全局配置（session 级懒引用，与 tools/builtin.py::configure_artifact_tool 同模式）──

_paths_getter = None          # Callable[[], Optional[AgentPaths]]
_session_id_getter = None     # Callable[[], str]
_stores: dict[str, NotepadStore] = {}   # session_id -> NotepadStore（进程内缓存，跨轮复用）
_stores_lock = threading.Lock()


def configure_notepad_store(paths_getter, session_id_getter) -> None:
    """由 agent/lifecycle.py 在初始化时调用，注入 AgentPaths / session_id 懒引用。"""
    global _paths_getter, _session_id_getter
    _paths_getter = paths_getter
    _session_id_getter = session_id_getter


def get_current_notepad() -> Optional[NotepadStore]:
    """
    返回当前 session 对应的 NotepadStore；若尚未配置或无 session_id 则返回 None。
    进程内按 session_id 缓存 NotepadStore 实例，避免每次工具调用都重新读盘。
    """
    if _paths_getter is None or _session_id_getter is None:
        return None
    paths = _paths_getter()
    session_id = _session_id_getter()
    if paths is None or not session_id:
        return None
    with _stores_lock:
        store = _stores.get(session_id)
        if store is None:
            store = NotepadStore(paths.session_notepad(session_id), session_id=session_id)
            _stores[session_id] = store
        return store


def reset_notepad_cache(session_id: Optional[str] = None) -> None:
    """测试/会话切换时清理进程内缓存。session_id=None 时清空全部。"""
    with _stores_lock:
        if session_id is None:
            _stores.clear()
        else:
            _stores.pop(session_id, None)


def _require_store() -> NotepadStore:
    store = get_current_notepad()
    if store is None:
        raise RuntimeError(
            "Notepad store is not configured for the current session "
            "(configure_notepad_store must be called during agent init)."
        )
    return store


# ── 内置工具 ─────────────────────────────────────────────────────────────────

@tool(
    name="notepad_add",
    description=(
        "Append a new entry to the persistent notepad. Use this whenever you learn or "
        "produce something you must not forget during this task: a key result, a file "
        "path, a config value, a constraint, a caveat, or an explicit instruction from "
        "the user. The notepad stays visible in the system prompt on every turn and "
        "survives history compaction, unlike ordinary conversation turns. "
        "Keep each entry short and self-contained (one fact/result/caveat per entry)."
    ),
    schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The information to remember. Be concise and specific.",
            },
            "tag": {
                "type": "string",
                "description": "Optional short category, e.g. 'fact', 'result', 'caution', 'todo'.",
            },
        },
        "required": ["content"],
    },
    requires_approval=False,
)
def notepad_add(content: str, tag: Optional[str] = None) -> str:
    store = _require_store()
    entry = store.add(content, tag=tag)
    return json.dumps({"added": True, "id": entry.id, "total_entries": len(store.entries)})


@tool(
    name="notepad_update",
    description=(
        "Update the content of an existing notepad entry, identified by its id "
        "(as shown in the notepad block in the system prompt, or via notepad_list)."
    ),
    schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The entry id to update."},
            "content": {"type": "string", "description": "The new content for this entry."},
        },
        "required": ["id", "content"],
    },
    requires_approval=False,
)
def notepad_update(id: str, content: str) -> str:
    store = _require_store()
    entry = store.update(id, content)
    if entry is None:
        return json.dumps({"updated": False, "error": f"No notepad entry with id={id!r}"})
    return json.dumps({"updated": True, "id": entry.id})


@tool(
    name="notepad_remove",
    description="Delete a notepad entry by id (e.g. it is no longer relevant or was a mistake).",
    schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The entry id to remove."},
        },
        "required": ["id"],
    },
    requires_approval=False,
)
def notepad_remove(id: str) -> str:
    store = _require_store()
    ok = store.remove(id)
    return json.dumps({"removed": ok, "id": id})


@tool(
    name="notepad_list",
    description=(
        "List all current notepad entries with their ids. Normally you don't need this — "
        "the full notepad content is already shown in the system prompt every turn — but "
        "it's useful when the notepad is large and you need exact ids to update/remove/summarize."
    ),
    schema={"type": "object", "properties": {}, "required": []},
    requires_approval=False,
)
def notepad_list() -> str:
    store = _require_store()
    return json.dumps({"entries": store.to_list(), "total_chars": store.total_chars()})


@tool(
    name="notepad_summarize",
    description=(
        "Merge several notepad entries into one condensed entry. Use this to keep the "
        "notepad lean when it grows large or contains redundant/outdated information — "
        "for example when asked to summarize the notepad during a history compaction. "
        "The listed replace_ids are deleted and replaced by a single new entry."
    ),
    schema={
        "type": "object",
        "properties": {
            "replace_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of the entries to merge and remove.",
            },
            "new_content": {
                "type": "string",
                "description": "The condensed content that replaces all of the above entries.",
            },
            "tag": {
                "type": "string",
                "description": "Optional short category for the new merged entry.",
            },
        },
        "required": ["replace_ids", "new_content"],
    },
    requires_approval=False,
)
def notepad_summarize(replace_ids: list, new_content: str, tag: Optional[str] = None) -> str:
    store = _require_store()
    entry = store.summarize(list(replace_ids), new_content, tag=tag)
    return json.dumps({"summarized": True, "new_id": entry.id, "total_entries": len(store.entries)})
