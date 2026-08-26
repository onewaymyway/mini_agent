"""
external_projects/ledger.py — 状态账本：读写 + 聚合

对应 `next_doc/external_projects_workspace_plan.md` 阶段 4 第 1、2 项。

核心思路（呼应原则三：可见性建立在"声明式注册 + 被动可读状态"上）：
每个外部项目按统一 schema，把自己的每次执行记录写进自己
`<root>/.agent/run_status.jsonl`（`Workspace.run_status_path`），不管
这次执行是被 daemon 触发、被 OS cron 触发、还是用户手动跑的，都写同
一份账本、同一个 schema。daemon 需要知道"某个项目现在情况如何"时，去
读这份账本，而不是要求账本的主人主动上报。

schema（每行一条 JSON 记录）：
    {
      "entrypoint":    str,             # project.yaml 里的 entrypoint key
      "started_at":    str,             # ISO-8601 UTC
      "finished_at":   str,             # ISO-8601 UTC
      "exit_code":     int,
      "trigger":       "daemon" | "external_cron" | "manual",
      "error_summary": str | null       # 失败时的简要摘要，成功为 null
    }
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from mini_agent.utils.atomic_write import atomic_append_jsonl

VALID_TRIGGERS = ("daemon", "external_cron", "manual")


@dataclass
class RunRecord:
    entrypoint: str
    started_at: str
    finished_at: str
    exit_code: int
    trigger: str
    error_summary: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RunRecord":
        return cls(
            entrypoint=data.get("entrypoint", ""),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            exit_code=int(data.get("exit_code", -1)),
            trigger=data.get("trigger", "manual"),
            error_summary=data.get("error_summary"),
        )


def _ledger_path(root: Path) -> Path:
    return Path(root) / ".agent" / "run_status.jsonl"


def record_run(
    root: Path,
    entrypoint: str,
    exit_code: int,
    trigger: str,
    *,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    error_summary: Optional[str] = None,
) -> RunRecord:
    """
    往 `<root>/.agent/run_status.jsonl` 追加一条执行记录。

    这是"降低外部项目遵循账本约定的成本"的最底层入口——外部项目的
    entrypoint 脚本里 `import` 这一个函数就能写账本，不需要自己处理
    路径拼接/JSON 序列化/原子写入。多数场景更推荐用下面的
    `track_run()` 上下文管理器，能自动填 `started_at`/`finished_at`/
    失败时的 `error_summary`，这个函数留给需要完全自控这几个字段的
    场景（比如 `scheduler.py::_run_entrypoint` 触发的是子进程，自己
    的 Python 代码不会抛异常，用不上 `track_run` 的自动捕获）。
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"trigger 必须是 {VALID_TRIGGERS} 之一，得到 '{trigger}'")

    now = datetime.now(timezone.utc).isoformat()
    record = RunRecord(
        entrypoint=entrypoint,
        started_at=started_at or now,
        finished_at=finished_at or now,
        exit_code=exit_code,
        trigger=trigger,
        error_summary=error_summary,
    )
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_append_jsonl(path, record.to_dict())
    return record


@contextmanager
def track_run(root: Path, entrypoint: str, *, trigger: str = "manual") -> Iterator["_RunHandle"]:
    """
    外部项目 entrypoint 脚本的推荐用法：

        from mini_agent.external_projects.ledger import track_run

        with track_run(".", "hotlist_scan", trigger="external_cron"):
            do_the_actual_scan()

    正常退出 `with` 块 → 记一条 `exit_code=0` 的成功记录；块内抛异常
    → 记一条 `exit_code=1`、`error_summary=<异常类型: 异常信息>` 的
    失败记录，然后异常照常向外抛出（这里不吞异常，写账本只是旁路
    副作用，不改变脚本本身的错误处理行为）。`started_at`/`finished_at`
    全自动填，调用方完全不需要关心账本 schema 的细节。
    """
    started_at = datetime.now(timezone.utc).isoformat()
    handle = _RunHandle()
    try:
        yield handle
    except Exception as exc:
        handle.exit_code = 1
        handle.error_summary = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        finished_at = datetime.now(timezone.utc).isoformat()
        record_run(
            root,
            entrypoint,
            handle.exit_code,
            trigger,
            started_at=started_at,
            finished_at=finished_at,
            error_summary=handle.error_summary,
        )


class _RunHandle:
    """`track_run()` 让渡给 `with` 块的句柄，允许块内显式覆盖退出码/摘要。"""

    def __init__(self) -> None:
        self.exit_code = 0
        self.error_summary: Optional[str] = None


def read_ledger(root: Path, *, limit: Optional[int] = None) -> List[RunRecord]:
    """
    读取一个外部项目的账本，按时间正序返回（最旧的在前）。

    账本文件不存在时返回空列表，不抛异常——一个从未跑过、或者刚注册
    还没执行过的项目，账本为空是正常状态，不是错误状态。单行解析失败
    （账本被意外截断/手工改坏）时跳过该行，不让一行坏数据拖垮整份
    账本的可读性，与 `registry.py` 对损坏文件的容错原则一致。
    """
    path = _ledger_path(root)
    if not path.exists():
        return []

    import json

    records: List[RunRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(RunRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    if limit is not None:
        records = records[-limit:]
    return records


def last_record(root: Path) -> Optional[RunRecord]:
    """账本里最后一条记录，账本为空/不存在时返回 None。"""
    records = read_ledger(root, limit=1)
    return records[0] if records else None
