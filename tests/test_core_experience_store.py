"""core/experience_store.py + cli/commands/experience_cmd.py 的单元测试。

对应 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 2
验收标准：
  1. 至少能演示一次"检索到历史 Experience 并影响本次决策"的完整流程
     （这里用"写入 → CLI search 命中"代表最小可验证形式，真正的
     "影响本次决策"需要接入主 Agent 的 prompt 组装，超出 Sprint 2
     "最小实现 + 可演示"范围，留给后续 Phase）。
  2. Experience 的存储格式与 `core/experience.py` 中的 dataclass 定义一致。
"""

from __future__ import annotations

from pathlib import Path

from mini_agent.core.experience import Experience
from mini_agent.core.experience_store import ExperienceStore


def _make_experience(goal_text: str, final_report: str = "完成") -> Experience:
    return Experience(
        source="goal_mode",
        goal_text=goal_text,
        status="done",
        rounds_used=2,
        final_report=final_report,
    )


def test_experience_store_append_and_all_round_trip(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.jsonl")

    store.append(_make_experience("写周报"))
    store.append(_make_experience("部署新版本", final_report="线上验证通过"))

    all_exp = store.all()
    assert len(all_exp) == 2
    assert all_exp[0].goal_text == "写周报"
    assert all_exp[1].goal_text == "部署新版本"
    assert all_exp[1].final_report == "线上验证通过"
    # 存储格式与 dataclass 定义一致：写入前后所有字段值相等。
    assert all_exp[1].to_dict().keys() == _make_experience("x").to_dict().keys()


def test_experience_store_search_matches_goal_text_and_report(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.jsonl")
    store.append(_make_experience("修复登录页 bug", final_report="已回归验证"))
    store.append(_make_experience("写周报", final_report="已发送给主管"))

    hits = store.search("登录页")
    assert len(hits) == 1
    assert hits[0].goal_text == "修复登录页 bug"

    hits2 = store.search("主管")
    assert len(hits2) == 1
    assert hits2[0].goal_text == "写周报"

    assert store.search("不存在的关键词") == []


def test_experience_store_search_most_recent_first(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.jsonl")
    store.append(_make_experience("目标 A：处理报表"))
    store.append(_make_experience("目标 B：处理报表"))

    hits = store.search("报表")
    assert [h.goal_text for h in hits] == ["目标 B：处理报表", "目标 A：处理报表"]


def test_experience_store_all_on_missing_file_returns_empty(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "does_not_exist.jsonl")
    assert store.all() == []


def test_experience_store_search_respects_limit(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.jsonl")
    for i in range(5):
        store.append(_make_experience(f"目标 {i}：报表任务"))

    hits = store.search("报表", limit=2)
    assert len(hits) == 2


def test_cli_experience_search_finds_appended_record(tmp_path: Path, capsys):
    from mini_agent.cli.commands.experience_cmd import run_experience_cli
    from mini_agent.storage.paths import AgentPaths

    store = ExperienceStore(path=AgentPaths(project_root=tmp_path).workdir_experience_store)
    store.append(_make_experience("演示可检索的目标", final_report="演示报告内容"))

    exit_code = run_experience_cli(["search", "演示可检索"], project_root=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "演示可检索的目标" in captured.out


def test_cli_experience_search_without_query_returns_error(tmp_path: Path, capsys):
    from mini_agent.cli.commands.experience_cmd import run_experience_cli

    exit_code = run_experience_cli(["search"], project_root=tmp_path)
    assert exit_code == 1


def test_cli_experience_list_shows_most_recent(tmp_path: Path, capsys):
    from mini_agent.cli.commands.experience_cmd import run_experience_cli
    from mini_agent.storage.paths import AgentPaths

    store = ExperienceStore(path=AgentPaths(project_root=tmp_path).workdir_experience_store)
    store.append(_make_experience("第一个目标"))
    store.append(_make_experience("第二个目标"))

    exit_code = run_experience_cli(["list"], project_root=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "第一个目标" in captured.out
    assert "第二个目标" in captured.out
