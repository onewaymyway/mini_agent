"""info_hub 命令行入口。

用法（在项目根目录执行）：
  python cli.py list                 列出信息源
  python cli.py validate             校验全部信息源配置
  python cli.py run <source_id>      立即抓取一个信息源
  python cli.py login <site>         打开有头浏览器登录站点（登录态保存在共享 profile）
  python cli.py login-status         查看各站点登录状态
  python cli.py report <plan_id>     立即生成一份报告
  python cli.py tick                 执行一次调度：运行到期的信息源并生成到期的报告
  python cli.py serve                启动看板（http://127.0.0.1:8765）并后台定时调度
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adapters.registry import list_adapters
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources


def cmd_list(settings) -> int:
    loaded = load_sources(settings)
    if not loaded.specs and not loaded.errors:
        print("（暂无信息源）")
        return 0
    for sid, spec in sorted(loaded.specs.items()):
        flag = "启用" if spec.enabled else "停用"
        print(f"{sid:20s} {flag}  [{spec.category}] {spec.kind:10s} {spec.adapter:10s} {spec.name}")
    for sid, errs in sorted(loaded.errors.items()):
        print(f"{sid:20s} 配置错误: {'; '.join(errs)}")
    return 0


def cmd_validate(settings) -> int:
    loaded = load_sources(settings)
    print(f"已加载 {len(loaded.specs)} 个信息源，{len(loaded.errors)} 个有错误")
    print(f"可用适配器: {', '.join(list_adapters())}")
    for sid, errs in sorted(loaded.errors.items()):
        for err in errs:
            print(f"  [{sid}] {err}")
    return 1 if loaded.errors else 0


def cmd_run(settings, source_id: str) -> int:
    loaded = load_sources(settings)
    if source_id in loaded.errors:
        print(f"信息源 {source_id} 配置有误: {'; '.join(loaded.errors[source_id])}")
        return 1
    spec = loaded.specs.get(source_id)
    if spec is None:
        print(f"未找到信息源 {source_id}")
        return 1
    result = run_source(spec, settings)
    if result.ok:
        print(f"[{source_id}] 成功，{result.item_count} 条，快照: {result.raw_path}")
        return 0
    print(f"[{source_id}] 失败: {result.error}")
    return 1


def cmd_login(settings, site: str) -> int:
    from core.browser_session import BrowserSession
    from core.settings import load_sites

    sites = load_sites(settings.paths.root)
    cfg = sites.get(site)
    if cfg is None:
        print(f"未在 config/sites.yaml 中定义站点 {site}")
        return 1
    session = BrowserSession(settings.paths.browser_profile_dir,
                             settings.paths.root / "browser" / "login_status.json")
    status = session.interactive_login(
        site,
        login_url=cfg["login_url"],
        check_url=cfg.get("check_url", cfg["login_url"]),
        expired_pattern=cfg.get("expired_pattern"),
    )
    if status == "logged_in":
        print(f"[{site}] 登录成功，登录态已保存到共享 profile")
        return 0
    print(f"[{site}] 验证失败：登录未生效，请重试")
    return 1


def cmd_login_status(settings) -> int:
    from core.browser_session import BrowserSession

    session = BrowserSession(settings.paths.browser_profile_dir,
                             settings.paths.root / "browser" / "login_status.json")
    data = session.login_status()
    if not data:
        print("（尚无登录记录）")
    for site, entry in sorted(data.items()):
        print(f"{site:20s} {entry.get('status', '-'):10s} 最近登录 {entry.get('last_login_at', '-')}")
    return 0


def cmd_report(settings, plan_id: str) -> int:
    from datetime import datetime
    from core.digest import write_report

    plan = next((p for p in settings.reports if p.get("id") == plan_id), None)
    if plan is None:
        print(f"未在 config/settings.yaml 中定义报告计划 {plan_id}")
        return 1
    paths = write_report(settings, plan, datetime.now().astimezone())
    print(f"报告已生成: {paths['markdown']}")
    return 0


def cmd_tick(settings) -> int:
    from core.tick import tick

    rep = tick(settings)
    print(f"运行信息源: {', '.join(rep.ran_sources) or '无'}")
    if rep.failed_sources:
        print(f"失败信息源: {', '.join(rep.failed_sources)}")
    if rep.skipped_sources:
        print(f"间隔无法解析，已跳过: {', '.join(rep.skipped_sources)}")
    print(f"生成报告: {', '.join(rep.generated_reports) or '无'}")
    return 0


def cmd_serve(settings) -> int:
    import threading
    from server.app import make_server, start_tick_loop

    stop = threading.Event()
    try:
        server = make_server(settings)
    except ValueError as exc:
        print(exc)
        return 1
    start_tick_loop(settings, stop, settings.tick_interval_minutes * 60)
    host, port = server.server_address[:2]
    print(f"看板已启动：http://{host}:{port}  （每 {settings.tick_interval_minutes} 分钟调度一次，Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="info_hub")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出信息源")
    sub.add_parser("validate", help="校验信息源配置")
    p_run = sub.add_parser("run", help="立即抓取一个信息源")
    p_run.add_argument("source_id")
    p_login = sub.add_parser("login", help="打开有头浏览器登录站点")
    p_login.add_argument("site")
    sub.add_parser("login-status", help="查看登录状态")
    p_report = sub.add_parser("report", help="立即生成一份报告")
    p_report.add_argument("plan_id")
    sub.add_parser("tick", help="执行一次调度")
    sub.add_parser("serve", help="启动看板与后台调度")
    args = parser.parse_args(argv)

    settings = load_settings(root)
    if args.command == "list":
        return cmd_list(settings)
    if args.command == "validate":
        return cmd_validate(settings)
    if args.command == "run":
        return cmd_run(settings, args.source_id)
    if args.command == "login":
        return cmd_login(settings, args.site)
    if args.command == "login-status":
        return cmd_login_status(settings)
    if args.command == "report":
        return cmd_report(settings, args.plan_id)
    if args.command == "tick":
        return cmd_tick(settings)
    if args.command == "serve":
        return cmd_serve(settings)
    return 2


if __name__ == "__main__":
    sys.exit(main())
