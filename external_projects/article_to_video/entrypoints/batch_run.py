"""entrypoints/batch_run.py — 批量队列命令行入口（R23 第三期）：无界面运行、查看、重排与重写报告。

用法：
  python entrypoints/batch_run.py                    # 前台运行队列，直到队列处理完；Ctrl+C 可中断
  python entrypoints/batch_run.py --status           # 只输出队列状态（JSON），不运行
  python entrypoints/batch_run.py --requeue          # 先把失败/已停止/停在检查点/超预算的条目全部重新排队，再运行
  python entrypoints/batch_run.py --requeue q0003    # 只重新排队这一篇，再运行
  python entrypoints/batch_run.py --report           # 只重写 report.json / report.md，不运行
  可加 --root <目录> 指定队列所在的项目集合目录（默认 ARTICLE_TO_VIDEO_HOME 或 <项目>/projects）。

注意：
  * 本入口不登记 cron、不在 project.yaml 中作为定时任务；批量生成会产生费用，只由人手动触发。
  * 队列的挡位与共用参数在界面「📦 批量生成」中设置（保存在 _queue/queue.json），命令行读取同一份。
  * 同一时间只允许一个运行者（界面或命令行）。已有运行者时本入口直接退出（退出码 3），不会重复启动。
  * 界面的「停止」按钮只在界面所在进程内立即中断；命令行运行时请用 Ctrl+C 中断，当前篇会记为「已停止」。

退出码：0 = 正常结束且没有失败条目；1 = 有失败条目，或参数/队列错误；3 = 已有运行者；130 = 被 Ctrl+C 中断。
输出 JSON（同时写入 _queue/report.json 与 report.md）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from _common import force_utf8_stdio  # noqa: E402

from article_to_video.batch import report, runner, store  # noqa: E402
from article_to_video.batch.store import QueueError  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BUSY = 3
EXIT_INTERRUPTED = 130


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None, *, pipeline_runner: Optional[Callable[..., Dict[str, Any]]] = None) -> int:
    """pipeline_runner 只供测试注入；命令行默认使用真实流水线。"""
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="批量队列：无界面运行、查看、重排与报告")
    ap.add_argument("--root", default=None, help="队列所在的项目集合目录（默认 ARTICLE_TO_VIDEO_HOME 或 <项目>/projects）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="只输出队列状态，不运行")
    mode.add_argument("--report", action="store_true", help="只重写 report.json 与 report.md，不运行")
    ap.add_argument("--requeue", nargs="?", const="__all__", default=None, metavar="ID",
                    help="运行前重新排队：不带 ID 表示全部未完成项；带 ID 只重排这一篇（如 q0003）")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else projects_root()
    try:
        if args.status:
            _emit({"ok": True, "root": str(root), **store.summary(root)})
            return EXIT_OK

        if args.report:
            rep = report.write_report(root)
            _emit({"ok": True, "root": str(root), "report": str(store.queue_dir(root) / report.REPORT_JSON),
                   "counts": rep["counts"]})
            return EXIT_OK

        if args.requeue is not None:
            target = None if args.requeue == "__all__" else args.requeue
            if target is not None and store.get_item(root, target) is None:
                raise QueueError(f"没有这个条目：{target}")
            n = store.requeue(root, target)
            print(f"已重新排队 {n} 篇", file=sys.stderr)

        if store.runner_alive(root):
            _emit({"ok": False, "error": "已有运行者在运行（界面或另一条命令行），本次未启动", "root": str(root),
                   **store.summary(root)})
            return EXIT_BUSY

        if not store.queue_file(root).is_file() or not store.load(root)["items"]:
            _emit({"ok": True, "root": str(root), "started": False, "reason": "队列为空，没有可运行的条目",
                   **store.summary(root)})
            report.write_report_quietly(root)
            return EXIT_OK

        kwargs = {"pipeline_runner": pipeline_runner} if pipeline_runner is not None else {}
        try:
            res = runner.run_queue(root, **kwargs)
        except KeyboardInterrupt:
            _emit({"ok": False, "error": "已被 Ctrl+C 中断；当前条目已记为「已停止」，可再次运行继续", "root": str(root),
                   **store.summary(root)})
            return EXIT_INTERRUPTED
    except QueueError as exc:
        _emit({"ok": False, "error": str(exc), "root": str(root)})
        return EXIT_FAILED

    if not res.get("started", True):
        _emit({"ok": False, "error": res.get("reason", "未启动"), "root": str(root), **store.summary(root)})
        return EXIT_BUSY
    failed = res.get("counts", {}).get("failed", 0)
    _emit({"ok": failed == 0, "root": str(root), "processed": res.get("processed", 0),
           "counts": res.get("counts", {}), "report": str(store.queue_dir(root) / report.REPORT_MD)})
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
