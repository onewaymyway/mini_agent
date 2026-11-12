"""本地反馈接收服务：接收专用浏览器插件发来的反馈与打开记录。

启动：  cd external_projects/info_hub && python -m server.feedback_receiver
安全：  只监听 127.0.0.1；每个请求需要 X-InfoHub-Token；CORS 只允许 chrome-extension:// 来源；
        只开放 /v1/feedback 与 /v1/open 两个写接口，内部复用 server/api.py 的校验与存储。
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from core.dedicated_browser import load_config
from core.settings import Settings, load_settings
from core.store import atomic_write_text
from server import api
from server.api import ApiError

MAX_BODY = 16 * 1024
TOKEN_HEADER = "X-InfoHub-Token"
ALLOWED_ORIGIN_PREFIX = "chrome-extension://"


def token_path(settings: Settings) -> Path:
    return settings.paths.state_dir / "receiver_token.txt"


def load_or_create_token(settings: Settings) -> str:
    path = token_path(settings)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    atomic_write_text(path, token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 等平台忽略权限位
    return token


def make_handler(settings: Settings, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "InfoHubReceiver/1"

        def log_message(self, fmt, *args):  # 不输出访问日志，避免记录令牌相关信息
            return

        def _origin_ok(self) -> Optional[str]:
            origin = self.headers.get("Origin", "")
            return origin if origin.startswith(ALLOWED_ORIGIN_PREFIX) else None

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            origin = self._origin_ok()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # 预检请求，不需要令牌
            if self._origin_ok():
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self._origin_ok())
                self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(403, {"error": "来源不允许"})

        def do_GET(self):
            if self.path == "/v1/ping":
                self._send(200, {"ok": True, "version": 1})
            else:
                self._send(404, {"error": "不存在"})

        def do_POST(self):
            if self.path not in ("/v1/feedback", "/v1/open"):
                return self._send(404, {"error": "不存在"})
            if self.headers.get("Origin") and not self._origin_ok():
                return self._send(403, {"error": "来源不允许"})
            given = self.headers.get(TOKEN_HEADER, "")
            if not given or not hmac.compare_digest(given, token):
                return self._send(401, {"error": "令牌无效"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, {"error": "请求长度无效"})
            if length <= 0 or length > MAX_BODY:
                return self._send(413 if length > MAX_BODY else 400, {"error": "请求体为空或过大"})
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send(400, {"error": "请求体不是合法 JSON"})
            if not isinstance(body, dict):
                return self._send(400, {"error": "请求体必须是对象"})
            try:
                if self.path == "/v1/feedback":
                    out = api.record_feedback(settings, body)
                else:
                    out = api.record_open_api(settings, body)
            except ApiError as exc:
                return self._send(exc.status, {"error": exc.message})
            self._send(200, out)

    return Handler


def make_server(settings: Settings, port: Optional[int] = None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("接收服务只允许绑定 127.0.0.1")
    token = load_or_create_token(settings)
    p = port if port is not None else load_config(settings.paths.root)["receiver_port"]
    return ThreadingHTTPServer((host, p), make_handler(settings, token))


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("INFO_HUB_ROOT") or Path(__file__).resolve().parents[1])
    settings = load_settings(root)
    port = int(argv[0]) if argv else None
    server = make_server(settings, port)
    host, actual = server.server_address[:2]
    print(f"info_hub 反馈接收服务已启动：http://{host}:{actual}")
    print(f"令牌文件：{token_path(settings)}（在扩展选项中填写该令牌）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
