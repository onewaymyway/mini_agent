"""stock_watch/iwencai_api.py — 通过 CDP 获取 hexin-v 令牌并调用问财新 API。

2026-08-27 新增。旧端点 `customized/chart/get-robot-data` 已失效，新端点
为 `unifiedwap/unified-wap/v2/result/get-robot-data`，要求 POST
`application/x-www-form-urlencoded` 请求体，并携带 `hexin-v` 请求头。

`hexin-v` 是问财前端 JS 动态计算的安全令牌，不存在于普通 cookie 中，
必须通过真实浏览器执行页面 JS 才能获得。本模块通过 CDP（Chrome DevTools
Protocol）连接用户已在运行的 Chrome 实例（端口 9334），从 Network
cookies 中读取 `v` 值作为 hexin-v 令牌，然后直接调用 API。

此方案不逆向加密算法、不使用 pywencai，仅利用用户自己在浏览器中已合法
获得的登录态，属于"将用户自己的浏览器会话接过来用"，不涉及绕过对方技术
访问控制措施。

设计取舍：
  - 内联了 browser_launch.py 的启动逻辑（_find_chrome_binary, spawn_browser,
    wait_port_alive），不直接 import 避免循环依赖。
  - hexin-v 每次请求前重新从浏览器读取，有效期约 15-20 分钟。
  - 如果 CDP 不可用，自动尝试启动一个带调试端口的 Chrome 实例。
  - 如果仍然失败，回退到旧的 requests 会话方式（通常也会 401）。
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger("stock_watch.iwencai_api")

# ── 常量 ────────────────────────────────────────────────────────────────────
IWENCAI_URL = "https://www.iwencai.com"
API_URL = f"{IWENCAI_URL}/unifiedwap/unified-wap/v2/result/get-robot-data"
DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222  # 与 browser_launch.py 默认端口一致
HEXIN_V_COOKIE_NAME = "v"  # 浏览器 cookie 中 hexin-v 的存储名
REQUEST_TTL_SEC = 15 * 60  # hexin-v 有效期 15 分钟，到期强制刷新
_LAUNCHED_BROWSER: Optional[subprocess.Popen] = None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


# ── Chrome 自动启动（参考 browser_launch.py）─────────────────────────────────

def _find_chrome_binary() -> Optional[str]:
    """尽力猜测一个可用的 Chrome/Chromium/Edge 可执行文件路径。"""
    candidates_by_os = {
        "Darwin": [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ],
        "Windows": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "Linux": [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        ],
    }
    system = platform.system()
    for candidate in candidates_by_os.get(system, []):
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    # 兜底：把所有系统的候选都试一遍 PATH 查找
    for names in candidates_by_os.values():
        for name in names:
            found = shutil.which(name)
            if found:
                return found
    return None


def _spawn_browser(
    port: int,
    headless: bool = True,
    binary: Optional[str] = None,
    start_url: str = IWENCAI_URL,
) -> subprocess.Popen:
    """启动一个带调试端口的 Chrome 实例。"""
    binary = binary or _find_chrome_binary()
    if not binary:
        raise RuntimeError("未找到可用的 Chrome/Chromium/Edge 可执行文件")
    user_data_dir = os.path.join(tempfile.gettempdir(), f"iwencai-browser-{port}")
    os.makedirs(user_data_dir, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1366,900",
    ]
    if headless:
        args += ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio"]
    args.append(start_url)
    if platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_port_alive(host: str, port: int, timeout: float = 15.0, proc: Optional[subprocess.Popen] = None) -> tuple:
    """轮询等待调试端口就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cdp_is_alive(host, port):
            return True, None
        if proc is not None:
            code = proc.poll()
            if code is not None:
                return False, f"浏览器进程已退出 (exit code={code})"
        time.sleep(0.3)
    return False, None


def ensure_browser_running(port: int = DEFAULT_CDP_PORT) -> bool:
    """确保带调试端口的 Chrome 正在运行。如果不存在则自动启动。

    Returns:
        True 表示 CDP 可用，False 表示失败。
    """
    global _LAUNCHED_BROWSER
    if _cdp_is_alive(DEFAULT_CDP_HOST, port):
        logger.debug("CDP 端口 %d 已有 Chrome 实例在运行", port)
        return True
    logger.info("CDP 端口 %d 不可用，尝试自动启动 Chrome...", port)
    try:
        proc = _spawn_browser(port, headless=False, start_url=IWENCAI_URL)
        _LAUNCHED_BROWSER = proc
        ok, err = _wait_port_alive(DEFAULT_CDP_HOST, port, timeout=20.0, proc=proc)
        if ok:
            logger.info("Chrome 启动成功，端口 %d 已就绪", port)
            return True
        else:
            logger.warning("Chrome 启动失败: %s", err)
            _LAUNCHED_BROWSER = None
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chrome 启动异常: %s", exc)
        return False


# ── 最小 CDP 客户端 ──────────────────────────────────────────────────────────

class CDPError(RuntimeError):
    """CDP 操作失败时抛出。"""


def _cdp_http(host: str, port: int, path: str, timeout: float = 5.0) -> Any:
    """发 CDP HTTP 请求（tab 列表等）。"""
    try:
        resp = requests.get(f"http://{host}:{port}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise CDPError(f"CDP HTTP 请求失败 {path}: {exc}") from exc


def _cdp_is_alive(host: str, port: int) -> bool:
    try:
        _cdp_http(host, port, "/json/version", timeout=1.0)
        return True
    except CDPError:
        return False


class CDPSession:
    """最小 CDP WebSocket 会话（仅实现本模块需要的操作）。"""

    def __init__(self, ws_url: str, host: str, port: int) -> None:
        self._ws_url = ws_url
        self._host = host
        self._port = port
        self._ws: Any = None
        self._msg_id = itertools.count(start=1)
        try:
            import websocket
        except ImportError:
            raise CDPError(
                "缺少 websocket-client 依赖。请运行：pip install websocket-client"
            )
        self._ws = websocket.create_connection(ws_url, timeout=10.0, suppress_origin=True)

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """发送 CDP 命令并返回响应数据。"""
        msg_id = next(self._msg_id)
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        self._ws.send(json.dumps(payload))
        while True:
            raw = self._ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg and msg["error"]:
                    raise CDPError(
                        f"CDP {method} 错误: {msg['error'].get('message')}"
                    )
                return msg.get("result", {})
            # 忽略无关事件
            continue

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None


import itertools


def _read_hexin_v_from_cdp(
    host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT
) -> Optional[str]:
    """通过 CDP 从浏览器 cookie 中读取 hexin-v（cookie 名为 `v`）。

    返回令牌字符串，CDP 不可用或令牌不存在时返回 None。
    """
    if not _cdp_is_alive(host, port):
        logger.debug("CDP 端口 %s:%s 不可达，跳过 hexin-v 读取", host, port)
        return None
    try:
        tabs = _cdp_http(host, port, "/json/list")
        page_tabs = [t for t in tabs if t.get("type") == "page"]
        if not page_tabs:
            logger.debug("CDP 没有可用的 page tab")
            return None
        tab = page_tabs[0]
        ws_url = tab["webSocketDebuggerUrl"]
        session = CDPSession(ws_url, host, port)
        try:
            session.send("Runtime.enable")
            session.send("Network.enable")
            # 确保在问财页面（如果没有，导航过去）
            current_url = tab.get("url", "")
            if not current_url.startswith(IWENCAI_URL):
                session.send("Page.navigate", {"url": IWENCAI_URL})
                time.sleep(3)  # 等待页面加载和 JS 执行
                # 重新获取 tab 信息（URL 可能变化）
                tabs = _cdp_http(host, port, "/json/list")
                page_tabs = [t for t in tabs if t.get("type") == "page"]
                if page_tabs:
                    tab = page_tabs[0]
            result = session.send(
                "Network.getCookies",
                {"urls": [IWENCAI_URL, "https://www.iwencai.com/"]},
            )
            cookies = result.get("cookies", [])
            for c in cookies:
                if c.get("name") == HEXIN_V_COOKIE_NAME:
                    domain = c.get("domain", "")
                    if "iwencai" in domain or domain == "www.iwencai.com":
                        return c.get("value")
            logger.debug("CDP 在问财 cookie 中未找到 '%s'", HEXIN_V_COOKIE_NAME)
            return None
        finally:
            session.close()
    except CDPError as exc:
        logger.debug("CDP 读取 hexin-v 失败: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("CDP 读取 hexin-v 异常: %s", exc)
        return None


# ── 令牌缓存 ─────────────────────────────────────────────────────────────────

_hexin_v_cache: Optional[str] = None
_hexin_v_ts: float = 0.0


def _get_hexin_v(force_refresh: bool = False) -> Optional[str]:
    """获取 hexin-v 令牌，带 TTL 缓存。"""
    global _hexin_v_cache, _hexin_v_ts
    now = time.monotonic()
    if force_refresh or _hexin_v_cache is None or (now - _hexin_v_ts) > REQUEST_TTL_SEC:
        token = _read_hexin_v_from_cdp()
        if token:
            _hexin_v_cache = token
            _hexin_v_ts = now
            logger.debug("hexin-v 令牌已获取（%d 字符）", len(token))
        else:
            logger.warning("无法从 CDP 获取 hexin-v 令牌，问财 API 可能 401")
    return _hexin_v_cache


# ── API 调用 ─────────────────────────────────────────────────────────────────


def fetch_iwencai_screener_direct(
    query: str, top_n: int = 100
) -> List[Dict[str, Any]]:
    """直接调用问财新 API 进行选股查询。

    Args:
        query: 自然语言查询，如 "今日涨停"、"市盈率小于20的股票的股票代码"。
        top_n: 最多返回条数。

    Returns:
        结果行列表，每条是一个 dict（列名→值）。

    Raises:
        DataSourceError: 查询失败时抛出。
    """
    from stock_watch.data_sources import DataSourceError

    # 确保 CDP 可用（自动启动 Chrome 如果必要）
    if not ensure_browser_running():
        raise DataSourceError(
            "无法启动 Chrome 浏览器。请手动启动一个带调试端口的 Chrome：\n"
            f'  chrome.exe --remote-debugging-port={DEFAULT_CDP_PORT} --remote-allow-origins=*\n'
            "然后在浏览器中访问 https://www.iwencai.com 并登录。"
        )

    token = _get_hexin_v()
    if not token:
        raise DataSourceError(
            "无法获取 hexin-v 令牌。请确保已在 Chrome 中登录问财，\n"
            "并确保 Chrome 以调试端口启动：\n"
            f'  chrome.exe --remote-debugging-port={DEFAULT_CDP_PORT} --remote-allow-origins=*\n'
            "或在 config/secrets.local.yaml 中配置 iwencai_cookie 字段。"
        )

    # 构建请求体（form-urlencoded）
    payload = {
        "question": query,
        "source": "Ths_iwencai_Xuangu",
        "version": "2.0",
        "secondary_intent": "stock",
        "rsh": "",
        "page": 1,
        "perpage": top_n,
        "log_info": json.dumps({"input_type": "click"}),
        "add_info": json.dumps({
            "urp": {"scene": 1, "company": 1, "business": 1},
            "contentType": "json",
            "plato": "gdpr",
        }),
    }

    headers = {
        "User-Agent": UA,
        "Referer": f"{IWENCAI_URL}/screener/result?w={quote(query)}&querytype=stock",
        "Accept": "application/json, text/plain, */*",
        "hexin-v": token,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "sec-ch-ua": '"Google Chrome";v="151", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Origin": IWENCAI_URL,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                API_URL,
                data=payload,
                headers=headers,
                timeout=20,
                allow_redirects=False,
            )
            resp.raise_for_status()
            break  # 成功则跳出重试循环
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                # 令牌过期，强制刷新后重试
                logger.warning("hexin-v 令牌 401，强制刷新后重试...")
                token = _get_hexin_v(force_refresh=True)
                if not token:
                    raise DataSourceError(
                        "hexin-v 令牌刷新失败，请重新在 Chrome 中登录问财"
                    ) from exc
                headers["hexin-v"] = token
                continue  # 使用新令牌重试
            elif exc.response is not None and exc.response.status_code == 403:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt  # 指数退避：2s, 4s, 8s
                    logger.warning(f"问财 API 403 频率限制，等待 {wait}s 后重试 ({attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                else:
                    raise DataSourceError(
                        f"问财 API 403 频率限制（已重试 {max_retries} 次）。请稍后再试。"
                    ) from exc
            else:
                raise DataSourceError(f"问财 API 请求失败: {exc}") from exc
        except requests.RequestException as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(f"问财 API 请求异常: {exc}，等待 {wait}s 后重试")
                time.sleep(wait)
                continue
            raise DataSourceError(f"问财 API 请求异常: {exc}") from exc
    else:
        # 重试耗尽
        raise DataSourceError(f"问财 API 请求失败（已重试 {max_retries} 次）")

    # 解析响应
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise DataSourceError(
            f"问财 API 返回非 JSON 响应（{len(resp.text)} 字节）: {resp.text[:200]}"
        ) from exc

    if data.get("code") not in (None, 0, "0"):
        msg = data.get("msg") or data.get("answer", "") or str(data)
        raise DataSourceError(f"问财 API 返回错误: {msg}")

    rows = _parse_iwencai_response(data, top_n)
    logger.debug("问财查询 '%s' 返回 %d 条结果", query, len(rows))
    return rows


def _parse_iwencai_response(data: Dict[str, Any], top_n: int) -> List[Dict[str, Any]]:
    """从问财 API 响应中提取结构化数据行。

    新格式（2026-08-28）：data.answer[0].txt[0].content.components[0].data
    包含 columns（列定义）和 rows（数据行）。
    """
    rows: List[Dict[str, Any]] = []
    try:
        answer_list = data.get("data", {}).get("answer", [])
        if not answer_list:
            return rows
        answer = answer_list[0]
        if not isinstance(answer, dict):
            return rows

        # 新格式：从 txt.content.components 提取表格
        txt_list = answer.get("txt", [])
        if isinstance(txt_list, list) and txt_list:
            txt_item = txt_list[0]
            if isinstance(txt_item, dict):
                content = txt_item.get("content", {})
                if isinstance(content, dict):
                    components = content.get("components", [])
                    for comp in components:
                        if not isinstance(comp, dict):
                            continue
                        comp_data = comp.get("data", {})
                        if not isinstance(comp_data, dict):
                            continue
                        columns = comp_data.get("columns", [])
                        table_rows = comp_data.get("datas", []) or comp_data.get("rows", [])
                        # 解析数据行 - 新格式使用 datas 字段，每行是 dict
                        if not columns and not table_rows:
                            continue
                        # 构建列名映射
                        headers = []
                        for col in columns:
                            if isinstance(col, dict):
                                headers.append(col.get("key") or col.get("label") or col.get("index_name", ""))
                            else:
                                headers.append(str(col))
                        # 解析数据行
                        for row_data in table_rows:
                            if isinstance(row_data, dict):
                                # 新格式：每行已经是 dict
                                rows.append(row_data)
                            elif isinstance(row_data, list):
                                # 旧格式：每行是 list
                                row = {}
                                for i, val in enumerate(row_data):
                                    if i < len(headers):
                                        row[headers[i]] = val
                                    else:
                                        row[f"col_{i}"] = val
                                rows.append(row)
                            else:
                                continue
                        if rows:
                            break

        # 旧格式兼容：answer.answer 列表或字符串
        if not rows:
            answer_text = answer.get("answer", "")
            if isinstance(answer_text, list):
                if len(answer_text) >= 2:
                    headers = [str(h) for h in answer_text[0]]
                    for row_data in answer_text[1:]:
                        if not isinstance(row_data, list):
                            continue
                        row = {}
                        for i, val in enumerate(row_data):
                            if i < len(headers):
                                row[headers[i]] = val
                            else:
                                row[f"col_{i}"] = val
                        rows.append(row)
            elif isinstance(answer_text, str):
                logger.debug("问财返回字符串格式答案，暂不支持直接解析")

        # 检查 ret_data 字段
        if not rows:
            ret_data = data.get("data", {}).get("ret_data", {})
            if isinstance(ret_data, dict):
                table = ret_data.get("table", [])
                if isinstance(table, list) and table:
                    headers = [str(h) for h in table[0]] if isinstance(table[0], list) else []
                    for row_data in table[1:]:
                        if isinstance(row_data, list):
                            row = {headers[i]: row_data[i] for i in range(min(len(headers), len(row_data)))}
                            rows.append(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析问财响应失败: %s", exc)

    return rows[:top_n]