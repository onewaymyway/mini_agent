"""
llm/providers/_http.py — LLM provider 的 HTTP 网络层辅助

只服务于 LLM provider，把 ``LLMConfig.trust_env``（来自 agent_config.json 的
``llm_network.trust_env``）落实到各 provider 实际使用的 HTTP 客户端上：

  - trust_env=True（默认）：什么都不做，各 SDK / urllib 保持原有默认行为，
    即读取 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY 及系统代理。
    返回 None / 不改动，保证默认路径与引入本模块之前逐字节一致。
  - trust_env=False：强制直连，忽略环境变量与系统代理。

不影响 web_search、webhook 通知、代理池等非 LLM 的网络调用。
"""

from __future__ import annotations

from typing import Any, Optional


def _httpx_flavour_of(client_cls: Any) -> Any:
    """返回 SDK 默认客户端所基于的 httpx 模块（可能是 ``httpx``，新版 SDK 为 ``httpx2``）。

    SDK 会拒绝另一个 flavour 的对象（例如 anthropic 新版拒绝 ``httpx.HTTPTransport``），
    所以 transport 必须取自与 DefaultHttpxClient 同一个包。
    """
    import importlib

    for cls in client_cls.__mro__:
        root = str(getattr(cls, "__module__", "")).partition(".")[0]
        if cls.__name__ == "Client" and root.startswith("httpx"):
            return importlib.import_module(root)
    import httpx
    return httpx


def build_sdk_http_client(sdk_module: Any, config: Any) -> Optional[Any]:
    """为 openai / anthropic SDK 构造 ``http_client``。

    trust_env=True 时返回 None（调用方不传 http_client，使用 SDK 默认客户端，行为不变）。

    trust_env=False 时返回强制直连的 SDK 默认客户端。注意仅传 ``trust_env=False``
    不够：部分 SDK 版本（如 anthropic 新版）的 DefaultHttpxClient 会在构造时自己调用
    ``get_environment_proxies()`` 建立代理 mounts，与 httpx 的 trust_env 无关。
    SDK 在调用方显式提供 ``transport`` 时会跳过这段逻辑，所以这里显式给一个
    ``trust_env=False`` 的 HTTPTransport，既不读环境代理，也沿用 SDK 的连接池上限。
    """
    if getattr(config, "trust_env", True):
        return None

    client_cls = getattr(sdk_module, "DefaultHttpxClient", None)
    if client_cls is None:
        # 过旧、没有 DefaultHttpxClient 的 SDK：用 httpx 的 trust_env=False 直连
        import httpx
        return httpx.Client(trust_env=False, follow_redirects=True)

    httpx_mod = _httpx_flavour_of(client_cls)
    transport_kwargs: dict = {"trust_env": False}
    try:
        import importlib
        base = importlib.import_module(f"{sdk_module.__name__}._base_client")
        limits = getattr(base, "DEFAULT_CONNECTION_LIMITS", None)
        if limits is not None:
            transport_kwargs["limits"] = limits
    except Exception:
        pass
    return client_cls(trust_env=False, transport=httpx_mod.HTTPTransport(**transport_kwargs))


def build_urllib_opener(config: Any):
    """为 urllib 系 provider（ollama）构造 opener。

    trust_env=True 时返回 None（调用方继续用 ``urllib.request.urlopen``，行为不变）。
    trust_env=False 时返回带空 ProxyHandler 的 opener，显式禁用任何代理。
    """
    if getattr(config, "trust_env", True):
        return None

    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))
