"""受限修复引擎：不挂 skill、只开白名单内置工具、到轮次上限即停止；可通过 settings.repair_engine 切回旧方式。"""

import pytest

from novel_writer import config, repair as RP, runner as R, settings as SET, state as st, steps as S, tool_api
from novel_writer.workspace import Workspace


class FakeResult:
    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.timed_out = ok, error, False


def _step(step_id="t.step"):
    return S.StepDef(id=step_id, title="测试步骤", stage="p0", category=S.CAT_CHAPTER_PREP,
                     executor=S.EXECUTOR_PYTHON, handler="read_chars")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("受限修复", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


@pytest.fixture
def toy(ws):
    base = ws.root / "数据"
    base.mkdir(parents=True, exist_ok=True)
    (base / "a.md").write_text("ok\nBAD one\n", encoding="utf-8")

    def validate(_ws):
        text = (base / "a.md").read_text(encoding="utf-8")
        return [f"a.md 第 {i} 行是 BAD" for i, l in enumerate(text.splitlines(), 1) if l.startswith("BAD")]

    RP.VALIDATORS["toy"] = validate
    yield base
    RP.VALIDATORS.pop("toy", None)


REQ = {"validator": "toy", "files": ["数据/a.md"], "problems": [], "scope": "数据"}


def test_engine_default_and_switch():
    assert RP.engine({}) == "restricted"
    assert RP.engine({"repair_engine": "agent"}) == "agent"
    assert RP.engine({"repair_engine": "乱写"}) == "restricted"
    assert SET.validate_settings({})["repair_engine"] == "restricted"
    assert SET.validate_settings({"repair_engine": "agent"})["repair_engine"] == "agent"
    with pytest.raises(SET.SettingsError):
        SET.validate_settings({"repair_engine": "x"})


def test_allowed_tools_cover_file_and_dir_but_not_dangerous():
    t = set(RP.REPAIR_ALLOWED_TOOLS)
    assert {"read_file", "write_file", "create_file", "patch_file", "list_dir", "tree_summary", "glob", "grep"} <= t
    assert not t & {"bash", "delete_file", "web_search", "skill_activate", "skill_list"}


def test_restricted_call_has_no_skill_and_absolute_paths(ws, toy):
    seen = {}

    def invoker(**kw):
        seen.update(kw)
        (toy / "a.md").write_text("ok\nfixed\n", encoding="utf-8")
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=REQ)
    assert out["fixed"]
    assert seen["skill"] is None
    assert seen["allowed_tools"] == list(RP.REPAIR_ALLOWED_TOOLS)
    assert str(ws.root / "数据" / "a.md") in seen["prompt"]          # 绝对路径
    assert "没有其他能力" in seen["prompt"] and "不要继续做任何其他工作" in seen["prompt"]


def test_agent_engine_keeps_old_call_shape(ws, toy):
    seen = {}

    def invoker(**kw):
        seen.update(kw)
        (toy / "a.md").write_text("ok\nfixed\n", encoding="utf-8")
        return FakeResult()

    RP.repair_attempt(ws, _step(), invoker=invoker, settings={"repair_engine": "agent"}, request=REQ)
    assert seen["skill"] == RP.REPAIR_SKILL and "allowed_tools" not in seen


def test_rollback_deletes_newly_created_files(ws, toy):
    stray = ws.root / "正文" / "第13章 越界.md"
    before = (toy / "a.md").read_bytes()

    def invoker(**kw):
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("越界写的正文", encoding="utf-8")
        (toy / "a.md").write_text("ok\nfixed\n", encoding="utf-8")
        return FakeResult()

    out = RP.repair_attempt(ws, _step(), invoker=invoker, settings={}, request=REQ)
    assert out["rolled_back"] and not stray.exists()                  # 越界的新文件被删除
    assert (toy / "a.md").read_bytes() == b"ok\nfixed\n"              # 范围内的修复保留
    assert before != (toy / "a.md").read_bytes()


def test_restricted_agent_really_has_only_whitelisted_tools_and_no_skills(tmp_path):
    """用真实框架构造受限 Agent：工具集恰好等于白名单、没有 skill 相关工具、策略为 stop。"""
    pytest.importorskip("mini_agent", reason="需要 mini_agent 框架")
    R._ensure_framework_on_path()
    try:
        from mini_agent.config import load_config
        cfg = load_config(project_root=config.PROJECT_ROOT)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"框架配置不可加载：{exc}")
    cfg.api_key = "test-key"                          # 只构造不调用，占位即可
    cfg.max_turns_on_limit = "compact_continue"      # 模拟全局配置
    cfg.max_turns_hard_limit = 250
    agent = R._build_restricted_agent(cfg, allowed_tools=list(RP.REPAIR_ALLOWED_TOOLS), max_turns=20, timeout=60)
    names = set(agent.registry._tools)
    assert names == set(RP.REPAIR_ALLOWED_TOOLS)
    assert not any("skill" in n for n in names)
    assert agent.skill_loader is None
    assert agent.cfg.max_turns_on_limit == "stop" and agent.cfg.max_turns_hard_limit == 20
