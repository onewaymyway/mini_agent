"""
tests/test_stock_watch_optimization_loop_e2e.py — 阶段 6（端到端验证）
验收测试

对应 next_doc/stock_watch_continuous_improvement_plan.md 阶段 6：

    reconcile_outcomes 发现某类打分逻辑长期高估某个数据源来源的标的
    → 写入 backlog → review session 读到 → 生成 enhancement 类型提案
    （带 change_type 标记）→ 人工查看提案与证据 → 手动
    land_maintenance_fix 落地 → 后续黄金案例测试纳入这个案例。

全程单测层面模拟，不接入真实 daemon 主循环的定时调度（阶段4"未落地"
部分明确推迟的那块，本测试不依赖它）。用到的每一段能力
（backlog / review / maintenance / stock_watch.outcomes /
stock_watch.golden_cases）都已经在各自的阶段测试里单独验证过，本文件
只验证"串起来是否真的能走通"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "external_projects" / "stock_watch"))

import mini_agent.tools.external_projects as ext_tools  # noqa: E402
from mini_agent.external_projects.backlog import append_item, read_backlog  # noqa: E402
from mini_agent.external_projects.maintenance import land_maintenance_fix  # noqa: E402
from mini_agent.external_projects.manifest import load_manifest  # noqa: E402
from mini_agent.external_projects.registry import ExternalProjectRegistry  # noqa: E402
from mini_agent.external_projects.review import (  # noqa: E402
    build_review_task_template_for,
    gather_review_briefing,
)

from stock_watch.candidate_pool import CandidateEntry  # noqa: E402
from stock_watch.golden_cases import evaluate, load_golden_cases  # noqa: E402
from stock_watch.outcomes import build_outcome_records, notable_outcomes  # noqa: E402


def _init_git_project(root: Path, files: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(root), check=True)


SCORING_WEIGHTS_BEFORE = (
    "# stock_watch 评分权重（简化演示：真实项目里这类常量在\n"
    "# stock_watch/candidate_pool.py，这里为了测试隔离用独立文件代替）\n"
    "SOURCE_WEIGHT = {\n"
    "    \"eastmoney_hot_rank\": 1.0,\n"
    "}\n"
)
SCORING_WEIGHTS_AFTER_ENHANCEMENT = (
    "# stock_watch 评分权重（简化演示：真实项目里这类常量在\n"
    "# stock_watch/candidate_pool.py，这里为了测试隔离用独立文件代替）\n"
    "# 2026-08-26: 下调 eastmoney_hot_rank 权重——回溯发现该来源单独\n"
    "# 高分的标的近 4 周持续跑输，见 improvement_backlog outcome_review 条目。\n"
    "SOURCE_WEIGHT = {\n"
    "    \"eastmoney_hot_rank\": 0.6,\n"
    "}\n"
)


def test_full_optimization_loop_outcome_review_to_golden_case(tmp_path, monkeypatch):
    root = tmp_path / "stock_watch"
    _init_git_project(root, {"config/scoring_weights.py": SCORING_WEIGHTS_BEFORE})
    (root / "project.yaml").write_text(
        "name: stock_watch\n"
        "entrypoints:\n"
        "  work:\n"
        f'    cmd: "{sys.executable} -c \\"pass\\""\n'
        "review:\n"
        "  cadence: weekly\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add project.yaml"], cwd=str(root), check=True)

    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("stock_watch", root)
    monkeypatch.setattr(ext_tools, "_registry", lambda: registry)

    # ── 1) reconcile_outcomes 发现问题：一批候选池快照标的里，
    #        eastmoney_hot_rank 单来源打出的高分标的持续大跌 ──────────
    snapshot = {
        "000002": CandidateEntry(
            code="000002", name="万科A", score=45.0,
            sources=["eastmoney_hot_rank"],
        ),
        "600519": CandidateEntry(
            code="600519", name="贵州茅台", score=140.0,
            sources=["eastmoney_hot_rank", "xueqiu_hot_stock"],
        ),
    }
    # mock 涨跌幅：000002（单来源高分）大跌，600519（多来源共识）继续上涨——
    # 呼应 stock_watch_continuous_improvement_plan.md 3.2 节的用途描述。
    change_pcts = {"000002": -23.4, "600519": 12.1}
    records = build_outcome_records(
        snapshot, change_pcts, {}, snapshot_date="20260729",
    )
    notable = notable_outcomes(records, threshold_pct=15.0)
    assert {r.code for r in notable} == {"000002"}

    item = notable[0]
    direction = "大涨" if item.change_pct > 0 else "大跌"
    summary = (
        f"{item.name}({item.code}) 在候选池快照分数 {item.score_at_snapshot:.1f}，"
        f"4 周后{direction} {item.change_pct:.1f}%，eastmoney_hot_rank 单来源打分"
        "可能被系统性高估，值得核对评分权重"
    )
    append_item(root, source="outcome_review", summary=summary, evidence_ref="synthetic-e2e-test")

    # ── 2) review session 读到这条待办，能被拼进任务模板 ────────────
    manifest = load_manifest(root)
    briefing = gather_review_briefing(manifest)
    assert len(briefing.open_backlog) == 1
    task_text = build_review_task_template_for(manifest)
    assert "eastmoney_hot_rank" in task_text
    assert "change_type=" in task_text and "enhancement" in task_text

    backlog_payload = json.loads(ext_tools.list_backlog(name="stock_watch"))
    assert backlog_payload["ok"] is True
    assert len(backlog_payload["items"]) == 1
    assert backlog_payload["items"][0]["status"] == "open"

    # ── 3) review session 生成 enhancement 提案（不是 fix——没有硬失败
    #        信号，只是权重值得商榷）────────────────────────────────
    fix_payload = json.loads(
        ext_tools.propose_fix(
            name="stock_watch",
            changes={"config/scoring_weights.py": SCORING_WEIGHTS_AFTER_ENHANCEMENT},
            message="Lower eastmoney_hot_rank weight based on 4-week outcome review",
            reason=summary,
            change_type="enhancement",
        )
    )
    assert fix_payload["ok"] is True, fix_payload
    assert fix_payload["change_type"] == "enhancement"
    assert "Do not land it yourself" in fix_payload["message"]
    branch = fix_payload["branch"]

    # 主分支此时完全不受影响——enhancement 提案只是留了一个分支待人工判断。
    content = (root / "config" / "scoring_weights.py").read_text(encoding="utf-8")
    assert "1.0" in content and "0.6" not in content

    # ── 4) 人工查看提案与证据（此处用直接读 diff 模拟"人工看过"）后，
    #        手动决定落地——不是任何自动化脚本触发的 ───────────────
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "HEAD", branch, "--", "config/scoring_weights.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "-    \"eastmoney_hot_rank\": 1.0,\n" in diff
    assert "+    \"eastmoney_hot_rank\": 0.6,\n" in diff

    land_maintenance_fix(root, branch)
    content = (root / "config" / "scoring_weights.py").read_text(encoding="utf-8")
    assert "0.6" in content

    # 待办本身状态流转仍然是独立、显式的一步（本机制不因为落地了改动就
    # 自动帮用户把 backlog 状态标记掉），这里模拟人工顺手标记。
    from mini_agent.external_projects.backlog import update_status

    updated = update_status(root, backlog_payload["items"][0]["id"], "landed")
    assert updated.status == "landed"
    assert len(read_backlog(root, status="open")) == 0

    # ── 5) 这类"多来源共识 vs 单来源高分"的误判模式固化进黄金案例——
    #        真正的固化产物是 tests/golden_cases/cases.json 里的
    #        "multi_source_consensus_outranks_single_mention"（阶段5
    #        新增，覆盖的正是本次 000002/600519 这类模式：单来源标的
    #        即使打了分也不该稳定跑赢多来源共识标的）。这里不重新发明
    #        一个案例，而是断言那个案例仍然通过——呼应阶段6"后续黄金
    #        案例测试纳入这个案例"：本次 outcome_review 发现的问题模式
    #        已经有对应的回归护栏在守着，且这条护栏跟本次触发它的
    #        outcome_review 证据（000002 单来源大跌）是同一类模式，不是
    #        巧合。真正把 SOURCE_WEIGHT 接入候选池打分逻辑（enhancement
    #        分支要做但本测试没有做的部分——本测试只验证"发现问题→
    #        提案→人工核对→落地"这条链路走得通，不代表打分代码已经真的
    #        改了）留给未来一次独立的 enhancement 迭代。
    golden_cases = load_golden_cases()
    pattern_case = next(
        c for c in golden_cases
        if c.id == "multi_source_consensus_outranks_single_mention"
    )
    result = evaluate(pattern_case)
    assert result.passed, (
        result.missing_included, result.unexpected_included, result.score_violations
    )
