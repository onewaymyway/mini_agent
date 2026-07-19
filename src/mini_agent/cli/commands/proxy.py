"""
cli/commands/proxy.py — /proxy slash 命令处理

/proxy                          — 显示代理池状态(等价于 /proxy status)
/proxy status                   — 查看最近一次 refresh 的可用节点列表
/proxy refresh                  — 立即重新抓取订阅 + 验证节点 + 生成可用列表(阻塞,可能需要几十秒)
/proxy sources                  — 列出已配置的订阅源
/proxy sources add-mibei77      — 添加 mibei77.com 作为订阅源
/proxy sources add-discovered   — 接入 discovered_sources.json(由 agent/skill 自动发现地址写入)
/proxy integration               — 查看代理接入其它模块的开关状态(默认全部关闭)
/proxy integration set <key> <value>
                                — 设置一个开关,例如 /proxy integration set llm_use_proxy true

具体的抓取/验证/生成逻辑都在 scripts/proxy_ctl.py 里(独立于 agent 主循环之外维护，
详见该文件顶部注释)，这里只是把它包成一个 REPL 命令，方便在会话里直接触发，
不用切出去开终端跑脚本。

接入其它模块(见 src/mini_agent/proxy/integration.py 和 docs/proxy-pool-guide.md):
  - llm_use_proxy: 主 LLM 请求是否走代理池
  - web_search_use_proxy: web_search/抓取类工具是否走代理池并在被限流时轮换节点
  - fixed_entry_forwarder_enabled / fixed_entry_forwarder_port: 是否起固定端口转发给外部应用
  三个开关默认都是关闭的，"要不要让 agent 的流量走一个不受控的免费节点池"是需要用户
  显式打开的产品决策，不适合在这里替用户做主。都可以用 /proxy integration set 命令控制，
  不需要手动编辑 integration.json。
"""

from __future__ import annotations

import asyncio

import mini_agent.ui.renderer as R
from mini_agent.agent import Agent
from mini_agent.storage.paths import AgentPaths


def handle_proxy_cmd(args: list[str], agent: Agent) -> None:
    from scripts.proxy_ctl import (
        _do_refresh,
        _load_sources_config,
        _save_sources_config,
    )

    paths = AgentPaths()

    if not args or args[0] == "status":
        _print_status(paths)

    elif args[0] == "refresh":
        R.print_info("Refreshing proxy pool (fetch subscriptions + validate nodes)...")
        try:
            payload = asyncio.run(_do_refresh(paths, keep_alive=3, check_url="https://www.gstatic.com/generate_204", concurrency=8))
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.commands.proxy.handle_proxy_cmd')
            R.print_error(f"proxy refresh failed: {e}")
            return
        R.print_success(f"{payload['nodes_ok']} / {payload['nodes_found']} node(s) usable.")
        _print_status(paths)

    elif args[0] == "sources":
        if len(args) >= 2 and args[1] == "add-mibei77":
            entries = _load_sources_config(paths)
            if any(e.get("type") == "mibei77" for e in entries):
                R.print_info("mibei77 source already configured")
            else:
                entries.append({"type": "mibei77", "name": "mibei77", "page_url": "https://www.mibei77.com/"})
                _save_sources_config(paths, entries)
                R.print_success("added mibei77 source")
        elif len(args) >= 2 and args[1] == "add-discovered":
            entries = _load_sources_config(paths)
            if any(e.get("type") == "discovered" for e in entries):
                R.print_info("discovered source already configured")
            else:
                entries.append({"type": "discovered", "name": "discovered"})
                _save_sources_config(paths, entries)
                R.print_success(f"added discovered source (reads {paths.workdir_proxy_discovered_sources})")
        else:
            entries = _load_sources_config(paths)
            if not entries:
                R.print_info("(no subscription sources configured — try `/proxy sources add-mibei77`)")
            else:
                R.console.print("\n[bold]Configured proxy subscription sources:[/bold]")
                for e in entries:
                    R.console.print(f"  [cyan]{e.get('name', e.get('type'))}[/cyan]  {e}")

    elif args[0] == "integration":
        from mini_agent.proxy.integration import load_integration_config, save_integration_config

        if len(args) >= 2 and args[1] == "set":
            if len(args) < 4:
                R.print_error("Usage: /proxy integration set <key> <value>")
                return
            key, raw = args[2], args[3]
            if raw.lower() in ("true", "false"):
                value = raw.lower() == "true"
            elif raw.isdigit():
                value = int(raw)
            else:
                value = raw
            cfg = save_integration_config(paths, **{key: value})
            R.print_success(f"set {key} = {cfg.get(key)}")
        else:
            cfg = load_integration_config(paths)
            R.console.print("\n[bold]Proxy integration switches[/bold] (all default OFF):")
            for k, v in cfg.items():
                R.console.print(f"  [cyan]{k}[/cyan] = {v}")
            R.console.print("  (change with: /proxy integration set <key> <value>)")

    else:
        R.print_error(
            "Usage: /proxy | /proxy status | /proxy refresh | /proxy sources [add-mibei77|add-discovered] "
            "| /proxy integration [set <key> <value>]"
        )


def _print_status(paths: AgentPaths) -> None:
    import json
    import time

    p = paths.workdir_proxy_available_list
    if not p.exists():
        R.print_info("No proxy pool data yet — run `/proxy refresh` first.")
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    age_min = (time.time() - data.get("generated_at", 0)) / 60
    R.console.print(
        f"\n[bold]Proxy pool[/bold] — generated {age_min:.1f} min ago, "
        f"{data['nodes_ok']}/{data['nodes_found']} node(s) usable "
        f"(protocol breakdown: {data.get('protocol_breakdown', {})}):"
    )
    for n in data.get("available", [])[:10]:
        R.console.print(
            f"  [green]{n['latency_ms']:>6.1f}ms[/green]  {n['protocol']:<7}  {n['name']}  ({n['server']}:{n['port']})"
        )
