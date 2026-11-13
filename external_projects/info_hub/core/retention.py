"""数据保留与清理。

原则：
- 可以清理的是“过程数据”：抓取快照、新增条目、运行记录、推荐展示记录、纯展示（impression）记录。
- 必须保留的是“用户数据”：反馈事件（feedback/events.jsonl）、打开与停留记录（exposures 中的 open / dwell）、
  偏好（feed/prefs.json）、学习权重与 LLM 缓存。这些文件本模块从不删除或改写其中的记录。
- 每个信息源保留最新的一份快照，即使它已超过保留天数，以便“抓取结果”页仍能显示最后一次结果。
- 清理可以先预览（dry_run），看板上先看数量再执行。
- 每天最多自动执行一次，在调度（tick）开始时触发；失败只记录，不影响抓取。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from core.evaluation import exposure_path
from core.feed import shown_path
from core.store import atomic_write_json, atomic_write_text, file_lock, read_json

DEFAULT_RETENTION: dict[str, Any] = {
    "enabled": True,            # 自动清理总开关（看板手动清理不受此影响）
    "raw_days": 31,             # 抓取快照：保留天数（需 ≥ 31，推荐与合并视图最长窗口为 30 天）
    "items_days": 31,           # 新增条目日志
    "runs_days": 90,            # 运行记录（每日一个文件）
    "shown_days": 90,           # 推荐展示记录 shown.jsonl（学习与回放最长用 60 天）
    "impressions_days": 90,     # 信息源的展示记录 exposures（只删 impression，open / dwell 保留）
    "run_every_hours": 24,      # 自动清理的最小间隔
}
# 下限：保证推荐、合并视图、学习权重等依赖的时间窗不会因为清理而变空
MIN_DAYS = {"raw_days": 31, "items_days": 2, "runs_days": 7, "shown_days": 60, "impressions_days": 7}
LAST_RUN_FILE = "retention.json"
OVERRIDES_FILE = "retention_prefs.json"  # 看板上保存的保留参数（优先于 settings.yaml）
# 看板可调范围（天数上限 3650；间隔 1–720 小时）
MAX_DAYS = 3650
RUN_EVERY_RANGE = (1, 720)
LABELS = {
    "enabled": "自动清理",
    "raw_days": "抓取快照保留天数",
    "items_days": "新增条目日志保留天数",
    "runs_days": "运行记录保留天数",
    "shown_days": "推荐展示记录保留天数",
    "impressions_days": "信息源展示记录保留天数",
    "run_every_hours": "自动清理最小间隔（小时）",
}


def load_retention(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    """合并 settings.yaml 的 retention 段；非法或低于下限的值回落到下限。"""
    cfg = dict(DEFAULT_RETENTION)
    user = (raw or {}).get("retention") or {}
    if not isinstance(user, dict):
        return cfg
    if isinstance(user.get("enabled"), bool):
        cfg["enabled"] = user["enabled"]
    for key, lo in MIN_DAYS.items():
        if key in user and isinstance(user[key], (int, float)) and not isinstance(user[key], bool):
            cfg[key] = min(MAX_DAYS, max(lo, int(user[key])))
    if isinstance(user.get("run_every_hours"), (int, float)) and not isinstance(user["run_every_hours"], bool):
        cfg["run_every_hours"] = float(min(RUN_EVERY_RANGE[1], max(RUN_EVERY_RANGE[0], user["run_every_hours"])))
    return cfg


def clamp_overrides(values: dict[str, Any]) -> dict[str, Any]:
    """看板提交的值：只保留可配置的键，限制在合法范围内；非法值丢弃。"""
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if key == "enabled" and isinstance(value, bool):
            out[key] = value
        elif key in MIN_DAYS and not isinstance(value, bool):
            try:
                out[key] = min(MAX_DAYS, max(MIN_DAYS[key], int(value)))
            except (TypeError, ValueError):
                continue
        elif key == "run_every_hours" and not isinstance(value, bool):
            try:
                out[key] = float(min(RUN_EVERY_RANGE[1], max(RUN_EVERY_RANGE[0], float(value))))
            except (TypeError, ValueError):
                continue
    return out


def read_overrides(settings) -> dict[str, Any]:
    data = read_json(settings.paths.state_dir / OVERRIDES_FILE, default=None)
    return clamp_overrides(data) if isinstance(data, dict) else {}


def save_overrides(settings, values: Optional[dict[str, Any]]) -> dict[str, Any]:
    """保存看板设置。values 为 None 表示恢复默认（清除看板上的设置）。"""
    path = settings.paths.state_dir / OVERRIDES_FILE
    if values is None:
        path.unlink(missing_ok=True)
        return {}
    merged = {**read_overrides(settings), **clamp_overrides(values)}
    atomic_write_json(path, merged)
    return merged


def effective_retention(settings) -> dict[str, Any]:
    """当前生效的保留参数：看板设置 > settings.yaml > 内置默认，且都经过范围限制。"""
    cfg = load_retention(settings.raw)
    cfg.update(read_overrides(settings))
    return cfg


def _new_report(dry_run: bool) -> dict[str, Any]:
    return {"dry_run": dry_run, "categories": {}, "protected": [
        "feedback/events.jsonl（反馈事件）", "feedback/exposures.jsonl 中的 open 与 dwell 记录（打开与停留）",
        "feed/prefs.json、feed/learned_weights.json、feed/llm_cache.json、feed/llm_usage.json",
        "state/ 下的信息源状态文件", "reports/ 下的报告文件"]}


def _add(report: dict[str, Any], category: str, files: int, rows: int, bytes_: int) -> None:
    cat = report["categories"].setdefault(category, {"files": 0, "rows": 0, "bytes": 0})
    cat["files"] += files
    cat["rows"] += rows
    cat["bytes"] += bytes_


def _day_of(name: str) -> Optional[datetime]:
    try:
        return datetime.strptime(name[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _prune_dated_files(folder: Path, cutoff_day: datetime, dry_run: bool) -> tuple[int, int]:
    """删除文件名以日期开头、且早于 cutoff_day 的文件（如 2026-10-01.log.md）。返回 (文件数, 字节数)。"""
    files = bytes_ = 0
    if not folder.exists():
        return 0, 0
    for path in folder.iterdir():
        if not path.is_file():
            continue
        day = _day_of(path.name)
        if day is None or day >= cutoff_day:
            continue
        files += 1
        bytes_ += _size(path)
        if not dry_run:
            path.unlink(missing_ok=True)
    return files, bytes_


def _prune_raw(settings, cutoff_day: datetime, dry_run: bool, report: dict[str, Any]) -> None:
    """抓取快照：data/raw/<id>/<日期>/<时间>.json。每个信息源保留最新一份，无论日期。"""
    root = settings.paths.raw_dir
    if not root.exists():
        return
    files_n = rows_n = bytes_n = 0
    for source_dir in root.iterdir():
        if not source_dir.is_dir():
            continue
        snapshots = sorted(p for p in source_dir.glob("*/*.json") if p.is_file())
        keep = snapshots[-1] if snapshots else None  # 最新一份始终保留
        for snap in snapshots:
            if snap == keep:
                continue
            day = _day_of(snap.parent.name)
            if day is None or day >= cutoff_day:
                continue
            files_n += 1
            bytes_n += _size(snap)
            if not dry_run:
                snap.unlink(missing_ok=True)
        if not dry_run:  # 清理空的日期目录
            for day_dir in source_dir.iterdir():
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    day_dir.rmdir()
    _add(report, "raw_snapshots", files_n, rows_n, bytes_n)


def _prune_items(settings, cutoff_day: datetime, dry_run: bool, report: dict[str, Any]) -> None:
    root = settings.paths.items_dir
    files_n = bytes_n = 0
    if root.exists():
        for source_dir in root.iterdir():
            if source_dir.is_dir():
                f, b = _prune_dated_files(source_dir, cutoff_day, dry_run)
                files_n += f
                bytes_n += b
    _add(report, "items", files_n, 0, bytes_n)


def _prune_runs(settings, cutoff_day: datetime, dry_run: bool, report: dict[str, Any]) -> None:
    f, b = _prune_dated_files(settings.paths.runs_dir, cutoff_day, dry_run)
    _add(report, "run_logs", f, 0, b)


def _rewrite_jsonl(path: Path, keep: Callable[[dict[str, Any]], bool], dry_run: bool) -> tuple[int, int, int]:
    """按条件过滤 JSONL 文件。返回 (删除行数, 保留行数, 释放字节数)。

    keep 返回 True 的行原样保留；无法解析的行一律保留（不丢弃未知数据）。预览时只统计，不改写文件。
    """
    if not path.exists():
        return 0, 0, 0
    removed = kept = freed = 0
    lines_out: list[str] = []
    with file_lock(path):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                text = line.rstrip("\n")
                if not text.strip():
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    lines_out.append(text)
                    kept += 1
                    continue
                if isinstance(row, dict) and not keep(row):
                    removed += 1
                    freed += len(line.encode("utf-8"))
                else:
                    lines_out.append(text)
                    kept += 1
        if removed and not dry_run:
            atomic_write_text(path, ("\n".join(lines_out) + "\n") if lines_out else "")
    return removed, kept, freed


def _as_aware(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.astimezone()


def _row_time(row: dict[str, Any]) -> Optional[datetime]:
    """记录时间（带时区）。没有时区的旧记录按本机时区解释。"""
    raw = row.get("time")
    if not isinstance(raw, str):
        return None
    try:
        t = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.astimezone()


def _prune_shown(settings, cutoff: datetime, dry_run: bool, report: dict[str, Any]) -> None:
    """推荐展示记录：早于 cutoff 的整行删除。"""
    def keep(row: dict[str, Any]) -> bool:
        t = _row_time(row)
        return t is None or t >= cutoff

    removed, kept, freed = _rewrite_jsonl(shown_path(settings.paths.data_dir), keep, dry_run)
    _add(report, "feed_shown", 0, removed, freed)


def _prune_impressions(settings, cutoff: datetime, dry_run: bool, report: dict[str, Any]) -> None:
    """信息源展示记录：只删除 impression 行；open 与 dwell（用户行为）永久保留。"""
    def keep(row: dict[str, Any]) -> bool:
        if row.get("kind") != "impression":
            return True
        t = _row_time(row)
        return t is None or t >= cutoff

    removed, kept, freed = _rewrite_jsonl(exposure_path(settings.paths.data_dir), keep, dry_run)
    _add(report, "source_impressions", 0, removed, freed)


def run_retention(settings, now: Optional[datetime] = None, *, dry_run: bool = False) -> dict[str, Any]:
    """执行一次清理（或预览）。返回各类数据删除的文件数、行数与字节数。"""
    now = now or datetime.now().astimezone()
    cfg = effective_retention(settings)
    report = _new_report(dry_run)
    report["now"] = now.isoformat(timespec="seconds")
    report["policy"] = {k: cfg[k] for k in DEFAULT_RETENTION if k in cfg and k.endswith("days")}

    def day_cutoff(days: int) -> datetime:
        return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    _prune_raw(settings, day_cutoff(cfg["raw_days"]), dry_run, report)
    _prune_items(settings, day_cutoff(cfg["items_days"]), dry_run, report)
    _prune_runs(settings, day_cutoff(cfg["runs_days"]), dry_run, report)
    _prune_shown(settings, now - timedelta(days=cfg["shown_days"]), dry_run, report)
    _prune_impressions(settings, now - timedelta(days=cfg["impressions_days"]), dry_run, report)
    report["total_bytes"] = sum(c["bytes"] for c in report["categories"].values())
    if not dry_run:
        _write_last_run(settings, now, report)
    return report


def _last_run_path(settings) -> Path:
    return settings.paths.state_dir / LAST_RUN_FILE


def read_last_run(settings) -> Optional[dict[str, Any]]:
    data = read_json(_last_run_path(settings), default=None)
    return data if isinstance(data, dict) else None


def _write_last_run(settings, now: datetime, report: dict[str, Any]) -> None:
    atomic_write_json(_last_run_path(settings), {"at": now.isoformat(timespec="seconds"),
                                                 "total_bytes": report["total_bytes"],
                                                 "categories": report["categories"]})


def maybe_run_retention(settings, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """到期（距上次自动清理超过 run_every_hours）且已开启时执行一次；否则返回 None。异常只记录，不抛出。"""
    now = now or datetime.now().astimezone()
    cfg = effective_retention(settings)
    if not cfg["enabled"]:
        return None
    last = read_last_run(settings) or {}
    try:
        last_at = _as_aware(datetime.fromisoformat(last["at"])) if last.get("at") else None
    except (ValueError, TypeError):
        last_at = None
    if last_at is not None and now - last_at < timedelta(hours=cfg["run_every_hours"]):
        return None
    try:
        return run_retention(settings, now)
    except OSError as exc:
        print(f"[retention] 自动清理失败: {type(exc).__name__}: {exc}")
        return None


__all__ = ["DEFAULT_RETENTION", "load_retention", "effective_retention", "clamp_overrides", "read_overrides",
           "save_overrides", "run_retention", "maybe_run_retention", "read_last_run"]
