"""
evolution/cleanup_catalog.py — 清理中心：按数据类型分别扫描 / 清理的类别目录。

设计见 next_doc/cleanup_center_plan.md。每个类别：
  - 固定的数据位置（由 AgentPaths 解析，客户端不能传路径）；
  - 自己的年龄判定（天数由调用方给，默认值见 CATEGORIES）；
  - 独立的 dry-run / 执行，执行前经过受保护文件清单检查。

会话与工作流运行不在这里（由 session_cleanup 负责）；adhoc 临时目录与孤立
raw_results 复用 session_cleanup 中已有的实现。
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.evolution.session_cleanup import (
    _dir_last_activity,
    _dir_size,
    cleanup_adhoc_temp,
    scan_orphan_raw_results,
)

MAX_AGE_DAYS = 3650
_ROTATED_LOG_RE = re.compile(r"(\.\d+|\.gz|\.jsonl\.\d+)$")


@dataclass(frozen=True)
class CategorySpec:
    key: str
    label: str
    description: str
    default_checked: bool
    default_age_days: float
    risk: str            # "low" | "medium" | "high"
    risk_note: str = ""


CATEGORIES: list[CategorySpec] = [
    CategorySpec("adhoc_temp", "adhoc 临时目录（temp）",
                 "无会话任务的临时工作区 .agent/adhoc/<key>/temp/ 中长期未活动的条目。output/ 永不触碰。",
                 True, 7, "low"),
    CategorySpec("raw_orphan", "孤立 raw_results",
                 "工具结果原文目录中，没有任何会话或工作流目录引用、且长期未写入的条目。",
                 True, 1, "low"),
    CategorySpec("cache", "缓存 .agent/cache",
                 "可重建的缓存条目（如工具缓存、本地向量模型缓存）。",
                 True, 30, "low", "缓存可重建，删除后会在下次使用时重新生成。"),
    CategorySpec("debug", "调试输出 .agent/debug",
                 "调试用的临时输出文件。",
                 True, 14, "low"),
    CategorySpec("global_rotated_logs", "全局已轮转日志 ~/.agent/logs",
                 "只删除已轮转的日志文件（.N / .gz / .jsonl.N）。正在写入的活动日志只报告大小，不删除。",
                 True, 30, "low"),
    CategorySpec("daily_reports", "每日融合报告 daily_reports",
                 "每日行为 + 目标 + 提交的融合报告。",
                 False, 90, "medium", "报告可能仍在被阅读或被其他功能引用。"),
    CategorySpec("events_archive", "系统事件归档 events_archive",
                 "events.jsonl 按天滚动的归档文件，用于事后回溯。",
                 False, 90, "medium", "归档用于事后回溯，删除后无法再查询这些事件。"),
    CategorySpec("daemon_run_outputs", "daemon 运行产出 daemon_run_outputs",
                 "daemon 为 goal / cron 运行生成的产出（goals/、cron/ 子目录）。",
                 False, 30, "high",
                 "属于产出位置，可能被产出项目登记引用；删除前请确认已导出或不再需要。"),
]

_SPEC_BY_KEY = {c.key: c for c in CATEGORIES}


@dataclass
class CategoryItem:
    name: str
    size_bytes: int
    last_activity: str
    action: str           # "keep" | "delete" | "failed"
    reason: str

    def to_dict(self) -> dict:
        return {
            "name": self.name, "size_bytes": self.size_bytes,
            "last_activity": self.last_activity, "action": self.action, "reason": self.reason,
        }


@dataclass
class CategoryResult:
    key: str
    label: str
    dry_run: bool
    items: list[CategoryItem] = field(default_factory=list)
    error: str = ""

    @property
    def to_delete(self) -> list[CategoryItem]:
        return [i for i in self.items if i.action == "delete"]

    @property
    def deleted(self) -> list[CategoryItem]:
        return [i for i in self.items if i.action == "deleted"]

    @property
    def failed(self) -> list[CategoryItem]:
        return [i for i in self.items if i.action == "failed"]

    @property
    def total_bytes(self) -> int:
        return sum(i.size_bytes for i in self.to_delete + self.deleted)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "dry_run": self.dry_run, "error": self.error,
            "items": [i.to_dict() for i in self.items],
            "total_bytes": self.total_bytes,
            "delete_count": len(self.to_delete),
            "deleted_count": len(self.deleted),
            "failed_count": len(self.failed),
        }


def list_categories() -> list[dict]:
    return [
        {
            "key": c.key, "label": c.label, "description": c.description,
            "default_checked": c.default_checked, "default_age_days": c.default_age_days,
            "risk": c.risk, "risk_note": c.risk_note,
        }
        for c in CATEGORIES
    ]


def _normalize_age(value, default: float) -> float:
    if value is None:
        return float(default)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError("天数必须是数字")
    if v != v or v < 0 or v > MAX_AGE_DAYS:
        raise ValueError(f"天数必须在 0–{MAX_AGE_DAYS} 之间")
    return v


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _delete_path(path: Path, project_root) -> None:
    from mini_agent.utils.protected_files_guard import is_protected as _is_protected
    if _is_protected(path, project_root):
        raise PermissionError("命中受保护文件清单")
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=False)
    else:
        path.unlink()


def _scan_children(base: Path, age_days: float, *, prefix: str = "") -> list[CategoryItem]:
    """base 下每个顶层条目：最后活动早于 age_days 则可删，否则保留。"""
    items: list[CategoryItem] = []
    if not base.is_dir():
        return items
    cutoff = time.time() - age_days * 86400.0
    try:
        children = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        return items
    for child in children:
        try:
            last = _dir_last_activity(child) if child.is_dir() else child.stat().st_mtime
            size = _dir_size(child) if child.is_dir() else child.stat().st_size
        except OSError:
            continue
        name = f"{prefix}{child.name}"
        if last >= cutoff:
            items.append(CategoryItem(name, size, _iso(last), "keep",
                                      f"最近 {age_days:g} 天内有活动"))
        else:
            items.append(CategoryItem(name, size, _iso(last), "delete",
                                      f"超过 {age_days:g} 天未活动"))
    return items


def _scan_rotated_logs(logs_dir: Path, age_days: float) -> list[CategoryItem]:
    items: list[CategoryItem] = []
    if not logs_dir.is_dir():
        return items
    cutoff = time.time() - age_days * 86400.0
    for f in sorted(logs_dir.iterdir(), key=lambda p: p.name):
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if _ROTATED_LOG_RE.search(f.name):
            if st.st_mtime >= cutoff:
                items.append(CategoryItem(f.name, st.st_size, _iso(st.st_mtime), "keep",
                                          f"已轮转，但最近 {age_days:g} 天内修改过"))
            else:
                items.append(CategoryItem(f.name, st.st_size, _iso(st.st_mtime), "delete",
                                          f"已轮转，超过 {age_days:g} 天未修改"))
        else:
            items.append(CategoryItem(f.name, st.st_size, _iso(st.st_mtime), "keep",
                                      "活动日志，不删除（仅报告大小）"))
    return items


def _resolve_base(paths, key: str) -> Optional[Path]:
    if key == "cache":
        return paths.cache_dir
    if key == "debug":
        return paths.workdir_dir / "debug"
    if key == "daily_reports":
        return paths.daily_reports_dir
    if key == "events_archive":
        return paths.system_events_archive_dir
    if key == "global_rotated_logs":
        return paths.global_logs_dir
    return None


def _scan_category(project_root, key: str, age_days: float, paths) -> list[CategoryItem]:
    if key == "adhoc_temp":
        raw = cleanup_adhoc_temp(project_root, min_age_days=age_days, dry_run=True)
        return [CategoryItem(i.dir_name, i.size_bytes, "", "delete", i.reason) for i in raw] + \
            _keep_adhoc_items(project_root, age_days)
    if key == "raw_orphan":
        kept, deleted, failed = scan_orphan_raw_results(
            project_root, paths.sessions_dir, min_age_hours=age_days * 24.0, dry_run=True)
        out = [CategoryItem(i.dir_name, i.size_bytes, "", "delete", i.reason) for i in deleted]
        out += [CategoryItem(i.dir_name, i.size_bytes, "", "keep", i.reason) for i in kept]
        return out
    if key == "global_rotated_logs":
        return _scan_rotated_logs(paths.global_logs_dir, age_days)
    if key == "daemon_run_outputs":
        base = paths.workdir_dir / "daemon_run_outputs"
        items: list[CategoryItem] = []
        for sub in ("goals", "cron"):
            items += _scan_children(base / sub, age_days, prefix=f"{sub}/")
        return items
    base = _resolve_base(paths, key)
    if base is None:
        return []
    return _scan_children(base, age_days)


def _keep_adhoc_items(project_root, age_days: float) -> list[CategoryItem]:
    """adhoc temp 中未过期条目，仅用于预览展示（不删）。"""
    out: list[CategoryItem] = []
    adhoc_root = Path(project_root) / ".agent" / "adhoc"
    if not adhoc_root.is_dir():
        return out
    cutoff = time.time() - age_days * 86400.0
    for key_dir in adhoc_root.iterdir():
        temp = key_dir / "temp"
        if not key_dir.is_dir() or not temp.is_dir():
            continue
        for entry in temp.iterdir():
            last = _dir_last_activity(entry)
            if last >= cutoff:
                out.append(CategoryItem(f"{key_dir.name}/temp/{entry.name}",
                                        _dir_size(entry) if entry.is_dir() else entry.stat().st_size,
                                        _iso(last), "keep", f"最近 {age_days:g} 天内有活动"))
    return out


def _execute_items(project_root, key: str, items: list[CategoryItem], paths) -> None:
    """对 action=delete 的条目真正删除，结果写回 action（deleted / failed）。"""
    for item in items:
        if item.action != "delete":
            continue
        try:
            if key == "adhoc_temp":
                adhoc_key, entry = item.name.split("/temp/", 1)
                target = Path(project_root) / ".agent" / "adhoc" / adhoc_key / "temp" / entry
            elif key == "raw_orphan":
                target = Path(project_root) / ".agent" / "raw_results" / item.name
            elif key == "global_rotated_logs":
                target = paths.global_logs_dir / item.name
            elif key == "daemon_run_outputs":
                target = paths.workdir_dir / "daemon_run_outputs" / item.name
            else:
                base = _resolve_base(paths, key)
                target = base / item.name
            _delete_path(target, project_root)
            item.action = "deleted"
        except Exception as exc:  # 单条失败不影响其它条目
            from mini_agent.errors import log_exception
            log_exception(exc, where=f"mini_agent.evolution.cleanup_catalog.{key}")
            item.action = "failed"
            item.reason = f"{item.reason}；删除失败：{exc.__class__.__name__}"


def validate_selection(keys: list[str], age_days: Optional[dict]) -> None:
    """纯参数校验（不触碰磁盘）。业务错误在进入线程池之前抛出，避免被熔断器计为系统故障。"""
    unknown = [k for k in keys if k not in _SPEC_BY_KEY]
    if unknown:
        raise ValueError(f"未知清理类别：{', '.join(unknown)}")
    for k, v in (age_days or {}).items():
        if k not in _SPEC_BY_KEY:
            raise ValueError(f"未知清理类别：{k}")
        _normalize_age(v, _SPEC_BY_KEY[k].default_age_days)


def run_categories(
    project_root,
    keys: list[str],
    *,
    age_days: Optional[dict] = None,
    dry_run: bool = True,
) -> list[CategoryResult]:
    """对选中的类别做扫描（dry_run）或扫描+删除。未知 key 抛 ValueError。"""
    from mini_agent.storage.paths import AgentPaths

    age_days = age_days or {}
    unknown = [k for k in keys if k not in _SPEC_BY_KEY]
    if unknown:
        raise ValueError(f"未知清理类别：{', '.join(unknown)}")
    if not project_root:
        raise ValueError("项目根目录不可用")

    paths = AgentPaths(Path(project_root))
    results: list[CategoryResult] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        spec = _SPEC_BY_KEY[key]
        days = _normalize_age(age_days.get(key), spec.default_age_days)
        result = CategoryResult(key=key, label=spec.label, dry_run=dry_run)
        try:
            items = _scan_category(project_root, key, days, paths)
            if not dry_run:
                _execute_items(project_root, key, items, paths)
            result.items = items
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where=f"mini_agent.evolution.cleanup_catalog.run.{key}")
            result.error = f"{spec.label} 处理失败：{exc.__class__.__name__}"
        results.append(result)
    return results


__all__ = ["CATEGORIES", "CategorySpec", "CategoryItem", "CategoryResult",
           "list_categories", "run_categories", "validate_selection", "MAX_AGE_DAYS"]
