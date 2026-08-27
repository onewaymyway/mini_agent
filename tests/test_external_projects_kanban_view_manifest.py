"""
tests/test_external_projects_kanban_view_manifest.py — 阶段 A 验收测试

对应 `next_doc/external_projects_generic_kanban_view_refactor_plan.md`
阶段 A：`manifest.py` 里 `dashboard.kanban_view` schema 的解析与校验。
"""

from __future__ import annotations

import pytest

from mini_agent.external_projects.manifest import (
    ProjectManifestError,
    parse_manifest,
)

BASE_ENTRYPOINTS = """
entrypoints:
  scan:
    cmd: "python entrypoints/run_scan.py"
  change_pool_state:
    cmd: "python entrypoints/change_pool_state.py"
"""

FULL_KANBAN_VIEW = """
dashboard:
  kanban_view:
    data_file: "data/pool_tracking_latest.json"
    id_field: "code"
    title_field: "name"
    state_field: "state"
    states:
      - value: "watching"
        label: "👀 观察池"
      - value: "focused"
        label: "🔎 重点关注"
      - value: "dropped"
        label: "⚪ 已淘汰"
        collapsed: true
    metric_fields:
      - field: "current_price"
        label: "当前价"
        format: "number"
      - field: "score"
        label: "分数"
    detail_list_field: "reasons"
    change_state:
      entrypoint: "change_pool_state"
      id_param: "code"
      state_param: "state"
      note_param: "note"
"""


def _yaml(*, name: str = "stock_watch", extra: str = "") -> str:
    return f"name: {name}\n{BASE_ENTRYPOINTS}{extra}"


def test_kanban_view_absent_defaults_to_none():
    manifest = parse_manifest(_yaml())
    assert manifest.kanban_view is None


def test_kanban_view_full_declaration_parses():
    manifest = parse_manifest(_yaml(extra=FULL_KANBAN_VIEW))
    kv = manifest.kanban_view
    assert kv is not None
    assert kv.data_file == "data/pool_tracking_latest.json"
    assert kv.id_field == "code"
    assert kv.title_field == "name"
    assert kv.state_field == "state"
    assert [s.value for s in kv.states] == ["watching", "focused", "dropped"]
    assert kv.states[2].collapsed is True
    assert kv.states[0].collapsed is False
    assert [m.field for m in kv.metric_fields] == ["current_price", "score"]
    assert kv.metric_fields[0].format == "number"
    assert kv.metric_fields[1].format == "text"  # 默认值
    assert kv.detail_list_field == "reasons"
    assert kv.change_state is not None
    assert kv.change_state.entrypoint == "change_pool_state"
    assert kv.change_state.id_param == "code"
    assert kv.change_state.state_param == "state"
    assert kv.change_state.note_param == "note"


def test_kanban_view_minimal_declaration_no_change_state():
    minimal = """
dashboard:
  kanban_view:
    data_file: "data/x.json"
    id_field: "id"
    title_field: "name"
    state_field: "state"
    states:
      - value: "a"
        label: "A"
"""
    manifest = parse_manifest(_yaml(extra=minimal))
    kv = manifest.kanban_view
    assert kv is not None
    assert kv.metric_fields == []
    assert kv.detail_list_field is None
    assert kv.change_state is None


@pytest.mark.parametrize(
    "missing_field",
    ["data_file", "id_field", "title_field", "state_field"],
)
def test_kanban_view_missing_required_field_raises(missing_field):
    lines = {
        "data_file": 'data_file: "data/x.json"',
        "id_field": 'id_field: "id"',
        "title_field": 'title_field: "name"',
        "state_field": 'state_field: "state"',
    }
    kept = "\n".join(v for k, v in lines.items() if k != missing_field)
    extra = f"""
dashboard:
  kanban_view:
    {kept}
    states:
      - value: "a"
        label: "A"
"""
    with pytest.raises(ProjectManifestError):
        parse_manifest(_yaml(extra=extra))


def test_kanban_view_empty_states_raises():
    extra = """
dashboard:
  kanban_view:
    data_file: "data/x.json"
    id_field: "id"
    title_field: "name"
    state_field: "state"
    states: []
"""
    with pytest.raises(ProjectManifestError):
        parse_manifest(_yaml(extra=extra))


def test_kanban_view_duplicate_state_value_raises():
    extra = """
dashboard:
  kanban_view:
    data_file: "data/x.json"
    id_field: "id"
    title_field: "name"
    state_field: "state"
    states:
      - value: "a"
        label: "A"
      - value: "a"
        label: "A again"
"""
    with pytest.raises(ProjectManifestError, match="重复声明"):
        parse_manifest(_yaml(extra=extra))


def test_kanban_view_invalid_metric_format_raises():
    extra = """
dashboard:
  kanban_view:
    data_file: "data/x.json"
    id_field: "id"
    title_field: "name"
    state_field: "state"
    states:
      - value: "a"
        label: "A"
    metric_fields:
      - field: "price"
        label: "Price"
        format: "currency"
"""
    with pytest.raises(ProjectManifestError, match="format"):
        parse_manifest(_yaml(extra=extra))


def test_kanban_view_change_state_unknown_entrypoint_raises():
    extra = """
dashboard:
  kanban_view:
    data_file: "data/x.json"
    id_field: "id"
    title_field: "name"
    state_field: "state"
    states:
      - value: "a"
        label: "A"
    change_state:
      entrypoint: "does_not_exist"
      id_param: "id"
      state_param: "state"
"""
    with pytest.raises(ProjectManifestError, match="未在 project.yaml 的 entrypoints 中声明"):
        parse_manifest(_yaml(extra=extra))
