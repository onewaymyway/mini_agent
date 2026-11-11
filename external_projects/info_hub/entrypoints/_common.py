"""entrypoints/_common.py — 各 entrypoint 共用的引导逻辑（风格对齐 stock_watch）。

职责：
  1. 把项目根加入 `sys.path`，使直接 `python entrypoints/xxx.py` 时能 import 本项目的 cli/core。
  2. `run_entrypoint()`：在 `tracked_run()` 里执行，把这次运行记进 `.agent/run_status.jsonl`
     （daemon 通过它看到执行结果）。检测不到 mini_agent 框架时退化为“只执行、不记账”——
     可独立运行是硬约束，账本只是可选加成。
  3. `INFO_HUB_ROOT` 环境变量：指定项目根目录（测试与多实例用，与 app.py 约定一致）；
     不设置则为本文件所在项目。账本与数据都跟着它走。
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("INFO_HUB_ROOT") or PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class _NullRunHandle:
    """降级占位句柄：接受任意属性赋值，不产生任何效果。"""

    def __setattr__(self, name: str, value) -> None:
        pass


@contextmanager
def tracked_run(entrypoint: str, *, trigger: str = "manual") -> Iterator[object]:
    try:
        from mini_agent.external_projects.ledger import track_run
    except ImportError:
        logging.getLogger("info_hub").info("未检测到 mini_agent 框架，跳过 run_status.jsonl 记账（不影响本次执行）")
        yield _NullRunHandle()
        return
    with track_run(ROOT, entrypoint, trigger=trigger) as handle:
        yield handle


def run_entrypoint(entrypoint: str, main_fn: Callable[[], int], *, trigger: str = "manual") -> int:
    """跑 `main_fn()`（返回退出码）。非 0 退出码会转成异常，确保账本记为失败；退出码原样返回。"""
    code = 1
    try:
        with tracked_run(entrypoint, trigger=trigger) as handle:
            code = main_fn()
            if code:
                handle.detail = f"{entrypoint} 以非零退出码结束: {code}"
                raise RuntimeError(f"{entrypoint} 以非零退出码结束: {code}")
    except Exception:  # noqa: BLE001 - 异常已由 track_run 记账，这里只保证退出码
        if code == 0:
            code = 1
    return code


def cli_main(argv: list[str]) -> int:
    """调用本项目 cli.main（与 `python cli.py ...` 同一条代码路径），根目录由 INFO_HUB_ROOT 决定。"""
    import cli

    return cli.main(argv, root=ROOT)
