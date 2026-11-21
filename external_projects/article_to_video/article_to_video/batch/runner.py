"""batch/runner.py — 队列运行者（R23）：串行取出条目 → 导入 → 流水线 → 失败重试 → 下一篇。

要点：
  * 一次只处理一篇；每次循环都重新读盘取队首，所以运行中追加的条目会在同一次运行里被处理。
  * 条目轮到时才导入创建项目（失败的条目不留下半成品）；项目 id 立刻写回条目，重试和续跑都复用同一个项目，
    流水线可重入，已完成的大纲、配音、渲染不会重做。
  * 流水线失败（state=failed）时重试，最多 max_retries 次（默认 3，共 1+3 次尝试），重试前等待 retry_delays；
    重试用尽标为 failed，然后继续下一篇。导入失败（文章为空等）是确定性错误，不重试。
    waiting / budget_exceeded / stopped 不重试，直接记录后继续下一篇。
  * 停止：stop.flag（跨进程）+ 对当前项目 cancel.request；当前条目标为 stopped，其余保持排队，可再次开始。
  * 同一时间只允许一个运行者（runner.lock，带心跳）；异常退出遗留的 running 在下次启动时改回排队。
  * 每次运行结束（包括被中断）写 _queue/report.json 与 report.md（第三期）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .. import cancel, joblog
from ..importer import ArticleImportError, import_article
from ..pipeline.run import default_providers, run_pipeline
from . import report, store

SCOPE = "queue"
HEARTBEAT_SEC = 15.0
WAIT_POLL_SEC = 0.5

PipelineRunner = Callable[[Path, Dict[str, Any]], Dict[str, Any]]

_CURRENT: Dict[str, Path] = {}     # 队列根目录 → 当前正在处理的项目目录（供进程内停止使用）
_CURRENT_LOCK = threading.Lock()


def _log(root: Path, message: str, level: str = "info", **fields: Any) -> None:
    joblog.log(store.queue_dir(root), SCOPE, message, level=level, **fields)


def default_pipeline_runner(pdir: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    """按共用参数运行一遍流水线（旧方案或新方案，由 params["scheme"] 决定）。
    引擎配置不可用等异常会抛出，由调用方当作一次失败。"""
    from ..app_settings import effective_group
    from ..pipeline.run import SCOPE as OLD_SCOPE
    scheme = params.get("scheme", "old")
    # 绑定本篇项目的日志：流水线内部的详细日志（大纲、讲稿、配音、合成…）才能写入项目日志，批量监控页可见。
    # 旧方案的 run_pipeline 自己也会绑定同一项目的 scope；新方案没有绑定，必须在这里绑定。
    scope = "deck" if scheme == "new" else OLD_SCOPE
    with joblog.bind(pdir, scope):
        prov = default_providers(pdir)
        joblog.bound_log(f"批量：本篇使用{'新方案（幻灯片）' if scheme == 'new' else '旧方案'}，"
                         f"挡位 {params['gear']}，运行到「{params['until']}」")
        if scheme == "new":
            if params.get("merge_all"):
                joblog.bound_log("批量：新方案暂不支持「多期合并版」，本次忽略该选项", level="warn")
            from ..deck.pipeline import run_deck_pipeline
            return run_deck_pipeline(pdir, gear=params["gear"], until=params["until"],
                                     approve=bool(params.get("auto_export", True)),
                                     max_images=params["max_images"] or None,
                                     max_minutes=params["max_minutes"] or None,
                                     llm=prov["llm"], tts=prov["tts"], image=prov["image"])
        outline_engine = effective_group(pdir, "pipeline")["outline_engine"]
        return run_pipeline(pdir, until=params["until"], gear=params["gear"],
                            max_images=params["max_images"] or None, max_minutes=params["max_minutes"] or None,
                            approve=bool(params.get("auto_export", True)), llm=prov["llm"], tts=prov["tts"],
                            image_provider=prov["image"], merge_all=params["merge_all"],
                            outline_engine=outline_engine)


def request_stop(root: Path) -> None:
    """请求停止：写停止标志；本进程正在处理的项目同时立即中断（阻塞的模型请求约 0.2 秒内返回）。"""
    store.request_stop(root)
    with _CURRENT_LOCK:
        pdir = _CURRENT.get(str(Path(root).resolve()))
    if pdir is not None:
        cancel.request(pdir)


class _Heartbeat:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SEC):
            try:
                store.touch_runner(self._root)
            except OSError:
                pass

    def __enter__(self) -> "_Heartbeat":
        self._t.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()


def _wait(root: Path, seconds: float, sleep: Callable[[float], None]) -> bool:
    """重试前等待；等待期间每 0.5 秒检查停止标志。返回 True 表示被要求停止。"""
    remaining = float(seconds)
    while remaining > 0:
        if store.stop_requested(root):
            return True
        step = min(WAIT_POLL_SEC, remaining)
        sleep(step)
        remaining -= step           # 按已睡眠的时长累计（而不是读时钟），注入的假 sleep 也能正常结束
    return store.stop_requested(root)


def _finish(root: Path, item_id: str, state: str, *, error: str = "", outputs=None, **extra: Any) -> None:
    store.update_item(root, item_id, state=state, error=error, outputs=list(outputs or []),
                      finished_at=_now(), **extra)


def _now() -> str:
    from ..project_store import now_iso
    return now_iso()


def _process(root: Path, item: Dict[str, Any], params: Dict[str, Any], pipeline_runner: PipelineRunner,
             sleep: Callable[[float], None]) -> str:
    """处理一个已被取出（running）的条目，返回它的最终状态。"""
    item_id, title = item["id"], item["title"]
    # 1) 导入（只做一次；重试与续跑复用项目）
    project_id = item.get("project_id") or ""
    if not project_id:
        try:
            text = (store.queue_dir(root) / item["source_path"]).read_text(encoding="utf-8")
            res = import_article(root, source_text=text, source_name=item["source_name"], title=title,
                                 author=store.effective_author(params, item), branding=params["branding"],
                                 settings=params["settings"])
        except (ArticleImportError, OSError) as exc:
            _log(root, f"《{title}》导入失败：{exc}", "error")
            _finish(root, item_id, "failed", error=f"导入失败：{exc}")
            return "failed"
        project_id = res["project_id"]
        store.update_item(root, item_id, project_id=project_id)
        _log(root, f"《{title}》已创建项目 {project_id}", "ok")
        for w in res["warnings"]:
            _log(root, f"《{title}》{w}", "warn")
    pdir = Path(root) / project_id

    # 2) 运行流水线，失败重试
    delays = params["retry_delays"]
    while True:
        ev = cancel.begin(pdir)
        cancel.bind(ev)
        with _CURRENT_LOCK:
            _CURRENT[str(Path(root).resolve())] = pdir
        try:
            status = pipeline_runner(pdir, params)
        except Exception as exc:  # noqa: BLE001 — 配置缺失等，当作一次失败
            status = {"state": "failed", "error": str(exc), "outputs": []}
        finally:
            with _CURRENT_LOCK:
                _CURRENT.pop(str(Path(root).resolve()), None)
        state = status.get("state", "failed")
        if state == "done":
            outs = status.get("outputs") or []
            _log(root, f"《{title}》完成：{'、'.join(outs) or '无输出'}", "ok")
            _finish(root, item_id, "done", outputs=outs)
            return "done"
        if state in ("waiting", "budget_exceeded", "stopped"):
            detail = (status.get("waiting") or {}).get("detail") or status.get("error") or ""
            level = "info" if state == "waiting" else "warn"
            _log(root, f"《{title}》{state}：{detail}", level)
            _finish(root, item_id, state, error=detail)
            return state
        # failed（或未知状态）→ 重试
        err = status.get("error") or "未知错误"
        failures = store.get_item(root, item_id)["failures"] + 1
        store.update_item(root, item_id, failures=failures, error=err)
        if failures > params["max_retries"]:
            _log(root, f"《{title}》失败，重试 {params['max_retries']} 次仍未成功：{err}", "error")
            _finish(root, item_id, "failed", error=err, failures=failures)
            return "failed"
        delay = delays[min(failures - 1, len(delays) - 1)]
        _log(root, f"《{title}》第 {failures} 次失败：{err}；{delay:g} 秒后重试（{failures}/{params['max_retries']}）", "warn")
        if _wait(root, delay, sleep):
            _finish(root, item_id, "stopped", error="用户请求停止", failures=failures)
            return "stopped"


def run_queue(root: Path, *, pipeline_runner: Optional[PipelineRunner] = None,
              sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """运行队列直到没有排队条目或被要求停止。已有运行者在跑时直接返回 {"started": False}。

    pipeline_runner / sleep 供测试注入；默认用真实流水线与 time.sleep。
    """
    root = Path(root)
    runner = pipeline_runner or default_pipeline_runner
    if not store.acquire_runner(root):
        return {"started": False, "reason": "已有运行者在运行", "processed": 0, **store.summary(root)}
    processed = 0
    try:
        with _Heartbeat(root):
            store.clear_stop(root)
            n = store.recover_running(root)
            if n:
                _log(root, f"恢复 {n} 个上次中断的条目为排队", "warn")
            _log(root, "队列开始运行")
            while not store.stop_requested(root):
                item = store.claim_next(root)
                if item is None:
                    break
                params = store.load(root)["params"]        # 每篇开始时读取最新的共用参数
                _log(root, f"开始处理《{item['title']}》（{item['id']}，挡位 {params['gear']}）")
                try:
                    final = _process(root, item, params, runner, sleep)
                except BaseException as exc:                # noqa: BLE001 — 包括 Cancelled；保证状态不停在 running
                    store.update_item(root, item["id"], state="stopped", error=f"中断：{exc!r}", finished_at=_now())
                    raise
                processed += 1
                if final == "stopped" and store.stop_requested(root):
                    break
            reason = "已停止" if store.stop_requested(root) else "队列已处理完"
            _log(root, f"队列运行结束：{reason}（本次处理 {processed} 篇）", "info")
    finally:
        store.release_runner(root)
        report.write_report_quietly(root)       # 先放开运行者锁再写报告，报告里的“运行中”才准确；失败不影响队列
    return {"started": True, "processed": processed, **store.summary(root)}


def start_background(root: Path, **kwargs: Any) -> bool:
    """在后台线程里运行队列（界面用）。已有运行者（本进程或命令行进程）时返回 False，不重复启动。"""
    root = Path(root)
    if store.runner_alive(root):
        return False
    threading.Thread(target=run_queue, args=(root,), kwargs=kwargs, daemon=True, name="queue-runner").start()
    return True
