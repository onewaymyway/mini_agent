"""代理池接入其它模块的统一开关。

设计原则(对应 docs/proxy-pool-guide.md "接入点 1/2/3"):
  - 每一路接入(主 LLM 请求 / web_search 抓取工具 / 固定端口转发给外部应用)
    都是独立开关,**默认全部关闭**——装了这个模块不会让任何请求悄悄开始走代理,
    "要不要让 agent 的流量走一个不受控的免费节点池"是用户显式决定的事,
    不应该由代码替用户做主。
  - 真正"拿到一个能用的本地 socks5 端口"这件事由调用方传入的 ProxyPool 实例负责
    (`pool.get_best_socks_url()` / `pool.get_rotating_socks_url()`),这个模块只
    负责"要不要问 pool 要"这一层开关判断,不重新实现取号逻辑、不直接碰
    available.json,避免和 pool.py 出现两套不一致的选节点逻辑。
  - 配置文件 <project_root>/.agent/proxy/integration.json,不存在时等价于全关。

用法示例(接入点 1: llm/providers/*.py 里创建 httpx client 的地方,pool 是启动时
建好、常驻后台刷新的一个 ProxyPool 实例):

    from mini_agent.proxy.integration import should_use_proxy_for_llm
    proxy_url = pool.get_best_socks_url() if should_use_proxy_for_llm(paths) else None
    client = httpx.AsyncClient(proxy=proxy_url, ...)   # None 时等价于不传 proxy,直连

用法示例(接入点 2: web_search/providers/*.py 里请求失败/被限流后换节点重试):

    from mini_agent.proxy.integration import should_use_proxy_for_web_search
    if should_use_proxy_for_web_search(paths):
        resp = await client.get(url, proxy=pool.get_rotating_socks_url())
        if resp.status_code in (403, 429):
            resp = await client.get(url, proxy=pool.get_rotating_socks_url())  # 换下一个重试

用法示例(接入点 3: 是否起 service.py 的固定端口转发给外部应用用):

    from mini_agent.proxy.integration import should_run_fixed_entry_forwarder
    enabled, port = should_run_fixed_entry_forwarder(paths)
    if enabled:
        await run_fixed_entry_forwarder(pool, listen_port=port)
"""

from __future__ import annotations

import json
from typing import Any

_DEFAULT_CONFIG: dict[str, Any] = {
    # 主 LLM 请求是否走代理池。默认关闭:正常情况下直连 Anthropic/OpenAI API
    # 大概率比经过一层不稳定的免费节点更快更稳,这是一个需要用户显式选择的取舍。
    "llm_use_proxy": False,
    # web_search / 抓取类工具是否走代理池并在被 403/429 限流时轮换节点。默认关闭。
    "web_search_use_proxy": False,
    # 是否起 service.py 里的固定端口转发,给非本项目的外部应用统一接入(接入点 3)。默认关闭。
    "fixed_entry_forwarder_enabled": False,
    "fixed_entry_forwarder_port": 1080,
}


def load_integration_config(paths) -> dict[str, Any]:
    p = paths.workdir_proxy_integration_config
    if not p.exists():
        return dict(_DEFAULT_CONFIG)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.proxy.integration.load_integration_config')
        return dict(_DEFAULT_CONFIG)
    merged = dict(_DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_integration_config(paths, **overrides: Any) -> dict[str, Any]:
    """更新并保存 integration.json 里的开关,例如:
        save_integration_config(paths, llm_use_proxy=True)
    未显式提供的开关维持原值(或默认关闭),不会被 overrides 之外的内容覆盖。
    """
    cfg = load_integration_config(paths)
    cfg.update(overrides)
    paths.ensure_workdir_proxy_dir()
    paths.workdir_proxy_integration_config.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return cfg


def should_use_proxy_for_llm(paths) -> bool:
    return bool(load_integration_config(paths).get("llm_use_proxy"))


def should_use_proxy_for_web_search(paths) -> bool:
    return bool(load_integration_config(paths).get("web_search_use_proxy"))


def should_run_fixed_entry_forwarder(paths) -> tuple[bool, int]:
    """返回 (是否应该起固定端口转发, 监听端口)。"""
    cfg = load_integration_config(paths)
    return bool(cfg.get("fixed_entry_forwarder_enabled")), int(cfg.get("fixed_entry_forwarder_port", 1080))
