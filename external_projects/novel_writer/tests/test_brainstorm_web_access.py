"""灵感构思「联网与执行权限」开关：默认关闭；开启后使用含 web_search / bash / skill 的 Agent，自动批准。"""

from pathlib import Path

import pytest

from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import studio_tools as ST
from novel_writer.brainstorm.workspace import BrainstormWorkspace


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    return w


def test_web_access_default_off(ws):
    assert CH.get_web_access(ws) is False


def test_web_access_toggle_persists(ws):
    CH.set_web_access(ws, True)
    assert CH.get_web_access(ws) is True
    CH.set_web_access(ws, False)
    assert CH.get_web_access(ws) is False


def test_changing_scope_does_not_clear_web_access(ws):
    CH.set_web_access(ws, True)
    CH.set_scope(ws, "read")
    assert CH.get_web_access(ws) is True          # set_scope 不能覆盖掉联网开关
    assert CH.get_scope(ws) == "read"


def test_setting_web_access_keeps_scope(ws):
    CH.set_scope(ws, "read")
    CH.set_web_access(ws, True)
    assert CH.get_scope(ws) == "read"


def test_open_registry_has_studio_tools_bash_and_web_search(ws):
    names = set(ST.open_registry(ws.root).names)
    assert {"studio_list", "studio_read", "studio_grep", "bash", "web_search"} <= names


def test_read_only_registry_is_unchanged(ws):
    # 默认的“读取文件”模式不能因为联网开关的实现而多出 bash / web_search
    names = set(ST.build_registry(ws.root).names)
    assert "bash" not in names and "web_search" not in names


def test_prompt_mentions_open_mode_only_when_enabled(ws):
    off = CH.build_prompt(ws, "想写一个港口故事", "自由")
    assert "联网与执行权限已开启" not in off
    CH.set_web_access(ws, True)
    on = CH.build_prompt(ws, "想写一个港口故事", "自由")
    assert "联网与执行权限已开启" in on
    assert "不可信数据" in on                      # 提示注入防护说明一并注入


def test_skill_dirs_point_at_main_project_skills():
    dirs = ST._open_skill_dirs(type("C", (), {"project_root": None, "skills_dir": None})())
    assert any(str(d).replace("\\", "/").endswith(".claude/skills") for d in dirs)


def test_open_agent_auto_approves_and_has_skill_loader(ws, monkeypatch):
    from novel_writer import config
    captured = {}

    class _Cfg:
        project_root = None
        skills_dir = None
        sandbox = True
        turn_judge = type("J", (), {"enabled": True})()

    monkeypatch.setattr(config, "load_llm_cfg", lambda: _Cfg())

    class _FakeAgent:
        def __init__(self, cfg, guard, registry, skill_loader, extra_tools_enabled):
            captured.update(guard=guard, skill_loader=skill_loader, extra=extra_tools_enabled,
                            names=set(registry.names))
    import mini_agent.agent as agent_mod
    monkeypatch.setattr(agent_mod, "Agent", _FakeAgent)

    ST.build_open_agent(ws.root)
    assert captured["guard"].auto_approve is True          # 不弹确认
    assert captured["guard"].sandbox is False              # 不开沙箱
    assert captured["skill_loader"] is not None            # 挂载框架 skill（含浏览器 skill）
    assert captured["extra"] is True
    assert {"bash", "web_search"} <= captured["names"]
