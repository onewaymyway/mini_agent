"""信息源健康状态：连续失败计数、降频、自动暂停、恢复。

状态文件 data/state/_health.json（纯文本 json）：
{"<source_id>": {"failures": 连续失败次数, "paused": bool, "last_error": "...",
                 "last_success_at": "...", "last_failure_at": "...", "paused_at": "..."}}

分两层，行为边界很重要：
- **记录与标红**（始终开启）：每次运行都会更新连续失败次数；失败达到 degrade_after 次，看板与报告就标红提示。
- **降频与自动暂停**（`health.enabled`，默认关闭，需显式开启）：
  失败达到 degrade_after 次后，抓取间隔按 backoff_factor 逐次放大（不超过 max_interval）；
  达到 pause_after 次后自动暂停，tick 不再运行该源，直到用户手动恢复
  （看板“恢复”、`python cli.py resume <id>`，或手动抓取一次成功）。

降频/暂停/恢复的事件会写进当天的运行记录，不静默。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from core.scheduler import parse_interval
from core.settings import Settings
from core.store import atomic_write_json, file_lock, read_json


@dataclass(frozen=True)
class HealthConfig:
    enabled: bool = False
    degrade_after: int = 3
    pause_after: int = 8
    backoff_factor: int = 2
    max_interval: str = "1d"


def load_health_config(settings: Settings) -> HealthConfig:
    raw = (settings.raw or {}).get("health") or {}
    d = HealthConfig()
    try:
        cfg = HealthConfig(
            enabled=bool(raw.get("enabled", d.enabled)),
            degrade_after=max(1, int(raw.get("degrade_after", d.degrade_after))),
            pause_after=max(1, int(raw.get("pause_after", d.pause_after))),
            backoff_factor=max(1, int(raw.get("backoff_factor", d.backoff_factor))),
            max_interval=str(raw.get("max_interval", d.max_interval)),
        )
        parse_interval(cfg.max_interval)
    except (TypeError, ValueError):
        return d  # 配置写错时退回默认（含“降频/暂停关闭”），不让坏配置中断调度
    return cfg


def _path(settings: Settings):
    return settings.paths.state_dir / "_health.json"


def read_all(settings: Settings) -> dict[str, dict[str, Any]]:
    return read_json(_path(settings), default={}) or {}


def status_of(entry: Optional[dict[str, Any]], cfg: HealthConfig) -> str:
    """ok | degraded（连续失败达标，标红）| paused。"""
    if not entry:
        return "ok"
    if entry.get("paused"):
        return "paused"
    if int(entry.get("failures", 0)) >= cfg.degrade_after:
        return "degraded"
    return "ok"


def effective_interval(every: str, entry: Optional[dict[str, Any]], cfg: HealthConfig) -> timedelta:
    """考虑降频后的实际抓取间隔。未开启降频或失败次数不足时就是 every 本身。"""
    base = parse_interval(every)
    failures = int((entry or {}).get("failures", 0))
    if not cfg.enabled or failures < cfg.degrade_after:
        return base
    steps = failures - cfg.degrade_after + 1
    cap = max(base, parse_interval(cfg.max_interval))
    scaled = base * (cfg.backoff_factor ** min(steps, 20))
    return min(scaled, cap)


def is_paused(settings: Settings, source_id: str) -> bool:
    return bool(read_all(settings).get(source_id, {}).get("paused"))


def _log_event(settings: Settings, when: datetime, source_id: str, label: str, detail: str) -> None:
    path = settings.paths.runs_dir / f"{when:%Y-%m-%d}.log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        if not path.exists():
            path.write_text(f"# 运行记录 {when:%Y-%m-%d}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {when:%H:%M:%S} `{source_id}` {label} | {detail}\n")


def record_result(settings: Settings, source_id: str, ok: bool, error: Optional[str] = None,
                  now: Optional[datetime] = None) -> Optional[str]:
    """记录一次运行结果，返回事件：None | "degraded" | "paused" | "recovered"。"""
    now = now or datetime.now().astimezone()
    cfg = load_health_config(settings)
    ts = now.isoformat(timespec="seconds")
    path = _path(settings)
    event: Optional[str] = None
    with file_lock(path):
        data = read_json(path, default={}) or {}
        e = data.get(source_id) or {"failures": 0, "paused": False}
        before = status_of(e, cfg)
        if ok:
            e.update(failures=0, paused=False, last_success_at=ts)
            e.pop("paused_at", None)
            e.pop("last_error", None)   # 已恢复，旧错误不再有效
            if before != "ok":
                event = "recovered"
        else:
            e["failures"] = int(e.get("failures", 0)) + 1
            e.update(last_error=error or "", last_failure_at=ts)
            if cfg.enabled and not e.get("paused") and e["failures"] >= cfg.pause_after:
                e.update(paused=True, paused_at=ts)
                event = "paused"
            elif status_of(e, cfg) == "degraded" and before == "ok":
                event = "degraded"
        data[source_id] = e
        atomic_write_json(path, data)
    if event == "recovered":
        _log_event(settings, now, source_id, "恢复", "此前处于连续失败状态，现已恢复正常")
    elif event == "degraded":
        tail = "已按 health 配置降频" if cfg.enabled else "未开启 health.enabled，仅标红提示，抓取频率不变"
        _log_event(settings, now, source_id, "降频" if cfg.enabled else "告警",
                   f"连续失败 {e['failures']} 次，{tail}；最近错误：{error}")
    elif event == "paused":
        _log_event(settings, now, source_id, "暂停",
                   f"连续失败 {e['failures']} 次，已自动暂停，需手动恢复；最近错误：{error}")
    return event


def resume(settings: Settings, source_id: str, now: Optional[datetime] = None) -> bool:
    """手动恢复：清除暂停与失败计数。返回此前是否有需要恢复的状态。"""
    now = now or datetime.now().astimezone()
    path = _path(settings)
    with file_lock(path):
        data = read_json(path, default={}) or {}
        e = data.get(source_id)
        if not e or (not e.get("paused") and not e.get("failures")):
            return False
        data[source_id] = {k: v for k, v in e.items() if k not in ("paused_at",)}
        data[source_id].update(failures=0, paused=False)
        atomic_write_json(path, data)
    _log_event(settings, now, source_id, "恢复", "用户手动恢复")
    return True
