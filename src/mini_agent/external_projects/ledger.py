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
      "started_at":    str,             # ISO-8601 本地时间（带 UTC 偏移量）
      "finished_at":   str,             # ISO-8601 本地时间（带 UTC 偏移量）
      "exit_code":     int,
      "trigger":       "daemon" | "external_cron" | "manual",
      "error_summary": str | null,      # 失败时的简要摘要（一行），成功为 null
      "detail":        str | null       # 失败时的详细诊断信息（多行，已截断），
                                         # 成功为 null；例如子进程的 stderr/stdout
                                         # 尾部、或 Python 异常的完整 traceback
    }

时间字段说明：早期版本这里存的是 UTC（`datetime.now(timezone.utc)`），
账本里能看到形如 `2026-08-27T04:34:42+00:00` 的记录、跟用户本地时钟对不
上，看起来像是"未来"或"凌晨在跑"的错觉。现在统一改成
`datetime.now().astimezone()`：取本机 wall-clock 时间，再补上本机时区的
UTC 偏移量，序列化出来仍然是无歧义的 ISO-8601（比如
`2026-08-27T12:34:42+08:00`），前端/CLI 直接显示这个字符串就是用户本地
时间，不需要再做时区换算。`read_ledger()`/`from_dict()` 对老账本里遗留
的 UTC（`+00:00`）记录不做迁移——它们本身仍是合法的 ISO-8601，只是历史
记录，新写入的行会自然是本地时间。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from mini_agent.utils.atomic_write import atomic_append_jsonl

VALID_TRIGGERS = ("daemon", "external_cron", "manual")

# detail 字段的最大长度（字符数）。诊断信息只需要"够定位问题"，不需要
# 完整保留——账本是长期追加的 jsonl，单条记录不加限制的话，一次异常
# 输出很长的 traceback/子进程日志就可能让账本文件膨胀失控。超出的部分
# 保留头部（异常类型/最外层调用栈通常在这）+ 尾部（实际抛出点通常在这），
# 中间用省略提示替换。
MAX_DETAIL_CHARS = 4000


def _now_local_iso() -> str:
    """当前本机时间，ISO-8601 格式，带本机时区偏移量（见模块 docstring）。"""
    return datetime.now().astimezone().isoformat()


def truncate_detail(text: Optional[str], *, limit: int = MAX_DETAIL_CHARS) -> Optional[str]:
    """把 detail 文本裁剪到 `limit` 字符以内，`None`/空字符串原样返回 `None`。"""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    head_len = limit * 2 // 3
    tail_len = limit - head_len - 20
    return f"{text[:head_len]}\n...(省略 {len(text) - head_len - tail_len} 字符)...\n{text[-tail_len:]}"


@dataclass
class RunRecord:
    entrypoint: str
    started_at: str
    finished_at: str
    exit_code: int
    trigger: str
    error_summary: Optional[str] = None
    detail: Optional[str] = None

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
            detail=data.get("detail"),
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
    detail: Optional[str] = None,
) -> RunRecord:
    """
    往 `<root>/.agent/run_status.jsonl` 追加一条执行记录。

    这是"降低外部项目遵循账本约定的成本"的最底层入口——外部项目的
    entrypoint 脚本里 `import` 这一个函数就能写账本，不需要自己处理
    路径拼接/JSON 序列化/原子写入。多数场景更推荐用下面的
    `track_run()` 上下文管理器，能自动填 `started_at`/`finished_at`/
    失败时的 `error_summary`/`detail`，这个函数留给需要完全自控这几个
    字段的场景（比如 `scheduler.py::_run_entrypoint` 触发的是子进程，
    自己的 Python 代码不会抛异常，用不上 `track_run` 的自动捕获）。

    `detail` 会被截断到 `MAX_DETAIL_CHARS`（见 `truncate_detail()`），
    调用方不需要自己控制长度。
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"trigger 必须是 {VALID_TRIGGERS} 之一，得到 '{trigger}'")

    now = _now_local_iso()
    record = RunRecord(
        entrypoint=entrypoint,
        started_at=started_at or now,
        finished_at=finished_at or now,
        exit_code=exit_code,
        trigger=trigger,
        error_summary=error_summary,
        detail=truncate_detail(detail),
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
    → 记一条 `exit_code=1`、`error_summary=<异常类型: 异常信息>`、
    `detail=<完整 traceback>` 的失败记录，然后异常照常向外抛出（这里
    不吞异常，写账本只是旁路副作用，不改变脚本本身的错误处理行为）。
    `started_at`/`finished_at` 全自动填，调用方完全不需要关心账本
    schema 的细节。

    如果块内代码已经知道比"异常类型: 异常信息"更有用的诊断信息（比如
    `run_kline_batch.py` 想记录"候选池 37 只全部失败，代码列表：..."），
    可以在 `yield` 出来的 `handle` 上提前设置
    `handle.detail = "..."` ——只要 `handle.detail` 在异常抛出前已经被
    设置过，这里就不会用 traceback 覆盖它。
    """
    started_at = _now_local_iso()
    handle = _RunHandle()
    try:
        yield handle
    except Exception as exc:
        import traceback

        handle.exit_code = 1
        handle.error_summary = f"{type(exc).__name__}: {exc}"
        if handle.detail is None:
            handle.detail = traceback.format_exc()
        raise
    finally:
        finished_at = _now_local_iso()
        record_run(
            root,
            entrypoint,
            handle.exit_code,
            trigger,
            started_at=started_at,
            finished_at=finished_at,
            error_summary=handle.error_summary,
            detail=handle.detail,
        )


class _RunHandle:
    """`track_run()` 让渡给 `with` 块的句柄，允许块内显式覆盖退出码/摘要/详情。"""

    def __init__(self) -> None:
        self.exit_code = 0
        self.error_summary: Optional[str] = None
        self.detail: Optional[str] = None


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
