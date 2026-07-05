"""
cli/commands/proxy.py — /proxy slash 命令处理

/proxy                    — 显示代理池状态(等价于 /proxy status)
/proxy status             — 查看最近一次 refresh 的可用节点列表
/proxy refresh            — 立即重新抓取订阅 + 验证节点 + 生成可用列表(阻塞,可能需要几十秒)
/proxy sources            — 列出已配置的订阅源
/proxy sources add-mibei77 — 添加 mibei77.com 作为订阅源

具体的抓取/验证/生成逻辑都在 scripts/proxy_ctl.py 里(独立于 agent 主循环之外维护，
详见该文件顶部注释)，这里只是把它包成一个 REPL 命令，方便在会话里直接触发，
不用切出去开终端跑脚本。

后续集成方向(暂未接入，先留好扩展点):
  - agent.llm_client 的 httpx client 初始化处 (llm/client_pool.py 或各 provider 文件)
    可以读取 AgentPaths().workdir_proxy_available_list 里延迟最低的节点，
    在配置里加一个 "use_proxy_pool": true 开关来控制是否启用。
  - web_search/ 下的 provider 可以在被限流/屏蔽时，从 available.json 里换下一个节点重试。
  这两处目前还是手动接线，因为要不要默认启用代理是产品决策，不适合在这里替用户做主。
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
        else:
            entries = _load_sources_config(paths)
            if not entries:
                R.print_info("(no subscription sources configured — try `/proxy sources add-mibei77`)")
            else:
                R.console.print("\n[bold]Configured proxy subscription sources:[/bold]")
                for e in entries:
                    R.console.print(f"  [cyan]{e.get('name', e.get('type'))}[/cyan]  {e}")

    else:
        R.print_error("Usage: /proxy | /proxy status | /proxy refresh | /proxy sources [add-mibei77]")


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
