"""
ensemble/judge.py — 候选结果评判 / 合并

支持四种策略：
  - llm_judge     用模型从 N 个候选里选最佳，或综合优点合并出一份新答案（默认，开放式任务）
  - first_success 跑到第一个通过校验的候选就用它（verifiable 任务默认）
  - vote          多数投票，适用于输出可直接比较（分类/固定格式）的场景
  - merge         强制让模型把多个候选的优点合并成一份新答案（不挑一个，而是综合）
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Callable, Optional

from .types import Candidate, EnsembleResult


def _ok_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return [c for c in candidates if c.ok]


def judge_first_success(
    candidates: list[Candidate],
    *,
    checker: Optional[Callable[[Candidate], bool]] = None,
) -> tuple[Optional[int], str, dict]:
    """
    返回第一个 passed_check==True（或 checker 校验通过）的候选。
    若都没通过，退化为返回第一个无 error 的候选（带说明）。
    """
    for c in candidates:
        if not c.ok:
            continue
        passed = c.passed_check
        if passed is None and checker is not None:
            try:
                passed = checker(c)
                c.passed_check = passed
            except Exception:
                passed = False
                c.passed_check = False
        if passed:
            return c.idx, f"候选 #{c.idx} 第一个通过校验", {str(c.idx): "passed"}

    ok = _ok_candidates(candidates)
    if ok:
        return ok[0].idx, "没有候选通过校验，回退使用第一个未出错的候选", {}
    return None, "所有候选均执行失败", {}


def judge_vote(candidates: list[Candidate]) -> tuple[Optional[int], str, dict]:
    ok = _ok_candidates(candidates)
    if not ok:
        return None, "所有候选均执行失败", {}
    counter = Counter(c.content.strip() for c in ok)
    top_content, top_count = counter.most_common(1)[0]
    for c in ok:
        if c.content.strip() == top_content:
            return c.idx, f"多数投票：{top_count}/{len(ok)} 个候选一致", {
                "votes": {k: v for k, v in counter.items()}
            }
    return ok[0].idx, "投票回退到第一个候选", {}


def _build_judge_prompt(candidates: list[Candidate], mode: str) -> str:
    blocks = []
    for c in candidates:
        if not c.ok:
            blocks.append(f"[候选 #{c.idx}]（执行失败：{c.error}）")
        else:
            blocks.append(f"[候选 #{c.idx}]\n{c.content}")
    joined = "\n\n".join(blocks)

    if mode == "merge":
        instruction = (
            "请综合以上所有候选答案的优点，给出一份合并后的最终答案（不要只是简单拼接，"
            "要消除矛盾、补全缺漏、保留各自最好的部分）。"
            "只输出严格 JSON：{\"final_content\": \"...\", \"reason\": \"合并思路简述\"}，不要其他文字。"
        )
    else:
        instruction = (
            "请从以上候选答案中选出质量最高、最准确、最完整的一个。"
            "只输出严格 JSON：{\"chosen_idx\": 数字, \"reason\": \"选择理由\", "
            "\"scores\": {\"候选idx\": \"简短点评\"}}，不要其他文字。"
        )
    return f"{joined}\n\n{instruction}"


def judge_llm(
    candidates: list[Candidate],
    cfg,
    *,
    mode: str = "select",       # "select" | "merge"
    judge_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
) -> tuple[Optional[int], str, str, dict]:
    """
    用模型评判/合并候选。
    返回 (chosen_idx_or_None, final_content_if_merge_or_chosen_content, reason, scores)
    """
    ok = _ok_candidates(candidates)
    if not ok:
        return None, "", "所有候选均执行失败", {}
    if len(ok) == 1:
        return ok[0].idx, ok[0].content, "只有一个有效候选，直接采用", {}

    try:
        from mini_agent.llm.base import LLMConfig
        from mini_agent.llm.factory import create_client

        base_llm_cfg = LLMConfig.from_app_config(cfg)
        llm_cfg = LLMConfig(
            provider=judge_provider or base_llm_cfg.provider,
            model=judge_model or base_llm_cfg.model,
            api_key=base_llm_cfg.api_key,
            base_url=base_llm_cfg.base_url,
            max_tokens=base_llm_cfg.max_tokens,
            temperature=0.0,
            requires_api_key=base_llm_cfg.requires_api_key,
            use_system_tool_call=base_llm_cfg.use_system_tool_call,
            system_message_format=base_llm_cfg.system_message_format,
        )
        client = create_client(llm_cfg)
        system = (
            "你是一个严格的结果评审员，负责从多个候选答案中选优或合并出最终答案。"
            "评判时关注：正确性、完整性、是否真正完成了任务要求、表达是否清晰。"
        )
        prompt = _build_judge_prompt(ok, mode)
        resp = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            tools=[],
        )
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        if mode == "merge":
            final_content = str(data.get("final_content", "")) or ok[0].content
            reason = str(data.get("reason", ""))
            return None, final_content, reason, {}
        else:
            chosen_idx = data.get("chosen_idx")
            reason = str(data.get("reason", ""))
            scores = data.get("scores", {}) or {}
            chosen = next((c for c in ok if c.idx == chosen_idx), None)
            if chosen is None:
                chosen = ok[0]
                reason = (reason + " [解析到的 chosen_idx 无效，回退到候选 #0]").strip()
            return chosen.idx, chosen.content, reason, scores
    except Exception as e:
        # 评判失败时保守回退：直接用第一个有效候选，避免阻塞主流程
        return ok[0].idx, ok[0].content, f"评判出错，回退使用候选 #{ok[0].idx}（{type(e).__name__}: {e}）", {}


def judge_candidates(
    candidates: list[Candidate],
    cfg,
    *,
    strategy: str = "llm_judge",
    judge_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    checker: Optional[Callable[[Candidate], bool]] = None,
) -> EnsembleResult:
    """
    统一评判入口。strategy: "llm_judge" | "first_success" | "vote" | "merge"
    返回的 EnsembleResult 只填 judge 相关字段，granularity/execution 由 runner 补充。
    """
    if strategy == "first_success":
        chosen_idx, reason, scores = judge_first_success(candidates, checker=checker)
        chosen = next((c for c in candidates if c.idx == chosen_idx), None)
        final_content = chosen.content if chosen else ""
    elif strategy == "vote":
        chosen_idx, reason, scores = judge_vote(candidates)
        chosen = next((c for c in candidates if c.idx == chosen_idx), None)
        final_content = chosen.content if chosen else ""
    elif strategy == "merge":
        chosen_idx, final_content, reason, scores = judge_llm(
            candidates, cfg, mode="merge", judge_model=judge_model, judge_provider=judge_provider,
        )
    else:  # llm_judge / 默认
        chosen_idx, final_content, reason, scores = judge_llm(
            candidates, cfg, mode="select", judge_model=judge_model, judge_provider=judge_provider,
        )

    return EnsembleResult(
        final_content=final_content,
        chosen_idx=chosen_idx,
        judge_strategy=strategy,
        granularity="",
        execution="",
        candidates=candidates,
        judge_reason=reason,
        judge_scores=scores,
    )
