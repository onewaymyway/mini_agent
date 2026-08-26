"""
external_projects/backlog.py — 改进积压账本：读写 + 状态流转

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 1。

设计动机（详见该文档第 3.3 节）：`run_status.jsonl`（阶段4）记的是
"执行成败"，回答不了"这个项目有哪些值得优化但还没处理的问题"。本模块
补一份同级、同风格的账本——外部项目自己（或代表它行动的 review
session/大管家对话）往里写一条"值得关注的软问题"，daemon/后续的 review
session 需要知道"现在积压了哪些待办"时，去读这份账本，而不是依赖某次
对话记住。

写法风格对齐 `ledger.py`：`atomic_append_jsonl` 追加写入、损坏行跳过
不炸整份文件、`root` 参数统一是外部项目的 `Workspace.root`。

schema（每行一条 JSON 记录）：
    {
      "id":           str,   # 短随机 id，供 update_status 定位
      "source":       "outcome_review" | "user_feedback" | "health_trend",
      "summary":      str,   # 一句话描述问题
      "evidence_ref": str | null,  # 支撑证据所在的文件/路径/行号等
      "status":       "open" | "proposed" | "landed" | "dismissed",
      "opened_at":    str,   # ISO-8601 UTC
      "resolved_at":  str | null,
    }
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mini_agent.utils.atomic_write import atomic_append_jsonl, atomic_write_jsonl

VALID_SOURCES = ("outcome_review", "user_feedback", "health_trend")
VALID_STATUSES = ("open", "proposed", "landed", "dismissed")


class BacklogError(ValueError):
    """改进积压账本操作失败（非法 source/status、条目不存在等）。"""


@dataclass
class BacklogItem:
    id: str
    source: str
    summary: str
    evidence_ref: Optional[str]
    status: str
    opened_at: str
    resolved_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BacklogItem":
        return cls(
            id=data.get("id", ""),
            source=data.get("source", "user_feedback"),
            summary=data.get("summary", ""),
            evidence_ref=data.get("evidence_ref"),
            status=data.get("status", "open"),
            opened_at=data.get("opened_at", ""),
            resolved_at=data.get("resolved_at"),
        )


def _backlog_path(root: Path) -> Path:
    return Path(root) / ".agent" / "improvement_backlog.jsonl"


def append_item(
    root: Path,
    *,
    source: str,
    summary: str,
    evidence_ref: Optional[str] = None,
) -> BacklogItem:
    """新增一条待办，初始状态固定为 `open`。"""
    if source not in VALID_SOURCES:
        raise BacklogError(f"source 必须是 {VALID_SOURCES} 之一，得到 '{source}'")
    if not summary or not summary.strip():
        raise BacklogError("summary 不能为空")

    item = BacklogItem(
        id=uuid.uuid4().hex[:12],
        source=source,
        summary=summary.strip(),
        evidence_ref=evidence_ref,
        status="open",
        opened_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _backlog_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_append_jsonl(path, item.to_dict())
    return item


def read_backlog(root: Path, *, status: Optional[str] = None) -> List[BacklogItem]:
    """读取全部待办，`status` 非 None 时只返回该状态的条目。

    与 `ledger.py::read_ledger` 一致的容错约定：账本不存在返回空列表，
    某一行损坏（非法 JSON）跳过该行、不炸整份文件——一条历史记录的
    格式问题不应该让 daemon 看不到其它所有条目。
    """
    path = _backlog_path(root)
    if not path.exists():
        return []

    import json

    items: List[BacklogItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            items.append(BacklogItem.from_dict(data))
        except (json.JSONDecodeError, TypeError):
            continue
    if status is not None:
        items = [i for i in items if i.status == status]
    return items


def update_status(
    root: Path,
    item_id: str,
    new_status: str,
) -> BacklogItem:
    """把某条待办的状态流转到 `new_status`（open→proposed→landed/dismissed）。

    实现方式是"整份重写"（`atomic_write_jsonl`），不是 `ledger.py`
    那种只追加——因为状态流转语义上是"修改已有记录"，账本量级是
    "待办个数"（几十到上百条），全量重写足够便宜，比引入一套
    "追加一条 status 变更事件、读取时再折叠"的事件溯源模型更直接、
    更容易审计（`cat` 文件就能看到每条待办的当前状态，不需要在脑内
    重放事件）。
    """
    if new_status not in VALID_STATUSES:
        raise BacklogError(f"status 必须是 {VALID_STATUSES} 之一，得到 '{new_status}'")

    items = read_backlog(root)
    found = None
    for item in items:
        if item.id == item_id:
            item.status = new_status
            if new_status in ("landed", "dismissed"):
                item.resolved_at = datetime.now(timezone.utc).isoformat()
            found = item
            break
    if found is None:
        raise BacklogError(f"未找到待办 id={item_id}")

    path = _backlog_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(path, [i.to_dict() for i in items])
    return found
