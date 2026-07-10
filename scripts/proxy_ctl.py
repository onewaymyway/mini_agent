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
    proxy.log       — 运行日志（`_setup_logging()` 懒加载初始化，
                       独立脚本模式和 agent 内部调用 `_do_refresh()` 两条
                       路径都会写入，详见 `_setup_logging()` 的 docstring）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.storage.paths import AgentPaths  # noqa: E402
from mini_agent.proxy.subscription import (  # noqa: E402
    MiBei77Source,
    ProxyNode,
    URLSubscriptionSource,
    fetch_all,
)
from mini_agent.proxy.validator import UNSUPPORTED_MARKER, validate_nodes  # noqa: E402


_log = logging.getLogger("mini_agent.proxy_ctl")
_log.propagate = False  # 不冒泡到 root logger，避免和 errors.py 的全局错误
                         # 日志转发/agent 自身的 logging 配置互相干扰


def _setup_logging(paths: AgentPaths, *, include_console: bool = False) -> None:
    """
    配置 proxy.log 的写入 handler。

    [修复记录 2026-07] 原实现直接调 `logging.basicConfig(handlers=[...])`
    配置 root logger，这在两种调用场景下都有问题：
      1. 独立脚本模式（`python scripts/proxy_ctl.py refresh`）：本身没问题，
         但所有 `logging.info/warning/error(...)` 调用走的是 root logger，
         等于把这个脚本自己的运行日志和其它库的日志混在一起。
      2. 更严重的是 agent 内部调用（`/proxy refresh` 命令、agent 自主调用
         proxy 工具）：这两条路径都是直接 import `_do_refresh()` 函数调用，
         此时已经跑在主 agent 进程里，`errors.py::install_global_error_logging()`
         早就给 root logger 加过 handler 了——而 `logging.basicConfig()`
         的行为是"root logger 已有 handler 时默认整个调用是 no-op"（除非
         传 `force=True`），所以原来的 `_setup_logging()` 在这条路径下
         实际上什么都没做，`proxy.log` 也就永远不会被创建。

    修复方式：改成给一个专属的具名 logger（`mini_agent.proxy_ctl`，
    `propagate=False`）挂 handler，不碰 root logger，天然不受 agent 主进程
    是否已经配置过 logging 影响；用 `logger.handlers` 判断是否已经装过，
    保证多次调用（比如同一 agent 进程里多次 `/proxy refresh`）不会重复叠加
    handler。`include_console` 控制是否同时输出到 stdout——独立脚本模式下
    需要（用户在终端里看得到进度），agent 内部调用时默认关闭，避免刷屏。
    """
    if _log.handlers:
        return  # 已经装过 handler，幂等跳过
    paths.ensure_workdir_proxy_dir()
    _log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(paths.workdir_proxy_log, encoding="utf-8")
    file_handler.setFormatter(formatter)
    _log.addHandler(file_handler)
    if include_console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        _log.addHandler(stream_handler)


def _load_sources_config(paths: AgentPaths) -> list[dict]:
    p = paths.workdir_proxy_sources_config
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _save_sources_config(paths: AgentPaths, entries: list[dict]) -> None:
    paths.ensure_workdir_proxy_dir()
    paths.workdir_proxy_sources_config.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _build_sources(entries: list[dict], paths: AgentPaths | None = None):
    """按 sources.json 里每条配置的 "type" 从注册表里找对应工厂构造 SubscriptionSource。
    新增订阅源类型只需要在 subscription.py 里用 @register_source_type 注册,这里不用改。
    未知 type 会打印警告并跳过,不影响其它已识别的源。"""
    from mini_agent.proxy.subscription import build_source_from_entry

    sources = []
    for e in entries:
        src = build_source_from_entry(e, paths)
        if src is None:
            _log.warning("unknown/invalid source entry, skipped: %s", e)
            continue
        sources.append(src)
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


def cmd_sources_add_discovered(paths: AgentPaths, args) -> None:
    """接入 discovered_sources.json 里的地址(参见 DiscoveredSource):
    这些地址通常由 agent 里的一个"发现订阅源"skill 写入,而不是手动配置的。
    这里只是往 sources.json 里加一条 {"type": "discovered"} 声明"要读取这个文件",
    真正的地址列表始终由 discovered_sources.json 单独维护,不混进 sources.json。"""
    entries = _load_sources_config(paths)
    if any(e.get("type") == "discovered" for e in entries):
        print("discovered source already configured, skip")
        return
    entries.append({"type": "discovered", "name": "discovered"})
    _save_sources_config(paths, entries)
    print(f"added discovered source (reads {paths.workdir_proxy_discovered_sources})")


def cmd_sources_remove(paths: AgentPaths, args) -> None:
    entries = _load_sources_config(paths)
    new_entries = [e for e in entries if e.get("name") != args.name]
    _save_sources_config(paths, new_entries)
    print(f"removed source '{args.name}' (if it existed)")


async def _do_refresh(paths: AgentPaths, keep_alive: int, check_url: str, concurrency: int) -> dict:
    import collections

    # [修复] 懒加载初始化 proxy.log handler：这是 agent 内部两条调用路径
    # （/proxy refresh 命令、agent 自主调用的 proxy 工具）唯一会经过的函数，
    # 独立脚本模式下 main() 已经调用过一次 _setup_logging(include_console=True)，
    # 这里再调用是幂等的（_log.handlers 非空直接返回），不会重复加 handler。
    _setup_logging(paths)

    entries = _load_sources_config(paths)
    if not entries:
        _log.warning("no subscription sources configured; run `sources add` / `sources add-mibei77` first")
        return {"nodes_found": 0, "nodes_ok": 0, "available": []}

    sources = _build_sources(entries, paths)
    _log.info("fetching from %d subscription source(s)...", len(sources))
    nodes, fetch_stats = await fetch_all(sources, return_stats=True)
    _log.info(
        "fetched %d raw node(s) across sources %s, %d duplicate(s) removed -> %d unique node(s)",
        fetch_stats["raw_total"], fetch_stats["per_source"],
        fetch_stats["duplicates_removed"], fetch_stats["deduped_total"],
    )

    # 协议分布统计: 帮助判断"验证通过率低"到底是节点普遍失效,还是协议覆盖率不够
    proto_counts = collections.Counter(n.protocol for n in nodes)
    _log.info(
        "parsed %d candidate node(s), protocol breakdown: %s",
        len(nodes), dict(proto_counts),
    )

    # 不管是否验证通过,先把全量节点原样落盘一份,方便排查"协议支持了但仍连不上"
    # 还是"协议压根没支持"这两种情况,也方便以后换协议实现时重新跑验证不用重新抓订阅
    paths.ensure_workdir_proxy_dir()
    all_nodes_payload = {
        "generated_at": time.time(),
        "total": len(nodes),
        "protocol_breakdown": dict(proto_counts),
        "nodes": [
            {
                "protocol": n.protocol, "name": n.name, "server": n.server,
                "port": n.port, "params": n.params, "raw": n.raw,
            }
            for n in nodes
        ],
    }
    paths.workdir_proxy_all_nodes_list.write_text(
        json.dumps(all_nodes_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _log.info("wrote %s (全部解析出的节点,不论是否验证通过)", paths.workdir_proxy_all_nodes_list)

    from mini_agent.proxy.local_proxy import can_handle_pure_python
    from mini_agent.proxy.external_engine import needs_external_engine, singbox_available, xray_available

    engine_available = singbox_available() or xray_available()
    # 精确到"这个具体节点"能不能处理,而不是笼统按协议名判断
    # (比如同样是 vless,带 flow=xtls-rprx-vision 的处理不了,不带的可以)
    unsupported_nodes = [
        n for n in nodes
        if not can_handle_pure_python(n) and (needs_external_engine(n) and not engine_available)
    ]
    if unsupported_nodes:
        by_reason = collections.Counter(
            f"{n.protocol}"
            + ("+reality" if n.params.get("security") == "reality" else "")
            + ("+vision" if n.params.get("flow") else "")
            for n in unsupported_nodes
        )
        _log.info(
            "当前环境没有 sing-box/xray,以下特性的节点会被跳过(共 %d 个): %s ；"
            "见 docs/proxy-pool-guide.md 了解如何装 sing-box 覆盖这些协议",
            len(unsupported_nodes), dict(by_reason),
        )

    _log.info("validating (concurrency=%d)...", concurrency)

    def _on_progress(done: int, total: int, r) -> None:
        if r.ok:
            tag = f"OK {r.latency_ms:>6.1f}ms"
        elif r.error and UNSUPPORTED_MARKER in r.error:
            tag = "SKIP(unsupported)"
        else:
            err_lines = (r.error or "").splitlines()
            err = (err_lines[0] if err_lines else "unknown error")[:60]
            tag = f"FAIL {err}"
        print(f"  [{done}/{total}] {r.node.protocol:<7} {r.node.name[:30]:<30} -> {tag}", flush=True)

    results = await validate_nodes(
        nodes, concurrency=concurrency, check_url=check_url, on_progress=_on_progress
    )
    ok_results = sorted(
        (r for r in results if r.ok and r.latency_ms is not None), key=lambda r: r.latency_ms
    )
    skipped_unsupported = sum(
        1 for r in results if not r.ok and r.error and "需要外部引擎" in r.error
    )
    tested_but_failed = len(results) - len(ok_results) - skipped_unsupported
    _log.info(
        "validation done: %d ok / %d total (%d skipped:协议不支持, %d 实际测试但连不上:节点本身失效/被墙/延迟超时)",
        len(ok_results), len(results), skipped_unsupported, tested_but_failed,
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
        "protocol_breakdown": dict(proto_counts),
        "keep_alive_recommended": keep_alive,
        "available": available,
    }
    paths.workdir_proxy_available_list.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _log.info("wrote %s", paths.workdir_proxy_available_list)
    return payload


def cmd_refresh(paths: AgentPaths, args) -> None:
    payload = asyncio.run(
        _do_refresh(paths, args.keep_alive, args.check_url, args.concurrency)
    )
    print(f"\n{payload['nodes_ok']} / {payload['nodes_found']} node(s) usable.")
    for n in payload["available"][:10]:
        print(f"  [{n['latency_ms']:>6.1f}ms] {n['protocol']:<7} {n['name']} ({n['server']}:{n['port']})")


def cmd_integration_show(paths: AgentPaths, args) -> None:
    from mini_agent.proxy.integration import load_integration_config

    cfg = load_integration_config(paths)
    print("代理接入其它模块的开关(默认全部关闭,需要显式打开):")
    for k, v in cfg.items():
        print(f"  {k} = {v}")


def cmd_integration_set(paths: AgentPaths, args) -> None:
    from mini_agent.proxy.integration import save_integration_config

    raw = args.value
    if raw.lower() in ("true", "false"):
        value: Any = raw.lower() == "true"
    elif raw.isdigit():
        value = int(raw)
    else:
        value = raw
    cfg = save_integration_config(paths, **{args.key: value})
    print(f"set {args.key} = {cfg.get(args.key)}")


def cmd_status(paths: AgentPaths, args) -> None:
    p = paths.workdir_proxy_available_list
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

    p = paths.workdir_proxy_available_list
    if not p.exists():
        _log.error("no available.json — run `refresh` first")
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    top = data["available"][:keep_alive]
    if not top:
        _log.error("available.json has no usable nodes — run `refresh` again")
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
            _log.info("activated %s (%s) on local port %d", node.name, node.protocol, running.local_port)
        except Exception as e:
            _log.warning("failed to activate %s: %s", node.name, e)

    if not running_list:
        _log.error("failed to activate any node")
        return

    class _StaticPool:
        """serve 子命令用的极简替身：固定用第一个（延迟最低）节点，不做刷新。"""

        def get_best_socks_url(self):
            return running_list[0].socks_url

    forwarder = await run_fixed_entry_forwarder(_StaticPool(), listen_port=listen_port)
    _log.info("fixed entry forwarder listening on 127.0.0.1:%d -> %s", listen_port, running_list[0].socks_url)
    _log.info("point your app's proxy setting to socks5://127.0.0.1:%d and it's done.", listen_port)
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
    src_sub.add_parser(
        "add-discovered", help="接入 discovered_sources.json(由 agent/skill 自动发现地址写入)"
    ).set_defaults(func=cmd_sources_add_discovered)
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

    p_int = sub.add_parser("integration", help="管理代理接入其它模块的开关(默认全部关闭)")
    int_sub = p_int.add_subparsers(dest="integration_action", required=True)
    int_sub.add_parser("show").set_defaults(func=cmd_integration_show)
    p_int_set = int_sub.add_parser("set", help="设置一个开关,例如: integration set llm_use_proxy true")
    p_int_set.add_argument("key")
    p_int_set.add_argument("value")
    p_int_set.set_defaults(func=cmd_integration_set)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    paths = AgentPaths()
    _setup_logging(paths, include_console=True)
    args.func(paths, args)


if __name__ == "__main__":
    main()
