"""entrypoints/cycle.py — 运行一轮闭环（P7）。

用法：
  python entrypoints/cycle.py <domain_id>            运行一轮（需挡位 G1 以上；各步骤受开关约束）
  python entrypoints/cycle.py <domain_id> --scheduled 由定时任务调用：需要打开「定时运行一轮」
  python entrypoints/cycle.py <domain_id> --stop     请求停止正在运行的一轮（在下一次模型调用前生效）
说明：每日 LLM 调用上限（本库与全局）与单步超时在运行中强制执行；超限即停并记录在报告中。
默认 project.yaml 不登记定时任务；打开定时需在设置中开启，并自行登记 cron。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import budget, domains, gears, settings  # noqa: E402
from article_garden.cycle import run_cycle  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    stop = "--stop" in args
    scheduled = "--scheduled" in args
    args = [a for a in args if a not in ("--stop", "--scheduled")]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        ctx = domains.DomainContext(args[0])
        if stop:
            budget.request_stop(ctx)
            print(json.dumps({"ok": True, "stop_requested": True}, ensure_ascii=False))
            return 0
        eff, _ = settings.effective_settings(ctx)
        ok, why = gears.allowed(eff, "cycle")
        if not ok:
            print(f"失败：{why}", file=sys.stderr)
            return 1
        ready, why = text_model_status()
        if not ready:
            print(f"失败：模型未就绪（{why}）", file=sys.stderr)
            return 1
        llm = budget.guard(ctx, MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"]),
                           timeout_min=eff["budget"]["step_timeout_minutes"])
        report = run_cycle(ctx, llm, scheduled=scheduled)
        print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
        return 0
    except (domains.DomainError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
