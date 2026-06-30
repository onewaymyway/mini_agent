"""
ensemble/decision.py — 是否触发 ensemble 的判定逻辑

两层判定（AUTO 模式下都会跑）：
  1. 规则层（便宜，先跑）：基于关键词 / 任务特征的启发式
  2. 模型自判层：用一次小成本 LLM 调用，让模型自己判断
     "这个任务是否值得花多次采样/多个 subagent 来比较取优"

同时提供 classify_task_type()，用于判断任务是不是"可验证/有明确通过标准"的类型
（代码执行、格式校验、工具结果等）——这类任务默认评判策略应使用 first_success
而不是 llm_judge。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

# ── 任务类型 ──────────────────────────────────────────────────────────────────

# "verifiable" — 有客观可执行的通过/失败标准（代码能跑通、格式校验通过、测试通过等）
#                这类任务适合 first_success：跑到第一个通过验证的候选就停止
# "open_ended" — 开放式生成/方案设计/写作/分析类，没有单一客观对错
#                这类任务适合 llm_judge：综合多个候选选优或合并
TaskType = str  # "verifiable" | "open_ended"

_VERIFIABLE_HINTS = (
    "运行", "执行", "测试", "test", "run", "compile", "编译",
    "通过", "pass", "校验", "validate", "lint", "build", "构建",
    "修复 bug", "fix bug", "debug", "报错", "exit code", "exitcode",
    "单元测试", "unit test", "assert",
)

_OPEN_ENDED_HINTS = (
    "设计方案", "对比", "比较", "写一篇", "写一个故事", "分析",
    "总结", "建议", "策略", "compare", "design", "essay", "summarize",
    "brainstorm", "方案", "评估优劣",
)

_HIGH_VALUE_HINTS = (
    "重要", "谨慎", "务必", "认真", "critical", "important", "careful",
    "对比几种", "多种方案", "不可逆", "生产环境", "production", "上线",
)


def classify_task_type(
    prompt: str,
    *,
    has_acceptance_criteria: bool = False,
    has_verifier_tool: bool = False,
) -> TaskType:
    """
    粗粒度识别任务类型，决定默认评判策略。

    优先级：
      1. 显式带验收标准 / 有可调用的验证工具（如 run_tests / lint）→ verifiable
      2. 命中"可验证类"关键词 → verifiable
      3. 命中"开放式"关键词 → open_ended
      4. 默认 → open_ended（更保守，走综合评判而不是“跑到一个就停”）
    """
    if has_acceptance_criteria or has_verifier_tool:
        return "verifiable"

    text = (prompt or "").lower()
    if any(h.lower() in text for h in _VERIFIABLE_HINTS):
        return "verifiable"
    if any(h.lower() in text for h in _OPEN_ENDED_HINTS):
        return "open_ended"
    return "open_ended"


# ── 规则层 ───────────────────────────────────────────────────────────────────

@dataclass
class TriggerDecision:
    trigger: bool
    reason: str
    task_type: TaskType
    judge_strategy: str          # "llm_judge" | "first_success"
    source: str                  # "rule" | "model" | "manual" | "always" | "off"


def _rule_based_signal(prompt: str, *, failed_recently: bool = False) -> Optional[bool]:
    """
    规则层快速判定。返回 True/False 表示"规则已经能下定论"，
    返回 None 表示规则层不确定，需要进一步问模型。
    """
    text = (prompt or "").lower()

    # 之前一次输出被校验/格式检测判定失败，重试时升级为 ensemble
    if failed_recently:
        return True

    if any(h.lower() in text for h in _HIGH_VALUE_HINTS):
        return True

    # 任务很短/很简单（如直接问答、单行指令）通常不值得 ensemble
    if len(text.strip()) < 12:
        return False

    return None  # 不确定，交给模型自判


def _model_based_signal(
    prompt: str,
    cfg,
    judge_model: Optional[str] = None,
) -> tuple[bool, str]:
    """
    模型自判层：用一次低成本调用问模型 "是否值得 ensemble"。
    返回 (是否触发, 理由)。任何异常都视为"不触发"，避免因为判定本身出错而拖垮主流程。
    """
    try:
        from mini_agent.llm.base import LLMConfig
        from mini_agent.llm.factory import create_client

        base_llm_cfg = LLMConfig.from_app_config(cfg)
        llm_cfg = LLMConfig(
            provider=base_llm_cfg.provider,
            model=judge_model or base_llm_cfg.model,
            api_key=base_llm_cfg.api_key,
            base_url=base_llm_cfg.base_url,
            max_tokens=200,
            temperature=0.0,
            requires_api_key=base_llm_cfg.requires_api_key,
            use_system_tool_call=base_llm_cfg.use_system_tool_call,
            system_message_format=base_llm_cfg.system_message_format,
        )
        client = create_client(llm_cfg)

        system = (
            "你是一个成本敏感的任务路由助手。判断给定任务是否值得用"
            "“多次采样/多路径取优(ensemble)”来提高质量——只有当任务存在出错风险、"
            "多种合理解法、或结果对正确性/质量要求较高时才值得；"
            "对于简单、明确、低风险的任务不值得（会浪费 token/时间）。"
            "只输出严格 JSON：{\"trigger\": true|false, \"reason\": \"一句话理由\"}，不要任何其他文字。"
        )
        resp = client.chat(
            messages=[{"role": "user", "content": f"任务：{prompt}"}],
            system=system,
            tools=[],
        )
        raw = (resp.text or "").strip()
        # 容错：去掉可能的 markdown 代码块包裹
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return bool(data.get("trigger", False)), str(data.get("reason", ""))
    except Exception as e:
        return False, f"模型自判失败，默认不触发（{type(e).__name__}）"


def should_trigger_ensemble(
    prompt: str,
    cfg,
    *,
    explicit: Optional[bool] = None,
    failed_recently: bool = False,
    has_acceptance_criteria: bool = False,
    has_verifier_tool: bool = False,
) -> TriggerDecision:
    """
    统一判定入口。

    Args:
        prompt: 任务/请求文本
        cfg: AppConfig（读取 cfg.ensemble）
        explicit: 调用方显式指定 True/False（manual 模式或工具参数传入时使用），
                  优先级最高，跳过 mode 判定
        failed_recently: 上一次结果被校验/格式检测判定失败（用于升级触发）
        has_acceptance_criteria / has_verifier_tool: 传给 classify_task_type
    """
    ens_cfg = getattr(cfg, "ensemble", None)
    task_type = classify_task_type(
        prompt,
        has_acceptance_criteria=has_acceptance_criteria,
        has_verifier_tool=has_verifier_tool,
    )
    judge_strategy = "first_success" if task_type == "verifiable" else (
        getattr(ens_cfg, "judge_strategy", "llm_judge") if ens_cfg else "llm_judge"
    )

    if ens_cfg is None or getattr(ens_cfg, "mode", "off") == "off":
        return TriggerDecision(False, "ensemble 功能未启用", task_type, judge_strategy, "off")

    mode = ens_cfg.mode

    if explicit is not None:
        return TriggerDecision(
            explicit,
            "调用方显式指定" if explicit else "调用方显式关闭",
            task_type, judge_strategy, "manual",
        )

    if mode == "manual":
        return TriggerDecision(False, "manual 模式需显式指定才触发", task_type, judge_strategy, "manual")

    if mode == "always":
        return TriggerDecision(True, "always 模式强制触发", task_type, judge_strategy, "always")

    if mode == "auto":
        rule_signal = _rule_based_signal(prompt, failed_recently=failed_recently)
        if rule_signal is True:
            return TriggerDecision(True, "规则命中：高风险/重试升级", task_type, judge_strategy, "rule")
        if rule_signal is False:
            return TriggerDecision(False, "规则判定：任务过简单，不值得 ensemble", task_type, judge_strategy, "rule")

        # 规则层不确定 → 模型自判层
        trigger, reason = _model_based_signal(prompt, cfg, judge_model=getattr(ens_cfg, "judge_model", None))
        return TriggerDecision(trigger, reason, task_type, judge_strategy, "model")

    return TriggerDecision(False, f"未知 mode={mode}", task_type, judge_strategy, "off")
