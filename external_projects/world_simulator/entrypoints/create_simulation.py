#!/usr/bin/env python
"""entrypoints/create_simulation.py — 用一句话意图创建一个模拟实例。

用法：
    python entrypoints/create_simulation.py "<一句话模拟意图>" [--template life_sim]

阶段一只支持 `life_sim` 模板（人生模拟），`--template` 参数已预留，
对应方案阶段六"新增模拟类型 = 新增一个 skill"的扩展点。
"""

from __future__ import annotations

import argparse
import logging

import _common  # noqa: F401

from world_simulator.config import DATA_DIR, PROJECT_ROOT, ensure_dirs

logger = logging.getLogger("world_simulator.create_simulation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intent", help="一句话模拟意图描述")
    parser.add_argument("--template", default="life_sim", help="场景模板名，默认 life_sim")
    args = parser.parse_args()

    try:
        from mini_agent.config import load_config
    except ImportError as exc:
        logger.error("未检测到 mini_agent 框架，无法调用推演引擎：%s", exc)
        _common.set_run_detail(f"mini_agent 未安装: {exc}")
        return 1

    from world_simulator.engine import SimEngineError, create_simulation
    from world_simulator.spec_generator import ScenarioGenerationError

    ensure_dirs()
    cfg = load_config(project_root=PROJECT_ROOT)

    try:
        manifest = create_simulation(
            cfg, PROJECT_ROOT, DATA_DIR, template=args.template, intent=args.intent
        )
    except (ScenarioGenerationError, SimEngineError) as exc:
        logger.error("创建模拟实例失败：%s", exc)
        _common.set_run_detail(str(exc)[:4000])
        return 1

    logger.info("模拟实例已创建：%s（%s）", manifest.sim_id, manifest.title)
    print(f"sim_id={manifest.sim_id}")
    print(f"title={manifest.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("create_simulation", main, trigger="manual"))
