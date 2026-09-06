"""tests/test_kanban_client_goal_tree_extras.py —
[goal_tree_kanban_integration_plan.md Stage 6 §5] 覆盖 `client.py`
新增/修改的四个方法：`goal_tree_report()` / `goal_node_page()` /
`build_goal_wiki()` / `add_goal_feedback()`（补 `about` 可选参数）。

只 mock `client._HTTP`（`_get`/`_post` 实际调用的共享 Session），校验
请求方法/路径/参数是否符合前置文档约定——尤其是「省略可选参数时不应该
在请求里出现该 key/参数」这条向后兼容要求。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import client as client_module  # noqa: E402
from client import AgentClient  # noqa: E402


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    return resp


def test_goal_tree_report_without_root_id_sends_no_params(monkeypatch):
    mock_get = MagicMock(return_value=_mock_response({"tree_report": {}}))
    monkeypatch.setattr(client_module._HTTP, "get", mock_get)

    c = AgentClient("http://localhost:8000")
    result = c.goal_tree_report()

    assert result == {"tree_report": {}}
    _, kwargs = mock_get.call_args
    assert kwargs["params"] is None
    # 树级汇总遍历整棵子树，默认 6s 超时在节点数较多时容易 ReadTimeout，
    # 用更宽松的超时（见 client.py 里 goal_tree_report 的说明）。
    assert kwargs["timeout"] == 20
    assert mock_get.call_args[0][0].endswith("/goals/tree_report")


def test_goal_tree_report_with_root_id_passes_it_as_query_param(monkeypatch):
    mock_get = MagicMock(return_value=_mock_response({"tree_report": {"root_id": "g1"}}))
    monkeypatch.setattr(client_module._HTTP, "get", mock_get)

    c = AgentClient("http://localhost:8000")
    c.goal_tree_report(root_id="g1")

    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"root_id": "g1"}


def test_goal_node_page_hits_expected_path(monkeypatch):
    mock_get = MagicMock(return_value=_mock_response({"page": {"goal_id": "g1"}}))
    monkeypatch.setattr(client_module._HTTP, "get", mock_get)

    c = AgentClient("http://localhost:8000")
    result = c.goal_node_page("g1")

    assert result == {"page": {"goal_id": "g1"}}
    assert mock_get.call_args[0][0].endswith("/goals/g1/page")
    assert mock_get.call_args[1]["timeout"] == 15


def test_build_goal_wiki_uses_post_and_query_param_not_body(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"rendered_count": 3}))
    monkeypatch.setattr(client_module._HTTP, "post", mock_post)

    c = AgentClient("http://localhost:8000")
    result = c.build_goal_wiki(root_id="g1")

    assert result == {"rendered_count": 3}
    _, kwargs = mock_post.call_args
    assert kwargs["params"] == {"root_id": "g1"}
    # root_id 走 query 参数，不应该被塞进 JSON body。
    assert kwargs["json"] is None
    assert kwargs["timeout"] == 30


def test_build_goal_wiki_without_root_id_sends_no_params(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"rendered_count": 0}))
    monkeypatch.setattr(client_module._HTTP, "post", mock_post)

    c = AgentClient("http://localhost:8000")
    c.build_goal_wiki()

    _, kwargs = mock_post.call_args
    assert kwargs["params"] is None


def test_add_goal_feedback_without_about_omits_key_from_body(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"goal": {"id": "g1"}}))
    monkeypatch.setattr(client_module._HTTP, "post", mock_post)

    c = AgentClient("http://localhost:8000")
    c.add_goal_feedback("g1", "这是一条反馈")

    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"text": "这是一条反馈"}
    assert "about" not in kwargs["json"]


def test_add_goal_feedback_with_about_includes_it_in_body(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"goal": {"id": "g1"}}))
    monkeypatch.setattr(client_module._HTTP, "post", mock_post)

    c = AgentClient("http://localhost:8000")
    c.add_goal_feedback("g1", "这是一条反馈", about="candidate:cid-1")

    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"text": "这是一条反馈", "about": "candidate:cid-1"}
