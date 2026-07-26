"""
browser_launch.py - 确保有一个可通过 CDP 连接的 Chrome，并管理 tab / 专用浏览器实例。

三种模式：
1. attach（默认，--ensure）：假设用户已经手动用调试端口启动了浏览器，本脚本只负责发现/连接。
   Windows 下让用户手动创建一个带 --remote-debugging-port 的快捷方式最省心，
   因为直接"接管"一个已经在跑、没开调试端口的 Chrome 是做不到的（Chrome 限制）。
2. --spawn（配合 --ensure）：只在调试端口不可用时才临时拉起一个实例，一次性用途。
3. --dedicated：显式创建一个**专门给 Agent 后续操作用的独立 Chrome 实例**——
   独立 profile、独立调试端口（默认 9333，与场景1的 9222 互不冲突）、可见窗口（默认非无头，
   也可加 --headless 用于纯抓取），并把 {name -> port/pid/profile_dir} 记录到本地注册表，
   后续脚本可以直接用 --port 9333（或 --instance-name）连接，不需要每次重新启动。

用法示例：
  # 场景1/2：连接或临时拉起
  python browser_launch.py --ensure                       # 检查/发现，不满足则报错并给出指引
  python browser_launch.py --ensure --spawn                # 若无可用调试端口，自动临时拉起一个

  # 场景3：专用浏览器实例（推荐用于"后续一系列自动化操作"）
  python browser_launch.py --dedicated                                # 起一个默认名为 default 的可见专用实例
  python browser_launch.py --dedicated --name work --start-url "https://example.com"
  python browser_launch.py --dedicated --name scraper --headless      # 服务器/沙盒纯抓取场景
  python browser_launch.py --list-dedicated                           # 查看当前已注册的专用实例
  python browser_launch.py --stop-dedicated work                      # 关闭并从注册表移除

  # tab 管理（对 attach/spawn/dedicated 任何一种已建立的连接都适用，指定 --port 即可）
  python browser_launch.py --list                          # 列出所有 tab
  python browser_launch.py --new "https://example.com"      # 新建 tab 并打开网址
  python browser_launch.py --close <target_id>
  python browser_launch.py --activate <target_id>           # 把某个 tab 切到前台（有 GUI 时可见）
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import time

from cdp_client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    is_debug_port_alive,
    list_tabs,
    new_tab,
    close_tab,
    activate_tab,
    version_info,
)
from utils import print_json, die


WINDOWS_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

LINUX_CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]

MAC_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# 专用实例默认端口，与"attach 用户已有浏览器"的 9222 分开，避免互相冲突
DEFAULT_DEDICATED_PORT = 9333
SKILL_HOME = os.path.join(os.path.expanduser("~"), ".cdp_skill")
REGISTRY_PATH = os.path.join(SKILL_HOME, "registry.json")
# [next_doc/browser_cdp_stability_fixes.md #1] 专用实例的 profile 目录必须放在
# 稳定、不会被外部流程清理的位置。历史实现放在项目内 "temp/cdp_brower_data"
# 下——"temp/" 这个名字在几乎所有工程约定里都意味着"可随时清空的临时目录"，
# workflow/CI/构建脚本经常会在每次运行前 rm -rf temp/，这会直接删掉专用浏览器
# 实例的整个 profile（含登录 cookies/session），表现为"每次新建的浏览器都不
# 记得之前的登录状态"——这不是 Chrome 或 CDP 连接的问题，是 profile 目录选址
# 选错了地方。改到 SKILL_HOME（~/.cdp_skill/profiles/），与 registry.json
# 放在同一个"本技能专属、不属于任何项目 temp 目录"的稳定位置下。
DEFAULT_PROFILE_ROOT = os.path.join(SKILL_HOME, "profiles")


def find_chrome_binary() -> str | None:
    system = platform.system()
    candidates = []
    if system == "Windows":
        candidates = WINDOWS_CHROME_CANDIDATES
    elif system == "Darwin":
        candidates = MAC_CHROME_CANDIDATES
    else:
        candidates = LINUX_CHROME_CANDIDATES

    for c in candidates:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        else:
            found = shutil.which(c)
            if found:
                # shutil.which() 可能只返回可执行文件名（如 "msedge.exe"）
                # 此时已确认它在 PATH 中，直接返回即可；
                # 不要再用 os.path.exists(found) 检查，因为相对名在当前目录不存在时会误判
                return found
    return None


def _remove_singleton_locks(profile_dir: str) -> None:
    """只删除 Chrome 单实例锁文件，不动其他数据（cookies/sessions 等）。

    Chrome 在 Windows/mac/Linux 下会用以下锁文件标记 profile 正被使用：
      - SingletonLock
      - SingletonSocket
      - SingletonCookie
    删除它们可以让新进程认为"没有实例在用这个 profile"，从而正常启动。
    """
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(profile_dir, name)
        try:
            if os.path.exists(p) or os.path.islink(p):
                os.remove(p)
        except Exception:
            pass


def _profile_lock_path(profile_dir: str) -> str:
    return os.path.join(profile_dir, ".mini_agent_lock.json")


def _read_profile_lock(profile_dir: str) -> dict | None:
    """[next_doc/browser_cdp_stability_fixes.md #2] registry.json 之外的
    第二条线索：profile_dir 下的锁文件记录了上次成功启动时的 port/pid/
    启动时间。registry 条目丢失（比如被误删/重置）但浏览器进程和 profile
    还在时，靠这个文件也能找回端口号去做真实探测，而不是直接判定"没有
    可用实例"就新建一个（新建会因为 profile 目录被同一个 Chrome 占用而
    启动失败，或者启动到另一个端口，制造出两个互不相关的实例）。"""
    p = _profile_lock_path(profile_dir)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "port" in data:
            return data
    except Exception:
        pass
    return None


def _write_profile_lock(profile_dir: str, port: int, pid: int) -> None:
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(_profile_lock_path(profile_dir), "w", encoding="utf-8") as f:
            json.dump({"port": port, "pid": pid, "started_at": time.time()}, f)
    except Exception:
        pass


def spawn_browser(
    binary: str,
    port: int,
    user_data_dir: str,
    headless: bool,
    start_url: str = "about:blank",
    window_size: str = "1366,900",
    user_agent: str = None,
) -> subprocess.Popen:
    # 确保使用绝对路径，避免 cwd 变化导致 profile 目录不一致
    user_data_dir = os.path.abspath(user_data_dir)
    # 只删除单实例锁文件，保留 cookies/sessions 等数据
    _remove_singleton_locks(user_data_dir)
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={window_size}",
        # 禁用可能触发单实例重定向的功能
        "--disable-features=TranslateUI,BackgroundMode,BackgroundFetch",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-network-requests",
        # 禁用缓存，确保每次获取最新数据
        "--disable-cache",
        "--disable-application-cache",
        "--disable-offline-load-stale-cache",
        "--disk-cache-size=0",
        "--media-cache-size=0",
    ]
    if user_agent:
        args.append(f"--user-agent={user_agent}")
    if headless:
        args += [
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--mute-audio",
        ]
    args.append(start_url)
    # Windows 下沙盒相关参数一般不需要；Linux 容器里跑 root 常需要 --no-sandbox
    if platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return proc


def wait_port_alive(host: str, port: int, timeout: float = 15.0, proc: subprocess.Popen | None = None) -> tuple[bool, str | None]:
    """轮询等待调试端口就绪。若传入 proc，会同时检测该进程是否提前退出（避免傻等一个已经崩溃的进程）。
    返回 (是否就绪, 提前退出时的错误信息)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_debug_port_alive(host, port, timeout=1.0):
            return True, None
        if proc is not None:
            code = proc.poll()
            if code is not None:
                return False, f"浏览器进程已退出 (exit code={code})，通常是参数不兼容或 profile 目录被占用"
        time.sleep(0.3)
    return False, None


def _cleanup_singleton_lock(profile_dir: str) -> None:
    """删除 Chrome profile 目录下的单例锁文件（仅在确认对应旧进程已不存在/已被我们杀掉后调用）。
    Windows 下 Chrome 用命名 mutex 做单例锁，不是文件锁，进程一退出锁自动释放，
    这里主要是清 Linux/mac 下残留的 SingletonLock/SingletonSocket/SingletonCookie。"""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(profile_dir, name)
        try:
            if os.path.exists(p) or os.path.islink(p):
                os.remove(p)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 专用实例注册表：记录 name -> {port, pid, profile_dir, headless}
# 这样一次会话里起的浏览器，后续所有脚本调用都能通过 --port 复用，不用每次重新 spawn。
# ---------------------------------------------------------------------------
def _load_registry() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        return {}
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_registry(reg: dict) -> None:
    os.makedirs(SKILL_HOME, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def _kill_pid(pid: int) -> None:
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """仅用于判断本技能自己记录在 registry 里的旧 pid 是否还活着，从不用于探测/操作外部进程。"""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
            ).stdout
            return str(pid) in out
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def cmd_dedicated(args: argparse.Namespace) -> None:
    reg = _load_registry()
    existing = reg.get(args.name)

    # [next_doc/browser_cdp_stability_fixes.md #2] registry.json 是本技能
    # 唯一的进程内状态来源，但它本身没有并发保护——多个 workflow/step 并行
    # 调用 --dedicated 时可能相互踩踏读写，也可能 registry.json 因为某些
    # 原因被清空/重置（比如另一个进程调用 --stop-dedicated 之后 registry
    # 条目被删了，但 profile_dir 和浏览器进程其实还在跑）。所以真实性判断
    # 一律以"真实探测端口是否活着"为准，registry 只是用来找"应该去探测
    # 哪个端口"的线索，不是判断本身；profile_dir 下的 lock 文件是
    # registry 之外的第二条线索，registry 丢失时兜底用它找回端口号。
    if not existing:
        existing = _read_profile_lock(args.user_data_dir or os.path.join(DEFAULT_PROFILE_ROOT, args.name))

    if existing and is_debug_port_alive(args.host, existing["port"]):
        info = version_info(args.host, existing["port"])
        print(
            f"[ok] 专用实例 '{args.name}' 已在运行 -> {args.host}:{existing['port']} "
            f"({info.get('Browser')})，直接复用（如需重开请先 --stop-dedicated {args.name}）"
        )
        reg[args.name] = existing  # 用 lock 文件恢复的信息回填 registry，下次不用再兜底
        _save_registry(reg)
        return

    # 同名实例存在但端口已经不通了：说明是本技能自己之前启动、后来挂掉/崩溃的孤儿进程。
    # 只处理 registry 里记录的、由本技能启动的 pid，绝不碰任何其他 Chrome 进程。
    if existing:
        old_pid = existing.get("pid")
        if old_pid and _pid_alive(old_pid):
            print(f"[info] 发现同名实例 '{args.name}' 的旧进程 (pid={old_pid}) 仍在运行但端口无响应，先清理它")
            _kill_pid(old_pid)
            time.sleep(0.5)
        _cleanup_singleton_lock(existing.get("profile_dir", ""))

    binary = args.binary or find_chrome_binary()
    if not binary:
        die("未找到 Chrome/Chromium 可执行文件，请用 --binary 指定路径")

    port = args.port or (existing or {}).get("port") or DEFAULT_DEDICATED_PORT
    # 端口被占用（且不是我们自己注册的）时自动往后找一个空闲端口
    tries = 0
    while is_debug_port_alive(args.host, port) and tries < 20:
        port += 1
        tries += 1

    profile_dir = args.user_data_dir or os.path.join(DEFAULT_PROFILE_ROOT, args.name)

    proc = spawn_browser(
        binary=binary,
        port=port,
        user_data_dir=profile_dir,
        headless=args.headless,
        start_url=args.start_url,
        window_size=args.window_size,
        user_agent=args.user_agent,
    )
    ok, early_error = wait_port_alive(args.host, port, timeout=args.spawn_timeout, proc=proc)
    if not ok:
        # 只杀本次自己刚拉起的这一个进程（proc.pid 是本次 Popen 返回的，不可能是别的已有进程）
        _kill_pid(proc.pid)
        reason = early_error or f"调试端口 {port} 在 {args.spawn_timeout}s 内未就绪"
        die(f"启动浏览器失败: {reason}（已清理本次启动的进程 pid={proc.pid}，未影响其他浏览器窗口）")

    info = version_info(args.host, port)
    reg[args.name] = {
        "port": port,
        "pid": proc.pid,
        "profile_dir": profile_dir,
        "headless": args.headless,
        "binary": binary,
    }
    _save_registry(reg)
    _write_profile_lock(profile_dir, port, proc.pid)

    tabs = list_tabs(args.host, port)
    tab_id = tabs[0]["id"] if tabs else None

    # 真正读一次页面状态再报告，而不是只确认"端口通了"——这是 agent 能否正确判断"打开成功"的关键
    state = _verify_tab_state(args.host, port, tab_id, wait_seconds=min(10.0, args.spawn_timeout))

    print(
        f"[ok] 已创建专用浏览器实例 '{args.name}' pid={proc.pid} headless={args.headless}\n"
        f"     -> {args.host}:{port} ({info.get('Browser')})\n"
        f"     profile_dir={profile_dir}\n"
        f"     首个 tab id={tab_id}\n"
        f"     当前页面: url={state.get('url')!r} title={state.get('title')!r} readyState={state.get('readyState')!r}\n"
        f"后续调用其他脚本时加上 --port {port} 即可连接这个专用实例，例如：\n"
        f"  python browser_nav.py --port {port} --tab {tab_id} --goto \"https://example.com\""
    )
    if state.get("error"):
        print(f"[warn] 读取页面状态时出错: {state['error']}，建议用 browser_nav.py --port {port} --tab {tab_id} 手动确认")


def _verify_tab_state(host: str, port: int, tab_id: str | None, wait_seconds: float = 10.0) -> dict:
    """连上目标 tab，轮询直到 document.readyState 不是 loading（或超时），返回真实的 url/title。
    这一步是为了让上层（agent）拿到的"打开成功"结论是基于实际读取的页面状态，而不是"进程/端口活着"这种间接信号。"""
    if not tab_id:
        return {"error": "没有可用的 tab"}
    try:
        from cdp_client import find_tab, connect_tab

        target = find_tab(host=host, port=port, tab_id=tab_id)
        session = connect_tab(target)
        try:
            session.send("Page.enable")
            deadline = time.time() + wait_seconds
            last = {}
            while time.time() < deadline:
                url = session.eval_js("location.href")
                title = session.eval_js("document.title")
                ready = session.eval_js("document.readyState")
                last = {"url": url, "title": title, "readyState": ready}
                if ready == "complete":
                    break
                time.sleep(0.3)
            return last
        finally:
            session.close()
    except Exception as e:
        return {"error": str(e)}


def cmd_list_dedicated(args: argparse.Namespace) -> None:
    reg = _load_registry()
    out = []
    for name, info in reg.items():
        alive = is_debug_port_alive(args.host, info["port"])
        out.append({**info, "name": name, "alive": alive})
    print_json(out)


def cmd_stop_dedicated(args: argparse.Namespace) -> None:
    reg = _load_registry()
    info = reg.pop(args.stop_dedicated, None)
    if not info:
        die(f"没有找到名为 '{args.stop_dedicated}' 的专用实例")
    _kill_pid(info["pid"])
    _save_registry(reg)
    print(f"[ok] 已停止专用实例 '{args.stop_dedicated}' (pid={info['pid']})")


def cmd_ensure(args: argparse.Namespace) -> None:
    if is_debug_port_alive(args.host, args.port):
        info = version_info(args.host, args.port)
        print(f"[ok] 已连接到调试端口 {args.host}:{args.port} -> {info.get('Browser')}")
        return

    if not args.spawn:
        die(
            "调试端口不可用。\n"
            "  方式一（推荐，可与用户共享同一浏览器窗口）：\n"
            "    Windows 下完全关闭 Chrome 后，用以下命令重新打开：\n"
            r'    "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222'
            "\n"
            "  方式二：加 --spawn 参数，临时拉起一个一次性实例；\n"
            "  方式三（推荐用于后续多步操作）：改用 --dedicated 创建一个可复用、带注册表的专用实例。"
        )

    binary = args.binary or find_chrome_binary()
    if not binary:
        die("未找到 Chrome/Chromium 可执行文件，请用 --binary 指定路径")

    proc = spawn_browser(
        binary=binary,
        port=args.port,
        user_data_dir=args.user_data_dir or os.path.join(DEFAULT_PROFILE_ROOT, "spawn"),
        headless=args.headless,
        start_url=args.start_url,
    )
    ok, early_error = wait_port_alive(args.host, args.port, timeout=args.spawn_timeout, proc=proc)
    if not ok:
        # 只杀本次自己刚拉起的这一个进程，不影响任何原本就在运行的浏览器
        _kill_pid(proc.pid)
        reason = early_error or f"调试端口 {args.port} 在 {args.spawn_timeout}s 内未就绪"
        die(f"启动浏览器失败: {reason}（已清理本次启动的进程 pid={proc.pid}，未影响其他浏览器窗口）")
    info = version_info(args.host, args.port)
    print(
        f"[ok] 已启动新浏览器实例 pid={proc.pid} headless={args.headless} "
        f"-> {args.host}:{args.port} ({info.get('Browser')})\n"
        f"     user_data_dir={args.user_data_dir or os.path.join(DEFAULT_PROFILE_ROOT, 'spawn')}"
    )


def cmd_list(args: argparse.Namespace) -> None:
    tabs = list_tabs(args.host, args.port)
    print_json(
        [
            {"id": t.get("id"), "title": t.get("title"), "url": t.get("url"), "type": t.get("type")}
            for t in tabs
        ]
    )


def cmd_new(args: argparse.Namespace) -> None:
    t = new_tab(args.new, args.host, args.port)
    print_json({"id": t.get("id"), "url": t.get("url")})


def cmd_close(args: argparse.Namespace) -> None:
    close_tab(args.close, args.host, args.port)
    print(f"[ok] 已关闭 tab {args.close}")


def cmd_activate(args: argparse.Namespace) -> None:
    activate_tab(args.activate, args.host, args.port)
    print(f"[ok] 已激活 tab {args.activate}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=None, help="不指定时: --ensure用9222，--dedicated用9333起自动探测空闲端口")

    parser.add_argument("--ensure", action="store_true", help="检查调试端口是否可用（默认端口 9222，即 attach 场景）")
    parser.add_argument("--spawn", action="store_true", help="配合 --ensure，若不可用则临时拉起一个一次性实例")

    parser.add_argument("--dedicated", action="store_true", help="创建/复用一个专门用于后续自动化操作的独立浏览器实例")
    parser.add_argument("--name", default="default", help="专用实例名称，可同时维护多个（如 work/scraper）")
    parser.add_argument("--list-dedicated", action="store_true", help="列出已注册的专用实例")
    parser.add_argument("--stop-dedicated", metavar="NAME", default=None, help="停止并移除指定名称的专用实例")

    parser.add_argument("--headless", action="store_true", help="无 GUI 环境/纯抓取场景使用")
    parser.add_argument("--binary", default=None, help="浏览器可执行文件路径，不给则自动探测")
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help=(
            "profile 目录。--ensure --spawn 默认 ./temp/cdp_brower_data/spawn；"
            "--dedicated 默认 ./temp/cdp_brower_data/<name>；一般不用手动设置"
        ),
    )
    parser.add_argument("--start-url", default="about:blank")
    parser.add_argument("--window-size", default="1366,900", help="可见窗口大小，例如 1920,1080")
    parser.add_argument("--user-agent", default=None, help="自定义 User-Agent 字符串")
    parser.add_argument("--spawn-timeout", type=float, default=30.0, help="等待调试端口就绪的超时时间，首次冷启动新 profile 建议保留默认值")

    parser.add_argument("--list", action="store_true", help="列出当前所有 tab")
    parser.add_argument("--new", metavar="URL", default=None, help="新建 tab 并打开 URL")
    parser.add_argument("--close", metavar="TARGET_ID", default=None, help="关闭指定 tab")
    parser.add_argument("--activate", metavar="TARGET_ID", default=None, help="把指定 tab 切到前台")

    args = parser.parse_args()

    if args.dedicated:
        cmd_dedicated(args)
        return
    if args.list_dedicated:
        cmd_list_dedicated(args)
        return
    if args.stop_dedicated:
        cmd_stop_dedicated(args)
        return

    # --list/--new/--close/--activate 若未显式给 --port，默认走 attach 场景的 9222
    if args.port is None:
        args.port = DEFAULT_PORT

    if args.ensure:
        cmd_ensure(args)
    if args.list:
        cmd_list(args)
    if args.new:
        cmd_new(args)
    if args.close:
        cmd_close(args)
    if args.activate:
        cmd_activate(args)

    if not any([args.ensure, args.list, args.new, args.close, args.activate]):
        parser.print_help()


if __name__ == "__main__":
    main()

