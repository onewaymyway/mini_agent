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
import subprocess
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
        import mini_agent  # noqa: F401
    except ImportError:
        logger.error(
            "未检测到 mini_agent 框架（AI 研判依赖 workflow/skill 引擎），"
            "请在装有 mini_agent 的 Python 环境下运行本 entrypoint。"
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

    cmd = [
        sys.executable, "-m", "mini_agent",
        "workflow", "run", WORKFLOW_NAME, json.dumps(inputs, ensure_ascii=False),
        "--project", str(PROJECT_ROOT),
    ]
    logger.info("触发 workflow：%s（标的 %s）", WORKFLOW_NAME, code)
    proc = subprocess.run(
        cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.stdout:
        logger.info(proc.stdout.strip())
    if proc.returncode != 0:
        logger.error("workflow 执行失败（returncode=%d）：%s", proc.returncode, proc.stderr.strip())
        _common.set_run_detail(
            f"workflow {WORKFLOW_NAME} 执行失败(rc={proc.returncode}): {proc.stderr[-2000:]}"
        )
        return 1

    expected_report = REPORTS_DIR / "analysis" / f"{code}_{run_ts}_ai.md"
    if not expected_report.exists():
        logger.error("workflow 声称成功但未找到预期报告文件：%s", expected_report)
        _common.set_run_detail(f"预期报告文件不存在: {expected_report}")
        return 1

    logger.info("AI 研判报告已生成: %s", expected_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("stock_analysis_ai", main, trigger="manual"))
