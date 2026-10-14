"""llm_network.trust_env —— 强制 LLM 请求直连（忽略环境/系统代理）。

覆盖：
  1. 配置：默认 True（向后兼容），agent_config.json 的 llm_network 块可关闭
  2. LLMConfig 传递：from_app_config / providers 条目覆盖 / 重建 client 不丢字段
  3. 各 provider 的 HTTP 客户端确实不再读取代理
  4. 端到端：环境代理指向一个必然连不上的端口时，trust_env=False 仍能直连成功
  5. 默认路径不变：trust_env=True 时仍然遵循环境代理
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from mini_agent.config.models import AppConfig, LLMNetworkConfig
from mini_agent.config.param_registry import load_all_nested_blocks
from mini_agent.llm.base import LLMConfig, resolve_trust_env
from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry
from mini_agent.llm.factory import create_client

_DEAD_PROXY = "http://127.0.0.1:1"   # 端口 1 无人监听：走了代理必然失败


@pytest.fixture
def dead_env_proxy(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, _DEAD_PROXY)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if self.path.endswith("/api/chat"):
            body = {"message": {"role": "assistant", "content": "pong"},
                    "done": True, "prompt_eval_count": 1, "eval_count": 1}
        elif self.path.endswith("/chat/completions"):
            body = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "pong"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        elif self.path.endswith("/messages"):
            body = {"id": "x", "type": "message", "role": "assistant", "model": "m",
                    "content": [{"type": "text", "text": "pong"}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def local_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


# ── 1. 配置 ──────────────────────────────────────────────────────────────

def test_default_is_backward_compatible():
    assert AppConfig().llm_network.trust_env is True
    assert LLMConfig(provider="openai", model="m").trust_env is True


def test_agent_config_json_block_is_loaded():
    blocks = load_all_nested_blocks({"llm_network": {"trust_env": False}})
    assert blocks["llm_network"].trust_env is False
    blocks = load_all_nested_blocks({})
    assert blocks["llm_network"].trust_env is True


# ── 2. LLMConfig 传递 ────────────────────────────────────────────────────

def test_from_app_config_propagates():
    cfg = AppConfig(api_key="k", llm_network=LLMNetworkConfig(trust_env=False))
    assert LLMConfig.from_app_config(cfg).trust_env is False
    assert LLMConfig.from_app_config(AppConfig(api_key="k")).trust_env is True


def test_resolve_trust_env_tolerates_missing_block():
    assert resolve_trust_env(SimpleNamespace()) is True


def test_provider_chain_item_overrides_global():
    off = AppConfig(api_key="k", llm_network=LLMNetworkConfig(trust_env=False))
    on = AppConfig(api_key="k")
    item = {"provider": "openai", "model": "m", "api_key": "k"}
    assert LLMClientPool._build_llm_config(item, off).trust_env is False
    assert LLMClientPool._build_llm_config(item, on).trust_env is True
    assert LLMClientPool._build_llm_config({**item, "trust_env": True}, off).trust_env is True
    assert LLMClientPool._build_llm_config({**item, "trust_env": False}, on).trust_env is False


def test_rebuild_client_with_key_keeps_trust_env():
    cfg = LLMConfig(provider="openai", model="m", api_key="a", trust_env=False)
    entry = ProviderEntry(config=cfg, client=create_client(cfg))
    entry.rebuild_client_with_key("b")
    assert entry.config.trust_env is False
    assert entry.client._client._client._mounts == {}


# ── 3. 各 provider 的 HTTP 客户端 ────────────────────────────────────────

@pytest.mark.parametrize("provider,attr", [("openai", "_client"), ("anthropic", "_client")])
def test_sdk_client_honors_trust_env(dead_env_proxy, provider, attr):
    on = create_client(LLMConfig(provider=provider, model="m", api_key="k", trust_env=True))
    off = create_client(LLMConfig(provider=provider, model="m", api_key="k", trust_env=False))
    # httpx 在 trust_env=True 时会按环境变量为代理 scheme 建 mounts；False 时为空
    assert getattr(on, attr)._client._mounts, "默认路径应当遵循环境代理（向后兼容）"
    assert getattr(off, attr)._client._mounts == {}


# ── 4/5. 端到端 ──────────────────────────────────────────────────────────

def _msgs():
    return [{"role": "user", "content": "ping"}]


def test_ollama_direct_when_trust_env_false(dead_env_proxy, local_server):
    off = create_client(LLMConfig(provider="ollama", model="m", base_url=local_server,
                                  trust_env=False, timeout=5))
    assert "pong" in off.chat(_msgs(), "", []).text


def test_ollama_default_still_uses_env_proxy(dead_env_proxy, local_server):
    on = create_client(LLMConfig(provider="ollama", model="m", base_url=local_server,
                                 trust_env=True, timeout=5))
    with pytest.raises(Exception):
        on.chat(_msgs(), "", [])


def test_openai_direct_when_trust_env_false(dead_env_proxy, local_server):
    off = create_client(LLMConfig(provider="openai", model="m", api_key="k",
                                  base_url=local_server, trust_env=False, timeout=5))
    assert "pong" in off.chat(_msgs(), "", []).text


def test_openai_default_still_uses_env_proxy(dead_env_proxy, local_server):
    on = create_client(LLMConfig(provider="openai", model="m", api_key="k",
                                 base_url=local_server, trust_env=True, timeout=5))
    with pytest.raises(Exception):
        on.chat(_msgs(), "", [])


def test_anthropic_direct_when_trust_env_false(dead_env_proxy, local_server):
    off = create_client(LLMConfig(provider="anthropic", model="m", api_key="k",
                                  base_url=local_server, trust_env=False, timeout=5))
    assert "pong" in off.chat(_msgs(), "", []).text
