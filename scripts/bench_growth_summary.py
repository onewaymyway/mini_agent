#!/usr/bin/env python
"""成长顾问聚合耗时压测（人工运行，不进 CI）。

对应 next_doc/growth_tab_split_and_trend_index_plan.md §2.1 / §8 第 5 项 / §11 验收 1。

做什么
------
1. 合成模式（默认）：在临时目录里造 N 个"已采纳、已到回访窗口、有报告、证据显著
   增长"的话题，每个话题 --points 条趋势快照，然后对下面四个聚合函数计时，并统计
   趋势文件的读取次数（趋势索引修复后应恒为 1，与话题数无关）：
       growth_topic_map / pending_followups / reports_needing_refresh /
       diagnostics_snapshot
2. "改动前基线"：逐话题不带索引调用 `_topic_trend_series(paths, key)`——这正是
   修复前 `growth_topic_map()` 的行为（每个话题整文件读取+解析一次，平方级），
   用来和当前耗时对比。话题数大时很慢（400 话题约半分钟），默认只跑到
   --baseline-max（200）；用 --no-baseline 完全跳过。
3. 真实数据模式（--project-root）：不造数据，只读地对已有项目目录里的数据计时，
   用来在真实环境复核（§12 风险 1）。只调用只读聚合函数，不写任何文件。

用法
----
    python scripts/bench_growth_summary.py                       # 50/100/200/400 话题
    python scripts/bench_growth_summary.py --topics 400 --check  # 验收：topic_map < 0.5s 且读取次数=1
    python scripts/bench_growth_summary.py --topics 100 200 --no-baseline
    python scripts/bench_growth_summary.py --project-root /path/to/project

--check：任一话题数下 growth_topic_map 超过 --threshold（默认 0.5 s）或趋势文件读取
次数不为 1，则退出码 1。注意耗时受机器影响，阈值只在验收（§11 第 1 条：400 话题
× 60 点）这个规模上有意义；读取次数是确定性断言，不受机器影响。
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mini_agent.config.models import GrowthAdvisorConfig  # noqa: E402
from mini_agent.evolution import growth_advisor as ga  # noqa: E402
from mini_agent.profile import UserProfile  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402

DAY = 86400


@contextmanager
def count_trend_reads(paths):
    """统计 `_read_jsonl` 对趋势文件的调用次数（其它文件放行）。"""
    trend_path = Path(paths.growth_topic_trend_path)
    real = ga._read_jsonl
    counter = {"n": 0}

    def wrapper(path):
        if Path(path) == trend_path:
            counter["n"] += 1
        return real(path)

    with mock.patch.object(ga, "_read_jsonl", wrapper):
        yield counter


def seed(paths, n_topics: int, points: int, seed_value: int = 7) -> list[str]:
    """造 n 个话题（与 tests/test_growth_trend_index.py::_seed_topics 同构，
    让 topic_map / followups / refresh 都真正走到趋势读取）。返回 dedupe_key 列表。"""
    rnd = random.Random(seed_value)
    backlog = ga.GrowthBacklog(paths)
    now = time.time()
    keys: list[str] = []
    for i in range(n_topics):
        title = f"主题{i:04d}"
        c = backlog.add_or_merge(
            title, "理由", ["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10_000, dismissed_cooldown_days=30,
        )
        ga.generate_growth_report(paths, c)
        c = backlog.add_or_merge(
            title, "新理由", [f"e{j}" for j in range(1, 9)],
            min_evidence_count=3, max_pending=10_000, dismissed_cooldown_days=30,
        )
        backlog.set_status(c.candidate_id, ga.STATUS_ACCEPTED)
        keys.append(c.dedupe_key())
    all_c = backlog.load_all()
    for c in all_c:
        c.accepted_at = now - 40 * DAY
    backlog.save_all(all_c)
    for key in keys:
        count = 2
        for k in range(points):
            count += rnd.choice([0, 1, 2])
            ga._append_jsonl(
                paths.growth_topic_trend_path,
                {"dedupe_key": key, "topic": key,
                 "scanned_at": now - (points - k) * DAY,
                 "evidence_count": count, "confidence": rnd.random()},
            )
    return keys


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def measure(paths) -> dict:
    """对四个聚合函数各计时一次，并记录各自的趋势文件读取次数。"""
    # 回访窗口 30 天 < 造数据里的"已采纳 40 天"，pending_followups 才会真正走到趋势读取
    # （默认窗口更长时合成候选还没到期，返回空，测不到任何东西）。
    cfg = GrowthAdvisorConfig(followup_review_days=30)
    cases = {
        "growth_topic_map": lambda: ga.growth_topic_map(paths),
        "pending_followups": lambda: ga.pending_followups(paths, cfg),
        "reports_needing_refresh": lambda: ga.reports_needing_refresh(paths, cfg),
        "diagnostics_snapshot": lambda: ga.diagnostics_snapshot(paths, cfg, UserProfile(), None),
    }
    res = {}
    for name, fn in cases.items():
        with count_trend_reads(paths) as cnt:
            out, secs = timed(fn)
        res[name] = {"secs": secs, "reads": cnt["n"], "size": _size(out)}
    return res


def _size(out):
    try:
        return len(out)
    except TypeError:
        return None


def baseline_n_plus_one(paths, keys: list[str]) -> float:
    """修复前行为：每个话题各自读取并解析整个趋势文件。"""
    _, secs = timed(lambda: [ga._topic_trend_series(paths, k) for k in keys])
    return secs


def trend_file_info(paths) -> str:
    p = Path(paths.growth_topic_trend_path)
    if not p.exists():
        return "趋势文件不存在"
    with p.open("rb") as f:
        lines = sum(1 for _ in f)
    return f"趋势文件 {lines} 行 / {p.stat().st_size / 1024:.0f} KiB"


def print_row(topics, points, res, base, info):
    print(f"\n── {topics} 话题 × {points} 点（{info}）" if points != "-" else f"\n── 真实数据（{info}）")
    print(f"  {'函数':<26}{'耗时':>10}{'趋势读取次数':>14}{'结果条数':>10}")
    for name, r in res.items():
        size = "-" if r["size"] is None else r["size"]
        print(f"  {name:<26}{r['secs']:>9.3f}s{r['reads']:>14}{size:>10}")
    if topics != "（真实）" and res["pending_followups"]["size"] == 0 and res["pending_followups"]["reads"]:
        print("  注：pending_followups 结果为 0 属正常——合成趋势证据数一直在涨，"
              "被判定为\"顺延一轮\"；趋势读取路径已被走到。")
    if base is not None:
        cur = res["growth_topic_map"]["secs"]
        ratio = f"（当前约快 {base / cur:.0f}×）" if cur > 0 else ""
        print(f"  {'[改动前基线] N+1 逐话题读取':<26}{base:>9.3f}s{topics:>14}{'':>10}  {ratio}")
    elif base is None:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topics", type=int, nargs="+", default=[50, 100, 200, 400],
                    help="话题数列表（合成模式），默认 50 100 200 400")
    ap.add_argument("--points", type=int, default=60, help="每个话题的趋势快照数（默认 60）")
    ap.add_argument("--no-baseline", action="store_true", help="不跑改动前 N+1 基线")
    ap.add_argument("--baseline-max", type=int, default=200,
                    help="话题数超过该值时跳过基线（默认 200，400 话题基线约半分钟）")
    ap.add_argument("--check", action="store_true",
                    help="验收模式：topic_map 超阈值或读取次数不为 1 时退出码 1")
    ap.add_argument("--threshold", type=float, default=0.5, help="--check 的 topic_map 耗时阈值（秒）")
    ap.add_argument("--project-root", type=Path, default=None,
                    help="真实数据模式：对已有项目目录只读计时，不造数据")
    args = ap.parse_args(argv)

    failures: list[str] = []

    if args.project_root:
        paths = AgentPaths(project_root=args.project_root)
        res = measure(paths)
        print_row("（真实）", "-", res, None, trend_file_info(paths))
        if args.check:
            failures += check(res, args.threshold, "真实数据")
        return finish(failures)

    # 合成模式：HOME 指到临时目录，避免任何路径解析落到真实 ~/.agent
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        os.environ["USERPROFILE"] = home
        for n in args.topics:
            with tempfile.TemporaryDirectory() as tmp:
                paths = AgentPaths(project_root=Path(tmp))
                print(f"造数据：{n} 话题 × {args.points} 点 ……", end="", flush=True)
                keys, seed_secs = timed(lambda: seed(paths, n, args.points))
                print(f" 用时 {seed_secs:.1f}s")
                res = measure(paths)
                base = None
                if not args.no_baseline and n <= args.baseline_max:
                    base = baseline_n_plus_one(paths, keys)
                elif not args.no_baseline:
                    print(f"  （话题数 {n} > --baseline-max {args.baseline_max}，跳过基线）")
                print_row(n, args.points, res, base, trend_file_info(paths))
                if args.check:
                    failures += check(res, args.threshold, f"{n} 话题")
    return finish(failures)


def check(res, threshold, label) -> list[str]:
    out = []
    t = res["growth_topic_map"]["secs"]
    if t >= threshold:
        out.append(f"[{label}] growth_topic_map {t:.3f}s ≥ 阈值 {threshold}s")
    for name, r in res.items():
        if r["reads"] != 1:
            out.append(f"[{label}] {name} 趋势文件读取 {r['reads']} 次（期望 1）")
    return out


def finish(failures: list[str]) -> int:
    print()
    if failures:
        print("✗ 验收未通过：")
        for f in failures:
            print("  -", f)
        return 1
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
