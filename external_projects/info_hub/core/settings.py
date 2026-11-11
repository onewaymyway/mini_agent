"""加载 config/*.yaml 与路径解析。所有路径相对于 info_hub 项目根目录。"""
from __future__ import annotations

import json

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是映射（dict）")
    return data


@dataclass(frozen=True)
class Paths:
    root: Path
    sources_dir: Path
    data_dir: Path
    reports_dir: Path
    browser_profile_dir: Path

    @property
    def source_index(self) -> Path:
        return self.sources_dir / "_index.json"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def items_dir(self) -> Path:
        return self.data_dir / "items"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"


@dataclass(frozen=True)
class Settings:
    paths: Paths
    defaults: dict[str, Any]
    base_reports: list[dict[str, Any]]
    categories: list[dict[str, str]]
    raw: dict[str, Any] = None  # type: ignore[assignment]

    @property
    def category_ids(self) -> set[str]:
        return {c["id"] for c in self.categories}

    @property
    def tick_interval_minutes(self) -> int:
        """后台 tick 间隔（分钟）。看板修改后的值优先于 settings.yaml 中的默认值，每次读取都是最新值。"""
        override = _read_tick_override(self.paths.state_dir / "runtime_config.json")
        if override is not None:
            return override
        return int(self.defaults.get("tick_interval_minutes", 5))

    @property
    def reports(self) -> list[dict[str, Any]]:
        """报告计划（已合并看板修改的间隔、时刻与窗口）。每次访问都是最新值。"""
        overrides = read_runtime_config(self.paths.state_dir / "runtime_config.json").get("reports") or {}
        out = []
        for plan in self.base_reports:
            merged = dict(plan)
            o = overrides.get(plan.get("id")) or {}
            for key in ("every", "at"):
                if key in o and key in plan:           # 不允许 every 与 at 互相切换
                    merged[key] = o[key]
            if "window" in o and ("every" in plan or "at" in plan):
                merged["window"] = o["window"]
            out.append(merged)
        return out


TICK_INTERVAL_MIN = 1
TICK_INTERVAL_MAX = 1440


def read_runtime_config(path: Path) -> dict[str, Any]:
    """读取看板保存的运行时配置（data/state/runtime_config.json）；缺失或损坏时返回空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_tick_override(path: Path) -> int | None:
    """读取运行时覆盖值；文件不存在或内容不合法时返回 None（回退到 settings.yaml）。"""
    value = read_runtime_config(path).get("tick_interval_minutes")
    if isinstance(value, int) and TICK_INTERVAL_MIN <= value <= TICK_INTERVAL_MAX:
        return value
    return None


def load_settings(root: Path | None = None) -> Settings:
    root = Path(root) if root else PROJECT_ROOT
    cfg = _load_yaml(root / "config" / "settings.yaml")
    cats = _load_yaml(root / "config" / "categories.yaml")
    p = cfg.get("paths", {})

    def _abs(key: str, default: str) -> Path:
        return (root / p.get(key, default)).resolve()

    paths = Paths(
        root=root,
        sources_dir=_abs("sources_dir", "sources"),
        data_dir=_abs("data_dir", "data"),
        reports_dir=_abs("reports_dir", "reports"),
        browser_profile_dir=_abs("browser_profile_dir", "browser/profile"),
    )
    categories = cats.get("categories", [])
    if not isinstance(categories, list):
        raise ValueError("categories.yaml 中 categories 必须是列表")
    return Settings(
        paths=paths,
        defaults=cfg.get("defaults", {}),
        base_reports=cfg.get("reports", []),
        categories=categories,
        raw=cfg,
    )


def load_sites(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """读取 config/sites.yaml 的 sites 映射。文件不存在时返回空字典。"""
    root = Path(root) if root else PROJECT_ROOT
    path = root / "config" / "sites.yaml"
    if not path.exists():
        return {}
    data = _load_yaml(path)
    sites = data.get("sites", {}) or {}
    if not isinstance(sites, dict):
        raise ValueError("sites.yaml 中 sites 必须是映射")
    return sites
