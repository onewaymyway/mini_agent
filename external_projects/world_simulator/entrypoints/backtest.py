#!/usr/bin/env python
"""entrypoints/backtest.py — 回测与校准命令行入口（第二十二轮 WP5）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP5；核心逻辑在 `world_simulator/backtest.py`。

子命令：
  check <case>                       校验案例并打印引擎将看到的（别名化+平移后的）
                                     意图与真值，**不调用 LLM、不写任何数据**——
                                     跑之前先用它核对案例。
  run <case> [--steps N] [--matcher rule|llm] [--set k=v ...] [--time-basis auto|years_per_step]
                                     跑一次回测（真实 LLM 调用，成本 ≈ 步数）。
  ab <case> --arm-b k=v [--arm-b k=v ...] [--repeats R] [--matcher rule|llm]
            [--time-basis auto|years_per_step]
                                     同一案例 A（不改设置）/B（覆盖设置）各重复 R
                                     次，输出指标分布对比（成本 ≈ 2×R×步数）。
  metric-check [case]                指标级回测（A8）：校验案例、打印每个截止日的拟合参数与金丝雀结果（不跑蒙特卡洛）。
  metric-run [--case ID ...] [--runs N] [--seed S] [--no-save]
                                     指标级回测：不调 LLM、不联网；输出区间覆盖率等，并写
                                     reports/backtest_metric/summary.json（预测简报开启『回测反馈』后读取）。
  rescore <result.json> --override 真值id=候选id|none [...]
                                     用户覆盖匹配结果并重算指标。

`--set`/`--arm-b` 的值按 JSON 解析（`true`/`3`/`"x"`），解析失败按字符串。
输出目录：`reports/backtest/<case_id>/<时间戳>-<label>/`（数据在其下的
`data/`，与真实 `data/` 隔离）。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import _common  # noqa: F401

from world_simulator import backtest as bt
from world_simulator.config import PROJECT_ROOT, REPORTS_DIR, ensure_dirs

logger = logging.getLogger("world_simulator.backtest")


def _parse_kv(items: Optional[List[str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"参数格式应为 key=value，收到：{item!r}")
        key, raw = item.split("=", 1)
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def _fmt(v: Any) -> str:
    return "无数据" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def _print_metrics(result: Dict[str, Any]) -> None:
    m = result["metrics"]
    print(f"案例 {result['case_id']}（{'已核对' if result['case_verified'] else '⚠ 案例未经核对'}）"
          f" · 匹配器 {result['matcher']} · 推进 {result['steps_run']}/{result['steps_requested']} 步")
    if result.get("aborted"):
        print(f"  ⚠ 中途终止：{result['aborted']}（已用已有历史打分）")
    print(f"  召回（范围内）{_fmt(m['recall'])}  精确（下界）{_fmt(m['precision'])}  "
          f"顺序一致性 τ {_fmt(m['kendall_tau'])}")
    ie = m["interval_error"]
    print(f"  区间误差 {_fmt(ie['mean_abs_years'])} 年（带符号 {_fmt(ie['mean_signed_years'])}，正=引擎更慢）  "
          f"前置违反 {m['prerequisite_violations']['violations']}/{m['prerequisite_violations']['checked']}  "
          f"阶段一致 {_fmt(m['stage']['agreement'])}")
    tb = result.get("time_basis") or {}
    if tb:
        print(f"  时间基准：{tb.get('basis_used')}（{tb.get('reported_steps')}/{tb.get('total_steps')} 步有 elapsed_days）"
              f" · 模拟跨度 {_fmt(result.get('horizon_years'))} 年")
    tt = m.get("tech_timing") or {}
    if tt.get("available"):
        print(f"  阶段迁移时点偏差 {_fmt(tt['mean_abs_years'])} 年（带符号 {_fmt(tt['mean_signed_years'])}，正=引擎更慢）  "
              f"可计时 {tt['n_timed']} · 起点已在 {tt['n_initial']} · 未到达 {tt['n_not_reached']}  "
              f"阶段到达率 {_fmt(tt['stage_reached_rate'])}")
    elif tt:
        print(f"  阶段迁移时点偏差：无数据（{tt.get('reason')}）")
    print(f"  结构性告警（WP4）：{result['structural_health']['warning_counts'] or '无'}")
    for c in result["caveats"]:
        print(f"  ※ {c}")


def _matcher(kind: str, workspace_root: Path):
    if kind == "rule":
        return bt.rule_matcher
    from world_simulator.config import load_llm_cfg

    return bt.make_llm_matcher(load_llm_cfg(), workspace_root)


def _metric_main(args) -> int:
    from world_simulator import backtest_metrics as bm

    try:
        if args.cmd == "metric-check":
            cases = [bm.load_case(Path(args.case))] if args.case else bm.load_cases()
            for c in cases:
                print(f"案例 {c.id}（{c.family}）{'已核对' if c.verified else '⚠ 未核对'}：{c.title}")
                for cut in c.cutoffs:
                    truth = bm.extract_truth(c, cut)
                    hz = bm.future_horizon_days(c, cut, truth)
                    digest = bm.leak_check(c, cut, hz)
                    pack = bm.build_pack(c, cut, future_horizon_days=hz)
                    print(f"  截止 {cut}：历史点 {pack.history_counts} · 视野 {hz / 365.25:.1f} 年 · 金丝雀通过 {digest[:8]}")
                    for k, v in pack.fits.items():
                        ps = v.get("params") or {x: v[x] for x in ("low", "mode", "high") if x in v}
                        print(f"    {k}: {json.dumps(ps, ensure_ascii=False)[:300]}")
            return 0
        runs = args.runs or bm.DEFAULT_RUNS
        seed = args.seed if args.seed is not None else bm.DEFAULT_SEED
        results = bm.run_all(runs=runs, seed=seed, only=args.cases or None)
        summary = bm.summarize(results, runs=runs, seed=seed)
        for r in results:
            if not r.get("ok"):
                print(f"✗ {r.get('case_id')} @ {r.get('cutoff')}：{r.get('error')}")
                continue
            print(f"{r['case_id']:<22s} 截止 {str(r['cutoff']):<11s} 覆盖率 {_fmt(r['run_coverage'])}")
        o = summary["overall"]
        print(f"整体：{o['cases']} 个案例 / {o['runs']} 次运行，按运行平均覆盖率 {_fmt(o['coverage'])}（名义 0.8）→ {bm.VERDICT_LABELS[o['verdict']]}")
        for fam, g in summary["by_family"].items():
            print(f"  {fam:<17s} 案例 {g['cases']} 覆盖率 {_fmt(g['coverage'])} → {bm.VERDICT_LABELS[g['verdict']]}")
        for c in summary["caveats"]:
            print(f"  ※ {c}")
        if not args.no_save and not args.cases:
            out = REPORTS_DIR / "backtest_metric" / bm.SUMMARY_NAME
            bm.write_json(out, summary)
            bm.write_json(out.with_name("results.json"), results)
            print(f"汇总：{out}")
        elif not args.no_save:
            print("（只跑了部分案例，不写汇总，避免用残缺数据影响置信等级）")
        return 0
    except bm.BacktestMetricError as exc:
        logger.error("%s", exc)
        _common.set_run_detail(str(exc))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check")
    p.add_argument("case")

    p = sub.add_parser("run")
    p.add_argument("case")
    p.add_argument("--steps", type=int, default=None)
    p.add_argument("--matcher", choices=["rule", "llm"], default="rule")
    p.add_argument("--set", dest="sets", action="append", default=[])
    p.add_argument("--label", default="run")
    p.add_argument("--time-basis", dest="time_basis", choices=["auto", "years_per_step"], default=None,
                   help="候选时间基准：auto（有 elapsed_days 的步用它）/ years_per_step（忽略 elapsed_days）；默认取案例声明")

    p = sub.add_parser("ab")
    p.add_argument("case")
    p.add_argument("--arm-b", dest="arm_b", action="append", required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--time-basis", dest="time_basis", choices=["auto", "years_per_step"], default=None,
                   help="两臂开关不同时 elapsed_days 可能只出现在一臂，用 years_per_step 固定到同一基准")
    p.add_argument("--steps", type=int, default=None)
    p.add_argument("--matcher", choices=["rule", "llm"], default="rule")

    p = sub.add_parser("metric-check")
    p.add_argument("case", nargs="?", default=None)

    p = sub.add_parser("metric-run")
    p.add_argument("--case", dest="cases", action="append", default=[])
    p.add_argument("--runs", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--no-save", dest="no_save", action="store_true")

    p = sub.add_parser("rescore")
    p.add_argument("result")
    p.add_argument("--override", action="append", required=True)

    args = parser.parse_args()
    ensure_dirs()
    if args.cmd in ("metric-check", "metric-run"):
        return _metric_main(args)
    try:
        if args.cmd == "check":
            case = bt.load_case(Path(args.case))
            view = bt.anonymize(case)
            print(f"案例 {case.id}：{case.title}  {'（已核对）' if case.verified else '⚠ 未经核对'}")
            if case.disclaimer:
                print(f"  说明：{case.disclaimer}")
            print(f"  模板 {case.template} · {case.steps} 步 · 每步约 {case.years_per_step} 年")
            print(f"  引擎将看到的意图：{view['intent']}")
            print("  真值（别名化+平移后）：")
            for t in view["truths"]:
                req = f" ← {','.join(t['requires'])}" if t["requires"] else ""
                print(f"    {t['id']}  {t['year']:.0f}  {t['name']}  [{t['stage'] or '-'}]{req}")
            return 0

        if args.cmd == "rescore":
            overrides = {}
            for item in args.override:
                if "=" not in item:
                    raise SystemExit(f"--override 格式应为 真值id=候选id|none，收到：{item!r}")
                tid, cid = item.split("=", 1)
                overrides[tid.strip()] = None if cid.strip().lower() in ("none", "null", "") else cid.strip()
            result = bt.rescore(Path(args.result), overrides)
            _print_metrics(result)
            return 0

        case = bt.load_case(Path(args.case))
        matcher = _matcher(args.matcher, PROJECT_ROOT)
        from world_simulator.config import load_llm_cfg

        cfg = load_llm_cfg()
        if args.cmd == "run":
            out = bt.default_out_dir(REPORTS_DIR, case.id, args.label)
            result = bt.run_case(case, cfg=cfg, workspace_root=PROJECT_ROOT, out_dir=out,
                                 matcher=matcher, steps=args.steps,
                                 settings_override=_parse_kv(args.sets), label=args.label,
                                 time_basis=args.time_basis)
            _print_metrics(result)
            print(f"结果：{out / 'result.json'}")
            return 0

        out = bt.default_out_dir(REPORTS_DIR, case.id, "ab")
        report = bt.run_ab(case, cfg=cfg, workspace_root=PROJECT_ROOT, out_root=out,
                           arms={"A": {}, "B": _parse_kv(args.arm_b)}, repeats=args.repeats,
                           matcher=matcher, steps=args.steps, time_basis=args.time_basis)
        if report.get("time_basis_warning"):
            print(f"⚠ {report['time_basis_warning']}")
        for arm, stats in report["arms"].items():
            print(f"臂 {arm}（{stats['_runs']} 次，中途终止 {stats['_aborted_runs']} 次）")
            for name, s in stats.items():
                if not name.startswith("_"):
                    print(f"  {name:26s} n={s['n']} 均值 {_fmt(s['mean'])} 标准差 {_fmt(s['stdev'])}")
        print(report["verdict_note"])
        print(f"报告：{out / 'ab_report.json'}")
        return 0
    except bt.BacktestError as exc:
        logger.error("%s", exc)
        _common.set_run_detail(str(exc))
        return 1
    except ImportError as exc:
        logger.error("未检测到 mini_agent 框架，无法调用推演引擎：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("backtest", main, trigger="manual"))
