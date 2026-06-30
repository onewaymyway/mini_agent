"""
cli/commands/ensemble.py — /ensemble slash 命令处理

/ensemble                      — 显示当前 ensemble 配置状态
/ensemble on|off                — 等价于 mode=manual / mode=off 的快捷开关
/ensemble mode <off|manual|auto|always>
/ensemble granularity <llm_call|subagent|both>
/ensemble n <int>
/ensemble execution <serial|parallel>
/ensemble strategy <llm_judge|first_success|vote|merge>
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_ensemble_cmd(args: list[str], agent) -> None:
    ens = agent.cfg.ensemble

    if not args or args[0] == "status":
        R.console.print("\n[bold]Ensemble (Best-of-N) status:[/bold]")
        R.console.print(f"  mode               : [cyan]{ens.mode}[/cyan]")
        R.console.print(f"  granularity        : [cyan]{ens.granularity}[/cyan]")
        R.console.print(f"  n                  : {ens.n}")
        R.console.print(f"  execution          : {ens.execution}")
        R.console.print(f"  judge_strategy     : {ens.judge_strategy}")
        R.console.print(f"  judge_model        : {ens.judge_model or '(同主模型)'}")
        R.console.print(f"  early_stop         : {ens.early_stop_on_consensus}")
        R.console.print(f"  max_concurrency    : {ens.max_concurrency}")
        R.console.print(
            "\n  Usage: /ensemble on|off | /ensemble mode <off|manual|auto|always> | "
            "/ensemble granularity <llm_call|subagent|both> | /ensemble n <int> | "
            "/ensemble execution <serial|parallel> | /ensemble strategy <llm_judge|first_success|vote|merge>"
        )
        return

    sub = args[0].lower()

    if sub == "on":
        ens.mode = "manual" if ens.mode == "off" else ens.mode
        R.print_success(f"Ensemble mode → {ens.mode}（Agent 可主动调用 run_ensemble_llm / run_ensemble_subagents）")

    elif sub == "off":
        ens.mode = "off"
        R.print_success("Ensemble mode → off")

    elif sub == "mode" and len(args) >= 2:
        v = args[1].lower()
        if v not in ("off", "manual", "auto", "always"):
            R.print_error("mode 必须是 off|manual|auto|always")
            return
        ens.mode = v
        R.print_success(f"Ensemble mode → {v}")

    elif sub == "granularity" and len(args) >= 2:
        v = args[1].lower()
        if v not in ("llm_call", "subagent", "both"):
            R.print_error("granularity 必须是 llm_call|subagent|both")
            return
        ens.granularity = v
        R.print_success(f"Ensemble granularity → {v}")

    elif sub == "n" and len(args) >= 2:
        try:
            n = int(args[1])
            if n < 1:
                raise ValueError
            ens.n = n
            R.print_success(f"Ensemble n → {n}")
        except ValueError:
            R.print_error("Usage: /ensemble n <正整数>")

    elif sub == "execution" and len(args) >= 2:
        v = args[1].lower()
        if v not in ("serial", "parallel"):
            R.print_error("execution 必须是 serial|parallel")
            return
        ens.execution = v
        R.print_success(f"Ensemble execution → {v}")

    elif sub == "strategy" and len(args) >= 2:
        v = args[1].lower()
        if v not in ("llm_judge", "first_success", "vote", "merge"):
            R.print_error("strategy 必须是 llm_judge|first_success|vote|merge")
            return
        ens.judge_strategy = v
        R.print_success(f"Ensemble judge_strategy → {v}")

    else:
        R.print_error(
            "Usage: /ensemble | /ensemble on|off | /ensemble mode <...> | "
            "/ensemble granularity <...> | /ensemble n <int> | /ensemble execution <...> | "
            "/ensemble strategy <...>"
        )
