"""
cli/commands/behavior.py — /behavior slash 命令处理

用法：
  /behavior status                 查看总开关/各采集器状态
  /behavior on                     打开总开关（不会自动打开任何子采集器）
  /behavior off                    关闭总开关（停止所有采集器）
  /behavior enable  <collector>    打开某个采集器
  /behavior disable <collector>    关闭某个采集器
  /behavior token                  查看/生成外部上报（浏览器插件/git/终端）用的 token
  /behavior recent [n]             查看最近 n 条事件（默认 20）
  /behavior clear                  清空所有已采集事件
  /behavior browser start          启动专用调试浏览器（CDP 方案）并开始采集
  /behavior browser stop [--kill]  停止采集；加 --kill 则同时关闭浏览器进程
  /behavior browser status         查看专用浏览器/CDP 连接状态
  /behavior git install <repo>     在指定仓库安装 commit/checkout 上报 hook
  /behavior terminal install       打印/追加 shell hook 片段（bash/zsh，命令级上报，敏感命令自动跳过）
  /behavior mobile android|ios     打印手机端（Tasker/快捷指令）接入模板
  /behavior report [today|<date>]  查看/生成"工作与生活画像"日报（分析层）

采集器名称: active_window / idle / browser_report / mobile_report / clipboard_meta / cdp_browser /
            git_activity / terminal_command / now_playing / app_lifecycle / daily_analysis
"""

from __future__ import annotations

import mini_agent.ui.renderer as R
from mini_agent.perception.behavior import get_manager


_COLLECTORS = (
    "active_window", "idle", "browser_report", "mobile_report", "clipboard_meta", "cdp_browser",
    "git_activity", "terminal_command", "now_playing", "app_lifecycle", "daily_analysis",
)


def _api_base_and_token(cfg) -> tuple[str, str]:
    """从 AppConfig 拼出本机 API 地址和 token，供 git/terminal hook 脚本使用。"""
    host = getattr(cfg.http, "host", "127.0.0.1") if cfg else "127.0.0.1"
    port = getattr(cfg.http, "port", 8765) if cfg else 8765
    api_token = ""
    if cfg:
        key_path = cfg.project_root / ".agent" / "agent_api.key"
        if key_path.exists():
            api_token = key_path.read_text(encoding="utf-8").strip()
        elif cfg.http.api_token:
            api_token = cfg.http.api_token
    report_url = f"http://{host}:{port}/v1/perception/report"
    return report_url, api_token


def handle_behavior_cmd(args: list[str], cfg=None) -> None:
    if not args:
        R.print_error("Usage: /behavior status|on|off|enable|disable|token|recent|clear|browser|git|terminal|report")
        return

    mgr = get_manager()
    action = args[0].lower()

    if action == "status":
        st = mgr.status()
        R.console.print(f"\n[bold]用户行为感知系统[/bold]  总开关: "
                         f"{'[green]开[/green]' if st['enabled'] else '[dim]关[/dim]'}")
        for name in _COLLECTORS:
            enabled = getattr(mgr.config, f"{name}_enabled")
            mark = "[green]✓[/green]" if enabled else " "
            if name in ("git_activity", "terminal_command", "daily_analysis", "mobile_report"):
                # 这三个不是本机常驻线程（外部上报 / 定时任务），没有 running/stopped 概念
                state = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
            else:
                running = st["collectors"].get(name, {}).get("running", False)
                state = "[green]running[/green]" if running else "[dim]stopped[/dim]"
            R.console.print(f"  {mark} {name:<18} {state}")
        R.console.print(
            f"\n  [dim]保留 {mgr.config.retention_days} 天 · "
            f"标题脱敏={'是' if mgr.config.redact_window_title else '否'} · "
            f"URL脱敏={'是' if mgr.config.redact_url_path else '否'}[/dim]\n"
        )
        return

    if action == "on":
        mgr.set_enabled(True)
        R.print_success("行为感知总开关已打开（子采集器仍需分别 /behavior enable <name>）")
        return

    if action == "off":
        mgr.set_enabled(False)
        R.print_success("行为感知总开关已关闭，所有采集器已停止")
        return

    if action in ("enable", "disable"):
        if len(args) < 2 or args[1] not in _COLLECTORS:
            R.print_error(f"Usage: /behavior {action} <{'|'.join(_COLLECTORS)}>")
            return
        name = args[1]
        mgr.set_collector_enabled(name, action == "enable")
        if action == "enable" and not mgr.config.enabled:
            R.print_info("提示：总开关当前是关闭状态，先执行 /behavior on 才会真正开始采集")
        R.print_success(f"{name} 已{'开启' if action == 'enable' else '关闭'}")
        return

    if action == "token":
        token = mgr.get_report_token()
        R.console.print(f"\n浏览器插件上报 token（配置到插件里）:\n  [cyan]{token}[/cyan]\n")
        return

    if action == "recent":
        n = 20
        if len(args) > 1 and args[1].isdigit():
            n = int(args[1])
        events = mgr.query(limit=n)
        if not events:
            R.console.print("[dim]暂无事件[/dim]")
            return
        import time as _t
        for e in events:
            ts = _t.strftime("%H:%M:%S", _t.localtime(e.timestamp))
            extra = e.app_name or e.domain or ""
            dur = f" ({e.duration_sec}s)" if e.duration_sec else ""
            R.console.print(f"  [dim]{ts}[/dim] [cyan]{e.source}[/cyan] {e.event_type} {extra}{dur}")
        return

    if action == "clear":
        n = mgr.clear()
        R.print_success(f"已清空 {n} 个事件文件")
        return

    if action == "browser":
        _handle_browser_sub(args[1:], mgr)
        return

    if action == "git":
        _handle_git_sub(args[1:], mgr, cfg)
        return

    if action == "terminal":
        _handle_terminal_sub(args[1:], mgr, cfg)
        return

    if action == "mobile":
        _handle_mobile_sub(args[1:], mgr, cfg)
        return

    if action == "report":
        _handle_report_sub(args[1:], mgr)
        return

    R.print_error(f"未知子命令: {action}")


def _handle_git_sub(args: list[str], mgr, cfg) -> None:
    sub = args[0].lower() if args else ""
    if sub != "install" or len(args) < 2:
        R.print_error("Usage: /behavior git install <repo_path>")
        return
    if not mgr.config.enabled or not mgr.config.git_activity_enabled:
        R.print_error("请先执行 /behavior on 和 /behavior enable git_activity")
        return

    from pathlib import Path
    report_url, api_token = _api_base_and_token(cfg)
    if not api_token:
        R.print_error("找不到本机 API token（需要先启动过一次 HTTP API，或在配置里设置 http.api_token）")
        return
    try:
        written = mgr.install_git_hooks(Path(args[1]).expanduser().resolve(), report_url, api_token)
    except Exception as e:
        R.print_error(f"安装失败: {e}")
        return
    R.print_success(f"已安装 git hook: {', '.join(str(p) for p in written)}")
    R.print_info("之后在这个仓库 commit / checkout 会自动上报（只报分支名+commit概要，不报 diff 内容）")


def _handle_terminal_sub(args: list[str], mgr, cfg) -> None:
    sub = args[0].lower() if args else "show"
    if not mgr.config.enabled or not mgr.config.terminal_command_enabled:
        R.print_error("请先执行 /behavior on 和 /behavior enable terminal_command")
        return

    report_url, api_token = _api_base_and_token(cfg)
    if not api_token:
        R.print_error("找不到本机 API token（需要先启动过一次 HTTP API，或在配置里设置 http.api_token）")
        return
    snippet = mgr.get_shell_hook_snippet(report_url, api_token)

    if sub == "show":
        R.console.print(snippet)
        R.print_info("把上面这段追加到 ~/.bashrc 或 ~/.zshrc 末尾并重新打开终端即可生效；"
                      "或执行 /behavior terminal install 自动追加到当前 shell 的 rc 文件。")
        return

    if sub == "install":
        import os
        from pathlib import Path
        shell = os.environ.get("SHELL", "")
        rc_name = ".zshrc" if "zsh" in shell else ".bashrc"
        rc_path = Path.home() / rc_name
        marker = "mini_agent behavior perception (terminal_command)"
        existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
        if marker in existing:
            R.print_info(f"{rc_path} 里已经安装过，跳过。如需更新 token，先手动删除对应片段再重新执行。")
            return
        with rc_path.open("a", encoding="utf-8") as f:
            f.write("\n" + snippet + "\n")
        R.print_success(f"已追加到 {rc_path}，重新打开终端（或 source {rc_path}）后生效")
        R.print_info("敏感命令（含 password/token/secret 等特征）会被自动跳过，不会上报")
        return

    R.print_error("Usage: /behavior terminal show|install")


def _handle_mobile_sub(args: list[str], mgr, cfg) -> None:
    platform = args[0].lower() if args else ""
    if platform not in ("android", "ios"):
        R.print_error("Usage: /behavior mobile android|ios")
        return
    if not mgr.config.enabled or not mgr.config.mobile_report_enabled:
        R.print_error("请先执行 /behavior on 和 /behavior enable mobile_report")
        return

    report_url, api_token = _api_base_and_token(cfg)
    if not api_token:
        R.print_error("找不到本机 API token（需要先启动过一次 HTTP API，或在配置里设置 http.api_token）")
        return

    # 手机端连的是这台电脑的局域网 IP，不是 127.0.0.1，这里提醒用户自己替换
    lan_url = report_url.replace("127.0.0.1", "<这台电脑的局域网IP>").replace("http://", "").replace("/v1/perception/report", "")
    template = mgr.get_mobile_template(platform, report_url, api_token)
    R.console.print(template)
    R.print_info(f"注意：手机和这台电脑需要在同一局域网，把 URL 里的 127.0.0.1 换成这台电脑的局域网 IP "
                 f"（当前示例假设电脑地址是 {lan_url}）")
    R.print_info("地理围栏场景只能用固定的 home/work/other 标签，不要把坐标发过来——服务端也会强制剔除坐标字段")
    from ...perception.behavior.analyzer import generate_daily_summary, load_daily_summary
    import datetime as _dt

    day = args[0] if args else "today"
    if day == "today":
        day = _dt.date.today().isoformat()

    summary = load_daily_summary(day)
    if summary is None:
        R.print_info(f"{day} 还没有生成过摘要，正在现算一次...")
        summary = generate_daily_summary(mgr, day)

    R.console.print(summary.get("markdown", "（暂无数据）"))


def _handle_browser_sub(args: list[str], mgr) -> None:
    sub = args[0].lower() if args else "status"

    if sub == "start":
        if not mgr.config.enabled:
            R.print_error("总开关未打开，请先执行 /behavior on")
            return
        try:
            st = mgr.browser_start()
        except Exception as e:
            R.print_error(f"启动失败: {e}")
            return
        R.print_success(f"专用调试浏览器已启动并开始采集: {st}")
        R.print_info("这个新窗口里的浏览行为会被采集；你平时用的浏览器不受影响。")
        return

    if sub == "stop":
        kill = len(args) > 1 and args[1] == "--kill"
        st = mgr.browser_stop(kill_browser=kill)
        R.print_success(f"已停止采集{'并关闭浏览器进程' if kill else '（浏览器窗口仍保留，可继续正常使用）'}: {st}")
        return

    if sub == "status":
        st = mgr.browser_status()
        R.console.print(f"\n[bold]专用调试浏览器[/bold]")
        R.console.print(f"  浏览器进程: {'[green]运行中[/green]' if st.get('browser_running') else '[dim]未运行[/dim]'}")
        R.console.print(f"  CDP 连接:   {'[green]已连接[/green]' if st.get('ws_connected') else '[dim]未连接[/dim]'}")
        R.console.print(f"  调试端口:   {st.get('port')}")
        if "open_pages" in st:
            R.console.print(f"  打开的页面: {st.get('open_pages')}")
        R.console.print()
        return

    R.print_error("Usage: /behavior browser start|stop [--kill]|status")
