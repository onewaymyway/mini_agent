"""
external_projects/scheduler.py — 按 `project.yaml` 的 `schedule` 触发外部项目

对应 `next_doc/external_projects_workspace_plan.md` 阶段 3 第三项。

设计边界（重要，呼应原则二）：
  - 这里的调度器只是"daemon 在场时的一个可选加成"：daemon 每隔一段
    时间调用一次 `run_due_entrypoints()`，对已注册且到期的 entrypoint
    发起一次 headless 子进程执行；即使 daemon 从未运行这个循环，
    entrypoint 依然可以被 OS cron / 用户手动执行，产出完全一致
    （因为最终都是同一条 `cmd`，走同一个 `_run_entrypoint`）。
  - 触发执行本身复用"subprocess 隔离"这个既有心智模型（workflow 的
    `runner.py` 对 `script`/`python_step` 类型 step 就是这样隔离执行
    的），但不直接依赖 workflow 内部实现——外部项目的 entrypoint 是
    "任意一条 shell 命令"，比 workflow step 的假设更少，所以这里用
    标准库 `subprocess` 直接实现，避免为了复用而引入不必要的耦合。
  - cron 表达式解析：只实现"下一次到期时间"需要的最小子集判断逻辑
    （分/时/日/月/周五个字段，支持 `*`、单值、逗号列表），不追求完整
    覆盖 cron 语法（比如步进 `*/5`），够用即可；真正生产场景如果需要
    更复杂的 cron 语义，建议交给 OS 原生 cron（本来就是原则二鼓励的
    退路），daemon 内置调度器只承担"daemon 在线时锦上添花"的角色。
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

from mini_agent.external_projects.manifest import (
    EntrypointSpec,
    ProjectManifest,
    build_cmd_with_params,
)
from mini_agent.external_projects.registry import ExternalProjectRegistry, RegisteredProject


@dataclass
class EntrypointRunResult:
    project_name: str
    entrypoint_key: str
    returncode: int
    trigger: str  # "daemon" | "manual"
    detail: Optional[str] = None  # 失败时子进程 stdout/stderr 尾部，成功为 None


def _cron_field_matches(field_expr: str, value: int) -> bool:
    if field_expr == "*":
        return True
    for token in field_expr.split(","):
        token = token.strip()
        if token and token.isdigit() and int(token) == value:
            return True
    return False


def cron_matches(cron_expr: str, moment: _dt.datetime) -> bool:
    """判断 `moment`（本地时间，精确到分钟）是否命中一条 5 字段 cron 表达式。"""
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式字段数不对: '{cron_expr}'")
    minute, hour, day, month, weekday = parts
    # Python weekday(): 周一=0..周日=6；cron 惯例周日=0/7，周一=1——这里
    # 统一换算成 cron 惯例（0=周日）方便与配置里 "1-5"（周一至周五）对齐。
    cron_weekday = (moment.isoweekday()) % 7  # 周一..周六=1..6，周日=0
    return (
        _cron_field_matches(minute, moment.minute)
        and _cron_field_matches(hour, moment.hour)
        and _cron_field_matches(day, moment.day)
        and _cron_field_matches(month, moment.month)
        and _cron_weekday_matches(weekday, cron_weekday)
    )


def _cron_weekday_matches(field_expr: str, weekday: int) -> bool:
    if field_expr == "*":
        return True
    for token in field_expr.split(","):
        token = token.strip()
        if "-" in token:
            lo, hi = token.split("-", 1)
            if lo.isdigit() and hi.isdigit() and int(lo) <= weekday <= int(hi):
                return True
        elif token.isdigit() and int(token) == weekday:
            return True
    return False


def _run_entrypoint(
    manifest: ProjectManifest,
    entrypoint: EntrypointSpec,
    *,
    trigger: str,
    params: Optional[Dict[str, str]] = None,
) -> EntrypointRunResult:
    """
    在外部项目自己的根目录下、以子进程方式执行一条 entrypoint 命令。

    `params`：按 `entrypoint.params` 声明拼成位置参数追加在 `cmd` 后面
    （`manifest.py::build_cmd_with_params()`，阶段6）。声明缺失的必填
    参数、或传了未声明的参数名，会在这里直接抛 `EntrypointParamError`
    ——不会执行任何子进程，调用方（API 路由/CLI）据此返回 400 而不是
    让命令带着空参数跑起来再报运行时错误。

    执行完成后（无论成功/失败/超时）都会往该项目自己的
    `<root>/.agent/run_status.jsonl` 写一条账本记录（阶段 4：
    `external_projects/ledger.py::record_run`），trigger 字段原样
    传入的 "daemon" 或 "manual"，与 entrypoint 脚本自己用 `track_run()`
    上报、或用户直接被 OS cron 触发写 `trigger="external_cron"`，三者
    共用同一份账本、同一个 schema，daemon 侧读的时候不需要区分来源。

    stdout/stderr 会被捕获（而不是像早期版本那样直接继承父进程的
    输出流）：一是这样才能在失败时把输出尾部存进账本的 `detail` 字段
    （否则 `returncode=1` 之外用户在看板/CLI 里什么都看不到，只能去
    翻 daemon 自己的日志，等于没记）；二是 `subprocess.run(shell=True)`
    不捕获输出时，headless 场景（真正被 OS cron 触发、父进程根本不是
    交互式终端）下这些输出本来就会被静默丢弃或混进 daemon 日志，捕获
    下来反而更可靠。命令自身如果有需要持久化的产出（图表文件等），
    走的是自己的文件系统写入，不受这里捕获 stdout 影响。
    """
    cmd = build_cmd_with_params(entrypoint, params)
    cwd = manifest.source_dir
    started_at = _local_iso()
    error_summary: Optional[str] = None
    detail: Optional[str] = None
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd) if cwd else None,
            timeout=entrypoint.timeout_sec,
            capture_output=True,
            text=True,
            errors="replace",
        )
        returncode = proc.returncode
        if returncode != 0:
            error_summary = f"entrypoint exited with code {returncode}"
            detail = _format_process_output(proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        returncode = -1
        error_summary = f"entrypoint timed out after {entrypoint.timeout_sec}s"
        detail = _format_process_output(
            exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout,
            exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr,
        )
    finished_at = _local_iso()

    if cwd is not None:
        from mini_agent.external_projects.ledger import record_run

        record_run(
            cwd,
            entrypoint.key,
            returncode,
            trigger,
            started_at=started_at,
            finished_at=finished_at,
            error_summary=error_summary,
            detail=detail,
        )

    return EntrypointRunResult(
        project_name=manifest.name,
        entrypoint_key=entrypoint.key,
        returncode=returncode,
        trigger=trigger,
        detail=detail,
    )


def _local_iso() -> str:
    """本机本地时间的 ISO-8601 表示（带时区偏移量）。

    与 `external_projects/ledger.py::_now_local_iso()` 是同一个逻辑，
    这里不直接 import 复用是为了避免 `scheduler.py` 在没有写账本需求
    的调用路径（`cwd is None`）上也强制依赖 ledger 模块的导入时机——
    两处都是一行 stdlib 调用，重复比额外耦合更便宜。
    """
    return _dt.datetime.now().astimezone().isoformat()


def _format_process_output(stdout: Optional[str], stderr: Optional[str]) -> Optional[str]:
    """把子进程的 stdout/stderr 拼成一段人可读的 detail 文本。

    优先展示 stderr（大多数程序的报错信息写在这里），stdout 附在后面
    提供上下文；两者都为空时返回 None（比如命令被 shell 直接判定语法
    错误、还没来得及产生任何输出）。真正的长度截断交给
    `ledger.record_run()` 内部的 `truncate_detail()` 统一处理，这里
    只负责拼接、不重复实现截断逻辑。
    """
    parts = []
    if stderr and stderr.strip():
        parts.append(f"[stderr]\n{stderr.strip()}")
    if stdout and stdout.strip():
        parts.append(f"[stdout]\n{stdout.strip()}")
    if not parts:
        return None
    return "\n\n".join(parts)


def run_due_entrypoints(
    registry: ExternalProjectRegistry,
    *,
    now: Optional[_dt.datetime] = None,
) -> List[EntrypointRunResult]:
    """
    扫描注册表里所有已启用的项目，触发本分钟内到期的 entrypoint。

    供 daemon 的后台调度循环每分钟调用一次；单次调用只触发"当前这一
    分钟"命中的 entrypoint，不做"错过的补跑"（补跑策略留给未来有实际
    需求时再设计，避免过早假设用户想要什么样的补跑语义）。
    """
    moment = now or _dt.datetime.now()
    results: List[EntrypointRunResult] = []
    for record in registry.list(enabled_only=True):
        try:
            manifest = registry.load_manifest_for(record.name)
        except Exception:
            # 单个项目的 manifest 解析失败不应该挡住其它项目被调度到，
            # 与 registry._load() 对损坏文件的容错原则一致。
            continue
        for entrypoint in manifest.scheduled_entrypoints():
            cron_expr = entrypoint.cron_expr
            if not cron_expr:
                continue
            if cron_matches(cron_expr, moment):
                results.append(_run_entrypoint(manifest, entrypoint, trigger="daemon"))
    return results


def trigger_run(
    registry: ExternalProjectRegistry,
    project_name: str,
    entrypoint_key: str,
    *,
    trigger: str = "manual",
    params: Optional[Dict[str, str]] = None,
) -> EntrypointRunResult:
    """立即触发某个已注册项目的某个 entrypoint 一次（供 CLI `projects run`/
    看板「▶️ 手动触发」使用）。`params` 见 `_run_entrypoint()` 说明。"""
    manifest = registry.load_manifest_for(project_name)
    entrypoint = manifest.entrypoint(entrypoint_key)
    return _run_entrypoint(manifest, entrypoint, trigger=trigger, params=params)
