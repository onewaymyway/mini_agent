"""
tests/test_cron_slot_mode_badge.py — cron「是否受槽位管理」标记

覆盖：
  1. stock_watch/project.yaml 的 telegraph_scan 声明为 concurrency: unmanaged
     （读真实文件，防止配置被改回去）
  2. CronScheduler.slot_mode()：goal_channel / local_handler / unmanaged / managed，
     判定顺序与 _fire() 一致
  3. 看板展示：_cron_slot_mode 优先用 daemon 下发值，老 daemon 缺字段时按 job 字段推断；
     _cron_type_label 覆盖各类型
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
from mini_agent.external_projects.manifest import load_manifest
from mini_agent.storage.paths import AgentPaths


# ── 1. 真实 project.yaml ─────────────────────────────────────────────────────

def test_telegraph_scan_declared_unmanaged_in_real_project_yaml():
    m = load_manifest(PROJECT_ROOT / "external_projects" / "stock_watch" / "project.yaml")
    ep = m.entrypoints["telegraph_scan"]
    assert ep.concurrency == "unmanaged"
    # 只改了并发档位，调度本身不变
    assert ep.cron_expr == "0,15,30,45 * * * *"
    assert ep.timeout_sec == 300


# ── 2. CronScheduler.slot_mode ───────────────────────────────────────────────

class _Cfg:
    pass


@pytest.fixture
def cs(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    paths = AgentPaths(project_root=wd)
    runner = CronJobRunner(_Cfg(), paths, max_concurrent=1)
    s = CronScheduler(paths, job_runner=runner)
    s.load()
    return s


def _job(job_id="user:x", **kw) -> CronJob:
    base = dict(id=job_id, name="n", schedule="interval:60", task_template="")
    base.update(kw)
    return CronJob(**base)


def test_slot_mode_message_is_managed(cs):
    assert cs.slot_mode(_job()) == "managed"


def test_slot_mode_goal_cycle_is_goal_channel(cs):
    assert cs.slot_mode(_job(run_mode="goal_cycle", goal_id="g1")) == "goal_channel"


def test_slot_mode_local_handler(cs):
    cs._local_handlers["sys:foo"] = lambda job: True
    assert cs.slot_mode(_job("sys:foo")) == "local_handler"


@pytest.mark.parametrize("run_mode", ["external_entrypoint", "command"])
def test_slot_mode_unmanaged_only_when_declared(cs, run_mode):
    assert cs.slot_mode(_job(run_mode=run_mode, concurrency="unmanaged")) == "unmanaged"
    assert cs.slot_mode(_job(run_mode=run_mode, concurrency="managed")) == "managed"


def test_slot_mode_unmanaged_ignored_for_message_job(cs):
    # message 必然涉及 LLM，字段被误写也按 managed（与 _is_unmanaged 一致）
    assert cs.slot_mode(_job(run_mode="message", concurrency="unmanaged")) == "managed"


# ── 3. 看板展示 ──────────────────────────────────────────────────────────────

_APP_DIR = PROJECT_ROOT / "apps" / "mini_agent_kanban"


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("streamlit")
    sys.path.insert(0, str(_APP_DIR))
    spec = importlib.util.spec_from_file_location("kanban_app_slot_badge_test", _APP_DIR / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kanban_app_slot_badge_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_kanban_prefers_daemon_slot_mode(app):
    assert app._cron_slot_mode({"slot_mode": "local_handler", "run_mode": "message"}) == "local_handler"
    assert app._cron_slot_mode({"slot_mode": "bogus", "run_mode": "message"}) == "managed"


def test_kanban_fallback_infers_for_old_daemon(app):
    assert app._cron_slot_mode({"run_mode": "external_entrypoint", "concurrency": "unmanaged"}) == "unmanaged"
    assert app._cron_slot_mode({"run_mode": "command", "concurrency": "unmanaged"}) == "unmanaged"
    assert app._cron_slot_mode({"run_mode": "external_entrypoint", "concurrency": "managed"}) == "managed"
    assert app._cron_slot_mode({"run_mode": "goal_cycle"}) == "goal_channel"
    assert app._cron_slot_mode({"run_mode": "message", "concurrency": "unmanaged"}) == "managed"
    assert app._cron_slot_mode({}) == "managed"


def test_kanban_type_labels(app):
    assert "系统任务" in app._cron_type_label({"id": "sys:x", "is_system": True})
    assert "系统任务" in app._cron_type_label({"id": "sys:x"})
    assert "外部项目" in app._cron_type_label({"id": "ext:stock_watch:telegraph_scan", "run_mode": "external_entrypoint"})
    assert "执行命令" in app._cron_type_label({"id": "user:a", "run_mode": "command"})
    assert "Goal" in app._cron_type_label({"id": "goal:1", "run_mode": "goal_cycle"})
    assert "消息" in app._cron_type_label({"id": "user:b", "run_mode": "message"})


def test_every_slot_mode_has_badge_and_description(app):
    for mode in ("managed", "unmanaged", "goal_channel", "local_handler"):
        badge, desc = app._CRON_SLOT_MODE_LABEL[mode]
        assert badge and desc
    assert "不受槽位管理" in app._CRON_SLOT_MODE_LABEL["unmanaged"][0]
    assert "受槽位管理" in app._CRON_SLOT_MODE_LABEL["managed"][0]
