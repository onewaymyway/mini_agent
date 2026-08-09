"""
perception/sentinel.py — 哨兵聚合面板（kanban_perception_gaps_improvement_plan.md 方向 A）

背景：看板已经积累了不少"数据已经在系统里产生了，但完全没有暴露，或者
暴露了但要用户主动翻到某个具体卡片才看得见"的信号——cron job 连续失败、
Objective 执行步骤卡在重试循环、wiki 隔离区积压、LLM 故障转移状态、
资源仲裁最近的降级/阻塞占比。本模块把这些**已经存在的**信号重新聚合到
一处，不引入任何新的采集/监控逻辑，也不做任何写操作。

跟 `perception/system_events.py`（跨子系统事件总线）是平级关系，但语义
不同：system_events 是"事件发生时主动发布"的推送式基础设施，本模块是
"按需扫描现有落盘状态"的拉取式聚合——哨兵面板不需要任何模块主动 publish
什么，纯粹是"把散落各处的已知状态收集到一次响应里"。

每一类扫描函数都遵循同一个失败降级约定：单个数据源读取失败时该类返回
空列表/空结构，用 log_exception 记录，不影响其它类别的展示——哨兵面板
本身是锦上添花的可观测性增强，不应该因为某一路数据源出问题就让整个
面板挂掉。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


# 阈值可配置（sentinel_summary 的 kwargs），这里只放默认值。见 A.4 风险 2：
# "失败一次就提醒"对偶发失败率高但无害的 job 太敏感，默认阈值 >=2。
DEFAULT_CRON_FAILURE_THRESHOLD = 2


def _scan_cron_consecutive_failures(
    paths: "AgentPaths", *, threshold: int = DEFAULT_CRON_FAILURE_THRESHOLD,
) -> list[dict]:
    """遍历所有 cron job 的 state.json，筛出 consecutive_failures >= threshold
    的条目。job 的 name/enabled 从 cron_jobs.json（CronScheduler 的持久化
    文件）读取，不需要一个存活的 CronScheduler 实例——纯读盘。

    尤其标出"已启用但一直在失败"这种最容易被忽视的组合：用户以为它在
    正常跑，实际上每次触发都失败。
    """
    out: list[dict] = []
    try:
        cron_jobs_dir = Path(paths.project_root) / ".agent" / "cron_jobs"
        if not cron_jobs_dir.is_dir():
            return out

        # job_id -> {name, enabled}，供补充展示用；缺失时退化为只显示 job_id。
        meta_by_id: dict[str, dict] = {}
        try:
            jobs_path = paths.workdir_dir / "cron_jobs.json"
            if jobs_path.exists():
                data = json.loads(jobs_path.read_text(encoding="utf-8"))
                for j in (data.get("jobs") or []):
                    jid = j.get("id")
                    if jid:
                        meta_by_id[jid] = {
                            "name": j.get("name") or jid,
                            "enabled": j.get("enabled", True),
                        }
        except Exception:
            pass

        from mini_agent.evolution.cron_job_workspace import CronJobWorkspace

        for job_dir in sorted(cron_jobs_dir.iterdir()):
            if not job_dir.is_dir():
                continue
            state_path = job_dir / "state.json"
            if not state_path.exists():
                continue
            # 目录名把 ':' 替换成了 '_'（见 CronJobWorkspace.__init__），
            # 反查 job_id 时优先用 cron_jobs.json 里的原始 id 匹配（一个
            # 目录名可能对应多种原始写法，但 replace(":", "_") 是单向的，
            # 这里退而求其次：先看有没有 meta 记录的 id 替换后等于目录名，
            # 匹配不到就直接用目录名本身当 job_id 展示（自定义 job 的常见形态）。
            job_id = job_dir.name
            for mid in meta_by_id:
                if mid.replace(":", "_") == job_dir.name:
                    job_id = mid
                    break

            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            failures = int(state.get("consecutive_failures") or 0)
            if failures < threshold:
                continue

            meta = meta_by_id.get(job_id, {})
            out.append({
                "job_id": job_id,
                "name": meta.get("name", job_id),
                "enabled": meta.get("enabled", True),
                "consecutive_failures": failures,
                "last_error": (state.get("last_error") or "")[:200],
                "status": state.get("status", ""),
            })
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.sentinel._scan_cron_consecutive_failures")
    return out


def _scan_objective_retry_hotspots(
    paths: "AgentPaths", *, near_limit_only: bool = True,
) -> list[dict]:
    """遍历 `.agent/objective_executions.json` 里状态为 running 的
    Objective，筛出有 step 的 retry_count 接近 MAX_STEP_RETRIES（默认只在
    最后一次重试前提醒，near_limit_only=True）的执行，提前给出"这个
    Objective 快要卡死了"的信号，而不是等到它彻底 failed、进入待办中心
    才知道。直接读原始 JSON 文件，不依赖一个存活的 ObjectiveExecutor
    实例（该文件本来就是它的持久化落盘格式）。
    """
    out: list[dict] = []
    try:
        from mini_agent.evolution.objective_executor import MAX_STEP_RETRIES

        exec_path = paths.workdir_dir / "objective_executions.json"
        if not exec_path.exists():
            return out
        data = json.loads(exec_path.read_text(encoding="utf-8"))
        threshold = max(1, MAX_STEP_RETRIES - 1) if near_limit_only else 1
        for ex in (data.get("executions") or []):
            if ex.get("status") != "running":
                continue
            hot_steps = [
                s for s in (ex.get("steps") or [])
                if int(s.get("retry_count") or 0) >= threshold
            ]
            if not hot_steps:
                continue
            worst = max(hot_steps, key=lambda s: s.get("retry_count") or 0)
            out.append({
                "execution_id": ex.get("execution_id"),
                "objective_id": ex.get("objective_id"),
                "title": ex.get("title") or ex.get("objective_id"),
                "hot_step_count": len(hot_steps),
                "max_retry_count": int(worst.get("retry_count") or 0),
                "max_retry_step_desc": (worst.get("description") or "")[:200],
                "last_error": (worst.get("error_msg") or "")[:200],
            })
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.sentinel._scan_objective_retry_hotspots")
    return out


def _scan_quarantine_backlog(paths: "AgentPaths") -> dict:
    """直接调用 wiki/quarantine.py 已有的 load_quarantine()，返回积压条数
    （只统计 status==pending/needs_human，已修复的不算积压）+ 最早一条的
    检测时间，供哨兵面板一行摘要展示。明细仍然通过 CLI
    （`cli/commands/quarantine.py`）处理。"""
    result = {"pending_count": 0, "earliest_first_seen_at": None, "items": []}
    try:
        from mini_agent.wiki.quarantine import load_quarantine, STATUS_REPAIRED

        records = load_quarantine(paths)
        pending = [r for r in records.values() if r.status != STATUS_REPAIRED]
        if not pending:
            return result
        pending.sort(key=lambda r: r.first_seen_at or 0.0)
        result["pending_count"] = len(pending)
        result["earliest_first_seen_at"] = pending[0].first_seen_at or None
        result["items"] = [
            {
                "page_path": r.page_path,
                "error_type": r.error_type,
                "status": r.status,
                "detect_count": r.detect_count,
                "first_seen_at": r.first_seen_at,
            }
            for r in pending[:20]
        ]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.sentinel._scan_quarantine_backlog")
    return result


def read_llm_pool_snapshot(client_pool) -> Optional[dict]:
    """[方向 B.1] 读取 LLMClientPool.snapshot()，并额外标注
    `switched_from_preferred`（当前激活的 entry 是否不是 fallback_chain
    的第一条）——这是用户最想第一眼看到的信号："是不是已经切离首选
    provider"，而不需要自己在 entries 列表里数第几个是 active。

    client_pool 为 None（没有配置故障转移链/agent 未就绪）时返回 None，
    调用方据此展示"未启用"而不是空列表（区分"没有数据"和"这个功能
    对当前配置不适用"）。
    """
    if client_pool is None:
        return None
    try:
        snap = client_pool.snapshot()
        current_idx = snap.get("current", 0)
        entries = snap.get("entries") or []
        return {
            "entries": entries,
            "current": current_idx,
            "switched_from_preferred": bool(current_idx) and current_idx != 0,
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.sentinel.read_llm_pool_snapshot")
        return None


def sentinel_summary(
    paths: "AgentPaths",
    *,
    client_pool=None,
    cron_failure_threshold: int = DEFAULT_CRON_FAILURE_THRESHOLD,
) -> dict:
    """哨兵聚合：把散落在各处、容易被忽略的"系统状态异常"信号收集到一处。
    每一类都只读现有落盘状态，不做任何写操作，失败降级为该类返回空
    列表/空结构，不影响其它类别的展示。

    client_pool 由调用方（api/routes.py）传入当前 bridge.agent._client_pool
    （本函数不负责从 bridge 取，保持纯函数、便于单测）。
    """
    cron_jobs_with_failures = _scan_cron_consecutive_failures(paths, threshold=cron_failure_threshold)
    stuck_objective_steps = _scan_objective_retry_hotspots(paths)
    quarantine_backlog = _scan_quarantine_backlog(paths)
    llm_failover_state = read_llm_pool_snapshot(client_pool)

    arbitration_recent_ratio = None
    try:
        from mini_agent.evolution.resource_arbiter import gating_ratio_summary
        arbitration_recent_ratio = gating_ratio_summary(paths, window_days=7.0)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.sentinel.sentinel_summary.arbitration")

    total_count = (
        len(cron_jobs_with_failures)
        + len(stuck_objective_steps)
        + quarantine_backlog.get("pending_count", 0)
        + (1 if (llm_failover_state or {}).get("switched_from_preferred") else 0)
    )

    return {
        "generated_at": time.time(),
        "total_count": total_count,
        "cron_jobs_with_failures": cron_jobs_with_failures,
        "stuck_objective_steps": stuck_objective_steps,
        "quarantine_backlog": quarantine_backlog,
        "llm_failover_state": llm_failover_state,
        "arbitration_recent_ratio": arbitration_recent_ratio,
    }
