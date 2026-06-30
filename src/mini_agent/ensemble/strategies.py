"""
ensemble/strategies.py — 候选生成策略

两种粒度：
  - llm_call: 相同 messages/system，多次调用 LLM（用 temperature 抖动制造多样性）
  - subagent: 用不同的 system_extra / prompt 变体派发多个 SubAgent，各自完整跑一遍任务
"""

from __future__ import annotations

import time
from typing import Optional

from .types import Candidate


def _jittered_temperature(base: float, i: int, n: int) -> float:
    """简单的温度抖动：第一个候选用 base（偏确定性），其余递增，制造多样性。"""
    if n <= 1:
        return base
    step = max(0.15, (0.9 - base) / max(1, n - 1))
    return min(1.0, base + step * i)


def make_llm_call(
    cfg,
    messages: list[dict],
    system: str,
    idx: int,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Candidate:
    """同一输入下发起一次独立的 LLM 调用，返回一个 Candidate。"""
    t0 = time.time()
    try:
        from mini_agent.llm.base import LLMConfig
        from mini_agent.llm.factory import create_client

        base_llm_cfg = LLMConfig.from_app_config(cfg)
        llm_cfg = LLMConfig(
            provider=base_llm_cfg.provider,
            model=model or base_llm_cfg.model,
            api_key=base_llm_cfg.api_key,
            base_url=base_llm_cfg.base_url,
            max_tokens=base_llm_cfg.max_tokens,
            temperature=base_llm_cfg.temperature if temperature is None else temperature,
            requires_api_key=base_llm_cfg.requires_api_key,
            use_system_tool_call=base_llm_cfg.use_system_tool_call,
            system_message_format=base_llm_cfg.system_message_format,
        )
        client = create_client(llm_cfg)
        resp = client.chat(messages=messages, system=system, tools=[])
        return Candidate(
            idx=idx,
            content=resp.text or "",
            source="llm_call",
            meta={"model": llm_cfg.model, "temperature": llm_cfg.temperature},
            latency_s=time.time() - t0,
        )
    except Exception as e:
        return Candidate(
            idx=idx, content="", source="llm_call",
            meta={}, error=f"{type(e).__name__}: {e}", latency_s=time.time() - t0,
        )


def build_subagent_variants(
    prompt: str,
    n: int,
    *,
    variant_prompts: Optional[list[str]] = None,
    variant_personas: Optional[list[str]] = None,
) -> list[dict]:
    """
    构造 N 个 SubAgent 任务变体（写入不同的 system_extra，制造视角差异）。

    Args:
        prompt: 基础任务 prompt（所有变体共享）
        n: 变体数量
        variant_prompts: 若提供，逐个覆盖任务 prompt 本身（更强的多样性，调用方自定义不同上下文）
        variant_personas: 若提供，逐个作为 system_extra 注入（如"保守""激进""verifier"角色）
    """
    default_personas = [
        "请从严谨、保守的角度完成任务，优先保证正确性和可验证性。",
        "请从全面、创造性的角度完成任务，尽量覆盖更多可能性和边界情况。",
        "请先完成任务，再以挑剔的复核者视角自我审查一遍，修正发现的问题。",
        "请用最简洁、最直接的方式完成任务，避免过度设计。",
    ]
    personas = variant_personas or default_personas

    variants = []
    for i in range(n):
        p = (variant_prompts[i] if variant_prompts and i < len(variant_prompts) else prompt)
        persona = personas[i % len(personas)]
        variants.append({
            "prompt": p,
            "name": f"ensemble-variant-{i}",
            "system_extra": persona,
            "tags": ["ensemble"],
        })
    return variants
