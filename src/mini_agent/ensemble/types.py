"""
ensemble/types.py — 共享数据结构（避免 runner/judge 互相循环 import）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Candidate:
    """一个候选结果（可能来自一次 LLM 调用，也可能来自一个 SubAgent 任务）。"""
    idx: int
    content: str
    source: str                      # "llm_call" | "subagent"
    meta: dict = field(default_factory=dict)   # 例如 variant 名称、模型、温度
    error: Optional[str] = None      # 该候选执行失败时填写
    passed_check: Optional[bool] = None  # verifiable 任务下，是否通过校验（first_success 用）
    latency_s: float = 0.0
    created_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class EnsembleResult:
    """一次 ensemble 运行的完整结果，便于落盘到 ensemble_run.json。"""
    final_content: str
    chosen_idx: Optional[int]
    judge_strategy: str
    granularity: str                 # "llm_call" | "subagent"
    execution: str                   # "serial" | "parallel"
    candidates: list[Candidate] = field(default_factory=list)
    judge_reason: str = ""
    judge_scores: dict = field(default_factory=dict)   # idx(str) -> score/explanation
    early_stopped: bool = False
    total_latency_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "final_content": self.final_content,
            "chosen_idx": self.chosen_idx,
            "judge_strategy": self.judge_strategy,
            "granularity": self.granularity,
            "execution": self.execution,
            "judge_reason": self.judge_reason,
            "judge_scores": self.judge_scores,
            "early_stopped": self.early_stopped,
            "total_latency_s": self.total_latency_s,
            "candidates": [
                {
                    "idx": c.idx,
                    "content": c.content,
                    "source": c.source,
                    "meta": c.meta,
                    "error": c.error,
                    "passed_check": c.passed_check,
                    "latency_s": c.latency_s,
                }
                for c in self.candidates
            ],
        }
