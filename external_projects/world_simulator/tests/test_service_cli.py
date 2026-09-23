"""tests/test_service_cli.py — `world_simulator/service_cli.py` 单测。

只测这一层自己的职责（参数解析、JSON 序列化、退出码），业务逻辑本身
已经在 `test_tool_api.py` 里覆盖，这里对 `tool_api.py` 的函数用
monkeypatch 换成固定返回值，保持测试聚焦。
"""

from __future__ import annotations

import json

import pytest

from world_simulator import service_cli, tool_api


def _run(monkeypatch, argv, patched_data=None):
    if patched_data is not None:
        # 按子命令名找到对应的 tool_api 函数名做个粗略映射，测试里
        # 直接 monkeypatch 具体函数即可，这里不做通用映射。
        pass
    return service_cli.main(argv)


def test_list_prints_ok_json_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(tool_api, "list_simulations", lambda: {"ok": True, "data": {"simulations": []}})
    code = service_cli.main(["list"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "data": {"simulations": []}}


def test_list_pretty_flag_indents_output(monkeypatch, capsys):
    monkeypatch.setattr(tool_api, "list_simulations", lambda: {"ok": True, "data": {"simulations": []}})
    code = service_cli.main(["--pretty", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\n" in out  # 有缩进就一定有换行


def test_error_result_exits_one(monkeypatch, capsys):
    monkeypatch.setattr(
        tool_api, "get_simulation",
        lambda sim_id, **kw: {"ok": False, "error": {"type": "not_found", "message": "没有这个实例"}},
    )
    code = service_cli.main(["get", "--sim-id", "nope"])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["type"] == "not_found"


def test_update_settings_invalid_json_arg_fails_before_calling_tool_api(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(tool_api, "update_settings", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(SystemExit) as exc_info:
        service_cli.main(["update-settings", "--sim-id", "x", "--updates", "not json"])
    assert exc_info.value.code == 1
    assert called["n"] == 0  # 校验失败不应该继续调用 tool_api
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["type"] == "validation_error"


def test_advance_builds_custom_option_dict_from_flags(monkeypatch, capsys):
    captured = {}

    def _fake_advance(sim_id, **kwargs):
        captured["sim_id"] = sim_id
        captured["kwargs"] = kwargs
        return {"ok": True, "data": {"step": 1}}

    monkeypatch.setattr(tool_api, "advance_simulation", _fake_advance)
    code = service_cli.main([
        "advance", "--sim-id", "abc",
        "--custom-option-label", "自定义选项",
        "--custom-option-description", "描述",
    ])
    assert code == 0
    assert captured["sim_id"] == "abc"
    assert captured["kwargs"]["custom_option"] == {"label": "自定义选项", "description": "描述"}


def test_delete_without_yes_refuses_before_calling_tool_api(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(tool_api, "delete_simulation", lambda sim_id: called.__setitem__("n", called["n"] + 1))
    code = service_cli.main(["delete", "--sim-id", "abc"])
    assert code == 1
    assert called["n"] == 0
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["type"] == "validation_error"


def test_delete_with_yes_calls_tool_api(monkeypatch, capsys):
    monkeypatch.setattr(tool_api, "delete_simulation", lambda sim_id: {"ok": True, "data": {"sim_id": sim_id, "deleted": True}})
    code = service_cli.main(["delete", "--sim-id", "abc", "--yes"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["deleted"] is True


def test_export_html_writes_file_and_omits_html_body_from_stdout(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        tool_api, "export_html",
        lambda sim_id, branch="main": {"ok": True, "data": {"sim_id": sim_id, "branch": branch, "html": "<html>hi</html>"}},
    )
    output_path = tmp_path / "out.html"
    code = service_cli.main(["export-html", "--sim-id", "abc", "--output", str(output_path)])
    assert code == 0
    assert output_path.read_text(encoding="utf-8") == "<html>hi</html>"
    out = json.loads(capsys.readouterr().out)
    assert "html" not in out["data"]
    assert out["data"]["output_file"] == str(output_path)
