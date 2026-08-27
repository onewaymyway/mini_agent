#!/usr/bin/env python
"""tools/fetch_iwencai_cookie.py — 通过 Chrome DevTools Protocol (CDP)
连接一个真实 Chrome，让用户手动完成问财（iwencai）的登录/验证，自动
检测到 `hexin-v` cookie 后写进 `config/secrets.local.yaml`。

背景：`data_sources.py` 里"问财 hexin-v 令牌"小节说明过，这个令牌是
前端一段混淆 JS 动态算出来的，不是简单的服务端 Set-Cookie，本项目
刻意不逆向那段加密逻辑。但一个**真实浏览器**加载页面时会自动执行那段
JS、自动算出正确的令牌——用户只要用真实浏览器打开一次页面（该验证/
登录就验证/登录），令牌就已经在浏览器的 cookie 里了。这个脚本自动化
的只是"打开浏览器 → 等令牌出现 → 读出来写进配置文件"这几个机械步骤，
用户自己要做的事（如果网站要求）跟平时用浏览器访问问财完全一样，不
存在这个脚本代替用户"骗过"验证的情况。

2026-08-27 改用 CDP 而不是 Playwright：跟 mini_agent 仓库里
`.claude/skills/browser-cdp` 这套浏览器自动化机制保持同样的思路——
直接用 Chrome 自带的 `--remote-debugging-port` + DevTools Protocol
连接**用户已经在用的真实 Chrome**，不需要额外装一个独立的 Chromium
内核（Playwright 的 `playwright install` 会下载一份跟系统 Chrome
分开的浏览器，重且跟用户已登录的会话是两码事）。这里没有直接 import
`.claude/skills/browser-cdp` 里的模块，是因为 stock_watch 按设计是
"完全自包含、可独立移动到任意路径/独立 git 仓库"的外部项目（见
PROJECT.md），硬依赖主仓库里一个具体路径下的 skill 会破坏这个前提；
所以这里用不到 200 行自己实现了一份足够用的最小 CDP 客户端（tab 发现
+ WebSocket 命令收发 + Network.getCookies），只依赖 `requests` 和
`websocket-client` 这两个通用库，跟该 skill 底层用的是同一套协议、
同一个心智模型，只是不共享代码。

两种连接方式（对应该 skill 文档里的"场景 A/B"）：
  --port 9222（默认）：假设用户已经用调试端口手动启动了 Chrome——这样
      打开的是用户的默认 profile，保留已有登录态，问财如果本来就登录
      过，可能不需要再验证一次。Windows 下建议创建一个桌面快捷方式，
      目标改成：
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=*
      macOS/Linux 类似，先完全退出 Chrome，再从终端加这两个参数启动。
      **`--remote-allow-origins=*` 不能省略**：见下面 2026-08-27 追加
      的说明，较新版本 Chrome 默认会拒绝这个脚本发起的 CDP WebSocket
      连接。`--spawn` 方式（下面）已经自动带上了这个参数，只有"自己
      手动启动 Chrome"这条路径需要用户自己在启动参数里加。
  --spawn：不想碰用户默认 profile 时，让本脚本自己拉起一个带独立临时
      profile 的新 Chrome 实例（用完不会保留登录态，每次都要重新过一遍
      验证/登录，但不影响用户平时在用的浏览器窗口）。

2026-08-27 追加：较新版本 Chrome（约 111+）出于安全加固，默认会校验
CDP WebSocket 握手时的 Origin 头，拒绝不在白名单内的连接来源——本脚本
用 `requests`/`websocket-client` 发起的连接会稳定收到
`403 Forbidden: Rejected an incoming WebSocket connection ...`（错误
信息本身其实已经点出了解法）。`--spawn` 路径下 `_spawn_chrome()` 已经
自动加上 `--remote-allow-origins=*` 规避；如果是手动启动 Chrome 走
默认 `--port 9222` 路径，需要用户自己在启动参数里加上这个 flag（见上面
更新后的示例），否则会在建立 WebSocket 会话这一步稳定失败。

2026-08-27 再次追加（修复"根本没等登录就退出、拿到没用的 cookie"）：
最初的实现把"检测到 `v` cookie 存在"当成"用户已完成登录/验证"的信号，
逐秒轮询，第一次就命中直接返回。但 `stock_watch/data_sources.py`"问财
hexin-v 令牌"一节说得很清楚：这个值是页面加载时那段混淆 JS **无条件**
算出来的，跟有没有登录/有没有过验证无关——也就是说页面刚打开、用户
还没来得及做任何操作，`v` cookie 就已经有值了。所以旧逻辑必然在第一
个轮询 tick（≤1 秒）就"成功"退出，写进配置文件的是页面刚加载时那个
未登录/未验证状态下算出来的令牌，服务端不认，等于白抓。

修复方式：不再把"cookie 存在"当作完成信号，改成两层判断：
  1. 交互式场景（`tools/fetch_iwencai_cookie.py` 由人在终端直接跑，
     `sys.stdin` 是 tty）：导航到页面后先记一次"登录前基线值"
     （仅用于后续比对，不作为结果），然后用 `input()` **真正阻塞**
     等用户在浏览器里完成登录/验证后手动按回车确认，确认后再读一次
     cookie。这是最可靠的信号来源——不去猜"登录成功"长什么样，直接
     让做了这件事的人告诉脚本"我做完了"。
  2. 非交互式场景（看板「▶️ 手动触发」调用 `entrypoints/` 那层包装时，
     子进程的 stdin 通常没有连到真正的终端，`input()` 会立刻
     `EOFError` 或永久阻塞，两者都不可用）：退化成"轮询等待值发生变化"
     ——只要读到的 `v` 值还等于登录前基线值，就继续等，直到超时或值
     变化。这不是 100% 可靠（理论上极端情况下问财可能登录前后算出同一
     个值），但比"完全不判断、第一次命中就走"要靠谱得多，且不需要
     交互输入。这条路径在 PROJECT.md"已知限制"里有对应说明。

用法：
    cd external_projects/stock_watch
    # 方式一：自己先手动把 Chrome 用调试端口开起来（别忘了带上
    # --remote-allow-origins=*，见上面的说明），然后：
    python tools/fetch_iwencai_cookie.py
    # 方式二：让脚本自己拉起一个独立实例（自动带上所需 flag）：
    python tools/fetch_iwencai_cookie.py --spawn

这个脚本是给人手动跑的交互式工具，不是 `entrypoints/` 下那种被
daemon/cron 无人值守调度的脚本（需要一个能显示窗口、能让人操作的桌面
环境），所以放在 `tools/` 而不是 `entrypoints/`，也不接入
`_common.run_entrypoint()` 那套账本机制。
"""

from __future__ import annotations

import argparse
import itertools
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

try:
    import websocket  # websocket-client
except ImportError:
    websocket = None  # 延迟到 main() 里统一报错，给出安装指引

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stock_watch.config import DEFAULT_SECRETS_PATH  # noqa: E402

IWENCAI_URL = "https://www.iwencai.com/"
COOKIE_NAME = "v"  # 页面里显示的参数名是 hexin-v，但实际存在 cookie 里的名字是 v
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222


# ── 最小 CDP 客户端（HTTP tab 发现 + WebSocket 命令收发） ──────────────
# 只实现这个脚本需要的几个操作，完整版见 .claude/skills/browser-cdp
# （见上方模块 docstring 关于为什么这里不直接复用那份代码的说明）。

class CDPError(RuntimeError):
    pass


def _http_json(host: str, port: int, path: str, timeout: float = 5.0) -> Any:
    resp = requests.get(f"http://{host}:{port}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _is_debug_port_alive(host: str, port: int) -> bool:
    try:
        _http_json(host, port, "/json/version", timeout=1.0)
        return True
    except Exception:
        return False


def _list_tabs(host: str, port: int) -> List[dict]:
    targets = _http_json(host, port, "/json/list")
    return [t for t in targets if t.get("type") == "page"]


def _new_tab(host: str, port: int, url: str) -> dict:
    resp = requests.post(
        f"http://{host}:{port}/json/new",
        data=urllib.parse.quote(url), timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


class CDPSession:
    """对单个 tab 建立的 WebSocket 会话，只实现同步 `send()`。"""

    def __init__(self, ws_url: str, host: str, port: int, timeout: float = 15.0):
        self._id_counter = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: Dict[int, dict] = {}
        self.timeout = timeout
        self._ws = websocket.create_connection(
            ws_url, timeout=timeout, origin=f"http://{host}:{port}",
        )

    def send(self, method: str, params: Optional[dict] = None) -> dict:
        msg_id = next(self._id_counter)
        with self._lock:
            self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if msg_id in self._pending:
                return self._pending.pop(msg_id)
            self._ws.settimeout(max(0.1, deadline - time.time()))
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CDPError(f"{method}: {data['error']}")
                return data.get("result", {})
            if "id" in data:
                self._pending[data["id"]] = data.get("result", {})
            # 其余是事件推送，这个脚本不关心，直接丢弃
        raise CDPError(f"等待 CDP 响应超时: {method}")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


# ── Chrome 进程管理（仅 --spawn 用到） ──────────────────────────────────

def _find_chrome_binary() -> Optional[str]:
    system = platform.system()
    candidates: List[str] = []
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    elif system == "Darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        candidates = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    for c in candidates:
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def _spawn_chrome(port: int) -> subprocess.Popen:
    binary = _find_chrome_binary()
    if not binary:
        raise RuntimeError(
            "没找到 Chrome/Chromium 可执行文件，请手动用 --remote-debugging-port "
            f"={port} 启动 Chrome 后不加 --spawn 重跑本脚本"
        )
    profile_dir = tempfile.mkdtemp(prefix="stock_watch_iwencai_cdp_")
    proc = subprocess.Popen(
        [
            binary,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            # 见模块 docstring"2026-08-27 追加"一节：较新版本 Chrome
            # （约 111+）默认会校验 CDP WebSocket 握手的 Origin 头，拒绝
            # 非白名单来源的连接（DevTools 协议的一项安全加固，防止恶意
            # 网页直接连本机调试端口）。不加这个参数，`_spawn_chrome()`
            # 拉起的实例在 `CDPSession.__init__()` 建立 WebSocket 时会
            # 稳定收到 403 Forbidden（`Rejected an incoming WebSocket
            # connection ...`），报错信息本身其实已经点出了这个 flag。
            "--remote-allow-origins=*",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if _is_debug_port_alive(DEFAULT_HOST, port):
            return proc
        time.sleep(0.3)
    proc.kill()
    raise RuntimeError(f"Chrome 启动后 {15}s 内调试端口仍不可用，已放弃")


def _find_hexin_v(cookies: List[dict]) -> Optional[str]:
    for c in cookies:
        if c.get("name") == COOKIE_NAME and "iwencai" in (c.get("domain") or ""):
            return c.get("value")
    return None


def _write_cookie_to_secrets(secrets_path: Path, cookie_value: str) -> None:
    """把拿到的令牌写进 `secrets.local.yaml`，保留文件里已有的其它字段
    （比如未来这个文件里存了别的敏感配置），不整体覆盖。
    """
    existing = {}
    if secrets_path.exists():
        existing = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
    existing["iwencai_cookie"] = cookie_value
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                         help=f"Chrome 调试端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--spawn", action="store_true",
                         help="调试端口不可用时，自己拉起一个带独立临时 profile 的新 Chrome 实例")
    parser.add_argument("--timeout", type=int, default=120,
                         help="最多等待用户完成登录/验证的秒数（默认 120）")
    parser.add_argument("--secrets-path", type=Path, default=DEFAULT_SECRETS_PATH,
                         help=f"写入的目标文件（默认 {DEFAULT_SECRETS_PATH}）")
    args = parser.parse_args()

    if websocket is None:
        print(
            "缺少依赖 websocket-client。请先运行：\n"
            "  pip install websocket-client requests PyYAML",
            file=sys.stderr,
        )
        return 1

    spawned_proc: Optional[subprocess.Popen] = None
    if not _is_debug_port_alive(args.host, args.port):
        if not args.spawn:
            print(
                f"{args.host}:{args.port} 上没有检测到可连接的 Chrome 调试端口。\n"
                "可以：\n"
                "  ① 手动完全退出 Chrome，再用命令行/快捷方式加上 "
                f"--remote-debugging-port={args.port} --remote-allow-origins=* "
                "重新打开（保留你已有的登录态；--remote-allow-origins=* 是较新版本 "
                "Chrome 的必需项，缺了这个参数即使调试端口能连上，后面建立 CDP "
                "WebSocket 会话时也会稳定收到 403 Forbidden）\n"
                "  ② 或者直接加 --spawn 让本脚本自己拉起一个独立实例（已自动带上"
                "所需参数，不带已有登录态，每次都要重新过验证）",
                file=sys.stderr,
            )
            return 1
        print(f"未检测到可用的调试端口，正在拉起一个独立 Chrome 实例（端口 {args.port}）...")
        spawned_proc = _spawn_chrome(args.port)

    try:
        tabs = _list_tabs(args.host, args.port)
        tab = tabs[0] if tabs else _new_tab(args.host, args.port, IWENCAI_URL)
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            # /json/new 返回的对象里也带这个字段；两种来源统一走同一段逻辑
            print("拿到的 tab 没有 webSocketDebuggerUrl，异常情况，放弃", file=sys.stderr)
            return 1

        try:
            session = CDPSession(ws_url, args.host, args.port)
        except Exception as exc:  # noqa: BLE001 - 转成对症的中文提示，而不是裸 traceback
            text = str(exc)
            if "403" in text or "Forbidden" in text or "remote-allow-origins" in text:
                print(
                    "建立 CDP WebSocket 会话被拒绝（403 Forbidden）。较新版本 Chrome"
                    "（约 111+）默认会校验 CDP 连接的来源，需要在启动 Chrome 时加上 "
                    "--remote-allow-origins=* 参数。若是手动启动的 Chrome（未加 "
                    "--spawn），请完全退出后加上这个参数重新启动；--spawn 方式理论上"
                    "已自动带上该参数，若仍出现此错误请确认脚本版本是否为最新。\n"
                    f"原始错误: {exc}",
                    file=sys.stderr,
                )
            else:
                print(f"建立 CDP WebSocket 会话失败: {exc}", file=sys.stderr)
            return 1
        try:
            session.send("Page.enable")
            session.send("Network.enable")
            session.send("Page.navigate", {"url": IWENCAI_URL})

            # 给页面加载 + 那段混淆 JS 跑完留一点时间，再去读"登录/验证前"
            # 的基线值。注意：这个值本身就会有内容（见模块 docstring
            # "2026-08-27 再次追加"一节），只是不能直接当结果用。
            time.sleep(2)
            baseline_result = session.send("Network.getCookies", {"urls": [IWENCAI_URL]})
            baseline_value = _find_hexin_v(baseline_result.get("cookies", []))

            print(f"已通过 CDP 打开 {IWENCAI_URL} ...")
            print("如果页面要求登录/滑块验证/短信验证，请在浏览器窗口里手动完成。")
            print(
                "注意：页面刚打开时 cookie 里可能已经有一个值了，那是登录/验证前"
                "算出来的临时值，服务端不认；请务必先在浏览器里实际完成登录/验证，"
                "不要看到这里没报错就以为已经好了。"
            )

            cookie_value: Optional[str] = None
            if sys.stdin.isatty():
                # 交互式：真正阻塞等用户确认，而不是靠轮询猜"是不是登录好了"。
                input("完成登录/验证后，请回到这个终端按回车键确认：")
                time.sleep(1)  # 给页面一点时间把确认后的新值写进 cookie
                result = session.send("Network.getCookies", {"urls": [IWENCAI_URL]})
                cookie_value = _find_hexin_v(result.get("cookies", []))
                if cookie_value and cookie_value == baseline_value:
                    print(
                        "警告：按回车后读到的令牌跟登录/验证前一模一样，大概率还没有"
                        "真正登录/验证成功（或者这次问财没有更新令牌），建议确认浏览器"
                        "里确实已经登录后重跑本脚本。",
                        file=sys.stderr,
                    )
            else:
                # 非交互式（比如看板「▶️ 手动触发」）：没有终端可以按回车确认，
                # 只能退化成"轮询等待值相对基线发生变化"，见模块 docstring。
                print(
                    "检测到当前不是交互式终端（stdin 非 tty），无法用回车确认，"
                    f"改为轮询等待令牌相对登录前的基线值发生变化，最多等待 "
                    f"{args.timeout} 秒。",
                )
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    result = session.send("Network.getCookies", {"urls": [IWENCAI_URL]})
                    current = _find_hexin_v(result.get("cookies", []))
                    if current and current != baseline_value:
                        cookie_value = current
                        break
                    time.sleep(1)
                    print(".", end="", flush=True)
                print()
        finally:
            session.close()
    finally:
        if spawned_proc is not None:
            spawned_proc.kill()

    if not cookie_value:
        print(
            "没有拿到有效的 hexin-v 令牌。可能原因：\n"
            "  - 交互式场景：按回车确认时其实还没真正完成登录/验证（重跑本脚本，"
            "确认浏览器里已经登录/通过验证后再按回车）\n"
            "  - 非交互式场景：等待 "
            f"{args.timeout} 秒内令牌值一直没有相对登录前的基线发生变化，说明这段"
            "时间内没有人在浏览器里完成登录/验证（可以加大 --timeout，或改成在有"
            "终端的环境里交互式跑本脚本，登录后手动按回车确认）\n"
            "  - 问财这次改了 cookie 名称/存放位置（这个脚本按名字 'v' "
            "查找，需要重新确认）\n"
            "没有写入任何文件。",
            file=sys.stderr,
        )
        return 1

    _write_cookie_to_secrets(args.secrets_path, cookie_value)
    print(f"已获取令牌并写入 {args.secrets_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
