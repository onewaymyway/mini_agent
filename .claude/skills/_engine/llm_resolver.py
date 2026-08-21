"""
llm_resolver.py
================
Generative-Capability 引擎的第二级检索裁决器（阶段二）。

对应文档: next_doc/generative-capability-skill-plan.md 第 6 节 / 实施记录阶段二。

设计要点（对应方案文档第 1 节原则 6）:
  - 只在 CapabilityEngine.resolve() 的第一级确定性匹配（domain/keyword）未命中
    或存在歧义时才会被调用，不是每次请求都触发。
  - 输入只是"候选成员摘要清单 + 用户请求文本"，不携带主对话历史，职责单一。
  - 输出必须是结构化的 member_id 列表，且引擎会用候选集合再做一次合法性过滤
    （见 capability_engine.py 的 resolve()），防止模型幻觉出不存在的 id。
  - 不使用 embedding，只用一次轻量 LLM 调用完成"从候选里选择"这一单一任务。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

RESOLVER_SYSTEM_PROMPT = """你是一个技能检索裁决器。你会收到一个用户请求和一份候选能力清单，
每个候选有 member_id 和 description。你的唯一任务是判断该请求应该匹配清单中的哪些
member_id（通常 0 个或 1 个，极少数情况下可能多个）。

只返回 JSON，不要有任何其他文字，格式:
{"member_ids": ["id1", "id2"]}
如果没有任何候选匹配，返回 {"member_ids": []}。
不要编造清单之外不存在的 id。"""


def build_llm_resolver(
    model: str = "claude-sonnet-5",
    api_key_env: str = "ANTHROPIC_API_KEY",
    timeout_seconds: int = 20,
) -> Callable[[str, list[dict]], list[str]]:
    """
    返回一个符合 CapabilityEngine 所需签名的 resolver:
        (request_text: str, candidates: list[{"member_id","description"}]) -> list[str]

    真实调用 Anthropic Messages API。需要环境变量中配置好 API key
    （本沙盒环境未配置，调用时会抛出明确异常，不会静默返回空结果掩盖问题，
    调用方应捕获异常并按"检索失败"处理，而不是当作"未命中"处理，两者语义不同）。
    """

    def _resolver(request_text: str, candidates: list[dict]) -> list[str]:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"未配置 {api_key_env}，无法调用 LLM 二级检索。"
                f"这是环境配置问题，不代表检索结果为空，请勿在上层把此异常当作 miss 处理。"
            )

        user_content = json.dumps(
            {"request": request_text, "candidates": candidates}, ensure_ascii=False
        )
        payload = json.dumps(
            {
                "model": model,
                "max_tokens": 300,
                "system": RESOLVER_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM 检索调用失败: {e}") from e

        text_blocks = [b["text"] for b in body.get("content", []) if b.get("type") == "text"]
        raw = "\n".join(text_blocks).strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM 检索返回非法 JSON: {raw!r}") from e

        return list(parsed.get("member_ids", []))

    return _resolver


def build_stub_resolver(fixed_result: list[str]) -> Callable[[str, list[dict]], list[str]]:
    """
    用于离线自测/CI 的桩实现：不发起任何网络调用，直接返回固定结果。
    仅用于验证 CapabilityEngine 与 resolver 之间的接线是否正确，
    不用于验证真实的语义裁决质量。
    """

    def _stub(request_text: str, candidates: list[dict]) -> list[str]:  # noqa: ARG001
        return fixed_result

    return _stub
