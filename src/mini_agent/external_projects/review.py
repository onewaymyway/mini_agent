"""
external_projects/review.py — 周期性"改进 review session"的任务模板构建

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 4。

**本模块管什么**：把"该为某个外部项目发起一次 review"这件事，翻译成
一段可以直接投进 mini_agent 输入队列的任务描述文本（`task_template`）
——复用本仓库已经验证过的模式（`evolution/cron_scheduler.py::CronJob.
task_template`，daemon 自身的 `sys:growth_advisor_daily` 等内置任务
走的就是这条路：定时把一段任务描述提交进会话，由带着相应工具的 agent
去执行），不是发明一套新的"agent 自主循环"机制。

**本模块不管什么**：不负责把生成的 job 实际注册进正在运行的 daemon
的 `CronScheduler`——那需要触达运行中 daemon 的 HTTP 层（当前
`DaemonClient` 只暴露了 `list_cron_jobs`/`run_cron_job`，没有"新增
job"的远程接口），是本文档第 4 节明确标注为"待接线"的部分，见
`next_doc/stock_watch_continuous_improvement_plan.md` 阶段 4 的验收
说明。本模块先把"任务模板长什么样"这个可测试、无需触达运行中 daemon
的部分做扎实——真正接线时，落地方式就是用这里 `build_review_task_
template()` 的返回值去调 `CronScheduler.add_job(id=..., schedule=
f"cron:{...}", task_template=..., tags=["external_project_review",
project_name])`，`schedule` 由 `ReviewSpec.cadence`（"weekly"/"daily"
等简单描述）换算成 `cron_scheduler.py` 认识的 cron 表达式。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from mini_agent.external_projects.backlog import BacklogItem, read_backlog
from mini_agent.external_projects.ledger import RunRecord, read_ledger
from mini_agent.external_projects.manifest import ProjectManifest

# review session 允许使用的工具集合——只读探查 + 只写"待办"/"提案分支"，
# 不包含任何能直接改动目标项目当前分支、或落地 enhancement 提案的工具
# （落地权限见 maintenance.py::propose_maintenance_fix 的 docstring 和
# 本文档第5节：`land_maintenance_fix` 对 `change_type="enhancement"`
# 永远只能由人工调用）。
REVIEW_SESSION_TOOLS = (
    "list_projects",
    "inspect_project",
    "list_backlog",
    "append_backlog_item",
    "propose_fix",  # 仅用于 change_type="enhancement" 的提案
)


@dataclass
class ReviewBriefing:
    """一次 review 需要的原始材料——从各账本读出来，还没拼成任务模板文本。

    拆成独立 dataclass（而不是直接在 `build_review_task_template` 里
    读文件+拼字符串）是为了让"读了什么材料"这一步本身也能被单独检查
    /单测，不用每次都从磁盘经过完整字符串拼装才能断言。
    """

    project_name: str
    recent_runs: List[RunRecord]
    open_backlog: List[BacklogItem]
    cadence: str


def gather_review_briefing(
    manifest: ProjectManifest, *, recent_runs_limit: int = 20
) -> ReviewBriefing:
    """从目标外部项目自己的账本（`.agent/run_status.jsonl`、
    `.agent/improvement_backlog.jsonl`）读出 review 需要的原始材料。

    不读 `data/outcome_ledger.jsonl`/`data/source_health.jsonl`——那是
    stock_watch 自己的私有账本 schema（第 3.1/3.2 节明确"这是股票系统
    特有的结构，框架不需要理解"），review session 里的 agent 应该用
    `inspect_project`/直接读文件的方式去看这些项目私有材料，本函数只
    负责框架能理解、任何外部项目都通用的两份账本。
    """
    if manifest.source_dir is None:
        raise ValueError(f"项目 '{manifest.name}' 的 manifest 没有 source_dir，无法读取账本")
    root = manifest.source_dir
    return ReviewBriefing(
        project_name=manifest.name,
        recent_runs=read_ledger(root, limit=recent_runs_limit),
        open_backlog=read_backlog(root, status="open"),
        cadence=manifest.review.cadence,
    )


def build_review_task_template(briefing: ReviewBriefing) -> str:
    """把 `ReviewBriefing` 拼成投进输入队列的任务描述文本。

    文本本身只做"给 agent 交代任务边界和已知材料摘要"，不替 agent 做
    判断——真正的分析（数据源健康趋势、结果回溯发现的问题、要不要生成
    enhancement 提案）留给拿到 `REVIEW_SESSION_TOOLS` 之后的 agent 自己
    去读项目目录、跑工具、做决定。
    """
    lines = [
        f"对外部项目 '{briefing.project_name}' 做一次周期性改进 review "
        f"（cadence: {briefing.cadence}）。",
        "",
        "背景：这不是纠错任务——不要求有报错或健康检查失败，目标是判断",
        "这个项目最近的表现有没有值得优化的地方，证据不足时不要臆断。",
        "",
        f"最近 {len(briefing.recent_runs)} 条执行记录：",
    ]
    if briefing.recent_runs:
        fail_count = sum(1 for r in briefing.recent_runs if not r.success)
        lines.append(f"  - 其中 {fail_count} 条失败（详情用 inspect_project 查看）")
    else:
        lines.append("  - （暂无执行记录）")

    lines.append("")
    lines.append(f"当前改进积压账本里有 {len(briefing.open_backlog)} 条待处理项：")
    for item in briefing.open_backlog[:10]:
        lines.append(f"  - [{item.source}] {item.summary}")
    if len(briefing.open_backlog) > 10:
        lines.append(f"  - ……以及另外 {len(briefing.open_backlog) - 10} 条")
    if not briefing.open_backlog:
        lines.append("  - （暂无）")

    lines.extend(
        [
            "",
            "请：",
            "1. 用 inspect_project 看一下当前健康状况和 project.yaml 声明；",
            "2. 逐条判断积压账本里的待处理项，能不能形成一个具体、机械性、"
            "有回归测试兜底的改动——能就用 propose_fix(change_type="
            "\"enhancement\") 生成一个可审核的分支（不要自己合并/落地）；",
            "3. 判断不了、影响面大、或者证据不足以支撑一个具体改动的，"
            "保持在积压账本里 open 状态，或者用 append_backlog_item 补充"
            "你发现的新证据，不要强行给出一个提案；",
            "4. 完成后用一段话总结这次 review 的结论，供用户查看。",
        ]
    )
    return "\n".join(lines)


def build_review_task_template_for(
    manifest: ProjectManifest, *, recent_runs_limit: int = 20
) -> str:
    """`gather_review_briefing` + `build_review_task_template` 的组合入口，
    供 CLI/未来的 daemon 接线直接调用。"""
    briefing = gather_review_briefing(manifest, recent_runs_limit=recent_runs_limit)
    return build_review_task_template(briefing)


# ── cadence → cron 表达式的最小映射 ────────────────────────────────────

_CADENCE_CRON_MAP = {
    "daily": "0 9 * * *",
    "weekly": "0 9 * * 1",
    "monthly": "0 9 1 * *",
}


def cadence_to_cron(cadence: str) -> Optional[str]:
    """把 `review.cadence` 的简单描述换算成 `cron_scheduler.py` 认识的
    cron 表达式；未来接线 `CronScheduler.add_job` 时会用到。不认识的
    cadence 返回 None，交给调用方决定报错还是回退到默认值——本函数只
    负责这个最小映射表，不做完整 cron 语法解析（呼应
    `external_projects/scheduler.py` 里"够用即可，不追求完整覆盖"的
    既有取舍）。
    """
    return _CADENCE_CRON_MAP.get(cadence.strip().lower())
