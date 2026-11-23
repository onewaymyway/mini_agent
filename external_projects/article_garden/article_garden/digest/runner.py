"""runner.py — 增量摘要运行器（方案 §5.1 增量、§19.4 流程 C）。

* 增量：摘要记录带 content_hash 与 digest_version；两者都没变且上次成功 → 零 LLM 调用。
* 单篇隔离：某篇失败只记录 failed（连同问题与最后一次输出），其它篇照常处理。
* 产物：wiki/digests/<id>.yaml（事实源之一；可删除后重跑）。
* 日志：经 joblog 写入库的 logs/job.jsonl，看板「运行日志」可见。用户停止（Cancelled）直接向上抛出。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from .. import budget, cancel, joblog, settings, store
from ..library import catalog
from . import summarize as sm

SCOPE = "digest"


def digest_path(ctx, article_id: str):
    return ctx.path("wiki", "digests", f"{article_id}.yaml")


def load_digest(ctx, article_id: str) -> Optional[Dict[str, Any]]:
    try:
        return store.read_yaml(digest_path(ctx, article_id), default=None)
    except Exception:  # noqa: BLE001 — 损坏的摘要视为“未摘要”，重跑即覆盖
        return None


def is_current(record: Optional[Dict[str, Any]], row: Dict[str, Any]) -> bool:
    return bool(record and record.get("status") == "ok"
                and record.get("content_hash") == row.get("content_hash")
                and record.get("digest_version") == sm.DIGEST_VERSION)


def status_of(ctx, row: Dict[str, Any]) -> str:
    rec = load_digest(ctx, row["id"])
    if rec is None:
        return "未摘要"
    if rec.get("status") != "ok":
        return "失败"
    return "已摘要" if is_current(rec, row) else "待更新"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_digest(ctx, llm, *, only: Optional[Iterable[str]] = None, force: bool = False) -> Dict[str, int]:
    """对库内文章做增量摘要。返回计数：{ok, failed, skipped}。"""
    eff, _ = settings.effective_settings(ctx)
    max_rounds = eff["budget"]["max_repair_rounds"]
    wanted = set(only) if only else None
    counts = {"ok": 0, "failed": 0, "skipped": 0}

    rows = [r for r in catalog.list_articles(ctx) if wanted is None or r["id"] in wanted]
    run_id = joblog.run_start(ctx.root, SCOPE, "文章摘要")
    try:
        for row in rows:
            if not force and is_current(load_digest(ctx, row["id"]), row):
                counts["skipped"] += 1
                continue
            try:
                _digest_one(ctx, llm, row, max_rounds, counts)
            except cancel.Cancelled:
                raise  # 用户停止：不当作单篇失败，整体结束
            except budget.StepTimeout as exc:
                # 单次模型调用超时：只让这一篇失败，其它篇继续（超时是单次调用的限制，不是整批的）
                counts["failed"] += 1
                _write_failed(ctx, row, [str(exc)], "")
                joblog.log(ctx.root, SCOPE, f"{row['id']}：{exc}，已跳过，继续其它文章", level="warn")
            except Exception as exc:  # noqa: BLE001 — 单篇失败不影响其它篇（step 已记录原因）
                counts["failed"] += 1
                _write_failed(ctx, row, [f"{type(exc).__name__}: {str(exc)[:200]}"], "")
    except cancel.Cancelled:
        joblog.run_end(ctx.root, SCOPE, run_id, "文章摘要", status="stopped", error="用户请求停止")
        raise
    except BaseException as exc:
        joblog.run_end(ctx.root, SCOPE, run_id, "文章摘要", status="failed",
                       error=f"{type(exc).__name__}: {exc}")
        raise
    if counts["failed"]:
        joblog.run_end(ctx.root, SCOPE, run_id, "文章摘要", status="failed",
                       error=f"{counts['failed']} 篇失败（见上方各篇记录，可单篇重试）")
    else:
        joblog.run_end(ctx.root, SCOPE, run_id, "文章摘要", status="ok")
    return counts


def _digest_one(ctx, llm, row: Dict[str, Any], max_rounds: int, counts: Dict[str, int]) -> None:
    body = catalog.read_body(ctx, row)
    with joblog.step(ctx.root, SCOPE, "摘要", item=row["id"]) as info:
        def on_round(msg: str) -> None:
            where = f"{row['id']}「{row.get('title', '')}」（来源文件：{row.get('source_name', '')}）"
            joblog.log(ctx.root, SCOPE, f"{where}：{msg}", level="warn")

        try:
            digest = sm.summarize(body, llm, max_repair_rounds=max_rounds, title_hint=row.get("title", ""),
                                  on_round=on_round)
        except sm.DigestFailed as exc:
            _write_failed(ctx, row, exc.issues, exc.last_output)
            counts["failed"] += 1
            info["status"] = "failed"
            info["detail"] = "；".join(exc.issues)[:600]
            return
        record = {
            "id": row["id"],
            "content_hash": row["content_hash"],
            "status": "ok",
            "generated_at": _now(),
            **digest,
            "issues": [],
        }
        store.write_yaml_atomic(digest_path(ctx, row["id"]), record)
        counts["ok"] += 1
        info["detail"] = f"{digest['kind']}／{digest['level']}，{len(digest['claims'])} 条断言"


def _write_failed(ctx, row: Dict[str, Any], issues, last_output: str) -> None:
    record = {
        "id": row["id"],
        "content_hash": row["content_hash"],
        "status": "failed",
        "generated_at": _now(),
        "digest_version": sm.DIGEST_VERSION,
        "issues": list(issues),
        "last_output": last_output,
    }
    store.write_yaml_atomic(digest_path(ctx, row["id"]), record)
