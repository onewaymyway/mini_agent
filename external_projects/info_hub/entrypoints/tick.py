#!/usr/bin/env python
"""entrypoints/tick.py — 执行一次调度：运行到期的信息源，生成到期的报告。

等价于 `python cli.py tick`。只执行到期任务，重复调用安全（间隔、降频、暂停都由 core 判定）。
可独立运行，也可由 OS 计划任务每 5 分钟调用；daemon 侧默认不调度（见 project.yaml 注释）。
"""
from __future__ import annotations

import _common


def main() -> int:
    return _common.cli_main(["tick"])


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("tick", main))
