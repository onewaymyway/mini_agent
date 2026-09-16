#!/usr/bin/env python
"""entrypoints/list_simulations.py — 列出所有模拟实例及其当前状态摘要。

用法：
    python entrypoints/list_simulations.py
"""

from __future__ import annotations

import logging

import _common  # noqa: F401

from world_simulator.config import DATA_DIR, ensure_dirs

logger = logging.getLogger("world_simulator.list_simulations")


def main() -> int:
    from world_simulator.engine import list_simulations

    ensure_dirs()
    manifests = list_simulations(DATA_DIR)
    if not manifests:
        print("（暂无模拟实例）")
        return 0

    for m in manifests:
        print(
            f"{m.sim_id}\ttemplate={m.template}\tstatus={m.status}\t"
            f"step={m.current_step}\tpilot_mode={m.pilot_mode}\t{m.title}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("list_simulations", main, trigger="manual"))
