"""
tests/test_protected_paths.py — Stage 0.1 验证

验证 scripts/protected_paths.py：
  - 清单非空
  - 覆盖文档要求的关键文件/目录：agent.py、permissions.py、hooks/、清单自身
  - is_protected_path() 对精确文件、目录前缀、非受保护路径的判定均正确
  - 该模块不 import mini_agent 包内任何东西（保持自包含，独立于被治理对象）
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ 不是 mini_agent 包的一部分，需要单独把仓库根目录加入 sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.protected_paths import (  # noqa: E402
    PROTECTED_PATHS,
    is_protected_path,
    list_protected_paths,
)


def test_list_non_empty():
    assert len(PROTECTED_PATHS) > 0
    assert len(list_protected_paths()) > 0


def test_covers_agent_py():
    assert is_protected_path("src/mini_agent/agent.py")


def test_covers_permissions_py():
    assert is_protected_path("src/mini_agent/permissions.py")


def test_covers_hooks_dir():
    # 目录本身
    assert is_protected_path("src/mini_agent/hooks/")
    # 目录下的具体文件也应受保护
    assert is_protected_path("src/mini_agent/hooks/loader.py")
    assert is_protected_path("src/mini_agent/hooks/runner.py")


def test_covers_self():
    assert is_protected_path("scripts/protected_paths.py")


def test_covers_evolution_package_pattern():
    # Stage 2 将新建 evolution/state_repo.py，提前画好红线
    assert is_protected_path("src/mini_agent/evolution/state_repo.py")
    assert is_protected_path("src/mini_agent/evolution/__init__.py")


def test_does_not_flag_unrelated_files():
    assert not is_protected_path("src/mini_agent/tools/builtin.py")
    assert not is_protected_path("README.md")
    assert not is_protected_path("tests/test_protected_paths.py")


def test_accepts_path_objects():
    assert is_protected_path(Path("src/mini_agent/agent.py"))


def test_normalizes_dot_slash_prefix():
    assert is_protected_path("./src/mini_agent/agent.py")


def test_module_is_self_contained():
    """
    清单文件不应 import mini_agent 包，否则一旦 mini_agent 包本身在演化过程
    中被改坏（语法错误、循环 import 等），这份"安全红线"也会跟着失效。

    只检查真实的 import 语句（代码行），不检查文档字符串里描述性提及的文字。
    """
    lines = (REPO_ROOT / "scripts" / "protected_paths.py").read_text(
        encoding="utf-8"
    ).splitlines()
    import_lines = [
        ln.strip() for ln in lines
        if ln.strip().startswith("import ") or ln.strip().startswith("from ")
    ]
    assert not any("mini_agent" in ln for ln in import_lines), import_lines
