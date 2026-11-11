"""看板服务：标准库 http.server 实现，无额外依赖。

安全约定：
- 只绑定 127.0.0.1，Host 头不是本机则拒绝。
- 所有 POST 必须携带请求头 X-Info-Hub: 1。浏览器跨站表单无法附加自定义头，
  从而防止其他网页在你不知情时触发抓取、生成草稿或确认写入信息源。
- 静态文件只从 ui/ 目录读取，报告路径限定在 reports/ 之内（见 api 模块）。

路由是正则表驱动的：新增接口只需在 build_router 中追加一行。
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from core.settings import Settings
from server import api
from server.api import ApiError

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
CUSTOM_HEADER = "X-Info-Hub"

# handler(settings, match, query_dict, body_dict) -> payload
Handler = Callable[[Settings, re.Match, dict, dict], Any]


def build_router() -> list[tuple[str, re.Pattern, Handler]]:
    R: list[tuple[str, re.Pattern, Handler]] = []

    def route(method: str, pattern: str):
        def deco(fn: Handler):
            R.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    # 信息源与运行
    @route("GET", r"/api/sources")
    def _(s, m, q, b): return api.list_sources(s)

    @route("POST", r"/api/sources/([a-z0-9_]+)/run")
    def _(s, m, q, b): return api.run_source_now(s, unquote(m.group(1)))

    @route("POST", r"/api/sources/([a-z0-9_]+)/enabled")
    def _(s, m, q, b): return api.set_source_enabled(s, unquote(m.group(1)), bool(b.get("enabled")))

    # 报告
    @route("GET", r"/api/reports")
    def _(s, m, q, b): return api.list_reports(s)

    @route("GET", r"/api/report")
    def _(s, m, q, b): return api.read_report(s, q.get("path", [""])[0])

    @route("GET", r"/api/report/speech")
    def _(s, m, q, b): return api.report_speech(s, q.get("path", [""])[0])

    @route("GET", r"/api/item/speech")
    def _(s, m, q, b): return api.item_speech(s, q.get("source", [""])[0], q.get("id", [""])[0])

    @route("POST", r"/api/reports/generate")
    def _(s, m, q, b): return api.generate_report_now(s, str(b.get("plan_id", "")))

    @route("POST", r"/api/tick")
    def _(s, m, q, b): return api.run_tick_now(s)

    # 运行记录与登录
    @route("GET", r"/api/runs")
    def _(s, m, q, b): return api.list_runs(s, q.get("day", [None])[0])

    @route("GET", r"/api/login-status")
    def _(s, m, q, b): return api.login_status(s)

    # P7：添加信息源
    @route("GET", r"/api/llm-status")
    def _(s, m, q, b): return api.provider_status(s)

    @route("POST", r"/api/sources/preview")
    def _(s, m, q, b): return api.preview_form(s, b)

    @route("POST", r"/api/sources/create")
    def _(s, m, q, b): return api.create_form(s, b)

    @route("POST", r"/api/sources/generate")
    def _(s, m, q, b): return api.generate_source(s, b)

    @route("GET", r"/api/drafts")
    def _(s, m, q, b): return api.list_drafts(s)

    @route("GET", r"/api/drafts/([a-z0-9_]+)")
    def _(s, m, q, b): return api.get_draft(s, m.group(1))

    @route("POST", r"/api/drafts/([a-z0-9_]+)/revise")
    def _(s, m, q, b): return api.revise_draft(s, m.group(1), b)

    @route("POST", r"/api/drafts/([a-z0-9_]+)/preview")
    def _(s, m, q, b): return api.preview_draft(s, m.group(1))

    @route("POST", r"/api/drafts/([a-z0-9_]+)/confirm")
    def _(s, m, q, b): return api.confirm_draft(s, m.group(1), b)

    @route("POST", r"/api/drafts/([a-z0-9_]+)/discard")
    def _(s, m, q, b): return api.discard_draft(s, m.group(1))

    return R


def match_route(router, method: str, path: str):
    """返回 (handler, match)；路径存在但方法不对时抛出 405。"""
    path_matched = False
    for m_name, pattern, fn in router:
        m = pattern.match(path)
        if m is None:
            continue
        path_matched = True
        if m_name == method:
            return fn, m
    if path_matched:
        raise ApiError(405, "请求方法不支持")
    raise ApiError(404, "未知接口")


def make_handler(settings: Settings):
    router = build_router()

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "info_hub/0.7"

        def log_message(self, fmt, *args):  # 减少控制台噪音
            pass

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, rel: str) -> None:
            base = UI_DIR.resolve()
            target = (UI_DIR / rel.lstrip("/")).resolve()
            if target != base and base not in target.parents:
                return self._send_json(403, {"error": "forbidden"})
            if not target.is_file():
                return self._send_json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            body = target.read_bytes()
            self.send_response(200)
            text_like = ctype.startswith("text") or ctype.endswith("javascript")
            self.send_header("Content-Type", f"{ctype}; charset=utf-8" if text_like else ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ApiError(400, f"请求体不是合法 JSON: {exc}")
            if not isinstance(data, dict):
                raise ApiError(400, "请求体必须是 JSON 对象")
            return data

        def _local_only(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost", "")

        def _dispatch(self, method: str) -> None:
            if not self._local_only():
                return self._send_json(403, {"error": "仅允许本机访问"})
            url = urlparse(self.path)
            if method == "POST" and self.headers.get(CUSTOM_HEADER) != "1":
                return self._send_json(403, {"error": f"缺少请求头 {CUSTOM_HEADER}: 1"})
            try:
                if url.path.startswith("/api/"):
                    fn, m = match_route(router, method, url.path)
                    query = parse_qs(url.query)
                    body = self._read_body() if method == "POST" else {}
                    return self._send_json(200, fn(settings, m, query, body))
                if method == "GET":
                    return self._send_static("index.html" if url.path in ("", "/") else url.path)
                return self._send_json(404, {"error": "未知接口"})
            except ApiError as exc:
                return self._send_json(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001 - 返回可读错误，不泄露堆栈
                return self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return RequestHandler


def start_tick_loop(settings: Settings, stop: threading.Event, interval_sec: int) -> threading.Thread:
    from core.tick import tick

    def loop():
        while not stop.is_set():
            try:
                tick(settings)
            except Exception as exc:  # noqa: BLE001 - 循环不能因单次失败退出
                print(f"[tick] 失败: {type(exc).__name__}: {exc}")
            stop.wait(interval_sec)

    t = threading.Thread(target=loop, name="info_hub-tick", daemon=True)
    t.start()
    return t


def make_server(settings: Settings, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    host = host or settings.server_host
    port = port if port is not None else settings.server_port
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("看板只允许绑定 127.0.0.1 或 localhost")
    return ThreadingHTTPServer((host, port), make_handler(settings))
