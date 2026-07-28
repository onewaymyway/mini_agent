"""external_input.builtin — 内置 ExternalInputSource 实现。

P4：`watch.py`（`WatchInputSource`）——RSS/JSON API/网页文本 diff 抓取 +
RuleEngine 规则匹配，是 ExternalInputSource 的第一个具体实现，验证网关
端到端闭环（source -> system_events -> IngestionPolicy -> /v1/inbox）。

导入本包即可触发 `@register_source("watch")` 装饰器完成注册；
`sources.yaml` 里配置 `type: watch` 的条目要求先 import 过本包
（或直接 `import mini_agent.external_input.builtin.watch`），
`GatewayPoller` 会在构造时统一 import 内置 source（见 poller.py 改动），
业务代码通常不需要手动 import 这里的模块。
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

__all__ = [
    "WatchInputSource",
    "WatchFetchError",
    "RuleEngine",
    "fetch_rss",
    "fetch_json_api",
    "fetch_html_text",
    "get_by_path",
]
