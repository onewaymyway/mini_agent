"""
perception/daily_snapshot.py — 通用"每日快照 + 降采样"存储小工具

背景（next_doc/kanban_perception_gaps_improvement_plan.md 方向 D.3
风险 1）：`growth_health_trend.jsonl`（成长顾问 N1）已经跑通一整套
"追加快照 → 按天降采样压缩旧记录 → 读出最近 N 个点画折线图"的模式。
如果每新增一个领域（D.1 Objective 完成率趋势、B.2 LLM 调用计数……）
都各自平行实现一份几乎相同的读写/压缩代码，会重蹈 growth_advisor
v4 文档里提到的"`growth_feedback_ledger.jsonl` 尚未纳入数据生命周期
管理"这类问题——本模块把"每天最多保留一条快照，超期的按天分桶只留
最新一条"这个通用逻辑抽出来。

**跟 `llm/call_stats.py` 的关系（有意的不一致，不是遗漏）**：调用计数
的降采样语义是"按天求和"（次数/token 数需要累加），跟这里"按天取最新
一条"的语义不同，所以 `call_stats.py` 没有改造成基于本模块实现——
勉强复用只会让接口参数变得更绕（需要传一个聚合函数），不如保持两份
独立但都很短的实现更清楚。本模块只服务于"每天一条快照，取最新覆盖
同一天的旧快照"这一类场景（`growth_health_trend` 的既有做法、以及新增
的 D.1 Objective 完成率趋势）。`growth_health_trend.jsonl` 本身当前
**没有**迁移到这个通用工具上——它已经有一套跑通并测试过的独立实现
（`_compact_health_trend_rows()`），本次不做无收益的迁移重构，只对
新增的 D.1 场景使用本模块。

调用契约：
  - `append_daily_snapshot(path, row)`：追加一条快照（`row` 必须含
    `recorded_at` 浮点时间戳字段），纯追加，不做压缩判断——压缩是否
    发生由调用方决定何时调 `compact_daily_snapshot_storage()`。
  - `compact_daily_snapshot_storage(path, raw_window_days=60)`：把
    `raw_window_days` 天之前的记录按天分桶、每桶只保留 `recorded_at`
    最大的一条，返回被压缩掉的行数。幂等操作。
  - `read_daily_snapshot_series(path, limit=30)`：按时间正序返回最近
    `limit` 个快照，早期的点丢弃（"只关心最近走势"，跟
    `health_trend_series()` 的调用契约一致）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(text + ("\n" if rows else ""), encoding="utf-8")


def append_daily_snapshot(path: Path, row: dict) -> None:
    """追加一条快照。`row` 应包含 `recorded_at` 浮点时间戳字段（供后续
    压缩/查询按天分桶使用）；缺失时按 0 处理，不阻止写入——调用方的
    职责，本函数不做校验，保持"纯追加"的简单契约。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact_daily_snapshot_storage(
    path: Path, *, raw_window_days: float = 60.0, now: Optional[float] = None,
) -> int:
    """把 `raw_window_days` 天之前的记录按天分桶，每桶只保留
    `recorded_at` 最大的一条（同一天多条快照场景的兜底；正常每天只有
    一条不会真的压缩掉数据）。返回被压缩掉的行数（0 表示本次没有可压缩
    的旧数据，不会触发写盘）。幂等操作。"""
    now = now if now is not None else time.time()
    rows = _read_jsonl(path)
    if not rows:
        return 0
    cutoff = now - raw_window_days * 86400
    recent = [r for r in rows if r.get("recorded_at", 0) >= cutoff]
    old = [r for r in rows if r.get("recorded_at", 0) < cutoff]
    if not old:
        return 0

    buckets: dict[int, dict] = {}
    for r in old:
        ts = r.get("recorded_at", 0)
        day_bucket = int(ts // 86400)
        existing = buckets.get(day_bucket)
        if existing is None or ts > existing.get("recorded_at", 0):
            buckets[day_bucket] = r
    compacted = list(buckets.values()) + recent
    compacted.sort(key=lambda r: r.get("recorded_at", 0))
    removed = len(rows) - len(compacted)
    if removed > 0:
        _write_jsonl(path, compacted)
    return removed


def read_daily_snapshot_series(path: Path, *, limit: int = 30) -> list[dict]:
    """按时间正序返回最近 `limit` 个快照，早期的点丢弃。"""
    rows = _read_jsonl(path)
    rows.sort(key=lambda r: r.get("recorded_at") or 0)
    return rows[-limit:] if limit else rows
