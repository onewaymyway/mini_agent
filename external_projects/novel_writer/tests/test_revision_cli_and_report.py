"""R6 收尾测试：CLI 全部子命令可解析、非 LLM 子命令在真实工作目录上可运行，评估报告导出内容。全部不调用真实 LLM。"""

import json
import sys
from pathlib import Path

import pytest

from novel_writer.revision_flow import config as CFG

from novel_writer.revision_flow import outline_check as OC
from novel_writer.revision_flow import review_adopt as RA
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import review_report as RR
from novel_writer.revision_flow import review_cards as RC

from test_revision_review_checks import chars, _inject  # noqa: F401
from test_revision_review_cards import CardInvoker, _novel, isolated  # noqa: F401
from test_revision_review_llm import ReviewStub

ENTRY = Path(__file__).resolve().parents[1] / "entrypoints"
sys.path.insert(0, str(ENTRY))
import post_revision as CLI  # noqa: E402

ALL_CMDS = ["accept", "adopt", "adopt-batch", "baseline", "change-add", "confirm", "diff", "discard", "exclude",
            "export", "outline-adopt", "outline-candidate", "outline-check", "outline-diff", "outline-discard",
            "outline-list", "preview", "resume", "review-cards", "review-checks", "review-estimate",
            "review-findings", "review-increment", "review-llm", "review-options", "review-export", "run",
            "settings", "status", "stop", "triage", "verify"]


def test_every_subcommand_parses_help(capsys):
    for cmd in ALL_CMDS:
        with pytest.raises(SystemExit) as exc:
            CLI.main(["--novel", ".", cmd, "--help"])
        assert exc.value.code == 0, cmd


def _ready(tmp_path):
    ws = _novel(tmp_path)
    CFG.update(ws, {"review_dimensions": list(CFG.REVIEW_DEFAULT_DIMENSIONS) + ["rule_legacy"]})   # 死亡/地点规则默认关闭，这些用例需要它
    RC.generate_cards(ws, None, CardInvoker())
    _inject(ws, 1, characters=chars(("甲", "战死沙场")))
    _inject(ws, 3, characters=chars(("甲", "在场")))
    return ws


def _run(ws, *argv):
    return CLI.main(["--novel", str(ws.root), *argv])


def test_cli_nonllm_flow_end_to_end(tmp_path, capsys):
    ws = _ready(tmp_path)
    assert _run(ws, "review-checks") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"] == {"严重": 1}
    assert _run(ws, "review-estimate") == 0
    est = json.loads(capsys.readouterr().out)
    assert "llm_review" in est and est["llm_review"]["checks_run"] is True
    assert _run(ws, "review-findings") == 0
    fid = json.loads(capsys.readouterr().out)["findings"][0]["id"]
    assert _run(ws, "review-options", "--finding", fid) == 0
    opts = json.loads(capsys.readouterr().out)
    assert len(opts) == 2                                                  # 矛盾类两个方向
    assert _run(ws, "outline-list") == 0 and json.loads(capsys.readouterr().out) == []
    assert _run(ws, "review-export") == 0
    exp = json.loads(capsys.readouterr().out)
    assert exp["path"].endswith("export.md") and (ws.root / exp["path"]).exists()
    assert _run(ws, "settings") == 0 and "review_dimensions" in json.loads(capsys.readouterr().out)


def test_cli_reports_business_errors_with_exit_2(tmp_path, capsys):
    ws = _novel(tmp_path, total=2, baseline=True)                           # 没有事实卡
    assert _run(ws, "review-checks") == 2
    err = json.loads(capsys.readouterr().err)
    assert err["ok"] is False and "事实卡" in err["error"]
    assert _run(ws, "review-export") == 2
    assert _run(ws, "outline-check") == 2                                   # 没有事实卡，确定性比对也拒绝


def test_report_export_contains_status_adjudication_and_history(tmp_path):
    ws = _ready(tmp_path)
    RK.run_checks(ws)
    RL.run_review(ws, None, ReviewStub(verdicts={"甲": "false_positive"}, whole_issue=False))
    pool = RK.load_findings(ws, "v001")
    dep = next(f for f in pool["findings"] if f["check"] == "departed_reappear")
    RK.set_status(ws, "v001", dep["id"], "rejected", "作者：这是回忆")
    res = RR.export(ws)
    text = res["text"]
    assert "# 全书评估报告（版本 v001）" in text
    assert "模型仲裁：误报" in text and "作者备注：作者：这是回忆" in text
    assert "处理状态：" in text and "已驳回" in text
    assert "原文已核实" in text or "原文未核实" in text or "第1章" in text
    assert (ws.root / "_revision" / "review" / "rev_v001" / "export.md").read_text(encoding="utf-8") == text


def test_report_export_requires_evaluation(tmp_path):
    ws = _novel(tmp_path, total=2, baseline=True)
    with pytest.raises(Exception, match="评估"):
        RR.export(ws)


def test_report_export_is_readonly_for_pool(tmp_path):
    ws = _ready(tmp_path)
    RK.run_checks(ws)
    before = (RK.review_dir(ws, "v001") / "findings.json").read_text(encoding="utf-8")
    RR.export(ws)
    assert (RK.review_dir(ws, "v001") / "findings.json").read_text(encoding="utf-8") == before
