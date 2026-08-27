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
def tracked_run(entrypoint: str, *, trigger: str = "external_cron") -> Iterator[object]:
    """让渡出账本 handle（`ledger.py::_RunHandle` 或降级场景下的占位对象），
    调用方可以在 `handle.detail` 上附加诊断信息（见 `run_entrypoint()`/
    `set_run_detail()`）。降级分支（检测不到 mini_agent 框架）让渡一个
    只接受属性赋值、不做任何事的占位对象，保持调用方 `handle.detail = ...`
    这行代码在两个分支下都不报错。
    """
    try:
        from mini_agent.external_projects.ledger import track_run
    except ImportError:
        # 本项目已脱离 mini_agent 所在的 Python 环境独立运行，
        # 或用户尚未 `pip install mini_agent` —— 退化为直接执行。
        logging.getLogger("stock_watch").info(
            "未检测到 mini_agent 框架，跳过 run_status.jsonl 记账（不影响本次执行）"
        )
        yield _NullRunHandle()
        return

    with track_run(PROJECT_ROOT, entrypoint, trigger=trigger) as handle:
        yield handle


class _NullRunHandle:
    """`tracked_run()` 降级分支（无 mini_agent 框架）让渡的占位句柄：
    接受任意属性赋值（`set_run_detail()` 照常调用），但不产生任何效果。
    """

    def __setattr__(self, name: str, value) -> None:
        pass


def append_backlog(summary: str, *, source: str = "outcome_review", evidence_ref: str = "") -> None:
    """往改进积压账本（`.agent/improvement_backlog.jsonl`）追加一条待办。

    与 `tracked_run` 同样的降级约定：检测不到 mini_agent 框架时静默
    跳过，不影响 entrypoint 本身的执行结果——写不写得进改进积压账本，
    从来不应该是"这次数据抓取/回溯任务算不算成功"的判定条件。
    """
    try:
        from mini_agent.external_projects.backlog import append_item
    except ImportError:
        logging.getLogger("stock_watch").info(
            "未检测到 mini_agent 框架，跳过改进积压账本写入（不影响本次执行）"
        )
        return
    try:
        append_item(PROJECT_ROOT, source=source, summary=summary, evidence_ref=evidence_ref or None)
    except Exception as exc:  # noqa: BLE001 - 记待办失败不应该让 entrypoint 本身失败
        logging.getLogger("stock_watch").warning("写入改进积压账本失败（已忽略）: %s", exc)


_current_run_handle = None  # 当前 tracked_run() 让渡出的 handle，供 set_run_detail() 使用


def set_run_detail(detail: str) -> None:
    """entrypoint 的 `main()` 在返回非 0 退出码之前调用，把"为什么失败"的
    详细信息（比如候选池里具体哪些标的失败、失败原因分别是什么）附到本次
    账本记录的 `detail` 字段上。

    不调用也没关系——`run_entrypoint()` 会退化成只记一条
    "{entrypoint} 以非零退出码结束: {code}" 的通用 `error_summary`，跟
    改造前行为一致，只是少了这份更具体的诊断信息。在没有 mini_agent
    框架（未走 `tracked_run` 的降级分支）时这里是空操作，不影响
    entrypoint 本身的返回值。
    """
    if _current_run_handle is not None:
        _current_run_handle.detail = detail


def run_entrypoint(entrypoint: str, main_fn, *, trigger: str = "external_cron") -> int:
    """统一的 entrypoint 执行入口：跑 `main_fn()`（返回 int 退出码），
    在 `tracked_run` 里执行，非 0 退出码会被转换成一次异常，确保账本
    正确记为失败（`SystemExit`/裸退出码不会被 `track_run` 的
    `except Exception` 捕获，直接用退出码会漏记，因此统一走这里）。
    退出码本身仍然原样返回给调用方，不被这层异常处理吞掉。

    `main_fn()` 内部如果想在返回非 0 之前留下比"退出码是几"更有用的
    诊断信息，调用本模块的 `set_run_detail(text)` 即可，会被自动带进
    这次账本记录的 `detail` 字段（见 `run_kline_batch.py` 的用法）。
    """
    global _current_run_handle
    code = 1
    try:
        with tracked_run(entrypoint, trigger=trigger) as handle:
            _current_run_handle = handle
            code = main_fn()
            if code:
                raise RuntimeError(f"{entrypoint} 以非零退出码结束: {code}")
    except RuntimeError:
        pass  # 已经在上面把 code 设成了 main_fn() 的真实返回值，不需要额外处理
    finally:
        _current_run_handle = None
    return code
