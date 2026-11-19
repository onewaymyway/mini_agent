"""外部项目聚合状态：健康探测并行（看板「获取外部项目状态失败：读超时」的优化）。"""
from __future__ import annotations

import sys
import time

from mini_agent.external_projects import status as st
from mini_agent.external_projects.registry import ExternalProjectRegistry

YAML = """
name: {n}
entrypoints:
  work:
    cmd: "{py} -c \\"pass\\""
health_check:
  cmd: "{py} -c \\"import time; time.sleep(0.8)\\""
"""


def _reg(tmp_path, count):
    reg = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    for i in range(count):
        root = tmp_path / f"p{i}"
        root.mkdir()
        (root / "project.yaml").write_text(YAML.format(n=f"p{i}", py=sys.executable), encoding="utf-8")
        reg.register(f"p{i}", root)
    return reg


def test_probes_run_in_parallel_and_keep_order(tmp_path):
    st._clear_health_cache()
    reg = _reg(tmp_path, 4)
    t0 = time.monotonic()
    out = st.aggregate_status(reg)
    elapsed = time.monotonic() - t0
    assert [p["name"] for p in out] == ["p0", "p1", "p2", "p3"]
    assert all(p["health"] == "healthy" for p in out)
    assert elapsed < 4 * 0.8 * 0.75      # 串行至少 3.2s，并行应明显更短


def test_second_call_hits_cache(tmp_path):
    st._clear_health_cache()
    reg = _reg(tmp_path, 3)
    st.aggregate_status(reg)
    t0 = time.monotonic()
    st.aggregate_status(reg)
    assert time.monotonic() - t0 < 0.5


def test_use_cache_false_probes_once_per_project(tmp_path, monkeypatch):
    st._clear_health_cache()
    reg = _reg(tmp_path, 3)
    calls = []
    real = st.win_subprocess.run

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(st.win_subprocess, "run", counting)
    st.aggregate_status(reg, use_cache=False)
    assert len(calls) == 3                # 强制刷新：每个项目恰好探测一次，不重复


def test_kanban_client_status_timeout_is_30s_and_no_read_retry(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))
    import client as kc
    seen = {}

    def fake_get(self, path, params=None, timeout=6, *, retry=True):
        seen.update(path=path, timeout=timeout, retry=retry)
        return {"projects": []}

    monkeypatch.setattr(kc.AgentClient, "_get", fake_get)
    kc.AgentClient.__new__(kc.AgentClient).external_projects_status()
    assert seen == {"path": "/self/external_projects", "timeout": 30, "retry": False}
