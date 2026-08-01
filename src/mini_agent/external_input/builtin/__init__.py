"""external_input.builtin — 内置 ExternalInputSource 实现。

P4：`watch.py`（`WatchInputSource`）——RSS/JSON API/网页文本 diff 抓取 +
RuleEngine 规则匹配，是 ExternalInputSource 的第一个具体实现，验证网关
端到端闭环（source -> system_events -> IngestionPolicy -> /v1/inbox）。

P7：`weather.py`（`WeatherInputSource`）——基于 Open-Meteo 免费预报 API
监控降雨概率/极端气温阈值，是"如何接入一种新的外部输入来源"的示例实现，
`channel` 默认落在 "weather" 频道。

外部数据知识化计划 P5（可选）：`arxiv_api.py`（`ArxivApiInputSource`）/
`github_release.py`（`GithubReleaseInputSource`）——比 `watch` 的通用
fetcher 信息密度更高的两种"追踪技术动态"专用来源，直接对接 arXiv 官方
API / GitHub Releases API，产生结构化的 title+abstract / tag+release note，
同样走 `channel: agent_watch`，复用 P1 `knowledge_extractor.py` 已有的
抽取管道，不需要改动下游消费链路。

导入本包即可触发 `@register_source(...)` 装饰器完成注册；
`sources.yaml` 里配置 `type: watch`/`type: weather`/`type: arxiv_api`/
`type: github_release` 的条目要求先 import 过本包（或直接 import 对应
模块），`GatewayPoller` 会在构造时统一 import 内置 source（见 poller.py
改动），业务代码通常不需要手动 import 这里的模块。
"""

from mini_agent.external_input.builtin.watch import (
    RuleEngine,
    WatchFetchError,
    WatchInputSource,
    fetch_html_text,
    fetch_json_api,
    fetch_rss,
    get_by_path,
)
from mini_agent.external_input.builtin.weather import (
    WeatherFetchError,
    WeatherInputSource,
)
from mini_agent.external_input.builtin.arxiv_api import (
    ArxivApiInputSource,
    ArxivFetchError,
    fetch_arxiv_entries,
)
from mini_agent.external_input.builtin.github_release import (
    GithubReleaseInputSource,
    GithubReleaseFetchError,
    fetch_releases,
)

__all__ = [
    "WatchInputSource",
    "WatchFetchError",
    "RuleEngine",
    "fetch_rss",
    "fetch_json_api",
    "fetch_html_text",
    "get_by_path",
    "WeatherInputSource",
    "WeatherFetchError",
    "ArxivApiInputSource",
    "ArxivFetchError",
    "fetch_arxiv_entries",
    "GithubReleaseInputSource",
    "GithubReleaseFetchError",
    "fetch_releases",
]
