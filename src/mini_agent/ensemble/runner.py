"""
ensemble/runner.py — 串行/并行执行调度器

统一入口：
  - run_llm_ensemble(...)      llm_call 粒度
  - run_subagent_ensemble(...) subagent 粒度

两者都支持 execution="serial" | "parallel"，
serial 模式下若 early_stop_on_consensus=True，会在每个候选产出后检查是否可以提前停止：
  - first_success 策略：一旦某个候选通过校验，立刻停止，不再跑剩余候选
  - 其他策略：若已产出的候选里出现 >= 半数内容高度一致，视为已有共识，提前停止
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from .types import Candidate, EnsembleResult
from .judge import judge_candidates
from .strategies import make_llm_call, build_subagent_variants


def _finalize(cfg, result: EnsembleResult, *, session_id: Optional[str] = None) -> EnsembleResult:
    """统一收尾：落盘 ensemble_run.json + 触发 EnsembleJudged hook。失败均静默吞掉，不影响主流程。"""
    run_record = result.to_dict()

    # 落盘
    try:
        from mini_agent.storage.paths import AgentPaths
        sid = session_id or getattr(cfg, "_current_session_id", None)
        if sid:
            paths = AgentPaths(cfg.project_root)
            out_dir = paths.session_dir(sid) / "ensemble"
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{int(time.time() * 1000)}_{result.granularity}.json"
            import json as _json
            (out_dir / fname).write_text(
                _json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8",
            )
    except Exception:
        pass

    # hooks
    try:
        from mini_agent.hooks.loader import get_hook_manager
        mgr = get_hook_manager()
        if mgr is not None:
            mgr.run("EnsembleJudged", run_record)
    except Exception:
        pass

    return result


def _consensus_reached(candidates: list[Candidate], total_n: int) -> bool:
    ok = [c for c in candidates if c.ok]
    if len(ok) < 2:
        return False
    from collections import Counter
    counter = Counter(c.content.strip() for c in ok)
    _, top_count = counter.most_common(1)[0]
    return top_count >= max(2, (total_n // 2) + 1)


# ── llm_call 粒度 ─────────────────────────────────────────────────────────────

def run_llm_ensemble(
    cfg,
    messages: list[dict],
    system: str,
    *,
    n: Optional[int] = None,
    execution: Optional[str] = None,
    strategy: Optional[str] = None,
    checker: Optional[Callable[[Candidate], bool]] = None,
    session_id: Optional[str] = None,
) -> EnsembleResult:
    """对相同的 messages/system 做多次独立 LLM 调用，再综合评判出最终结果。"""
    ens_cfg = getattr(cfg, "ensemble", None)
    n = n or (ens_cfg.n if ens_cfg else 3)
    execution = execution or (ens_cfg.execution if ens_cfg else "parallel")
    strategy = strategy or (ens_cfg.judge_strategy if ens_cfg else "llm_judge")
    early_stop = bool(ens_cfg.early_stop_on_consensus) if ens_cfg else True
    max_concurrency = (ens_cfg.max_concurrency if ens_cfg else 3)
    judge_model = getattr(ens_cfg, "judge_model", None) if ens_cfg else None

    t0 = time.time()
    candidates: list[Candidate] = []
    early_stopped = False

    if execution == "parallel":
        with ThreadPoolExecutor(max_workers=max(1, min(n, max_concurrency))) as pool:
            futures = {
                pool.submit(make_llm_call, cfg, messages, system, i): i
                for i in range(n)
            }
            for fut in as_completed(futures):
                candidates.append(fut.result())
        candidates.sort(key=lambda c: c.idx)
    else:
        for i in range(n):
            c = make_llm_call(cfg, messages, system, i)
            candidates.append(c)
            if early_stop:
                if strategy == "first_success" and checker is not None and c.ok:
                    if checker(c):
                        c.passed_check = True
                        early_stopped = True
                        break
                elif strategy != "first_success" and _consensus_reached(candidates, n):
                    early_stopped = True
                    break

    result = judge_candidates(candidates, cfg, strategy=strategy, judge_model=judge_model, checker=checker)
    result.granularity = "llm_call"
    result.execution = execution
    result.early_stopped = early_stopped
    result.total_latency_s = time.time() - t0
    return _finalize(cfg, result, session_id=session_id)

def run_subagent_ensemble(
    cfg,
    prompt: str,
    *,
    n: Optional[int] = None,
    execution: Optional[str] = None,
    strategy: Optional[str] = None,
    variant_prompts: Optional[list[str]] = None,
    variant_personas: Optional[list[str]] = None,
    active_skills: Optional[list[str]] = None,
    checker: Optional[Callable[[Candidate], bool]] = None,
    timeout: float = 600,
    session_id: Optional[str] = None,
) -> EnsembleResult:
    """
    用多个 SubAgent（不同上下文/提示词）各自完整跑一遍任务，再综合评判。
    依赖 tools/orchestration.py 里已初始化的全局 TaskManager（init_task_manager）。
    """
    from mini_agent.tools.orchestration import get_task_manager
    from mini_agent.orchestrator.task import Task, TaskStatus

    ens_cfg = getattr(cfg, "ensemble", None)
    n = n or (ens_cfg.n if ens_cfg else 3)
    execution = execution or (ens_cfg.execution if ens_cfg else "parallel")
    strategy = strategy or (ens_cfg.judge_strategy if ens_cfg else "llm_judge")
    early_stop = bool(ens_cfg.early_stop_on_consensus) if ens_cfg else True
    judge_model = getattr(ens_cfg, "judge_model", None) if ens_cfg else None

    mgr = get_task_manager()
    if mgr is None:
        return EnsembleResult(
            final_content="[error] TaskManager 未初始化，无法执行 subagent 粒度的 ensemble",
            chosen_idx=None, judge_strategy=strategy, granularity="subagent",
            execution=execution, candidates=[], judge_reason="TaskManager not initialized",
        )

    variants = build_subagent_variants(
        prompt, n, variant_prompts=variant_prompts, variant_personas=variant_personas,
    )

    t0 = time.time()
    candidates: list[Candidate] = []
    early_stopped = False

    def _to_candidate(idx: int, variant: dict, rec) -> Candidate:
        if rec is None:
            return Candidate(idx=idx, content="", source="subagent", meta=variant,
                              error="task wait timeout")
        if rec.status == TaskStatus.DONE and rec.result is not None:
            return Candidate(
                idx=idx, content=rec.result.output, source="subagent",
                meta={"name": variant["name"], "persona": variant["system_extra"]},
                latency_s=(rec.finished_at or time.time()) - (rec.started_at or time.time()),
            )
        err = rec.result.error if rec.result else f"task status={rec.status.value}"
        return Candidate(idx=idx, content="", source="subagent", meta=variant, error=err)

    if execution == "parallel":
        task_ids = []
        for i, v in enumerate(variants):
            task = Task(
                prompt=v["prompt"], name=v["name"], system_extra=v["system_extra"],
                tags=v["tags"], active_skills=active_skills or [],
            )
            task_ids.append((i, v, mgr.submit(task)))
        for i, v, tid in task_ids:
            rec = mgr.wait(tid, timeout=timeout)
            candidates.append(_to_candidate(i, v, rec))
        candidates.sort(key=lambda c: c.idx)
    else:
        for i, v in enumerate(variants):
            task = Task(
                prompt=v["prompt"], name=v["name"], system_extra=v["system_extra"],
                tags=v["tags"], active_skills=active_skills or [],
            )
            tid = mgr.submit(task)
            rec = mgr.wait(tid, timeout=timeout)
            c = _to_candidate(i, v, rec)
            candidates.append(c)
            if early_stop:
                if strategy == "first_success" and checker is not None and c.ok:
                    if checker(c):
                        c.passed_check = True
                        early_stopped = True
                        break
                elif strategy != "first_success" and _consensus_reached(candidates, n):
                    early_stopped = True
                    break

    result = judge_candidates(candidates, cfg, strategy=strategy, judge_model=judge_model, checker=checker)
    result.granularity = "subagent"
    result.execution = execution
    result.early_stopped = early_stopped
    result.total_latency_s = time.time() - t0
    return _finalize(cfg, result, session_id=session_id)
