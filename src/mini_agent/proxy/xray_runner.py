"""把 ProxyNode(vmess/vless/ss/trojan) 转换成本地 xray-core 进程 + 本地 SOCKS5 端口。

依赖: 需要机器上有 xray 可执行文件 (https://github.com/XTLS/Xray-core)。
这里只负责"生成配置 + 拉起/关闭子进程",不做协议本身的重新实现——
自己解析 vmess/vless 加密细节没有必要,xray-core 本身就是最标准的实现。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .subscription import ProxyNode


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_xray_binary() -> str | None:
    found = shutil.which("xray")
    if found:
        return found
    exe_name = "xray.exe" if sys.platform == "win32" else "xray"
    project_root = Path.cwd()
    for c in (project_root / "tools" / exe_name, project_root / "tools" / "xray" / exe_name):
        if c.is_file():
            return str(c)
    return None


def xray_binary_available() -> bool:
    return _find_xray_binary() is not None


def build_outbound(node: ProxyNode) -> dict:
    """按协议生成 xray outbound 配置片段。"""
    if node.protocol == "vmess":
        p = node.params
        return {
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": node.server,
                        "port": node.port,
                        "users": [
                            {
                                "id": p.get("id"),
                                "alterId": int(p.get("aid", 0) or 0),
                                "security": p.get("scy", "auto"),
                            }
                        ],
                    }
                ]
            },
            "streamSettings": {
                "network": p.get("net", "tcp"),
                "security": p.get("tls", ""),
                "wsSettings": {"path": p.get("path", "/"), "headers": {"Host": p.get("host", "")}}
                if p.get("net") == "ws"
                else None,
            },
        }
    if node.protocol == "vless":
        p = node.params
        return {
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": node.server,
                        "port": node.port,
                        "users": [{"id": p.get("_userinfo"), "encryption": p.get("encryption", "none")}],
                    }
                ]
            },
            "streamSettings": {
                "network": p.get("type", "tcp"),
                "security": p.get("security", ""),
                "wsSettings": {"path": p.get("path", "/"), "headers": {"Host": p.get("host", "")}}
                if p.get("type") == "ws"
                else None,
            },
        }
    if node.protocol == "trojan":
        p = node.params
        return {
            "protocol": "trojan",
            "settings": {
                "servers": [{"address": node.server, "port": node.port, "password": p.get("_userinfo")}]
            },
            "streamSettings": {"network": "tcp", "security": p.get("security", "tls")},
        }
    if node.protocol == "ss":
        p = node.params
        return {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": node.server,
                        "port": node.port,
                        "method": p.get("method"),
                        "password": p.get("password"),
                    }
                ]
            },
        }
    raise ValueError(f"unsupported protocol for xray: {node.protocol}")


@dataclass
class RunningProxy:
    node: ProxyNode
    local_port: int
    process: asyncio.subprocess.Process
    config_path: Path

    @property
    def socks_url(self) -> str:
        return f"socks5://127.0.0.1:{self.local_port}"

    async def stop(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
        self.config_path.unlink(missing_ok=True)


async def start_local_proxy(node: ProxyNode, local_port: int | None = None) -> RunningProxy:
    """为单个节点起一个本地 xray 进程,监听 SOCKS5。"""
    binary = _find_xray_binary()
    if not binary:
        raise RuntimeError("未找到 xray 可执行文件,请安装后放到 PATH 或项目根目录的 tools/ 下")

    local_port = local_port or find_free_port()
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": local_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            }
        ],
        "outbounds": [build_outbound(node)],
    }

    fd, path_str = tempfile.mkstemp(prefix="xray_node_", suffix=".json")
    config_path = Path(path_str)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        binary,
        "run",
        "-c",
        str(config_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # 给 xray 一点启动时间
    await asyncio.sleep(0.3)
    return RunningProxy(node=node, local_port=local_port, process=process, config_path=config_path)