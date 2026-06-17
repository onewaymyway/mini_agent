"""
history/raw_history.py — Raw history（完整事件日志）

设计：
  raw history 是一条不可删除的事件流，记录所有原始信息：
  - 真实用户消息、工具结果、assistant 回复（与当前状态 history 相同）
  - 压缩事件（compact_event）：记录发生了 compact，以及压缩前后的 count
    原来的所有内容不会从 raw history 中删除
  - session_resume：跨 session 加载时追加的标记

  当前状态 history（active history）是可以从 raw history 通过 replay() 函数精确还原的：
    replay(raw_history) → active_history

  这样设计的好处：
  1. 历史永远不丢失（raw 保留一切）
  2. 当前状态 history 始终可以从 raw 重建（可审计、可调试）
  3. 反思机制可以读取 raw 获得完整上下文，不受压缩影响

存储：
  raw history 存在 session 目录下的 raw_history.json（与 history.json 并列）。
  当前状态 history 仍保存在 history.json（不变）。
  HistoryManager 在每次修改 active history 时同步追加 raw history。

注意：
  raw history 只追加，不压缩，不回滚（快照/retry/rollback 只影响 active history）。
  这意味着 raw history 会持续增长，适合用于反思和审计，不适合直接发给 LLM。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from mini_agent.history.entry import HType


class RawHistory:
    """
    Raw history 管理器。

    职责：
    - 追加原始条目（不删除）
    - 保存到 raw_history.json（原子写入）
    - 从文件加载（兼容旧格式：无 raw_history.json 时返回空）
    - 提供 replay() 函数：从 raw 还原当前状态 active history
    """

    def __init__(self) -> None:
        self._raw: list[dict] = []

    @property
    def entries(self) -> list[dict]:
        """返回 raw history 列表的浅拷贝（防止外部意外修改）。"""
        return list(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def append(self, msg: dict) -> None:
        """追加一条条目，自动注入 _ts（UTC 毫秒精度时间戳）。
        active history 中的条目不含 _ts；_ts 仅在写入 raw 时添加。
        """
        from mini_agent.history.entry import _now_ts
        entry = dict(msg)   # 浅拷贝，不修改调用方传入的对象
        if "_ts" not in entry:
            entry["_ts"] = _now_ts()
        self._raw.append(entry)

    def append_compact_event(self, before_count: int, after_count: int, strategy: str) -> None:
        """记录一次 compact 操作（不追加实际消息，只追加事件记录）。
        通过 self.append() 写入，自动注入 _ts。
        """
        from mini_agent.history.entry import make_compact_event
        self.append(make_compact_event(before_count, after_count, strategy))

    def clear(self) -> None:
        """清空 raw history（通常只在测试或显式重置时调用）。"""
        self._raw.clear()

    def load_from_file(self, path: Path) -> None:
        """从 raw_history.json 加载，文件不存在时静默跳过。"""
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._raw = data
        except Exception:
            # 文件损坏时忽略，保持当前状态
            pass

    def save_to_file(self, path: Path) -> None:
        """原子写入 raw_history.json。"""
        text = json.dumps(self._raw, ensure_ascii=False, indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            os.unlink(tmp)
            raise
        os.replace(tmp, path)


# ── replay 函数 ──────────────────────────────────────────────────────────────

def replay(raw_history: list[dict]) -> list[dict]:
    """
    从 raw history 精确还原当前状态 active history。

    规则：
    1. 正常条目（user_input / tool_result / assistant_reply /
       reminder / skill_context / role_agent / session_resume 等）
       直接保留（剥离 _type 不是这里的工作，to_llm_messages() 负责）
    2. compact_event：
       - 遇到 compact_event，清空当前 active buffer，代表从这里开始是压缩后状态
       - compact_event 本身不写入 active（它只是 raw 的元数据）
       - 实际压缩后的消息在 compact_event 之后紧跟，按正常规则保留
    3. COMPACT_EVENT 条目本身不进入 active history

    返回：active history（含 _type 字段，可直接赋给 HistoryManager._history）
    """
    active: list[dict] = []

    for msg in raw_history:
        t = msg.get("_type")

        if t == HType.COMPACT_EVENT:
            # compact 事件：清空 active buffer（接下来的条目是压缩后的新起点）
            active.clear()
            continue

        # 其他所有条目原样保留（含 _type）
        active.append(dict(msg))

    return active
