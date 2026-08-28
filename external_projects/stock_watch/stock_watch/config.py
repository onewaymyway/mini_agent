"""stock_watch/config.py — 加载 `config/watchlist.yaml`。

刻意用轻量 dataclass + `yaml.safe_load`，不引入 pydantic，与本机制
框架层（`workspace.py` / `manifest.py`）已经确立的风格保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "watchlist.yaml"
# 存 Cookie/令牌这类敏感值的独立文件，跟主配置分开是为了能整个文件
# `.gitignore` 掉，不至于哪天不小心把 `watchlist.yaml` 一起提交进版本
# 库把 cookie 也带进去。仓库里带的 `secrets.local.yaml.example` 是模板
# （被跟踪），用户 `cp` 一份改名成 `secrets.local.yaml` 后按需填值。
DEFAULT_SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.local.yaml"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
POOL_SNAPSHOTS_DIR = DATA_DIR / "pool_snapshots"
OUTCOME_LEDGER_PATH = DATA_DIR / "outcome_ledger.jsonl"
SOURCE_HEALTH_PATH = DATA_DIR / "source_health.jsonl"
# 阶段2（stock_watch_pool_state_tracking_and_kanban_plan.md）：状态区间
# 跟踪的结构化产出物，供未来看板直接读取，不强迫看板解析 Markdown 表格。
POOL_TRACKING_LATEST_PATH = DATA_DIR / "pool_tracking_latest.json"


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
    outcomes: Dict[str, Any] = field(default_factory=dict)
    signals: Dict[str, Any] = field(default_factory=dict)
    secrets: Dict[str, Any] = field(default_factory=dict)

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

    @property
    def outcome_lookback_days(self) -> int:
        """结果回溯的窗口：回溯"多少天前"的候选池快照。"""
        return int(self.outcomes.get("lookback_days", 7))

    @property
    def outcome_notable_gain_pct(self) -> float:
        """涨跌幅超过这个百分比（绝对值）时，视为"值得关注的结果"，
        写入改进积压账本供 review session 参考（见结果回溯任务）。"""
        return float(self.outcomes.get("notable_gain_pct", 15.0))

    def source_enabled(self, name: str) -> bool:
        return bool(self.sources.get(name, False))

    @property
    def signal_categories_enabled(self) -> Dict[str, bool]:
        """阶段3自算信号的分类开关（`price`/`announcement`/`news`），
        默认全部关闭——自算信号是新增能力，需要用户在
        `config/watchlist.yaml` 里显式打开，不随升级自动生效，避免
        候选池分数/理由的语义突然发生变化（呼应
        `stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段3
        "可通过 sources.* 开关逐个灰度开启"的设计）。"""
        return {
            "price": bool(self.signals.get("price_enabled", False)),
            "announcement": bool(self.signals.get("announcement_enabled", False)),
            "news": bool(self.signals.get("news_enabled", False)),
        }

    @property
    def announcement_weights(self) -> Dict[str, float]:
        return dict(self.signals.get("announcement_weights", {}) or {})

    @property
    def signal_scan_max_targets(self) -> int:
        """每次 `run_signal_scan.py` 最多分析多少只标的（避免无差别对
        整个候选池做行情+公告+新闻抓取，请求量过大）。"""
        return int(self.signals.get("scan_max_targets", 20))

    @property
    def iwencai_cookie(self) -> Optional[str]:
        """问财（iwencai）`hexin-v` 令牌的手动配置值，来自
        `config/secrets.local.yaml` 的 `iwencai_cookie` 字段（不是
        `watchlist.yaml`——见该文件顶部关于为什么单独拆一个文件的说明）。
        没配置时返回 `None`，`data_sources.py` 据此退化到其他策略。"""
        value = self.secrets.get("iwencai_cookie")
        return str(value) if value else None


def _load_secrets(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(path: Path = DEFAULT_CONFIG_PATH, secrets_path: Path = DEFAULT_SECRETS_PATH) -> WatchlistConfig:
    if not path.exists():
        return WatchlistConfig(secrets=_load_secrets(secrets_path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    seeds = [SeedStock(**item) for item in raw.get("seeds", []) or []]
    return WatchlistConfig(
        seeds=seeds,
        sources=raw.get("sources", {}) or {},
        candidate_pool=raw.get("candidate_pool", {}) or {},
        screener=raw.get("screener", {}) or {},
        kline=raw.get("kline", {}) or {},
        outcomes=raw.get("outcomes", {}) or {},
        signals=raw.get("signals", {}) or {},
        secrets=_load_secrets(secrets_path),
    )


def save_config(cfg: WatchlistConfig, secrets_path: Path = DEFAULT_SECRETS_PATH) -> None:
    """把 WatchlistConfig 写回 secrets.local.yaml（只更新 secrets 部分）。

    这是为 data_sources._try_refresh_iwencai_cookie_via_cdp() 提供的辅助，
    用于在通过 CDP 拿到新 cookie 后自动更新配置文件。
    """
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(
        yaml.safe_dump(cfg.secrets or {}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POOL_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "kline").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "candidate_pool").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "screener").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "analysis").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "pool_tracking").mkdir(parents=True, exist_ok=True)
