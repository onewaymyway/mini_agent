"""tests/test_dep_graph.py

Sprint 0 依赖关系扫描脚本（`scripts/dep_graph.py`）的测试。分两部分：
  1. 用一个手工构造的小型假仓库，验证相对 import 解析（`__init__.py`
     与普通模块的 `__package__` 基准不同，是最容易写错的地方）与
     inbound/outbound 分类逻辑本身是对的。
  2. 用当前真实仓库跑一遍 goal_mode，做一次不依赖具体数字的冒烟检查
     （只要求脚本不报错、且已知会 import goal_mode 的文件确实出现在
     inbound 里），避免仓库演进后这个数字类断言变得脆弱。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "dep_graph.py"


def _load_dep_graph_module():
    import sys

    spec = importlib.util.spec_from_file_location("dep_graph", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclasses 在解析字符串类型注解时会通过 sys.modules[cls.__module__]
    # 查找命名空间，模块必须先注册到 sys.modules 才能正常 exec（否则
    # `@dataclass class ImportEdge` 里的注解解析会报 AttributeError）。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def dg(request):
    return _load_dep_graph_module()


@pytest.fixture
def fake_src(tmp_path):
    """构造一个最小假包：
        mini_agent/
          __init__.py                 (from .goal_mode import GoalRunner)
          caller.py                   (from mini_agent.goal_mode.runner import GoalRunner)
          goal_mode/
            __init__.py               (from .runner import GoalRunner)
            runner.py                 (from .executor import CoarseStepExecutor;
                                        from mini_agent.ui import renderer)
            executor.py               (无 mini_agent 内部依赖)
          ui/
            __init__.py
            renderer.py
    """
    root = tmp_path / "mini_agent"
    (root).mkdir()
    (root / "__init__.py").write_text("from .goal_mode import GoalRunner\n")
    (root / "caller.py").write_text("from mini_agent.goal_mode.runner import GoalRunner\n")

    gm = root / "goal_mode"
    gm.mkdir()
    (gm / "__init__.py").write_text("from .runner import GoalRunner\n")
    (gm / "runner.py").write_text(
        "from .executor import CoarseStepExecutor\n"
        "from mini_agent.ui import renderer\n"
    )
    (gm / "executor.py").write_text("X = 1\n")

    ui = root / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("")
    (ui / "renderer.py").write_text("Y = 1\n")

    return root


def test_relative_import_resolves_correctly_for_init_and_module(dg, fake_src):
    edges = dg.scan_repo(fake_src)
    imported = {(e.from_file, e.imported) for e in edges}

    # __init__.py 的 `from .goal_mode import GoalRunner` 应解析为
    # mini_agent.goal_mode（以自身包 mini_agent 为基准，不多退一级）。
    assert ("mini_agent/__init__.py", "mini_agent.goal_mode") in imported
    # goal_mode/__init__.py 的 `from .runner import ...` 应解析为
    # mini_agent.goal_mode.runner（以 goal_mode 包自身为基准）。
    assert ("mini_agent/goal_mode/__init__.py", "mini_agent.goal_mode.runner") in imported
    # goal_mode/runner.py（非 __init__）的 `from .executor import ...`
    # 应解析为 mini_agent.goal_mode.executor（以 goal_mode 包为基准，
    # 即退一级去掉 "runner" 这个模块名本身）。
    assert ("mini_agent/goal_mode/runner.py", "mini_agent.goal_mode.executor") in imported


def test_inbound_and_outbound_classification(dg, fake_src):
    edges = dg.scan_repo(fake_src)
    result = dg.filter_for_module(edges, "goal_mode")

    inbound_files = {e.from_file for e in result["inbound"]}
    outbound_external_targets = {e.imported for e in result["outbound_external"]}

    # inbound：mini_agent/__init__.py 与 mini_agent/caller.py 都 import 了
    # goal_mode，且它们本身不属于 goal_mode 内部文件。
    assert inbound_files == {"mini_agent/__init__.py", "mini_agent/caller.py"}

    # outbound_external：goal_mode/runner.py import 了包外的 mini_agent.ui
    # （`from mini_agent.ui import renderer`——脚本按 import 语句本身的
    # module 路径记录，不进一步区分"renderer 是子模块还是包属性"，这是
    # 静态 AST 分析的已知局限，见脚本文件头说明）；
    # 它对 .executor 的 import 属于 goal_mode 内部，不应出现在 outbound_external 里。
    assert outbound_external_targets == {"mini_agent.ui"}


def test_smoke_on_real_repo_goal_mode(dg):
    """对真实仓库跑一次，只做存在性检查，不锁死具体数字（避免脆弱）。"""
    real_root = REPO_ROOT / "src" / "mini_agent"
    edges = dg.scan_repo(real_root)
    result = dg.filter_for_module(edges, "goal_mode")

    inbound_files = {e.from_file for e in result["inbound"]}
    # cli/commands/goal_mode_cmd.py 是已知会深度 import goal_mode 具体符号的调用方，
    # 只要它还存在于仓库里，就应该出现在 inbound 里。
    assert "mini_agent/cli/commands/goal_mode_cmd.py" in inbound_files
    # goal_mode 自己的文件不应该出现在自己的 inbound 里。
    assert not any(f.startswith("mini_agent/goal_mode/") for f in inbound_files)
