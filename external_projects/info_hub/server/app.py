"""看板服务：标准库 http.server 实现，无额外依赖。

安全约定：
- 只绑定 127.0.0.1，不对外网暴露。
- 所有 POST 必须携带请求头 X-Info-Hub: 1。浏览器跨站表单无法附加自定义头，
  从而防止其他网页在你不知情时触发抓取或报告生成。
- 静态文件只从 ui/ 目录读取，报告路径限定在 reports/ 之内。
"""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from core.settings import Settings
from server import api
from server.api import ApiError

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
CUSTOM_HEADER = "X-Info-Hub"


def build_routes(settings: Settings) -> tuple[dict, dict]:
    get_routes: dict[str, Callable[[dict[str, list[str]]], Any]] = {
        "/api/sources": lambda q: api.list_sources(settings),
        "/api/reports": lambda q: api.list_reports(settings),
        "/api/report": lambda q: api.read_report(settings, q.get("path", [""])[0]),
        "/api/report/speech": lambda q: api.report_speech(settings, q.get("path", [""])[0]),
        "/api/item/speech": lambda q: api.item_speech(settings, q.get("source", [""])[0], q.get("id", [""])[0]),
        "/api/runs": lambda q: api.list_runs(settings, q.get("day", [None])[0]),
        "/api/login-status": lambda q: api.login_status(settings),
    }
    post_routes: dict[str, Callable[[dict[str, Any]], Any]] = {
        "/api/tick": lambda body: api.run_tick_now(settings),
        "/api/reports/generate": lambda body: api.generate_report_now(settings, body.get("plan_id", "")),
    }
    return get_routes, post_routes


def make_handler(settings: Settings):
    get_routes, post_routes = build_routes(settings)

    class Handler(BaseHTTPRequestHandler):
        server_version = "info_hub/0.5"

        def log_message(self, fmt, *args):  # 减少控制台噪音
            pass

        # ---- 工具 ----
        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, rel: str) -> None:
            target = (UI_DIR / rel.lstrip("/")).resolve()
            if UI_DIR.resolve() not in target.parents and target != UI_DIR.resolve():
                self._send_json(403, {"error": "forbidden"})
                return
            if not target.is_file():
                self._send_json(404, {"error": "not found"})
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith("text") or ctype.endswith("javascript") else ctype)
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

        # ---- 分发 ----
        def do_GET(self):
            if not self._local_only():
                return self._send_json(403, {"error": "仅允许本机访问"})
            url = urlparse(self.path)
            try:
                if url.path.startswith("/api/"):
                    handler = get_routes.get(url.path)
                    if handler is None:
                        return self._send_json(404, {"error": "未知接口"})
                    return self._send_json(200, handler(parse_qs(url.query)))
                if url.path in ("", "/"):
                    return self._send_static("index.html")
                return self._send_static(url.path)
            except ApiError as exc:
                return self._send_json(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001 - 返回可读错误，不泄露堆栈
                return self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):
            if not self._local_only():
                return self._send_json(403, {"error": "仅允许本机访问"})
            if self.headers.get(CUSTOM_HEADER) != "1":
                return self._send_json(403, {"error": f"缺少请求头 {CUSTOM_HEADER}: 1"})
            url = urlparse(self.path)
            try:
                parts = url.path.split("/")
                # /api/sources/<id>/run  与  /api/sources/<id>/enabled
                if len(parts) == 5 and parts[1] == "api" and parts[2] == "sources":
                    sid, action = parts[3], parts[4]
                    body = self._read_body()
                    if action == "run":
                        return self._send_json(200, api.run_source_now(settings, sid))
                    if action == "enabled":
                        return self._send_json(200, api.set_source_enabled(settings, sid, bool(body.get("enabled"))))
                    return self._send_json(404, {"error": "未知操作"})
                handler = post_routes.get(url.path)
                if handler is None:
                    return self._send_json(404, {"error": "未知接口"})
                return self._send_json(200, handler(self._read_body()))
            except ApiError as exc:
                return self._send_json(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001
                return self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


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
