"""external_input/config.py — sources.yaml 加载（P2）

设计背景见 next_doc/external_input_gateway_design.md §3.3、§4。

只负责"把 .agent/external_input/sources.yaml 读成一组 SourceConfig"，
不负责调度（poller.py）、不负责路由（policy.py，P3）。policies.yaml 的
加载留到 P3 跟 IngestionPolicy 一起做，这里先只做 P2 路线图范围内的
sources.yaml。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

# 可选依赖：PyYAML（风格对齐 reminders/loader.py、wiki/parser.py 等已有模块——
# 项目里 yaml 不是强制依赖，缺失时应该优雅降级而不是 ImportError 直接炸穿）。
try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

DEFAULT_INTERVAL_SECONDS = 300  # sources.yaml 没写 interval_seconds 时的默认轮询间隔


@dataclass
class SourceConfig:
    """一个已注册 source 实例的配置（对应 sources.yaml 里的一条记录）。"""

    id: str
    type: str
    enabled: bool = True
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    params: dict = field(default_factory=dict)
    channel: str = ""
    """该 source 归属的分类频道（P7）。sources.yaml 里可选填 `channel:`；
    留空时 GatewayPoller 在发布事件前会回填成 `type`（即默认"一种来源
    类型 = 一个频道"），不强制使用者必须显式配置才能用上按频道分类。"""

    @staticmethod
    def from_dict(d: dict) -> "SourceConfig":
        source_id = str(d.get("id", "")).strip()
        source_type = str(d.get("type", "")).strip()
        if not source_id:
            raise ValueError(f"sources.yaml 条目缺少 id 字段: {d!r}")
        if not source_type:
            raise ValueError(f"sources.yaml 条目缺少 type 字段: {d!r}")
        interval = d.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL_SECONDS
        if interval <= 0:
            interval = DEFAULT_INTERVAL_SECONDS
        channel = str(d.get("channel", "") or "").strip()
        return SourceConfig(
            id=source_id,
            type=source_type,
            enabled=bool(d.get("enabled", True)),
            interval_seconds=interval,
            params=dict(d.get("params") or {}),
            channel=channel or source_type,
        )


class SourcesConfigError(Exception):
    """sources.yaml 存在但内容非法（YAML 语法错误 / 顶层结构不是预期形状）。
    单条记录缺字段不算这一类——那种情况按"跳过这一条、其余照常加载"处理，
    见 load_sources_config() 的说明。"""


def load_sources_config(paths: "AgentPaths") -> list[SourceConfig]:
    """读取 .agent/external_input/sources.yaml，返回 SourceConfig 列表。

    容错策略（与项目里配置类加载的一贯风格一致——单点配置错误不该拖垮
    整个网关）：
      - 文件不存在 → 返回空列表（网关此时没有任何 source，是合法状态）；
      - PyYAML 未安装 → 返回空列表（不是 fatal error，只是功能降级）；
      - 顶层不是 `{"sources": [...]}` 形状 → 抛 SourcesConfigError（这是
        明显的配置格式错误，应该让调用方知道，而不是静默吞掉）；
      - 某一条记录缺 id/type → 跳过这一条，其余条目正常加载（避免一条
        手滑的配置拖垮所有已经配好的 source）。
    """
    config_path = paths.external_input_sources_config
    if not config_path.exists():
        return []
    if not _HAS_YAML:
        return []

    try:
        raw = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise SourcesConfigError(f"sources.yaml 解析失败: {exc}") from exc

    if not isinstance(raw, dict) or "sources" not in raw or not isinstance(raw.get("sources"), list):
        raise SourcesConfigError(
            "sources.yaml 顶层结构应为 {sources: [...]}，"
            f"实际读到: {type(raw).__name__}"
        )

    configs: list[SourceConfig] = []
    for entry in raw.get("sources", []):
        if not isinstance(entry, dict):
            continue
        try:
            configs.append(SourceConfig.from_dict(entry))
        except ValueError:
            # 单条记录缺字段：跳过，不让整份配置加载失败。
            continue
    return configs


def get_source_config(paths: "AgentPaths", source_id: str) -> Optional[SourceConfig]:
    """按 id 查找单个 source 配置，供诊断命令/看板（P6）按需查询单个来源。"""
    for cfg in load_sources_config(paths):
        if cfg.id == source_id:
            return cfg
    return None
