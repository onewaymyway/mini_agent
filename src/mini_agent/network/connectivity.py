"""
network/connectivity.py — 通用网络连通性检测。

设计目标：这是一个独立、无副作用的基础能力，不绑定任何具体调用场景
（LLM 请求 / web_search / MCP / 任何 HTTP 客户端），谁都能 import 来用：

    from mini_agent.network.connectivity import is_online, wait_until_online, is_connectivity_exception

    if not is_online():
        ...

    recovered = wait_until_online(max_wait=300)  # 最多等 5 分钟

    except Exception as e:
        if is_connectivity_exception(e):
            ...

三个核心能力：
  is_online()              — 探测当前是否联网（TCP 连接到几个公共地址，带短期缓存）
  wait_until_online()       — 阻塞轮询直到网络恢复或超时
  is_connectivity_exception() — 判断一个异常"看起来像"网络层失败（DNS/连接/超时），
                                 而不是业务逻辑错误（比如 401 鉴权失败、400 参数错误）

典型用途（本项目里）：LLM 调用重试框架（llm/retry.py）用它来判断"请求失败是不是
因为断网"——如果是，重试没有意义（网络没恢复，重试大概率还是失败，纯粹浪费重试
预算和时间），应该改成等网络恢复再重试，而不是按固定退避策略盲目重试。

不依赖第三方库，只用标准库 socket，所以在任何环境（包括没装 requests/httpx 的
最小环境）都能正常工作。
"""
from __future__ import annotations

import socket
import ssl
import time
from typing import Callable, Optional, Sequence

# ── 探测目标 ────────────────────────────────────────────────────────────────
# 选取几个不同厂商、大概率常年在线的公共地址做 TCP 443 探测，尽量覆盖国内外
# 网络环境（只要能连上其中任意一个即认为"在线"，不要求全部可达——很多内网/
# 代理环境只能访问其中一部分）。TCP connect 探测本身不发送任何应用层数据，
# 也不依赖 DNS 之外的协议细节，是最轻量、误报率最低的连通性检测方式。
DEFAULT_PROBE_TARGETS: tuple = (
    ("1.1.1.1", 443),        # Cloudflare DNS
    ("8.8.8.8", 443),        # Google DNS
    ("223.5.5.5", 443),      # 阿里 DNS，兼顾国内网络环境
    ("119.29.29.29", 443),   # 腾讯 DNS，同上
)

_CACHE_TTL_SECONDS = 2.0  # 短期缓存：避免同一秒内被多处调用方反复触发真实探测

_last_check_ts: float = 0.0
_last_check_result: bool = True  # 启动时乐观假设在线，避免误判影响首次调用


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    """对单个目标做一次 TCP connect 探测，成功即视为该目标可达。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_online(
    timeout: float = 2.5,
    use_cache: bool = True,
    targets: Optional[Sequence[tuple]] = None,
) -> bool:
    """
    探测当前是否联网。任意一个探测目标可达即返回 True。

    Args:
        timeout:   单个探测目标的连接超时（秒）
        use_cache: 是否使用短期缓存（默认 True）。短时间内被多处高频调用时
                   避免每次都发起真实网络探测；需要"这一刻的确切状态"时
                   （比如 wait_until_online 轮询期间）传 False 强制重新探测。
        targets:   自定义探测目标列表，不传则用 DEFAULT_PROBE_TARGETS

    Returns:
        True  = 至少一个探测目标可达，判定为在线
        False = 所有探测目标均不可达，判定为离线
    """
    global _last_check_ts, _last_check_result

    now = time.monotonic()
    if use_cache and (now - _last_check_ts) < _CACHE_TTL_SECONDS:
        return _last_check_result

    online = any(_tcp_probe(host, port, timeout) for host, port in (targets or DEFAULT_PROBE_TARGETS))
    _last_check_result = online
    _last_check_ts = now
    return online


def wait_until_online(
    check_interval: float = 5.0,
    max_wait: float = 0.0,
    on_waiting: Optional[Callable[[float], None]] = None,
    targets: Optional[Sequence[tuple]] = None,
) -> bool:
    """
    阻塞轮询，直到网络恢复或等待超时。

    Args:
        check_interval: 每次探测之间的间隔（秒）
        max_wait:       最长等待时间（秒），<= 0 表示不限时长、一直等到恢复为止
        on_waiting:     每轮探测仍未恢复时的回调，签名 on_waiting(elapsed_seconds)，
                        可用于打印"已等待 N 秒，仍未恢复网络"之类的提示；
                        回调内部异常会被吞掉，不影响轮询本身
        targets:        透传给 is_online() 的自定义探测目标

    Returns:
        True  — 网络已恢复
        False — 达到 max_wait 仍未恢复（仅当 max_wait > 0 时可能返回 False）
    """
    start = time.monotonic()
    while True:
        if is_online(use_cache=False, targets=targets):
            return True
        elapsed = time.monotonic() - start
        if max_wait and max_wait > 0 and elapsed >= max_wait:
            return False
        if on_waiting:
            try:
                on_waiting(elapsed)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.network.connectivity.wait_until_online')
                pass
        time.sleep(check_interval)


# ── 网络类异常识别 ────────────────────────────────────────────────────────────

# 标准库里能直接覆盖的网络类异常。ConnectionError 是内置基类，覆盖
# ConnectionRefusedError / ConnectionResetError / ConnectionAbortedError /
# BrokenPipeError；socket.timeout 在 Python 3.10+ 是 TimeoutError 的别名，
# 两个都列出来兼容旧版本。
_NETWORK_ERROR_TYPES: tuple = (
    ConnectionError,
    socket.gaierror,     # DNS 解析失败
    socket.timeout,
    TimeoutError,
    ssl.SSLError,
)

# 第三方 SDK/HTTP 库的连接类异常类名。不在模块顶层直接 import 这些包——
# mini-agent 支持多种 LLM provider，不能假设所有包都装了；用运行时尝试
# import 的方式，装了就识别，没装就跳过，不影响主流程。
_THIRD_PARTY_NETWORK_EXCEPTIONS: tuple = (
    ("httpx", "ConnectError"),
    ("httpx", "ConnectTimeout"),
    ("httpx", "ReadTimeout"),
    ("httpx", "NetworkError"),
    ("requests.exceptions", "ConnectionError"),
    ("requests.exceptions", "Timeout"),
    ("requests.exceptions", "ConnectTimeout"),
    ("urllib3.exceptions", "NewConnectionError"),
    ("urllib3.exceptions", "MaxRetryError"),
    ("anthropic", "APIConnectionError"),
    ("anthropic", "APITimeoutError"),
    ("openai", "APIConnectionError"),
    ("openai", "APITimeoutError"),
)

# 兜底：以上异常类型都没命中时，看异常信息里有没有典型的网络失败措辞。
# 这是最后一道防线，不同库、不同操作系统的报错文案差异很大，能覆盖多少
# 算多少，不追求穷举完备——宁可漏判（当成非网络异常走正常重试逻辑，无非是
# 多重试几次浪费一点时间），也不要错判把"业务错误"当成"网络错误"进而
# 无限期阻塞等待。
_NETWORK_ERROR_MESSAGE_HINTS: tuple = (
    "connection error",
    "connection refused",
    "connection reset",
    "connection aborted",
    "remote end closed connection",
    "name or service not known",
    "nodename nor servname provided",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "network is unreachable",
    "no route to host",
    "failed to establish a new connection",
    "read timed out",
    "max retries exceeded with url",
    "connection timed out",
)


def is_connectivity_exception(exc: BaseException) -> bool:
    """
    判断一个异常"看起来像"网络层失败（DNS 解析失败/连接被拒/连接超时等），
    而不是业务逻辑错误（鉴权失败、参数错误、模型返回 4xx 等）。

    判断顺序：标准库异常类型 → 已安装的第三方 SDK 异常类型 → 异常文案关键词兜底。
    任意一步命中即返回 True。
    """
    if isinstance(exc, _NETWORK_ERROR_TYPES):
        return True

    for module_name, class_name in _THIRD_PARTY_NETWORK_EXCEPTIONS:
        try:
            module = __import__(module_name, fromlist=[class_name])
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.network.connectivity.is_connectivity_exception')
            continue
        cls = getattr(module, class_name, None)
        if cls is not None and isinstance(exc, cls):
            return True

    message = str(exc).lower()
    return any(hint in message for hint in _NETWORK_ERROR_MESSAGE_HINTS)
