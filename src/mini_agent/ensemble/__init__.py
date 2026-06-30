"""
ensemble — 多结果合并取优（Best-of-N / Ensemble）

对同一任务获取多个候选结果，再综合评判出最优结果，支持两种粒度：
  - llm_call  粒度：相同输入（messages/system）多次调用 LLM
  - subagent  粒度：用不同上下文/提示词派发多个 SubAgent 跑同一任务

两种粒度都支持 串行(serial) / 并行(parallel) 执行，
是否触发由 EnsembleConfig.mode 控制：
  off     完全关闭
  manual  仅当调用方显式要求时触发
  auto    由规则 + 模型自判 决定是否触发
  always  强制对所有匹配的任务触发（调试/评测用）

对外主要入口：
  - decide_and_run()  — AUTO 模式下"先判断要不要做，再执行"的一站式入口
  - run_llm_ensemble() / run_subagent_ensemble() — 直接执行某一粒度的 ensemble
"""

from .runner import Candidate, EnsembleResult, run_llm_ensemble, run_subagent_ensemble
from .decision import should_trigger_ensemble, classify_task_type
from .judge import judge_candidates

__all__ = [
    "Candidate",
    "EnsembleResult",
    "run_llm_ensemble",
    "run_subagent_ensemble",
    "should_trigger_ensemble",
    "classify_task_type",
    "judge_candidates",
]
