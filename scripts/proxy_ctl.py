#!/usr/bin/env python3
"""scripts/proxy_ctl.py — 代理池控制脚本

独立于 mini_agent 主 agentic loop 之外的一个命令行工具，负责完整的
"订阅抓取 -> 节点验证 -> 生成可用代理列表 -> (可选)常驻转发服务" 流程。

之所以先做成独立脚本而不是直接塞进 agent 主循环：
  - 抓取/验证是分钟级的网络密集型操作，跟 agent 的对话轮次没有强关联，
    更适合按固定周期（cron / 定时任务）跑，而不是每次对话都重新验证一遍。
  - 独立脚本可以先跑通、先验证正确性，agent 侧的 /proxy 命令
    (见 src/mini_agent/cli/commands/proxy.py) 只是"读取这个脚本产出的
    可用列表 + 按需拉起本地转发"的薄封装，两边解耦，互不阻塞开发进度。

子命令：
    proxy_ctl.py sources list
    proxy_ctl.py sources add <name> <url>
    proxy_ctl.py sources add-mibei77
    proxy_ctl.py sources remove <name>
    proxy_ctl.py refresh [--keep-alive N] [--check-url URL]
    proxy_ctl.py status
    proxy_ctl.py serve [--listen-port 1080]

产出文件（默认路径 ~/.agent/proxy/，可通过 AgentPaths 统一管理）：
    sources.json    — 订阅源配置
    available.json  — 最近一次 refresh 后，按延迟排序的可用节点列表
    proxy.log       — 运行日志
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.storage.paths import AgentPaths  # noqa: E402
from mini_agent.proxy.subscription import (  # noqa: E402
    MiBei77Source,
    ProxyNode,
    URLSubscriptionSource,
    fetch_all,
)
from mini_agent.proxy.validator import validate_nodes  # noqa: E402


def _setup_logging(paths: AgentPaths) -> None:
    paths.ensure_global_proxy_dir()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(paths.global_proxy_log, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _load_sources_config(paths: AgentPaths) -> list[dict]:
    p = paths.global_proxy_sources_config
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _save_sources_config(paths: AgentPaths, entries: list[dict]) -> None:
    paths.ensure_global_proxy_dir()
    paths.global_proxy_sources_config.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _build_sources(entries: list[dict]):
    sources = []
    for e in entries:
        if e.get("type") == "mibei77":
            sources.append(MiBei77Source(e.get("page_url", "https://www.mibei77.com/")))
        elif e.get("type") == "url":
            sources.append(URLSubscriptionSource(e["name"], e["url"]))
    return sources


def cmd_sources_list(paths: AgentPaths, args) -> None:
    entries = _load_sources_config(paths)
    if not entries:
        print("(no subscription sources configured yet)")
        return
    for e in entries:
        print(f"- {e.get('name', e.get('type'))}: {e}")


def cmd_sources_add(paths: AgentPaths, args) -> None:
    entries = _load_sources_config(paths)
    entries.append({"type": "url", "name": args.name, "url": args.url})
    _save_sources_config(paths, entries)
    print(f"added source '{args.name}' -> {args.url}")


def cmd_sources_add_mibei77(paths: AgentPaths, args) -> None:
    entries = _load_sources_config(paths)
    if any(e.get("type") == "mibei77" for e in entries):
        print("mibei77 source already configured, skip")
        return
    entries.append({"type": "mibei77", "name": "mibei77", "page_url": "https://www.mibei77.com/"})
    _save_sources_config(paths, entries)
    print("added mibei77 source")


def cmd_sources_remove(paths: AgentPaths, args) -> None:
    entries = _load_sources_config(paths)
    new_entries = [e for e in entries if e.get("name") != args.name]
    _save_sources_config(paths, new_entries)
    print(f"removed source '{args.name}' (if it existed)")


async def _do_refresh(paths: AgentPaths, keep_alive: int, check_url: str, concurrency: int) -> dict:
    entries = _load_sources_config(paths)
    if not entries:
        logging.warning("no subscription sources configured; run `sources add` / `sources add-mibei77` first")
        return {"nodes_found": 0, "nodes_ok": 0, "available": []}

    sources = _build_sources(entries)
    logging.info("fetching from %d subscription source(s)...", len(sources))
    nodes = await fetch_all(sources)
    logging.info("parsed %d candidate node(s), validating (concurrency=%d)...", len(nodes), concurrency)

    results = await validate_nodes(nodes, concurrency=concurrency, check_url=check_url)
    ok_results = sorted(
        (r for r in results if r.ok and r.latency_ms is not None), key=lambda r: r.latency_ms
    )
    skipped_unsupported = sum(
        1 for r in results if not r.ok and r.error and "not supported" in r.error
    )
    logging.info(
        "validation done: %d ok / %d total (%d skipped: unsupported protocol e.g. vmess/vless)",
        len(ok_results), len(results), skipped_unsupported,
    )

    available = [
        {
            "protocol": r.node.protocol,
            "name": r.node.name,
            "server": r.node.server,
            "port": r.node.port,
            "params": r.node.params,
            "latency_ms": round(r.latency_ms, 1),
            "raw": r.node.raw,
        }
        for r in ok_results
    ]

    payload = {
        "generated_at": time.time(),
        "nodes_found": len(nodes),
        "nodes_ok": len(ok_results),
        "keep_alive_recommended": keep_alive,
        "available": available,
    }
    paths.ensure_global_proxy_dir()
    paths.global_proxy_available_list.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logging.info("wrote %s", paths.global_proxy_available_list)
    return payload


def cmd_refresh(paths: AgentPaths, args) -> None:
    payload = asyncio.run(
        _do_refresh(paths, args.keep_alive, args.check_url, args.concurrency)
    )
    print(f"\n{payload['nodes_ok']} / {payload['nodes_found']} node(s) usable.")
    for n in payload["available"][:10]:
        print(f"  [{n['latency_ms']:>6.1f}ms] {n['protocol']:<7} {n['name']} ({n['server']}:{n['port']})")


def cmd_status(paths: AgentPaths, args) -> None:
    p = paths.global_proxy_available_list
    if not p.exists():
        print("no available.json yet — run `refresh` first")
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    age = time.time() - data.get("generated_at", 0)
    print(f"generated {age/60:.1f} min ago — {data['nodes_ok']} / {data['nodes_found']} usable")
    for n in data["available"]:
        print(f"  [{n['latency_ms']:>6.1f}ms] {n['protocol']:<7} {n['name']} ({n['server']}:{n['port']})")


async def _do_serve(paths: AgentPaths, listen_port: int, keep_alive: int) -> None:
    from mini_agent.proxy.subscription import ProxyNode
    from mini_agent.proxy.local_proxy import start_local_proxy
    from mini_agent.proxy.service import run_fixed_entry_forwarder

    p = paths.global_proxy_available_list
    if not p.exists():
        logging.error("no available.json — run `refresh` first")
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    top = data["available"][:keep_alive]
    if not top:
        logging.error("available.json has no usable nodes — run `refresh` again")
        return

    running_list = []
    for n in top:
        node = ProxyNode(
            protocol=n["protocol"], name=n["name"], server=n["server"],
            port=n["port"], raw=n["raw"], params=n["params"],
        )
        try:
            running = await start_local_proxy(node)
            running_list.append(running)
            logging.info("activated %s (%s) on local port %d", node.name, node.protocol, running.local_port)
        except Exception as e:
            logging.warning("failed to activate %s: %s", node.name, e)

    if not running_list:
        logging.error("failed to activate any node")
        return

    class _StaticPool:
        """serve 子命令用的极简替身：固定用第一个（延迟最低）节点，不做刷新。"""

        def get_best_socks_url(self):
            return running_list[0].socks_url

    forwarder = await run_fixed_entry_forwarder(_StaticPool(), listen_port=listen_port)
    logging.info("fixed entry forwarder listening on 127.0.0.1:%d -> %s", listen_port, running_list[0].socks_url)
    logging.info("point your app's proxy setting to socks5://127.0.0.1:%d and it's done.", listen_port)
    async with forwarder:
        await forwarder.serve_forever()


def cmd_serve(paths: AgentPaths, args) -> None:
    try:
        asyncio.run(_do_serve(paths, args.listen_port, args.keep_alive))
    except KeyboardInterrupt:
        print("\nstopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxy_ctl", description="mini_agent 代理池控制脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p_src = sub.add_parser("sources", help="管理订阅源")
    src_sub = p_src.add_subparsers(dest="sources_action", required=True)
    src_sub.add_parser("list").set_defaults(func=cmd_sources_list)
    p_add = src_sub.add_parser("add", help="添加一个通用 URL 订阅源")
    p_add.add_argument("name")
    p_add.add_argument("url")
    p_add.set_defaults(func=cmd_sources_add)
    src_sub.add_parser("add-mibei77", help="添加 mibei77.com 订阅源").set_defaults(func=cmd_sources_add_mibei77)
    p_rm = src_sub.add_parser("remove", help="移除一个订阅源")
    p_rm.add_argument("name")
    p_rm.set_defaults(func=cmd_sources_remove)

    p_refresh = sub.add_parser("refresh", help="抓取订阅 + 验证节点 + 生成可用列表")
    p_refresh.add_argument("--keep-alive", type=int, default=3, help="建议常驻的节点数量（写入 available.json 供 serve 使用）")
    p_refresh.add_argument("--check-url", default="https://www.gstatic.com/generate_204")
    p_refresh.add_argument("--concurrency", type=int, default=8, help="并发验证的节点数上限")
    p_refresh.set_defaults(func=cmd_refresh)

    sub.add_parser("status", help="查看最近一次 refresh 的结果").set_defaults(func=cmd_status)

    p_serve = sub.add_parser("serve", help="常驻服务: 起固定端口转发到最佳节点")
    p_serve.add_argument("--listen-port", type=int, default=1080)
    p_serve.add_argument("--keep-alive", type=int, default=3)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    paths = AgentPaths()
    _setup_logging(paths)
    args.func(paths, args)


if __name__ == "__main__":
    main()
