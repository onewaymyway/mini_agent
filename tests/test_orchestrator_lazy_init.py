"""orchestrator/__init__.py 惰性再导出（Phase 10 · orchestrator 拆分评估 Step 1）。

固定三件事：
1. 兼容：旧写法 ``from mini_agent.orchestrator import X`` 对 ``__all__`` 里全部名字仍可用；
2. 惰性：只 import LLM 层（只需要 ``concurrency``）时，不再连带加载 Agent/工具层；
3. 一致：``_LAZY_EXPORTS`` 与 ``__all__`` 不漂移，每个名字都指向真实存在的对象。

导入隔离类断言必须在**子进程**里做——当前 pytest 进程的 ``sys.modules`` 早已被其它测试污染。
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import mini_agent.orchestrator as orch

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run_isolated(code: str) -> dict:
    """在干净解释器里执行 code，code 需 print 一行 JSON。"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        env={"PYTHONPATH": _SRC, "PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# 1. 兼容性
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(orch.__all__))
def test_every_name_in_all_resolves(name):
    assert getattr(orch, name) is not None


def test_legacy_from_import_still_works():
    from mini_agent.orchestrator import (  # noqa: F401
        Task, TaskManager, SubAgent, TaskDashboard, StatusBar,
        CountingSemaphore, TaskStatus,
    )
    from mini_agent.orchestrator.task import Task as TaskDirect
    assert Task is TaskDirect  # 与子模块路径拿到的是同一个对象


def test_star_import_exports_all():
    ns: dict = {}
    exec("from mini_agent.orchestrator import *", ns)
    for name in orch.__all__:
        assert name in ns


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError):
        orch.this_name_does_not_exist  # noqa: B018
    assert not hasattr(orch, "this_name_does_not_exist")


def test_dir_lists_public_names():
    assert set(orch.__all__) <= set(dir(orch))


# --------------------------------------------------------------------------
# 2. 惰性（子进程隔离）
# --------------------------------------------------------------------------

def test_importing_llm_layer_no_longer_pulls_agent_and_tools():
    """回归点：LLM 层只为用 concurrency，不应间接拉起 Agent/工具层。"""
    out = _run_isolated(
        "import sys, json\n"
        "import mini_agent.llm.providers._base_mixin\n"
        "print(json.dumps({\n"
        "  'orch': sorted(m for m in sys.modules if m.startswith('mini_agent.orchestrator')),\n"
        "  'heavy': [m for m in ('mini_agent.agent', 'mini_agent.tools', 'mini_agent.skills') if m in sys.modules],\n"
        "}))\n"
    )
    assert out["heavy"] == [], f"LLM 层导入仍连带加载重包: {out['heavy']}"
    assert set(out["orch"]) == {"mini_agent.orchestrator", "mini_agent.orchestrator.concurrency"}


def test_import_package_alone_loads_no_submodule():
    out = _run_isolated(
        "import sys, json\n"
        "import mini_agent.orchestrator\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('mini_agent.orchestrator.'))))\n"
    )
    assert out == []


def test_access_loads_only_the_owning_submodule_then_caches():
    out = _run_isolated(
        "import sys, json\n"
        "import mini_agent.orchestrator as o\n"
        "o.CountingSemaphore\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('mini_agent.orchestrator.'))\n"
        "cached = 'CountingSemaphore' in vars(o)\n"
        "print(json.dumps({'loaded': loaded, 'cached': cached}))\n"
    )
    assert out["loaded"] == ["mini_agent.orchestrator.concurrency"]
    assert out["cached"] is True


# --------------------------------------------------------------------------
# 3. 一致性
# --------------------------------------------------------------------------

def test_lazy_exports_and_all_are_in_sync():
    assert set(orch._LAZY_EXPORTS) == set(orch.__all__)
    assert len(orch.__all__) == len(set(orch.__all__))  # 无重复


def test_lazy_export_targets_really_define_the_name():
    for name, sub in orch._LAZY_EXPORTS.items():
        mod = importlib.import_module(sub, "mini_agent.orchestrator")
        assert hasattr(mod, name), f"{sub} 里没有 {name}"


def test_single_state_after_lazy_access():
    """惰性访问不能造成第二份 concurrency 全局状态（Step 2 若做 shim 需保持此性质）。"""
    from mini_agent.orchestrator import concurrency as c1
    import mini_agent.orchestrator.concurrency as c2
    assert c1 is c2
    assert orch.get_task_sem is c2.get_task_sem
