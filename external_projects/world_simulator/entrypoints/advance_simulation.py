#!/usr/bin/env python
"""entrypoints/advance_simulation.py — 推进模拟实例一步。

用法：
    python entrypoints/advance_simulation.py <sim_id> [--choice <option_id>]
    python entrypoints/advance_simulation.py --all-autopilot --steps 1   # 供 batch_advance_daily 调度（阶段四实现前为占位，见下）

阶段一实现"指定单个 sim_id 手动推进"；`--all-autopilot` 对应方案
`project.yaml` 里的 `batch_advance_daily` 调度入口（第 4 节），阶段四
（自动挡）已实现，只推进"开启了自动挡且处于进行中"的实例——手动挡
实例不受影响，见 `world_simulator/autopilot.py`。
"""

from __future__ import annotations

import argparse
import logging

import _common  # noqa: F401

from world_simulator.config import DATA_DIR, PROJECT_ROOT, ensure_dirs

logger = logging.getLogger("world_simulator.advance_simulation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sim_id", nargs="?", help="模拟实例 id")
    parser.add_argument("--choice", default=None, help="本步选择的选项 id，缺省由引擎给出默认走向")
    parser.add_argument(
        "--all-autopilot", action="store_true",
        help="批量推进所有开启自动挡的实例（阶段四实现，阶段一先明确报错）",
    )
    parser.add_argument("--steps", type=int, default=1)
    args = parser.parse_args()

    if args.all_autopilot:
        try:
            from mini_agent.config import load_config
        except ImportError as exc:
            logger.error("未检测到 mini_agent 框架，无法调用推演引擎：%s", exc)
            _common.set_run_detail(f"mini_agent 未安装: {exc}")
            return 1

        from world_simulator.autopilot import run_batch_autopilot

        ensure_dirs()
        cfg = load_config(project_root=PROJECT_ROOT)
        results = run_batch_autopilot(cfg, PROJECT_ROOT, DATA_DIR, steps=args.steps)

        if not results:
            logger.info("没有已开启自动挡且处于进行中的实例，本次跳过。")
            print("（没有需要推进的自动挡实例）")
            return 0

        n_ok = sum(1 for r in results if r.ok)
        n_failed = sum(1 for r in results if not r.ok)
        n_paused = sum(1 for r in results if r.paused_for_review)
        for r in results:
            if r.ok:
                note = "（触发暂停等待确认）" if r.paused_for_review else ""
                print(f"{r.sim_id}: ok, next_step={r.next_step}{note}")
            else:
                print(f"{r.sim_id}: FAILED, {r.error}")
        logger.info(
            "自动挡批量推进完成：成功 %s，失败 %s，触发暂停 %s", n_ok, n_failed, n_paused
        )
        _common.set_run_detail(f"成功 {n_ok}，失败 {n_failed}，触发暂停 {n_paused}")
        return 1 if n_failed and not n_ok else 0

    if not args.sim_id:
        logger.error("用法: python entrypoints/advance_simulation.py <sim_id> [--choice <option_id>]")
        return 2

    try:
        from mini_agent.config import load_config
    except ImportError as exc:
        logger.error("未检测到 mini_agent 框架，无法调用推演引擎：%s", exc)
        _common.set_run_detail(f"mini_agent 未安装: {exc}")
        return 1

    from world_simulator.engine import SimEngineError, advance

    ensure_dirs()
    cfg = load_config(project_root=PROJECT_ROOT)

    try:
        next_state = advance(
            cfg, PROJECT_ROOT, DATA_DIR, args.sim_id,
            choice_option_id=args.choice, chosen_by="user",
        )
    except SimEngineError as exc:
        logger.error("推进失败：%s", exc)
        _common.set_run_detail(str(exc)[:4000])
        return 1

    logger.info("推进成功，新状态 step=%s：%s", next_state.step, next_state.summary)
    print(f"step={next_state.step}")
    print(f"summary={next_state.summary}")
    if next_state.narrative:
        print(f"narrative={next_state.narrative}")
    for opt in next_state.options:
        print(f"option: {opt.id} — {opt.label}：{opt.description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("advance_simulation", main, trigger="manual"))
