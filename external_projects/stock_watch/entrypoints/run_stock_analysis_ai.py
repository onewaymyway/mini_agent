#!/usr/bin/env python
"""entrypoints/run_stock_analysis_ai.py — 个股综合分析 + AI 研判（功能 4 的
AI 化版本，见 next_doc/external_projects_agent_skill_workflow_integration_
plan.md 阶段1）。

用法：
    python entrypoints/run_stock_analysis_ai.py 600519 [贵州茅台]

与 `run_stock_analysis.py` 的分工：
  - `run_stock_analysis.py`：只抓材料，不调 LLM，产出"材料报告"。
  - 本 entrypoint：抓材料（复用同一份 `stock_watch.analysis.collect()`，
    确定性 Python 代码，不进 workflow） + 触发一次 `stock_analysis_ai`
    workflow（`skill_agent` 用 `stock-analysis-judge` skill 给出 AI 综合
    研判，`tool_call` 落盘最终报告），产出"AI 研判报告"
    （`reports/analysis/<code>_<run_ts>_ai.md`）。

[实现记录 2026-08-30] 最初版本用 `subprocess` 调 `mini-agent workflow run`
CLI 子进程，实测踩了一个坑：`run_workflow_cli()` 的既有约定是"命令本身
有没有跑起来"和"工作流执行结果好不好"是两回事，**前台同步执行即使工作流
内部某个 step 失败，CLI 进程退出码依然是 0**（见该函数 docstring），
子进程真实的失败原因只体现在它打印到 stdout 的摘要文本里——而这条摘要
在独立 CLI 场景下由 `ui/terminal.py` 的后台线程异步落盘，`_flush_terminal()`
理论上会在进程退出前 flush，但跨进程用文本 stdout 传递"结构化执行结果"
本来就脆弱（没有稳定的机器可读格式可解析，只能猜测式地找关键字）。改为
直接在本进程内调用 `WorkflowRunner.run()`，拿到结构化的 `WorkflowRunResult`
（`status`/`step_results[].error` 等字段），彻底避免"子进程退出码看不出
工作流是否真的成功"这类问题，失败原因也能被准确记进账本的 `detail` 字段。

这是 external_projects_workspace_plan.md 原则二的一个例子：本 entrypoint
本身仍然可以被 OS 级 cron / 用户手动独立执行，不依赖 daemon 进程；它
唯一的运行时依赖是"同一台机器上装好了 mini_agent 这个 Python 库"（跟
`_common.tracked_run()` 依赖 `mini_agent.external_projects.ledger` 是
同一类依赖，属于"引擎能力"而不是"daemon 进程"）——如果检测不到，直接
清楚地报错退出，不静默跳过 AI 研判这一步（跟 tracked_run/backlog 的
"降级不影响主流程"不是一回事：这里 AI 研判本来就是本 entrypoint 唯一
要做的事，没有它就没有存在的意义）。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime

import _common  # noqa: F401

from stock_watch.analysis import collect
from stock_watch.config import PROJECT_ROOT, REPORTS_DIR, ensure_dirs

logger = logging.getLogger("stock_watch.analysis_ai_entry")

WORKFLOW_NAME = "stock_analysis_ai"


def main() -> int:
    if len(sys.argv) < 2:
        logger.error("用法: python entrypoints/run_stock_analysis_ai.py <代码> [名称]")
        return 2

    code = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else code

    try:
        from mini_agent.config import load_config
        from mini_agent.workflow.store import WorkflowStore
        from mini_agent.workflow.runner import WorkflowRunner
    except ImportError as exc:
        logger.error(
            "未检测到 mini_agent 框架（AI 研判依赖 workflow/skill 引擎），"
            "请在装有 mini_agent 的 Python 环境下运行本 entrypoint：%s", exc,
        )
        _common.set_run_detail("mini_agent 未安装，无法运行 AI 研判 workflow")
        return 1

    ensure_dirs()
    (REPORTS_DIR / "analysis").mkdir(parents=True, exist_ok=True)

    logger.info("抓取 %s(%s) 的公告/股吧/新闻材料...", name, code)
    material = collect(code, name)
    if len(material.errors) >= 3:
        logger.error("三类材料全部抓取失败，跳过 AI 研判：%s", material.errors)
        _common.set_run_detail(f"材料抓取全部失败: {material.errors}")
        return 1

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    inputs = {
        "code": code,
        "name": name,
        "run_ts": run_ts,
        "material_json": json.dumps(asdict(material), ensure_ascii=False),
    }

    # [直接调用引擎，见上方"实现记录"] cfg 显式指向本项目根（stock_watch
    # 有自己的 project.yaml，框架据此把 skill/workflow 私有目录解析到
    # <root>/skills、<root>/workflows，见 config/prompt_builder.py::
    # _resolve_skills_dir() 与 workflow/store.py::WorkflowStore 的对应
    # 修正，next_doc/external_projects_agent_skill_workflow_integration_
    # plan.md 第1节）。
    cfg = load_config(project_root=PROJECT_ROOT)
    store = WorkflowStore(PROJECT_ROOT)
    wf = store.load(WORKFLOW_NAME)
    if wf is None:
        logger.error(
            "找不到 workflow 定义 %r（预期路径：%s/workflows/%s.yaml）",
            WORKFLOW_NAME, PROJECT_ROOT, WORKFLOW_NAME,
        )
        _common.set_run_detail(f"workflow {WORKFLOW_NAME} 未找到，请检查 workflows/ 目录")
        return 1

    logger.info("触发 workflow：%s（标的 %s）", WORKFLOW_NAME, code)
    runner = WorkflowRunner(cfg)
    result = runner.run(wf, inputs)

    if result.status != "done":
        failed_steps = [
            f"{sr.step_id}({sr.status.value}): {sr.error}"
            for sr in result.step_results
            if sr.status.value != "done"
        ]
        detail = f"workflow 状态={result.status}；" + "；".join(failed_steps)
        logger.error("workflow 执行未成功：%s", detail)
        _common.set_run_detail(detail[:4000])
        return 1

    expected_report = REPORTS_DIR / "analysis" / f"{code}_{run_ts}_ai.md"
    if not expected_report.exists():
        logger.error("workflow 状态为 done 但未找到预期报告文件：%s", expected_report)
        _common.set_run_detail(
            f"workflow done 但报告文件缺失: {expected_report}；"
            f"workflow_session_id={result.workflow_session_id}"
        )
        return 1

    logger.info("AI 研判报告已生成: %s", expected_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("stock_analysis_ai", main, trigger="manual"))
