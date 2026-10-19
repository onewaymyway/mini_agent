"""output_registry/patrol.py — P4.5 数据采集 与 P5 全局巡检（只读）。

对应 next_doc/management_output_project_model_plan.md 第 8.6 节与第 9 章 P4.5、P5。

两种模式：
  - collect：P4.5。只采集每个产出项目的指标，追加写入 metrics.jsonl，**不生成任何告警**。
    用于积累阈值校准所需的实际分布。
  - report：P5。基于阈值生成巡检报告（findings），写入 patrol/<时间戳>.json 与 patrol/latest.json。

约束：
  1. 只读：不修改产出项目目录、不修改 _registry/、不修改 goals.json；
  2. 唯一写入位置：<project_root>/.agent/output_registry/ 下；
  3. 阈值目前为**未校准**的默认值（UNCALIBRATED），报告中明确标注。
     校准需要 collect 模式积累的真实数据，测试数据不能作为校准依据；
  4. 巡检只建议，不执行任何归档、删除或关联动作。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mini_agent.evolution.output_projects_root import PROJECT_INFO_FILENAME, resolve_root
from mini_agent.output_registry.registry import list_links, load_index, load_record
from mini_agent.utils.atomic_write import atomic_write_json

PATROL_DIRNAME = "output_registry"
METRICS_FILENAME = "metrics.jsonl"
PATROL_SUBDIR = "patrol"
LATEST_FILENAME = "latest.json"

# 未校准的默认阈值。report 模式的报告会原样附上这些值与 calibration="uncalibrated"。
UNCALIBRATED_THRESHOLDS: dict[str, Any] = {
    "calibration": "uncalibrated",
    "stale_days": 30,          # 停滞：超过该天数没有任何文件变动
    "version_gap_days": 30,    # 版本缺口：有未发布的变更，且距上次发布超过该天数
    "file_scan_limit": 5000,   # 单项目文件统计上限
}

_TERMINAL_GOAL_STATUSES = ("completed", "abandoned", "cancelled", "failed")
_FILE_SCAN_LIMIT = 5000


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _patrol_dir(project_root: Path | str) -> Path:
    return Path(project_root) / ".agent" / PATROL_DIRNAME


def _read_goal_status_map(project_root: Path | str) -> dict[str, str]:
    """只读解析 goals.json：id → status。文件缺失或损坏返回空映射。"""
    path = Path(project_root) / ".agent" / "goals.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for g in data.get("goals", []) or []:
        if isinstance(g, dict) and "id" in g:
            out[g["id"]] = g.get("status", "")
    return out


def _latest_file_mtime(project_dir: Path) -> tuple[Optional[float], int, bool]:
    """返回 (最新文件 mtime, 文件数, 是否截断)。只读。"""
    latest: Optional[float] = None
    count = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(project_dir):
        for fn in filenames:
            count += 1
            try:
                m = os.stat(os.path.join(dirpath, fn)).st_mtime
            except OSError:
                continue
            if latest is None or m > latest:
                latest = m
            if count >= _FILE_SCAN_LIMIT:
                truncated = True
                return latest, count, truncated
    return latest, count, truncated


def _iso_to_epoch(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def collect_metrics(project_root: Path | str, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """采集每个产出项目的指标。纯读取，不产生告警。"""
    now_dt = _now(now)
    now_ts = now_dt.timestamp()
    root = resolve_root(Path(project_root))
    if not root.is_dir():
        return []
    goal_status = _read_goal_status_map(project_root)
    index = load_index(project_root)
    link_by_project: dict[str, list[dict]] = {}
    for link in list_links(project_root):
        link_by_project.setdefault(link["output_id"], []).append(link)

    rows: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        slug = entry.name
        latest_mtime, file_count, truncated = _latest_file_mtime(entry)
        record = load_record(project_root, slug)
        registered = record is not None
        record = record or {}
        versions = record.get("versions", [])
        latest_release_ts = None
        latest_version = None
        for v in versions:
            ts = _iso_to_epoch(v.get("released_at"))
            if ts is not None and (latest_release_ts is None or ts > latest_release_ts):
                latest_release_ts = ts
                latest_version = v.get("version")

        links = link_by_project.get(slug, [])
        broken = [
            l["management_id"] for l in links
            if l["management_id"] not in index.get("management_roots", {})
            or l["management_id"] not in goal_status
        ]
        owner = record.get("owner_goal_id")
        rows.append({
            "slug": slug,
            "collected_at": now_dt.isoformat(),
            "has_project_info": (entry / PROJECT_INFO_FILENAME).is_file(),
            "file_count": file_count,
            "file_count_truncated": truncated,
            "days_since_update": (
                round((now_ts - latest_mtime) / 86400, 2) if latest_mtime is not None else None
            ),
            "latest_version": latest_version,
            "days_since_release": (
                round((now_ts - latest_release_ts) / 86400, 2) if latest_release_ts is not None else None
            ),
            "unreleased_changes": bool(
                latest_mtime is not None
                and (latest_release_ts is None or latest_mtime > latest_release_ts)
            ),
            "status": record.get("status", "floating"),
            "owner_goal_id": owner,
            "owner_goal_status": goal_status.get(owner) if owner else None,
            "link_count": len(links),
            "broken_link_targets": broken,
            "managed": bool(record.get("managed", False)),
            "registered": registered,
        })
    return rows


def append_metrics(project_root: Path | str, rows: list[dict[str, Any]]) -> Path:
    """P4.5：把本次采集追加到 metrics.jsonl（每行一条项目记录）。"""
    path = _patrol_dir(project_root) / METRICS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def evaluate(rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """P5：按阈值生成 findings。每条 finding 只是建议，不执行任何动作。"""
    findings: list[dict[str, Any]] = []
    for r in rows:
        slug = r["slug"]

        def add(kind: str, detail: str, suggestion: str) -> None:
            findings.append({"slug": slug, "kind": kind, "detail": detail, "suggestion": suggestion})

        if not r.get("registered", True):
            add("unregistered", "目录存在但未登记进注册表",
                "确认后一键接入（scripts/output_project_lifecycle.py adopt）")
        if not r["has_project_info"]:
            add("missing_intro", "缺少 PROJECT_INFO.md", "补充项目介绍")
        if r["days_since_update"] is not None and r["days_since_update"] > thresholds["stale_days"]:
            add("stale", f"已 {r['days_since_update']} 天没有文件变动（阈值 {thresholds['stale_days']}）",
                "确认项目是否仍在推进；如已结束可归档")
        if (r["unreleased_changes"] and r["days_since_release"] is not None
                and r["days_since_release"] > thresholds["version_gap_days"]):
            add("version_gap", f"有未发布的变更，距上次发布 {r['days_since_release']} 天",
                "考虑发布新版本")
        if r["unreleased_changes"] and r["latest_version"] is None and r["managed"]:
            add("never_released", "受管项目从未发布版本", "发布首个版本，或确认仍在构建中")
        if r["owner_goal_status"] in _TERMINAL_GOAL_STATUSES:
            add("orphan", f"所属管理项目已为 {r['owner_goal_status']}",
                "重新打开管理项目，或将项目转为游离/归档")
        for target in r["broken_link_targets"]:
            add("broken_link", f"关联指向不存在的管理项目 {target}", "解除该关联或恢复目标节点")
        if r["status"] == "floating":
            add("floating", "游离项目，尚未绑定管理项目", "绑定到已有管理项目，或派生新的管理项目")
    return findings


def run_report(
    project_root: Path | str,
    thresholds: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """P5：采集 + 评估，写入报告文件。只写 .agent/output_registry/patrol/。"""
    th = dict(UNCALIBRATED_THRESHOLDS)
    th.update(thresholds or {})
    now_dt = _now(now)
    rows = collect_metrics(project_root, now_dt)
    findings = evaluate(rows, th)
    report = {
        "schema": "output_registry.patrol.v1",
        "generated_at": now_dt.isoformat(),
        "thresholds": th,
        "projects_scanned": len(rows),
        "findings_total": len(findings),
        "findings": findings,
        "read_only": True,
    }
    out_dir = _patrol_dir(project_root) / PATROL_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_dt.strftime("%Y%m%dT%H%M%S")
    atomic_write_json(out_dir / f"{stamp}.json", report)
    atomic_write_json(out_dir / LATEST_FILENAME, report)
    return report


def run_collect(project_root: Path | str, now: Optional[datetime] = None) -> dict[str, Any]:
    """P4.5：只采集、不告警。返回采集条数与写入位置。"""
    now_dt = _now(now)
    rows = collect_metrics(project_root, now_dt)
    path = append_metrics(project_root, rows)
    return {"mode": "collect", "projects": len(rows), "written_to": str(path), "alerts": 0}
