"""
perception/behavior/events.py — 统一事件模型 + 落盘存储

所有采集器（无论本机线程还是 HTTP 上报）都产出同一种 ActivityEvent，
下游（查询层 / agent context 注入）只需要认识这一种结构，方便横向扩展
新的采集来源而不用改动存储和查询逻辑。
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any

from .config import _behavior_dir  # noqa: F401 (复用目录约定)


@dataclass
class ActivityEvent:
    """一条用户行为事件。

    source:      事件来源，如 "windows_active_window" / "macos_active_window" /
                 "linux_active_window" / "idle" / "browser_ext" / "clipboard_meta"
    event_type:  事件类型，如 "app_focus" / "idle_start" / "idle_end" /
                 "page_visit" / "tab_switch" / "clipboard_copy"
    app_name:    前台应用名（可选）
    window_title:窗口标题（可选，脱敏模式下为空）
    domain:      浏览器域名（可选）
    url_path:    浏览器路径（可选，脱敏模式下为空）
    duration_sec:该事件持续时长（可选，如某窗口停留了多久）
    meta:        采集器自定义扩展字段
    """

    timestamp: float = field(default_factory=time.time)
    source: str = ""
    event_type: str = ""
    app_name: Optional[str] = None
    window_title: Optional[str] = None
    domain: Optional[str] = None
    url_path: Optional[str] = None
    duration_sec: Optional[float] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ActivityEvent":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


class BehaviorEventStore:
    """按天分文件的 JSONL 事件存储，线程安全（单进程内用锁保护写入）。

    文件布局：~/.agent/behavior/events/<YYYY-MM-DD>.jsonl
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._dir = (base_dir or _behavior_dir()) / "events"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file_for(self, ts: float) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        return self._dir / f"{day}.jsonl"

    def append(self, event: ActivityEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        path = self._file_for(event.timestamp)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def append_many(self, events: list[ActivityEvent]) -> None:
        with self._lock:
            # 按天分组，减少重复 open
            by_day: dict[Path, list[str]] = {}
            for e in events:
                p = self._file_for(e.timestamp)
                by_day.setdefault(p, []).append(json.dumps(e.to_dict(), ensure_ascii=False))
            for path, lines in by_day.items():
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")

    def query(
        self,
        since: Optional[float] = None,
        until: Optional[float] = None,
        source: Optional[str] = None,
        limit: int = 500,
    ) -> list[ActivityEvent]:
        """按时间范围/来源查询，最近的排在前面。"""
        results: list[ActivityEvent] = []
        files = sorted(self._dir.glob("*.jsonl"), reverse=True)
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.events.BehaviorEventStore.query')
                continue
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.events.BehaviorEventStore.query')
                    continue
                if since is not None and d.get("timestamp", 0) < since:
                    continue
                if until is not None and d.get("timestamp", 0) > until:
                    continue
                if source is not None and d.get("source") != source:
                    continue
                results.append(ActivityEvent.from_dict(d))
                if len(results) >= limit:
                    return results
        return results

    def clear(self) -> int:
        """清空所有事件文件，返回删除的文件数。"""
        n = 0
        for path in self._dir.glob("*.jsonl"):
            try:
                path.unlink()
                n += 1
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.events.BehaviorEventStore.clear')
                pass
        return n

    def purge_older_than(self, days: int) -> int:
        """删除超过 retention_days 的事件文件，返回删除的文件数。"""
        cutoff = time.time() - days * 86400
        n = 0
        for path in self._dir.glob("*.jsonl"):
            try:
                day_str = path.stem  # YYYY-MM-DD
                day_ts = time.mktime(time.strptime(day_str, "%Y-%m-%d"))
                if day_ts < cutoff:
                    path.unlink()
                    n += 1
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.events.BehaviorEventStore.purge_older_than')
                continue
        return n
