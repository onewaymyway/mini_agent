"""
scripts/protected_paths.py — 受保护路径清单（T3 治理红线）

属于 self_evolution_implementation_plan.md Stage 0.1。

**为什么放在这里，而不是 src/mini_agent/ 包内**：
设计文档强调"T3 判定逻辑本身要在 agent 可写范围之外"——如果这份清单本身
也是 agent 在自我演化过程中可以自由改动的代码，那么它作为安全红线就毫无
意义（agent 理论上可以先悄悄放宽清单，再去改受保护文件）。

因此本文件：
  - 不放在 src/mini_agent/ 包内，避免被当作"普通源码"批量重构/格式化
  - 不依赖项目内其他模块（不 import mini_agent.*），保持判定逻辑自包含，
    即使 mini_agent 包本身在演化中被改坏，本文件依然能独立工作
  - 后续 Stage 2 的 StateRepo 会直接 import 本文件来做 T3 强制判定

使用方式：
    from scripts.protected_paths import PROTECTED_PATHS, is_protected_path

    if is_protected_path("src/mini_agent/agent.py"):
        tier = "T3"  # 强制升级，即使调用方传入了别的 tier
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

# ── 受保护路径清单 ──────────────────────────────────────────────────────────
#
# 条目可以是：
#   - 精确文件路径（相对仓库根目录，使用 "/" 分隔，跨平台时统一转换）
#   - 目录路径（以 "/" 结尾，表示该目录下所有文件均受保护）
#   - 正则表达式字符串（用 re.compile 编译后做 fullmatch，需以 "re:" 前缀标注）
#
# 覆盖范围（按设计文档要求，至少包含以下几类）：
#   1. agent.py —— agentic loop 主循环，是整个系统行为的核心
#   2. permissions.py —— 权限/审批门控，是安全机制本身
#   3. hooks/ —— 生命周期钩子加载与执行，决定了"什么时候会运行额外代码"
#   4. 本清单文件自身 —— 防止"先放宽清单、再改受保护文件"的绕过路径
#
# 之后随着 Stage 2 StateRepo 落地，如有新增的安全关键模块（例如未来的
# evolution/state_repo.py 本身），应追加到此列表，而不是新建另一份清单。

PROTECTED_PATHS: tuple[str, ...] = (
    # 1. agentic loop 主循环
    # 说明：Stage 12 起，agent.py 已从单文件拆分为 src/mini_agent/agent/ 目录
    # （core.py + 多个职责 Mixin 文件）。保留旧的单文件路径字符串是无害的
    # 防御性冗余（该路径已不存在，不会被误判为"未受保护"）；新增的目录条目
    # 才是实际生效、覆盖整个 agent 包的红线。
    "src/mini_agent/agent.py",
    "src/mini_agent/agent/",

    # 2. 权限/审批门控
    "src/mini_agent/permissions.py",

    # 3. 生命周期 hooks（整个目录及其所有子文件）
    "src/mini_agent/hooks/",

    # 4. 本清单文件自身（防止绕过）
    "scripts/protected_paths.py",
)

# 正则形式的补充规则（用于匹配上面静态列表无法覆盖的模式，例如未来新增的
# evolution/state_repo.py 及其同目录下的安全网核心文件）。
# 目前先覆盖 evolution 包整体（Stage 2 将在这里新建 StateRepo），
# 提前画好红线，避免 StateRepo 自己把自己改没了。
PROTECTED_PATTERNS: tuple[str, ...] = (
    r"src/mini_agent/evolution/.*",
)

_compiled_patterns = tuple(re.compile(p) for p in PROTECTED_PATTERNS)


def _normalize(path: Union[str, Path]) -> str:
    """统一转换为以 '/' 分隔的相对路径字符串，便于跨平台比较。"""
    p = Path(path)
    s = p.as_posix() if isinstance(path, Path) else str(path).replace("\\", "/")
    # 去掉开头的 "./"
    if s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def is_protected_path(path: Union[str, Path]) -> bool:
    """
    判断给定路径（相对仓库根目录）是否落在受保护清单内。

    匹配规则：
      - 精确文件路径：完全相等
      - 目录路径（以 "/" 结尾）：path 以该目录为前缀
      - 正则模式（PROTECTED_PATTERNS）：fullmatch

    参数 path 可以是相对路径（推荐）或绝对路径；绝对路径时只比较其
    相对仓库根目录部分行为可能不准确，调用方应尽量传入相对路径。
    """
    norm = _normalize(path)

    for entry in PROTECTED_PATHS:
        if entry.endswith("/"):
            if norm == entry.rstrip("/") or norm.startswith(entry):
                return True
        else:
            if norm == entry:
                return True

    for pattern in _compiled_patterns:
        if pattern.fullmatch(norm):
            return True

    return False


def list_protected_paths() -> tuple[str, ...]:
    """返回当前生效的全部静态受保护路径（不含正则规则），供 CLI/审计展示。"""
    return PROTECTED_PATHS


__all__ = [
    "PROTECTED_PATHS",
    "PROTECTED_PATTERNS",
    "is_protected_path",
    "list_protected_paths",
]
