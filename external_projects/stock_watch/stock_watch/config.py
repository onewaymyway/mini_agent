"""stock_watch/config.py — 加载 `config/watchlist.yaml`。

刻意用轻量 dataclass + `yaml.safe_load`，不引入 pydantic，与本机制
框架层（`workspace.py` / `manifest.py`）已经确立的风格保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "watchlist.yaml"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"


@dataclass
class SeedStock:
    code: str
    name: str
    market: str = "sh"
    type: str = "stock"  # "stock" | "etf"

    @property
    def ak_symbol(self) -> str:
        """akshare 常用的 `sh600519` / `sz000001` 风格代码。"""
        return f"{self.market}{self.code}"


@dataclass
class WatchlistConfig:
    seeds: List[SeedStock] = field(default_factory=list)
    sources: Dict[str, bool] = field(default_factory=dict)
    candidate_pool: Dict[str, Any] = field(default_factory=dict)
    screener: Dict[str, Any] = field(default_factory=dict)
    kline: Dict[str, Any] = field(default_factory=dict)

    @property
    def max_pool_size(self) -> int:
        return int(self.candidate_pool.get("max_size", 100))

    @property
    def score_decay_days(self) -> int:
        return int(self.candidate_pool.get("score_decay_days", 7))

    @property
    def default_screener_queries(self) -> List[str]:
        return list(self.screener.get("default_queries", []))

    @property
    def kline_days(self) -> int:
        return int(self.kline.get("days", 120))

    @property
    def kline_adjust(self) -> str:
        return str(self.kline.get("adjust", "qfq"))

    def source_enabled(self, name: str) -> bool:
        return bool(self.sources.get(name, False))


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> WatchlistConfig:
    if not path.exists():
        return WatchlistConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    seeds = [SeedStock(**item) for item in raw.get("seeds", []) or []]
    return WatchlistConfig(
        seeds=seeds,
        sources=raw.get("sources", {}) or {},
        candidate_pool=raw.get("candidate_pool", {}) or {},
        screener=raw.get("screener", {}) or {},
        kline=raw.get("kline", {}) or {},
    )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "kline").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "candidate_pool").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "screener").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "analysis").mkdir(parents=True, exist_ok=True)
