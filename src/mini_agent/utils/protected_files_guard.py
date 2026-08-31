"""
utils/protected_files_guard.py — 受保护文件清单判定的统一封装（阶段 2）

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 四、
"三层防护机制" 第 2 层：内部清理函数的代码级 guard。

**为什么需要这一层薄封装**：`scripts/protected_files.py` 故意放在
`src/mini_agent/` 包外（保持独立性，理由见该文件顶部说明），各删除点
如果各自重复"把仓库根目录加入 sys.path 再 import"这段样板代码，
维护成本高且容易出现细节不一致（例如仓库根目录的层级计算错误）。
这里统一封装一次，各删除点只需要
`from mini_agent.utils.protected_files_guard import is_protected`。

**失败时的安全默认**：与 `evolution/state_repo.py` 对
`scripts/protected_paths.py` 加载失败时的取舍一致——"宁可错杀，不可放过"。
判定模块本身加载失败，或扫描过程中抛出异常，一律视为"受保护"（即跳过
删除），而不是静默放行：本模块存在的意义就是防止误删，判定不了的情况下
放行等于失去了这层防护的价值；跳过删除的代价只是"这次维护任务少清理了
一项候选"，不影响正确性，下一轮维护任务还会重新判定。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Union

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def is_protected(path: Union[str, Path], project_root: Union[str, Path]) -> bool:
    """
    判断 path 是否命中当前生效的受保护文件清单（`protected_files.txt`）。

    project_root 应为调用方所在的项目根目录（各删除点通常本来就持有这个
    值，例如 `AgentPaths.project_root` 或函数参数里的同名值）。

    加载/扫描失败时返回 True（视为受保护，跳过删除），理由见模块顶部
    说明。
    """
    try:
        from scripts.protected_files import ProtectedFilesGuard
        return ProtectedFilesGuard(project_root).is_protected(path)
    except Exception:
        return True


__all__ = ["is_protected"]
