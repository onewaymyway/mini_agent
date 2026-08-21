"""
llm_resolver.py
================
Generative-Capability 引擎的第二级检索裁决器（阶段二 / 阶段九改造）。

对应文档: next_doc/generative-capability-skill-plan.md 第 6 节 / 实施记录阶段二、阶段九。

设计要点（对应方案文档第 1 节原则 6）:
  - 只在 CapabilityEngine.resolve() 的第一级确定性匹配（domain/keyword）未命中
    或存在歧义时才会被调用，不是每次请求都触发。
  - 输入只是"候选成员摘要清单 + 用户请求文本"，不携带主对话历史，职责单一。
  - 输出必须是结构化的 member_id 列表，且引擎会用候选集合再做一次合法性过滤
    （见 capability_engine.py 的 resolve()），防止模型幻觉出不存在的 id。
  - 不使用 embedding，只用一次轻量 LLM 调用完成"从候选里选择"这一单一任务。

阶段九改造说明
--------------
此前本模块自行用 urllib 拼 Anthropic Messages API 请求——这是方案文档"通用
引擎"里唯一一处没有走框架统一 LLM 调用基础设施（`llm/service.py::LLMHelper`，
见 next_doc/llm_helper_unification_plan.md）的地方，带来两个实际问题：
  1. 只能硬编码 provider=anthropic，用户切换到 openai/nvidia/ollama 等其他
     provider（或用 /model 切换模型）后，这里仍然固定打 Anthropic 的 endpoint，
     跟主 agent 当前实际在用的 provider/model 完全脱节。
  2. 没有复用 LLMClientPool 的多 key 轮转/多配置 fallback，也没有走
     RetryPolicy，是一条自成一体、行为不一致的调用路径。

现在改为接收调用方传入的 `llm_helper`（通常是 `Agent.llm_helper`），通过
`LLMHelper.ask()` 发起单轮、无工具、只要文本的调用，自动获得：跟随 /model
切换、client_pool 的多 key/fallback、统一的 RetryPolicy、call_stats 计数。
不传 `llm_helper` 时按 `ensemble/judge.py::judge_llm` 的既有约定，退化为
`LLMHelper.from_config(cfg)`（要求传入 cfg，单次构造，不跟随后续 /model
切换）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from mini_agent.llm.service import LLMHelper

RESOLVER_SYSTEM_PROMPT = """你是一个技能检索裁决器。你会收到一个用户请求和一份候选能力清单，
每个候选有 member_id 和 description。你的唯一任务是判断该请求应该匹配清单中的哪些
member_id（通常 0 个或 1 个，极少数情况下可能多个）。

只返回 JSON，不要有任何其他文字，格式:
{"member_ids": ["id1", "id2"]}
如果没有任何候选匹配，返回 {"member_ids": []}。
不要编造清单之外不存在的 id。"""


def build_llm_resolver(
    llm_helper: Optional["LLMHelper"] = None,
    *,
    cfg: Any = None,
    override_model: Optional[str] = None,
    override_provider: Optional[str] = None,
    max_retries: int = 2,
) -> Callable[[str, list[dict]], list[str]]:
    """
    返回一个符合 CapabilityEngine 所需签名的 resolver:
        (request_text: str, candidates: list[{"member_id","description"}]) -> list[str]

    改为统一走框架的 `LLMHelper`（见 llm/service.py），不再自行拼 urllib 请求。

    Args:
        llm_helper: 通常传入 `Agent.llm_helper`，复用主 agent 当前
            provider/model 与 LLMClientPool 的多 key/fallback 能力，并跟随
            /model 切换。这是推荐用法（`capability_call` 工具即如此接线）。
        cfg: `llm_helper` 未传时的兜底——用 `LLMHelper.from_config(cfg)` 单次
            构造一条独立链（不跟随后续 /model 切换）。`llm_helper` 与 `cfg`
            至少要传一个，否则会在调用时抛出明确异常，而不是静默失败。
        override_model / override_provider: 可选，覆盖裁决用的模型/provider
            （不影响主 agent 当前配置，走 `LLMHelper` 的 override 分支）。
        max_retries: 传给 `LLMHelper.ask()` 的重试预算（检索裁决属于轻量
            单轮调用，默认给 2 次，小于主对话循环常用的 3 次）。

    调用失败（LLM 调用异常或返回非法 JSON）时抛出 RuntimeError，语义等同于
    此前版本——由调用方（CapabilityEngine.resolve()）区分"检索失败"与
    "确定没有匹配"，不会把异常静默当成空结果处理。
    """

    def _resolver(request_text: str, candidates: list[dict]) -> list[str]:
        from mini_agent.llm.service import LLMHelper

        helper = llm_helper
        if helper is None:
            if cfg is None:
                raise RuntimeError(
                    "build_llm_resolver 既未传入 llm_helper 也未传入 cfg，无法构造 "
                    "LLMHelper。请传入 Agent.llm_helper（推荐，跟随 /model 切换），"
                    "或传入 cfg 退化为 LLMHelper.from_config(cfg)。"
                )
            helper = LLMHelper.from_config(cfg)

        user_content = json.dumps(
            {"request": request_text, "candidates": candidates}, ensure_ascii=False
        )

        try:
            raw = helper.ask(
                user_content,
                system=RESOLVER_SYSTEM_PROMPT,
                max_retries=max_retries,
                override_model=override_model,
                override_provider=override_provider,
                override_temperature=0.0,
            )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM 检索调用失败: {e}") from e

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
