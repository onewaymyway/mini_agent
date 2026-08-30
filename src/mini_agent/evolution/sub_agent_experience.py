"""evolution/sub_agent_experience.py — 子 Agent 经历回写
（next_doc/self_awareness_identity_evolution_plan.md §2.4）。

SubAgent（orchestrator/sub_agent.py）执行结束后此前只回写任务结果
（TaskResult），不回写"这次经历对主身份意味着什么"。本模块在 SubAgent
生命周期结束时（`_run_body()` 的 `finally` 块）做一次轻量、纯规则式的
信号检测——不发起 LLM 调用（那个 finally 块跑在 SubAgent 自己的线程上，
是任务生命周期的收尾路径，不适合在这里加一次可能阻塞/超时的 LLM 反思
调用，与项目里"cron 后台异步做语义总结，热路径只做规则式轻量记录"的
既有取舍一致，见 agent/reflection.py 里 daemon 模式下巩固循环从同步
session-end 路径迁移到 CronScheduler 的说明）。

只在检测到信号时才写（对齐"没有摩擦和洞察就返回空数组"的克制原则，不为
凑数量强行生成）：
  - 任务失败（status == FAILED 且有非空 error）
  - 任务"异常耗时"（turns 或 tool_calls 明显超出常规范围，说明这次执行
    过程本身值得注意，即使最终成功）

写入的是纯事实性的轻量摘要（task_id/task_name/signal_type/error 片段/
turns/tool_calls），不做语义提炼——真正"这次经历改变了我对自己哪方面
认识"的语义总结，交给 §2.2 `self_narrative.py` 的周期性 LLM 归纳任务
去做，本模块只负责把"值得关注的原始经历"筛出来落盘，避免叙事生成时
要从全部 SubAgent 记录里重新筛一遍。
"""

from __future__ import annotations

import json
import time
from typing import Optional

# 经验性阈值：远超常规单轮子任务规模的 turns/tool_calls 视为"异常耗时"，
# 与 daemon_stability_and_ux_improvement_plan.md 里对"卡死/困难任务"的
# 经验判定量级一致（数量级参考，不要求精确校准）。
_HIGH_TURNS_THRESHOLD = 15
_HIGH_TOOL_CALLS_THRESHOLD = 30

_MAX_LOG_ENTRIES_READ = 200  # load_recent_experiences 默认最多回看的行数


def maybe_record_experience(
    paths,
    *,
    task_id: str,
    task_name: str,
    status: str,
    error: str = "",
    turns: int = 0,
    tool_calls: int = 0,
) -> Optional[dict]:
    """SubAgent 生命周期结束时调用。检测到信号才写入，返回写入的记录；
    没有信号时返回 None，不写文件。"""
    signal_type = None
    if status == "FAILED" and (error or "").strip():
        signal_type = "failure"
    elif turns >= _HIGH_TURNS_THRESHOLD or tool_calls >= _HIGH_TOOL_CALLS_THRESHOLD:
        signal_type = "high_effort"

    if signal_type is None:
        return None

    record = {
        "at": time.time(),
        "task_id": task_id,
        "task_name": task_name[:120],
        "status": status,
        "signal_type": signal_type,
        "error_excerpt": (error or "")[:300],
        "turns": turns,
        "tool_calls": tool_calls,
    }

    try:
        p = paths.sub_agent_experience_log_path
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception:
        return None

    return record


def load_recent_experiences(paths, *, limit: int = 10) -> list[dict]:
    """只读加载最近 limit 条子 Agent 经历记录（按时间倒序），供
    self_narrative.py 生成叙事时作为额外证据源，或看板只读展示。"""
    p = paths.sub_agent_experience_log_path
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-_MAX_LOG_ENTRIES_READ:]
    except Exception:
        return []
    out = []
    # 文件本身就是按写入顺序追加的，直接倒序读取即可得到"最近优先"，
    # 不依赖 at 字段排序（同一批快速写入时 time.time() 精度不够，排序
    # 会退化成不稳定的原始顺序）。
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out
