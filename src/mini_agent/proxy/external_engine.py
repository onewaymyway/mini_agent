"""外部引擎 fallback(sing-box 优先,xray 次选)。

纯 Python 目前能安全实现的只有 ss / trojan / 普通 vless(tls 或 none,没有
xtls-rprx-vision flow、没有 REALITY)。以下几种情况纯 Python 明确做不了,
必须借助外部成熟实现,不然出的错很难排查(看起来像实现了,实际连不上):

  - vmess:自定义 AEAD 头部加密(自定义 KDF 链 + AES-ECB 认证 ID)
  - vless + flow=xtls-rprx-vision:需要按 Vision 规则做流量填充/拼接
  - vless + security=reality:REALITY 需要伪造借用真实网站证书的 TLS
    ClientHello,依赖 uTLS 级别的指纹伪装能力
  - hysteria2:基于 QUIC(UDP)的协议,和 TCP 系的 ss/vless/trojan 完全是
    两套技术栈,需要 aioquic 从头实现认证+流复用

这里选择接入 **sing-box**(https://github.com/SagerNet/sing-box)而不是
xray-core 作为主要 fallback 引擎,因为 sing-box 一个二进制就能覆盖上面列的
全部协议(包括 hysteria2 和 vless-reality-vision),而 xray-core 不支持
hysteria2。机器上如果只装了 xray(没装 sing-box),vmess 和"不带
reality/vision 的 vless"仍然可以退化用 xray 处理,但 hysteria2 和
vless-reality-vision 只有 sing-box 能覆盖。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .subscription import ProxyNode
from .xray_runner import find_free_port

# 除了系统 PATH,也在项目本地目录里找,这样可以直接把下载的二进制解压到项目里,
# 不用去改系统环境变量。约定路径: <project_root>/tools/<name>/<name>(.exe)
# 以及 <project_root>/tools/<name>(.exe) (不建子目录也认)。
_LOCAL_BIN_SUBDIRS = ["tools"]


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found

    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    project_root = Path.cwd()  # proxy_ctl.py / repl 启动时的工作目录即项目根目录
    candidates = [
        project_root / "tools" / exe_name,
        project_root / "tools" / name / exe_name,
        project_root / "sing_box" / exe_name,
        project_root / "sing_box" / name / exe_name,
    ]
    # print("candidates:",candidates)
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def singbox_available() -> bool:
    return _find_binary("sing-box") is not None


def xray_available() -> bool:
    return _find_binary("xray") is not None


def needs_external_engine(node: ProxyNode) -> bool:
    """判断这个节点纯 Python 是否处理不了,需要走外部引擎。"""
    if node.protocol in ("vmess", "hysteria2", "hy2"):
        return True
    if node.protocol == "vless":
        p = node.params
        if p.get("flow"):  # 主要是 xtls-rprx-vision
            return True
        if p.get("security") == "reality":
            return True
    return False


def _bool_param(v) -> bool:
    return str(v).lower() in ("1", "true", "yes")


def build_singbox_outbound(node: ProxyNode) -> dict:
    p = node.params
    if node.protocol == "vless":
        tls: dict = {}
        security = p.get("security", "")
        if security in ("tls", "reality"):
            tls = {
                "enabled": True,
                "server_name": p.get("sni") or p.get("host") or node.server,
                "insecure": _bool_param(p.get("allowInsecure")),
            }
            if p.get("fp"):
                tls["utls"] = {"enabled": True, "fingerprint": p.get("fp")}
            if security == "reality":
                tls["reality"] = {
                    "enabled": True,
                    "public_key": p.get("pbk", ""),
                    "short_id": p.get("sid", ""),
                }
        outbound = {
            "type": "vless",
            "tag": "proxy",
            "server": node.server,
            "server_port": node.port,
            "uuid": p.get("_userinfo", ""),
            "packet_encoding": "xudp",
        }
        if p.get("flow"):
            outbound["flow"] = p["flow"]
        if tls:
            outbound["tls"] = tls
        net_type = p.get("type", "tcp")
        if net_type == "ws":
            outbound["transport"] = {
                "type": "ws",
                "path": p.get("path", "/"),
                "headers": {"Host": p.get("host", node.server)},
            }
        elif net_type == "grpc":
            outbound["transport"] = {"type": "grpc", "service_name": p.get("serviceName", "")}
        return outbound

    if node.protocol == "vmess":
        return {
            "type": "vmess",
            "tag": "proxy",
            "server": node.server,
            "server_port": node.port,
            "uuid": p.get("id", ""),
            "security": p.get("scy", "auto"),
            "alter_id": int(p.get("aid", 0) or 0),
        }

    if node.protocol == "trojan":
        return {
            "type": "trojan",
            "tag": "proxy",
            "server": node.server,
            "server_port": node.port,
            "password": p.get("_userinfo", ""),
            "tls": {"enabled": True, "server_name": p.get("sni") or node.server,
                     "insecure": _bool_param(p.get("allowInsecure"))},
        }

    if node.protocol == "ss":
        return {
            "type": "shadowsocks",
            "tag": "proxy",
            "server": node.server,
            "server_port": node.port,
            "method": p.get("method"),
            "password": p.get("password"),
        }

    if node.protocol in ("hysteria2", "hy2"):
        outbound = {
            "type": "hysteria2",
            "tag": "proxy",
            "server": node.server,
            "server_port": node.port,
            "password": p.get("_userinfo", ""),
            "tls": {
                "enabled": True,
                "server_name": p.get("sni") or p.get("peer") or node.server,
                "insecure": _bool_param(p.get("insecure")),
            },
        }
        obfs_type = p.get("obfs")
        if obfs_type:
            outbound["obfs"] = {
                "type": obfs_type,
                "password": p.get("obfs-password") or p.get("obfsParam", ""),
            }
        return outbound

    raise ValueError(f"sing-box outbound builder: unsupported protocol '{node.protocol}'")


async def _unlink_with_retry(path: Path, attempts: int = 5, delay: float = 0.2) -> None:
    """Windows 上进程刚退出的一瞬间,文件句柄有时还没被内核完全释放,
    立刻 unlink 会报 PermissionError(WinError 32)。用短暂重试代替一次性失败,
    多次重试后仍失败就放弃(只是临时文件没删掉,不影响功能)。"""
    for i in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if i == attempts - 1:
                return
            await asyncio.sleep(delay)


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
                # kill() 只是发信号,Windows 上进程(以及它打开的配置文件句柄)
                # 不会在 kill() 返回的瞬间就立刻释放,必须再 wait 一次确保真正退出,
                # 否则下面的 unlink 大概率撞上"文件被其他程序占用"。
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    pass
        await _unlink_with_retry(self.config_path)


async def start_local_proxy_singbox(node: ProxyNode, local_port: int | None = None) -> RunningProxy:
    binary = _find_binary("sing-box")
    if not binary:
        raise RuntimeError("未找到 sing-box 可执行文件,请安装后放到 PATH 或项目根目录的 tools/ 下: https://github.com/SagerNet/sing-box/releases")

    local_port = local_port or find_free_port()
    config = {
        "log": {"level": "error"},
        "inbounds": [
            {"type": "socks", "tag": "in", "listen": "127.0.0.1", "listen_port": local_port}
        ],
        "outbounds": [build_singbox_outbound(node)],
    }
    fd, path_str = tempfile.mkstemp(prefix="singbox_node_", suffix=".json")
    # mkstemp 返回的 fd 必须关掉:不关的话这个文件句柄由我们自己的 Python 进程
    # 一直占着,Windows 上后面 unlink 时哪怕 sing-box 子进程早就退出了,也会
    # 因为"另一个程序"(其实就是我们自己)占用文件而报 PermissionError(WinError 32)。
    os.close(fd)
    config_path = Path(path_str)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        binary, "run", "-c", str(config_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.4)
    return RunningProxy(node=node, local_port=local_port, process=process, config_path=config_path)


async def start_local_proxy(node: ProxyNode, local_port: int | None = None):
    """按可用引擎自动选择: 优先 sing-box(协议覆盖面最广),否则退化到 xray(仅 vmess/普通 vless)。"""
    if singbox_available():
        return await start_local_proxy_singbox(node, local_port)
    if xray_available() and node.protocol in ("vmess", "vless"):
        from . import xray_runner

        return await xray_runner.start_local_proxy(node, local_port)
    raise RuntimeError(
        f"节点协议 '{node.protocol}' 需要外部引擎(sing-box 或 xray),但本机都没检测到。"
        "推荐安装 sing-box(覆盖面更广,支持 hysteria2/reality/vision): "
        "https://github.com/SagerNet/sing-box/releases"
    )