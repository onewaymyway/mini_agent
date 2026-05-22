"""
llm/providers/ollama.py — Ollama 本地模型 provider

通过 Ollama REST API 调用本地模型，无需 API key。
支持任何已通过 `ollama pull <model>` 下载的模型。

默认连接 http://localhost:11434（可通过 LLMConfig.base_url 修改）。

工具调用依赖模型本身的支持（llama3.1、mistral-nemo、qwen2.5 等支持）。
不支持工具调用的模型仍可正常使用，工具调用请求会被忽略。
"""

from __future__ import annotations

import json
from llm.base import (
    LLMClient,
    LLMConfig,
    LLMResponse,
    LLMUsage,
    ToolCall,
    ToolSchema,
    StreamCallback,
    LLMProviderError,
    LLMTimeoutError,
)

_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider(LLMClient):
    """
    Ollama 本地模型 provider，通过 HTTP 直接调用 Ollama REST API。
    不依赖任何第三方 SDK（仅使用标准库 urllib）。
    """

    def __init__(self, config: LLMConfig) -> None:
        # Ollama 不需要 API key
        config.requires_api_key = False
        super().__init__(config)
        self._base_url = (config.base_url or _DEFAULT_BASE_URL).rstrip("/")

    # ── LLMClient 接口实现 ────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """非流式调用 Ollama /api/chat。"""
        payload = self._build_payload(messages, system, tools, stream=False)
        raw = self._post("/api/chat", payload)
        return self._parse_response(raw)

    def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        on_token: StreamCallback,
    ) -> LLMResponse:
        """流式调用，逐行解析 NDJSON 并触发 on_token。"""
        payload = self._build_payload(messages, system, tools, stream=True)
        text_parts: list[str] = []
        last_raw: dict = {}

        for line in self._post_stream("/api/chat", payload):
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            token = chunk.get("message", {}).get("content", "")
            if token:
                on_token(token)
                text_parts.append(token)
            if chunk.get("done"):
                last_raw = chunk

        # 从最后一个 chunk 提取用量
        usage = LLMUsage(
            input_tokens=last_raw.get("prompt_eval_count", 0),
            output_tokens=last_raw.get("eval_count", 0),
            total_tokens=(
                last_raw.get("prompt_eval_count", 0)
                + last_raw.get("eval_count", 0)
            ),
        )
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=[],   # 流式模式暂不解析工具调用（Ollama 支持有限）
            usage=usage,
            stop_reason="end_turn",
            raw=last_raw,
        )

    def format_tools(self, tools: list[ToolSchema]) -> list[dict]:
        """Ollama tool 格式与 OpenAI function-calling 格式一致。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    @property
    def provider_name(self) -> str:
        return "Ollama"

    # ── HTTP 工具 ─────────────────────────────────────────────────────────────

    def _build_payload(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        stream: bool,
    ) -> dict:
        full_messages = messages
        if system:
            full_messages = [{"role": "system", "content": system}] + list(messages)
        payload: dict = {
            "model": self.config.model,
            "messages": full_messages,
            "stream": stream,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            },
        }
        if tools:
            payload["tools"] = self.format_tools(tools)
        return payload

    def _post(self, path: str, payload: dict) -> dict:
        """发送 POST 请求，返回解析后的 JSON dict。"""
        import urllib.request, urllib.error
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise LLMProviderError(
                f"Ollama connection failed at {self._base_url}: {e}\n"
                "Is Ollama running? Try: ollama serve"
            )
        except TimeoutError:
            raise LLMTimeoutError(f"Ollama request timed out after {self.config.timeout}s")

    def _post_stream(self, path: str, payload: dict):
        """发送流式 POST 请求，逐行 yield NDJSON 字符串。"""
        import urllib.request, urllib.error
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                for line in resp:
                    yield line.decode("utf-8")
        except urllib.error.URLError as e:
            raise LLMProviderError(f"Ollama stream failed: {e}")

    def _parse_response(self, raw: dict) -> LLMResponse:
        """解析非流式响应，包括工具调用。"""
        msg = raw.get("message", {})
        text = msg.get("content", "")
        tool_calls: list[ToolCall] = []

        for i, tc in enumerate(msg.get("tool_calls", [])):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(
                ToolCall(
                    id=f"ollama-tc-{i}",   # Ollama 不提供 ID，自动生成
                    name=fn.get("name", ""),
                    input=args,
                )
            )

        usage = LLMUsage(
            input_tokens=raw.get("prompt_eval_count", 0),
            output_tokens=raw.get("eval_count", 0),
            total_tokens=raw.get("prompt_eval_count", 0) + raw.get("eval_count", 0),
        )
        done_reason = raw.get("done_reason", "stop")
        stop_reason = "tool_use" if tool_calls else ("end_turn" if done_reason == "stop" else done_reason)

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
            raw=raw,
        )
