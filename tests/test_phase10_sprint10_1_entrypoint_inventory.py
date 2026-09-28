"""tests/test_phase10_sprint10_1_entrypoint_inventory.py

`scripts/entrypoint_inventory.py`（Phase 10 Sprint 10-1）的测试。

前半部分用最小的假项目树验证抽取/分类逻辑（含首次在真实仓库上运行时暴露的
两个缺陷：包再导出未跟随、`_COMMANDS` 带类型标注赋值未识别）；后半部分在
真实仓库上做两条稳定事实的冒烟检查。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "entrypoint_inventory.py"
_spec = importlib.util.spec_from_file_location("entrypoint_inventory", _SCRIPT)
inv = importlib.util.module_from_spec(_spec)
sys.modules["entrypoint_inventory"] = inv
_spec.loader.exec_module(inv)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path
    pkg = "src/mini_agent"
    for d in ("", "cli", "cli/commands", "api", "ui", "runtime"):
        _write(root, f"{pkg}/{d}/__init__.py".replace("//", "/"), "")
    _write(root, f"{pkg}/runtime/__init__.py", "class AgentRuntime: ...\n")

    # 命令实现在子模块，包 __init__ 只做再导出（真实仓库 cli/commands 的形状）
    _write(root, f"{pkg}/cli/commands/goal_cmd.py", '''
def handle_goal_cmd(args, agent):
    from mini_agent.runtime import AgentRuntime
    from mini_agent.goal_mode.runner import GoalRunner
    rt = AgentRuntime(agent=agent)
    return GoalRunner, rt
''')
    _write(root, f"{pkg}/cli/commands/wf_cmd.py", '''
def handle_wf_cmd(args, agent):
    return WorkflowRunner(args)
''')
    _write(root, f"{pkg}/cli/commands/__init__.py",
           "from mini_agent.cli.commands.goal_cmd import handle_goal_cmd\n"
           "from mini_agent.cli.commands.wf_cmd import handle_wf_cmd\n")

    _write(root, f"{pkg}/cli/repl.py", '''
from mini_agent.cli.commands import handle_goal_cmd, handle_wf_cmd

def _helper_chain_1(): return _helper_chain_2()
def _helper_chain_2(): return _helper_chain_3()
def _helper_chain_3(): return _helper_chain_4()
def _helper_chain_4(): return _helper_chain_5()
def _helper_chain_5(): return ObjectiveExecutor()

def _shallow(): return CronScheduler()

def handle_slash(text, agent):
    parts = text.split()
    name = parts[0]
    if name == "goal":
        handle_goal_cmd(parts[1:], agent)
    elif name in ("wf", "workflow"):
        handle_wf_cmd(parts[1:], agent)
    elif name == "sched" and len(parts) >= 1:
        _shallow()
    elif name == "deep":
        _helper_chain_1()
    elif name == "plain":
        print("hi")
''')

    _write(root, f"{pkg}/api/routes.py", '''
from fastapi import APIRouter
router = APIRouter(prefix="/v1")

@router.get("/goals")
async def list_goals():
    """列出 Goal。底层是 GoalBacklog。"""
    return GoalBacklog()

@router.post("/echo", summary="Echo")
def echo():
    return 1

def create_app(app):
    @app.get("/health")
    def health():
        return "ok"
''')

    _write(root, f"{pkg}/ui/terminal.py", '''
_COMMANDS: list[tuple[str, str, list]] = [
    ("/goal", "Set a goal", []),
    ("/x", "Uses ObjectiveExecutor internally", []),
    ("/wf", "Run a Workflow", []),
]
''')
    _write(root, f"{pkg}/cli/parser.py", '''
import argparse
def build_parser():
    p = argparse.ArgumentParser(description="demo")
    p.add_argument("--a", help="normal help")
    p.add_argument("--b", help="drives GoalRunner directly")
    return p
''')
    return root


# ── 抽取与分类 ────────────────────────────────────────────────────────

def _cli(rep):  # name -> entry
    return {e["name"]: e for e in rep["cli"]}


def test_cli_extracts_eq_in_and_compound_conditions(tree):
    cli = _cli(inv.build_report(tree))
    assert {"/goal", "/wf", "/workflow", "/sched", "/deep", "/plain"} <= set(cli)


def test_reexport_through_package_init_is_followed(tree):
    """回归：首次真实运行时，`/goal` 因未跟随 `cli/commands/__init__.py` 的再导出
    而被误判为“未走 AgentRuntime”。"""
    goal = _cli(inv.build_report(tree))["/goal"]
    assert goal["kind"] == "mixed"
    assert goal["legacy"] == ["GoalRunner"]


def test_legacy_only_and_none_and_alias_share_result(tree):
    cli = _cli(inv.build_report(tree))
    assert cli["/wf"]["kind"] == "legacy" and cli["/wf"]["legacy"] == ["WorkflowRunner"]
    assert cli["/workflow"]["legacy"] == cli["/wf"]["legacy"]
    assert cli["/plain"]["kind"] == "none"
    assert cli["/sched"]["legacy"] == ["CronScheduler"]


def test_depth_limit_is_a_lower_bound_not_a_guarantee(tree):
    """链路超过 MAX_DEPTH 跳就看不到——这正是报告里说“下限估计”的原因。
    该测试固化这一已知局限，防止有人误把 kind=none 当成“已收敛”的证明。"""
    assert inv.MAX_DEPTH == 3
    assert _cli(inv.build_report(tree))["/deep"]["kind"] == "none"


def test_http_prefix_methods_and_nested_routes(tree):
    http = {e["name"]: e for e in inv.build_report(tree)["http"]}
    assert set(http) == {"GET /v1/goals", "POST /v1/echo", "GET /health"}
    assert http["GET /v1/goals"]["kind"] == "legacy"
    assert http["GET /v1/goals"]["legacy"] == ["GoalBacklog"]
    assert http["GET /health"]["kind"] == "none"


def test_summary_counts_add_up(tree):
    s = inv.build_report(tree)["summary"]
    for surface in ("cli", "http"):
        x = s[surface]
        assert x["total"] == x["runtime"] + x["mixed"] + x["legacy"] + x["none"]


# ── 术语扫描 ──────────────────────────────────────────────────────────

def test_terminology_scan_finds_class_names_but_not_plain_concepts(tree):
    """回归：`_COMMANDS` 带类型标注（AnnAssign），首次运行时字符串数为 0。"""
    term = inv.build_report(tree)["terminology"]
    assert term["cli_menu"]["strings"] == 3
    hits = {(h["term"]) for h in term["cli_menu"]["class_hits"]}
    assert hits == {"ObjectiveExecutor"}, "只有真正的类名才计入验收判定项"
    # “Workflow” 只是产品概念词：单独计数，不算类名命中
    assert term["cli_menu"]["concept_word_counts"].get("Workflow") == 1
    assert [h["term"] for h in term["cli_args"]["class_hits"]] == ["GoalRunner"]
    assert [h["term"] for h in term["http_docs"]["class_hits"]] == ["GoalBacklog"]


# ── 其它入口 ──────────────────────────────────────────────────────────

def test_other_entrypoints_distinguish_inprocess_from_http(tree):
    _write(tree, "weixin_bot.py", "from mini_agent.agent import Agent\nimport os\n")
    _write(tree, "apps/kanban/app.js", "fetch(`${base}/v1/turns`)\n")
    _write(tree, "apps/mini_agent_kanban/x.py", "from mini_agent.config import load_config\n")
    others = {o["entry"]: o for o in inv.build_report(tree)["other_entrypoints"]}
    assert "进程内" in others["weixin_bot.py"]["how"]
    assert "mini_agent.agent" in others["weixin_bot.py"]["evidence"]
    assert "进程内直接导入" in others["apps/mini_agent_kanban"]["how"]


def test_render_markdown_and_cli_main(tree, tmp_path, capsys):
    md = inv.render_markdown(inv.build_report(tree))
    assert "### 汇总" in md and "附录 A" in md and "`/goal`" in md and "`GET /v1/goals`" in md
    out = tmp_path / "o.json"
    assert inv.main(["--root", str(tree), "--format", "json", "--out", str(out)]) == 0
    assert '"summary"' in out.read_text(encoding="utf-8")


# ── 真实仓库冒烟：两条稳定事实 ─────────────────────────────────────────

def test_real_repo_goal_command_is_mixed_runtime_plus_legacy():
    """Phase 8 Sprint 8-1 的既定事实：`/goal` 用 AgentRuntime 包着旧 GoalRunner。"""
    rep = inv.build_report(REPO_ROOT)
    goal = _cli(rep)["/goal"]
    assert goal["kind"] == "mixed"
    assert "GoalRunner" in goal["legacy"]


def test_real_repo_route_count_matches_independent_regex():
    """用与脚本完全不同的手段（逐行正则）数一遍装饰器，防止 AST 抽取悄悄漏路由。"""
    api = REPO_ROOT / "src/mini_agent/api"
    # 路径允许为空串：`@persona_candidate_router.get("")` 是挂在路由前缀上的合法路由
    # （首版正则要求以 "/" 开头，因此比 AST 少数了这一条）。
    pat = re.compile(r"^\s*@[A-Za-z_]+\.(get|post|put|delete|patch|websocket|head|options)\(\s*[\"']", re.M)
    expected = sum(
        len(pat.findall((api / f).read_text(encoding="utf-8", errors="replace")))
        for f in ("routes.py", "capability_routes.py", "persona_candidate_routes.py", "server.py")
    )
    assert expected > 100
    assert len(inv.build_report(REPO_ROOT)["http"]) == expected


def test_script_has_no_backslash_inside_fstring_expressions():
    """项目 requires-python >= 3.10。f-string 表达式里含反斜杠在 3.10/3.11 是
    SyntaxError（3.12 才放开）——本机 3.12 跑不出这个问题，所以用 AST 静态守住。"""
    import ast
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    src = _SCRIPT.read_text(encoding="utf-8")
    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue):
            segment = ast.get_source_segment(src, node.value) or ""
            assert "\\" not in segment, f"f-string 表达式含反斜杠（行 {node.lineno}）：{segment}"


def test_markdown_table_cell_escapes_pipe_and_newline(tree):
    """含 `|` 与换行的文档字符串节选不能破坏 Markdown 表格。"""
    _write(tree, "src/mini_agent/api/routes.py", '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/x")
def x():
    """first line | with pipe
    second line mentions GoalBacklog"""
''')
    md = inv.render_markdown(inv.build_report(tree))
    row = next(l for l in md.splitlines() if "`GoalBacklog`" in l and l.startswith("| http_docs"))
    assert row.count("|") - row.count("\\|") == 5, row
    assert "\n" not in row
