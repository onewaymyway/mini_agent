"""entrypoints/_common.py — 各 entrypoint 共用的引导逻辑。

职责：
  1. 把项目根加入 `sys.path`，使得 `import stock_watch.xxx` 在直接
     `python entrypoints/xxx.py` 运行时也能工作（不要求先 `pip install -e .`）。
  2. 提供 `tracked_run()`：优先复用 mini_agent 框架的
     `external_projects.ledger.track_run()` 写执行账本（阶段 4 约定的
     `<root>/.agent/run_status.jsonl`）；如果本项目已经被移动到独立
     路径、不再和 mini_agent 装在同一个 Python 环境里，则自动退化为
     "不写账本、只执行"，不因为账本写不了就让整个 entrypoint 失败——
     呼应原则二：可独立运行是硬约束，daemon/框架配套能力是可选加成。
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@contextmanager
def tracked_run(entrypoint: str, *, trigger: str = "external_cron") -> Iterator[None]:
    try:
        from mini_agent.external_projects.ledger import track_run
    except ImportError:
        # 本项目已脱离 mini_agent 所在的 Python 环境独立运行，
        # 或用户尚未 `pip install mini_agent` —— 退化为直接执行。
        logging.getLogger("stock_watch").info(
            "未检测到 mini_agent 框架，跳过 run_status.jsonl 记账（不影响本次执行）"
        )
        yield
        return

    with track_run(PROJECT_ROOT, entrypoint, trigger=trigger):
        yield


def run_entrypoint(entrypoint: str, main_fn, *, trigger: str = "external_cron") -> int:
    """统一的 entrypoint 执行入口：跑 `main_fn()`（返回 int 退出码），
    在 `tracked_run` 里执行，非 0 退出码会被转换成一次异常，确保账本
    正确记为失败（`SystemExit`/裸退出码不会被 `track_run` 的
    `except Exception` 捕获，直接用退出码会漏记，因此统一走这里）。
    退出码本身仍然原样返回给调用方，不被这层异常处理吞掉。
    """
    code = 1
    try:
        with tracked_run(entrypoint, trigger=trigger):
            code = main_fn()
            if code:
                raise RuntimeError(f"{entrypoint} 以非零退出码结束: {code}")
    except RuntimeError:
        pass  # 已经在上面把 code 设成了 main_fn() 的真实返回值，不需要额外处理
    return code
