"""cycle.py — 一整轮闭环（方案 §3、§9、§14 P7）。

固定顺序：摘要 → 概念与关系 → 知识树提案 → 缺口与选题 → 调研 → 成文 → 审阅与发布。
每一步都受挡位与开关约束（gears.py），每一步的模型调用都经过 GuardedLLM（预算、超时、停止）。
* 单步失败不影响其它步骤（记录在报告中）；预算用尽、用户停止则整轮结束。
* 知识树变更只生成提案，不自动应用（方案 §5.3 的“先预览、后应用”）。
* 没有变化的步骤不调用模型：摘要靠内容哈希；概念与关系靠“摘要签名”；提案靠“是否有未挂载概念”。
* 报告写入 runs/last_cycle.json，并记入运行日志。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import budget, cancel, gears, joblog, settings, store
from .digest import runner as drunner
from .planning import states, topics
from .research import runner as rrunner
from .wiki import build as wbuild
from .wiki import coverage, placement
from .writing import runner as wrunner

SCOPE = "cycle"


def _failure_summary(failed: list) -> str:
    """把失败的步骤与原因写成一行，便于在日志中直接看出是哪一步、为什么失败。"""
    return "；".join(f"{x['step']}：{x['detail']}" for x in failed)[:600]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest_signature(ctx) -> list:
    from .library import catalog
    sig = []
    for row in catalog.list_articles(ctx):
        rec = drunner.load_digest(ctx, row["id"]) or {}
        if rec.get("status") == "ok":
            sig.append([row["id"], row.get("content_hash"), row.get("origin", "imported")])
    return sorted(sig)


def _g(ctx, llm, eff, sr):
    return budget.guard(ctx, llm, timeout_min=eff["budget"]["step_timeout_minutes"], since=sr["since"])


def _step_digest(ctx, llm, eff, rep, sr) -> str:
    c = drunner.run_digest(ctx, _g(ctx, llm, eff, sr))
    return f"成功 {c['ok']}，失败 {c['failed']}，跳过 {c['skipped']}"


def _step_wiki(ctx, llm, eff, rep, sr) -> str:
    sig = _digest_signature(ctx)
    if not sig:
        return "还没有摘要成功的文章，跳过"
    last = store.read_json(ctx.path("wiki", "last_build.json"), default=None)
    if last and last.get("signature") == sig:
        return "文章与摘要没有变化，跳过（零模型调用）"
    res = wbuild.build(ctx, _g(ctx, llm, eff, sr))
    store.write_json_atomic(ctx.path("wiki", "last_build.json"), {"signature": sig, "at": _now()})
    return f"{res['concepts']} 个概念，{res['edges']} 条边，{res['conflicts']} 处冲突，失败 {res['failed_concepts']}"


def _step_tree(ctx, llm, eff, rep, sr) -> str:
    if placement.load_proposal(ctx):
        return "已有待确认的提案，未自动应用（请在看板或 build_tree --apply 中确认）"
    proposal = placement.make_proposal(ctx, _g(ctx, llm, eff, sr), max_repair_rounds=eff["budget"]["max_repair_rounds"])
    coverage.compute(ctx)
    if proposal is None:
        return "没有未挂载的概念，无变化"
    return "已生成挂载提案，等待确认（不自动应用）"


def _step_topics(ctx, llm, eff, rep, sr) -> str:
    res = topics.run(ctx, _g(ctx, llm, eff, sr))
    msg = f"新增选题 {res['proposed']} 个" + ("（已自动放行）" if res.get("auto_approved") else "")
    if sr.get("manual"):
        # 「全部执行」：点击即授权放行，但仍受每轮选题上限约束
        limit = int(eff["budget"]["max_topics_per_cycle"])
        pending = [r["id"] for r in states.load_topics(ctx) if r["status"] == states.PROPOSED][:limit]
        for tid in pending:
            states.approve(ctx, tid)
        msg += f"；全部执行已放行 {len(pending)} 个"
    return msg


def _step_research(ctx, llm, eff, rep, sr) -> str:
    ok, why = gears.allowed(eff, "research")
    if not ok:
        return f"跳过：{why}"
    limit = int(eff["budget"]["max_topics_per_cycle"])
    done = []
    for row in [r for r in states.load_topics(ctx) if r["status"] == states.APPROVED][:limit]:
        res = rrunner.run(ctx, row["id"], llm=_g(ctx, llm, eff, sr), max_rounds=eff["budget"]["max_repair_rounds"],
                          search_backend=sr.get("search"), fetcher=sr.get("fetcher"))
        done.append(f"{row['id']}→{res['result']}")
    return "；".join(done) or "没有已放行的选题"


def _step_write(ctx, llm, eff, rep, sr) -> str:
    ok, why = gears.allowed(eff, "write")
    if not ok:
        return f"跳过：{why}"
    limit = int(eff["budget"]["max_articles_per_cycle"])
    done = []
    for row in [r for r in states.load_topics(ctx) if r["status"] == states.RESEARCHED][:limit]:
        res = wrunner.write(ctx, row["id"], _g(ctx, llm, eff, sr), max_rounds=eff["budget"]["max_repair_rounds"])
        done.append(f"{row['id']}→{res['result']}")
    return "；".join(done) or "没有已调研且待成文的选题"


def _step_review(ctx, llm, eff, rep, sr) -> str:
    ok, why = gears.allowed(eff, "review")
    if not ok:
        return f"跳过：{why}"
    done = []
    for row in [r for r in states.load_topics(ctx) if r["status"] == states.DRAFTED]:
        ck = store.read_json(ctx.path("drafts", row["id"], "checks.json"), default=None) or {}
        if not ck.get("passed"):
            continue
        wrunner.approve(ctx, row["id"])
        msg = f"{row['id']}→已审阅"
        if gears.allowed(eff, "publish")[0]:
            wrunner.publish(ctx, row["id"])
            msg += "→已发布"
        done.append(msg)
    return "；".join(done) or "没有待审阅的草稿"


STEPS = [("摘要", _step_digest), ("概念与关系", _step_wiki), ("知识树提案", _step_tree),
         ("缺口与选题", _step_topics), ("调研", _step_research), ("成文", _step_write), ("审阅与发布", _step_review)]


MANUAL_OVERRIDES = {"gear": "G3", "auto_approve": True, "auto_research": True, "auto_write": True,
                    "auto_publish": False, "scheduled_cycle": False}


def _manual_eff(eff: Dict[str, Any]) -> Dict[str, Any]:
    """「全部执行」的有效设置：用户点击即授权自动放行、调研、成文、标为已审阅；
    但不自动发布（auto_publish 保持关闭），知识树提案仍需人工确认。预算与停止照常生效。"""
    return {**eff, "automation": {**eff["automation"], **MANUAL_OVERRIDES}}


def run_cycle(ctx, llm, *, scheduled: bool = False, manual: bool = False, search_backend: Any = None,
              fetcher: Any = None, steps: Optional[list] = None) -> Dict[str, Any]:
    eff, _ = settings.effective_settings(ctx)
    if manual:
        eff = _manual_eff(eff)
    else:
        ok, why = gears.allowed(eff, "cycle")
        if not ok:
            raise ValueError(why)
        if scheduled and not gears.allowed(eff, "scheduled")[0]:
            raise ValueError("定时运行未开启（设置 → 自动化 → 定时运行一轮）")
    if llm is None:
        raise ValueError("运行一轮需要文本模型，请先确认模型配置可用")

    budget.clear_stop(ctx)
    sr = {"search": search_backend, "fetcher": fetcher, "since": time.time(), "manual": manual}
    rep: Dict[str, Any] = {"started_at": _now(), "gear": "全部执行" if manual else eff["automation"]["gear"],
                           "scheduled": scheduled,
                           "steps": [], "stopped": None}
    run_id = joblog.run_start(ctx.root, SCOPE, "一轮闭环")
    chosen = [s for s in STEPS if steps is None or s[0] in steps]
    for name, fn in chosen:
        rec = {"step": name, "status": "ok", "detail": ""}
        try:
            with joblog.step(ctx.root, SCOPE, name, heartbeat=True):   # 给本步骤的模型调用归属
                rec["detail"] = fn(ctx, llm, eff, rep, sr)
        except budget.BudgetStop as exc:
            rec.update(status="stopped", detail=str(exc))
            rep["steps"].append(rec)
            rep["stopped"] = "budget"
            break
        except (cancel.Cancelled, budget.StepTimeout) as exc:
            if isinstance(exc, cancel.Cancelled):
                rec.update(status="stopped", detail=str(exc))
                rep["steps"].append(rec)
                rep["stopped"] = "stop"
                break
            rec.update(status="timeout", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — 单步失败不影响其它步骤
            rec.update(status="failed", detail=f"{type(exc).__name__}: {str(exc)[:200]}")
        rep["steps"].append(rec)
        joblog.log(ctx.root, SCOPE, f"{name}：{rec['detail']}", level="ok" if rec["status"] == "ok" else "warn")

    if rep["stopped"] is None and budget.stop_requested(ctx):
        rep["stopped"] = "stop"                       # 停止请求发生在最后一次模型调用之后：如实记录
    rep["budget"] = budget.usage(ctx)
    rep["finished_at"] = _now()
    store.write_json_atomic(ctx.path("runs", "last_cycle.json"), rep)
    end = {"ok": "ok", "budget": "stopped", "stop": "stopped"}.get(rep["stopped"] or "", "ok")
    failed = [x for x in rep["steps"] if x["status"] in ("failed", "timeout")]
    if failed and not rep["stopped"]:
        end = "failed"
    joblog.run_end(ctx.root, SCOPE, run_id, "一轮闭环", status=end,
                   error=(f"已停止：{rep['stopped']}" if rep["stopped"] else _failure_summary(failed) or None))
    return rep
