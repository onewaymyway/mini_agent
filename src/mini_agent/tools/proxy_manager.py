"""
tools/proxy_manager.py — 代理池管理工具（供 agent 自己调用，而不只是人在 CLI/REPL 里操作）

设计动机：
  - `scripts/proxy_ctl.py`(独立脚本)和 `/proxy` slash 命令(人在 REPL 里手动敲)之外，
    agent 自己有时也需要在推理过程中查看/控制代理池状态——比如判断"要不要为了抓取某个
    经常超时的站点而临时打开 web_search_use_proxy"，或者在完成一批订阅源发现后主动
    触发一次 refresh。这些操作需要暴露成工具（tool_use），模型才能在对话里直接调用，
    而不是只能提示用户去敲命令。
  - 所有开关默认关闭，工具本身不会替用户做主；agent 打开开关也需要在 reason 里说明原因，
    便于事后审计（同 skill_activate 的设计）。
  - 抓取/验证是分钟级的网络密集型操作，proxy_refresh 工具会阻塞到完成，模型调用前应该
    告知用户可能需要等待。

工具列表：
  proxy_status()                        — 查看最近一次 refresh 的可用节点摘要
  proxy_refresh()                       — 立即重新抓取订阅 + 验证节点（阻塞）
  proxy_sources_list()                  — 列出已配置的订阅源
  proxy_sources_add(type, name, url)    — 添加一个订阅源（url / mibei77 / discovered）
  proxy_integration_get()               — 查看代理接入其它模块的开关状态
  proxy_integration_set(key, value, reason) — 修改一个开关（默认关闭，需说明原因）

注册方式（在 agent 初始化时调用）：
  from mini_agent.tools.proxy_manager import register_proxy_tools
  register_proxy_tools(registry, paths)
"""

from __future__ import annotations

import json

import mini_agent.ui.renderer as R
from . import ToolRegistry


def register_proxy_tools(registry: ToolRegistry, paths) -> None:
    """将代理池管理工具注册到指定 registry。paths 是 AgentPaths(cfg.project_root) 实例，
    以闭包方式绑定，与 skill_manager.py 里绑定 skill_loader 的方式一致。"""

    # ── proxy_status ──────────────────────────────────────────────────────────

    def proxy_status() -> str:
        """
        Show the proxy pool status from the most recent `refresh`: how many nodes are
        usable, when it was last refreshed, and the top usable nodes sorted by latency.
        Call this before proxy_refresh if you just want to check existing data without
        re-fetching (refresh is slow — this is instant).
        """
        p = paths.workdir_proxy_available_list
        if not p.exists():
            return json.dumps({"status": "no_data", "message": "No proxy pool data yet — call proxy_refresh first."})
        import time as _time

        data = json.loads(p.read_text(encoding="utf-8"))
        return json.dumps(
            {
                "status": "ok",
                "generated_minutes_ago": round((_time.time() - data.get("generated_at", 0)) / 60, 1),
                "nodes_ok": data.get("nodes_ok"),
                "nodes_found": data.get("nodes_found"),
                "protocol_breakdown": data.get("protocol_breakdown", {}),
                "top_nodes": data.get("available", [])[:10],
            },
            ensure_ascii=False,
            indent=2,
        )

    registry.register_fn(
        fn=proxy_status,
        name="proxy_status",
        description=(
            "Show the proxy pool's last refresh result (usable node count, latency-sorted "
            "top nodes, protocol breakdown). Instant, does not re-fetch. Use proxy_refresh "
            "if the data looks stale or missing."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        requires_approval=False,
        group="proxy",
    )

    # ── proxy_refresh ─────────────────────────────────────────────────────────

    def proxy_refresh(concurrency: int = 8) -> str:
        """
        Re-fetch all configured subscription sources, deduplicate, validate every node by
        actually opening a local proxy and making a real HTTP request through it, then
        write the ranked usable list. This is network-intensive and can take from several
        seconds to a few minutes depending on how many nodes are found — call proxy_status
        first if recent data would be good enough.
        """
        import asyncio

        from scripts.proxy_ctl import _do_refresh

        R.print_info("[proxy_refresh] fetching subscriptions + validating nodes (may take a while)...")
        try:
            payload = asyncio.run(
                _do_refresh(paths, keep_alive=3, check_url="https://www.gstatic.com/generate_204", concurrency=concurrency)
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps({"status": "error", "error": str(e)})
        return json.dumps(
            {
                "status": "ok",
                "nodes_ok": payload["nodes_ok"],
                "nodes_found": payload["nodes_found"],
                "top_nodes": payload["available"][:10],
            },
            ensure_ascii=False,
            indent=2,
        )

    registry.register_fn(
        fn=proxy_refresh,
        name="proxy_refresh",
        description=(
            "Re-fetch all subscription sources, deduplicate, and actually test every node "
            "over a real HTTP request (not just TCP connect). Blocking and can take a while "
            "for large subscriptions. Only call this when you actually need fresh proxy data "
            "(e.g. proxy_status shows no data, or it's clearly stale)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "concurrency": {
                    "type": "integer",
                    "description": "Max number of nodes validated in parallel (default 8). Lower it if the machine seems overloaded.",
                },
            },
            "required": [],
        },
        requires_approval=False,
        group="proxy",
    )

    # ── proxy_sources_list / proxy_sources_add ───────────────────────────────

    def proxy_sources_list() -> str:
        """
        List all configured subscription sources (where nodes are fetched from).
        Call this before proxy_sources_add to avoid adding a duplicate.
        """
        from scripts.proxy_ctl import _load_sources_config

        entries = _load_sources_config(paths)
        return json.dumps({"sources": entries, "count": len(entries)}, ensure_ascii=False, indent=2)

    registry.register_fn(
        fn=proxy_sources_list,
        name="proxy_sources_list",
        description="List all configured proxy subscription sources.",
        input_schema={"type": "object", "properties": {}, "required": []},
        requires_approval=False,
        group="proxy",
    )

    def proxy_sources_add(type: str, name: str = "", url: str = "") -> str:
        """
        Add a subscription source. type="url" needs both name and url (a generic subscription
        link). type="mibei77" adds the mibei77.com page scraper (name/url ignored). type=
        "discovered" wires in discovered_sources.json, which is meant to be populated by a
        separate "find subscription sources" skill via DiscoveredSource.append_entry() rather
        than by this tool — use this only to register that the file should be read.
        """
        from scripts.proxy_ctl import _load_sources_config, _save_sources_config

        entries = _load_sources_config(paths)
        if type == "mibei77":
            if any(e.get("type") == "mibei77" for e in entries):
                return json.dumps({"status": "already_configured"})
            entries.append({"type": "mibei77", "name": "mibei77", "page_url": "https://www.mibei77.com/"})
        elif type == "discovered":
            if any(e.get("type") == "discovered" for e in entries):
                return json.dumps({"status": "already_configured"})
            entries.append({"type": "discovered", "name": "discovered"})
        elif type == "url":
            if not name or not url:
                return json.dumps({"error": "type='url' requires both 'name' and 'url'"})
            entries.append({"type": "url", "name": name, "url": url})
        else:
            return json.dumps({"error": f"unknown type '{type}', expected one of: url, mibei77, discovered"})
        _save_sources_config(paths, entries)
        return json.dumps({"status": "added", "sources": entries}, ensure_ascii=False, indent=2)

    registry.register_fn(
        fn=proxy_sources_add,
        name="proxy_sources_add",
        description=(
            "Add a proxy subscription source. type must be one of: 'url' (generic subscription "
            "link, requires name+url), 'mibei77' (scrapes mibei77.com's daily post), 'discovered' "
            "(reads discovered_sources.json, populated separately). Call proxy_sources_list first "
            "to avoid duplicates."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["url", "mibei77", "discovered"]},
                "name": {"type": "string", "description": "Source name, required for type='url'."},
                "url": {"type": "string", "description": "Subscription URL, required for type='url'."},
            },
            "required": ["type"],
        },
        requires_approval=False,
        group="proxy",
    )

    # ── proxy_integration_get / proxy_integration_set ────────────────────────

    def proxy_integration_get() -> str:
        """
        Show whether the agent's own requests (main LLM calls, web_search/fetch tools) are
        configured to route through the proxy pool, and whether the fixed-entry forwarder for
        external apps is running. All switches default to OFF.
        """
        from mini_agent.proxy.integration import load_integration_config

        return json.dumps(load_integration_config(paths), ensure_ascii=False, indent=2)

    registry.register_fn(
        fn=proxy_integration_get,
        name="proxy_integration_get",
        description=(
            "Show the current proxy integration switches: llm_use_proxy, web_search_use_proxy, "
            "fixed_entry_forwarder_enabled/port. All default to False/off."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        requires_approval=False,
        group="proxy",
    )

    def proxy_integration_set(key: str, value, reason: str) -> str:
        """
        Toggle one proxy integration switch. Because these switches change how the agent's own
        traffic is routed (e.g. sending LLM requests through an untrusted free proxy node),
        always explain in `reason` why this change is needed. Turning llm_use_proxy on is
        rarely a good idea — direct connections to Anthropic/OpenAI are normally faster and
        more reliable than a free public node.
        """
        from mini_agent.proxy.integration import save_integration_config

        if isinstance(value, str):
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif value.isdigit():
                value = int(value)
        cfg = save_integration_config(paths, **{key: value})
        R.print_info(f"[proxy_integration_set] {key} = {cfg.get(key)} (reason: {reason})")
        return json.dumps({"status": "ok", "key": key, "value": cfg.get(key), "config": cfg}, ensure_ascii=False, indent=2)

    registry.register_fn(
        fn=proxy_integration_set,
        name="proxy_integration_set",
        description=(
            "Toggle a proxy integration switch (llm_use_proxy / web_search_use_proxy / "
            "fixed_entry_forwarder_enabled / fixed_entry_forwarder_port). All default to off; "
            "flipping one changes real traffic routing, so always give a reason. Call "
            "proxy_integration_get first to see current values and avoid redundant calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": [
                        "llm_use_proxy",
                        "web_search_use_proxy",
                        "fixed_entry_forwarder_enabled",
                        "fixed_entry_forwarder_port",
                    ],
                },
                "value": {
                    "description": "New value: true/false for the boolean switches, an integer port for fixed_entry_forwarder_port.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this switch needs to change right now.",
                },
            },
            "required": ["key", "value", "reason"],
        },
        requires_approval=False,
        group="proxy",
    )
