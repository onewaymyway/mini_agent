"""
scripts/protected_files.py — 用户级"受保护文件清单"判定模块

属于 next_doc/protected_files_manifest_and_delete_guard_plan.md 阶段 0。

**与 scripts/protected_paths.py 的区别**（务必不要混淆两者）：
  - protected_paths.py 解决的是"防止自我演化机制改坏框架核心源码"，
    清单是硬编码在代码里的固定列表，服务对象是框架自身的源码文件。
  - 本模块解决的是"防止例行维护任务 / agent 删除用户数据文件"，清单由
    用户在运行时通过 `protected_files.txt` 声明，服务对象是用户的项目
    数据（sessions、缓存、笔记等），条目内容运行时可变。
  两者语义、服务对象、生效时机都不同，故意保持完全独立的两份实现，不
  互相依赖、不合并。

**为什么放在这里，而不是 src/mini_agent/ 包内**：
与 protected_paths.py 同样的理由——不依赖能被自我演化流程批量改动的
代码路径，不 import mini_agent.* ，保持自包含、独立可用。

使用方式：
    from scripts.protected_files import ProtectedFilesGuard

    guard = ProtectedFilesGuard(project_root)
    if guard.is_protected("/abs/path/to/candidate"):
        ...  # 跳过删除

    # 或者用一次性函数（内部各自新建一个 Guard，适合低频调用场景）
    from scripts.protected_files import is_protected_file, list_protected_files

参见 next_doc/protected_files_manifest_and_delete_guard_plan.md 三、
"清单文件设计" 一节，本文件的实现与该节约定逐条对应：
  - 清单文件名固定为 protected_files.txt（3.1）
  - 每次调用都重新扫描，不做常驻缓存（3.2）
  - 只扫描两个固定位置：`<project_root>/protected_files.txt` 与
    `<project_root>/.agent/protected_files.txt`，两者取并集（3.2）
  - 相对路径以清单文件自身所在目录为基准解析（3.3）
  - 目录写法（末尾 "/"）表示整个目录树受保护，判定逻辑与
    protected_paths.py::is_protected_path() 的目录前缀匹配方式一致（3.3）
  - 被扫描到的 protected_files.txt 自身无条件加入受保护集合，即使清单
    内容为空/只有注释（3.4）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

# 清单文件的约定文件名（3.1）
MANIFEST_FILENAME = "protected_files.txt"


def _normalize(path: Union[str, Path]) -> str:
    """统一转换为绝对路径的 POSIX 风格字符串，便于跨平台比较。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve().as_posix()


@dataclass(frozen=True)
class ProtectedEntry:
    """一条受保护条目，解析自清单文件中的某一行（或清单文件自身）。"""

    # 规范化后的绝对路径（不含末尾 "/"）
    path: str
    # True 表示整个目录树受保护，False 表示精确文件
    is_dir: bool
    # 该条目来自哪份清单文件（用于诊断/展示）
    source_manifest: str


def _manifest_search_paths(project_root: Union[str, Path]) -> tuple[Path, ...]:
    """返回按约定固定扫描的两个清单文件位置（3.2）。"""
    root = Path(project_root)
    return (
        root / MANIFEST_FILENAME,
        root / ".agent" / MANIFEST_FILENAME,
    )


def _parse_manifest(manifest_path: Path) -> list[ProtectedEntry]:
    """解析单份清单文件，返回其中声明的条目（不含清单文件自身这一条，
    调用方负责把清单文件自身也追加为受保护条目——3.4）。"""
    entries: list[ProtectedEntry] = []
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return entries

    base_dir = manifest_path.resolve().parent
    source = manifest_path.as_posix()

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        is_dir = stripped.endswith("/")
        raw = stripped[:-1] if is_dir else stripped
        candidate = Path(raw)

        # 相对路径以清单文件自身所在目录为基准解析（3.3），
        # 绝对路径原样使用。
        resolved = candidate if candidate.is_absolute() else (base_dir / candidate)
        entries.append(
            ProtectedEntry(
                path=resolved.resolve().as_posix(),
                is_dir=is_dir,
                source_manifest=source,
            )
        )

    return entries


class ProtectedFilesGuard:
    """
    单次扫描 + 判定的封装。每次构造都重新扫描清单文件（不做常驻缓存，
    对应设计文档 3.2 的"每次用到都重新扫描"约定），实例本身可在一次
    维护任务/一次 prompt 拼装内多次复用（避免同一批候选项判定时重复
    扫描磁盘），但不建议跨任务长期持有。
    """

    def __init__(self, project_root: Union[str, Path]) -> None:
        self.project_root = Path(project_root).resolve()
        self._entries: list[ProtectedEntry] = self._scan()

    def _scan(self) -> list[ProtectedEntry]:
        entries: list[ProtectedEntry] = []
        for manifest_path in _manifest_search_paths(self.project_root):
            if not manifest_path.is_file():
                continue

            # 清单文件自身无条件受保护（3.4），即使内容为空/只有注释。
            entries.append(
                ProtectedEntry(
                    path=manifest_path.resolve().as_posix(),
                    is_dir=False,
                    source_manifest=manifest_path.as_posix(),
                )
            )
            entries.extend(_parse_manifest(manifest_path))

        return entries

    def is_protected(self, path: Union[str, Path]) -> bool:
        """判断给定路径（相对或绝对均可）是否落在当前生效的受保护清单内。"""
        norm = _normalize(path)

        for entry in self._entries:
            if entry.is_dir:
                if norm == entry.path or norm.startswith(entry.path + "/"):
                    return True
            else:
                if norm == entry.path:
                    return True

        return False

    def list_entries(self) -> tuple[ProtectedEntry, ...]:
        """返回当前生效的全部受保护条目（含自动纳入的清单文件本身），
        供 prompt 摘要展示 / 备份任务枚举使用。"""
        return tuple(self._entries)


# ── 一次性调用的便捷函数（内部各自新建 Guard，适合低频调用场景） ──────────


def is_protected_file(path: Union[str, Path], project_root: Union[str, Path]) -> bool:
    return ProtectedFilesGuard(project_root).is_protected(path)


def list_protected_files(
    project_root: Union[str, Path],
) -> tuple[ProtectedEntry, ...]:
    return ProtectedFilesGuard(project_root).list_entries()


__all__ = [
    "MANIFEST_FILENAME",
    "ProtectedEntry",
    "ProtectedFilesGuard",
    "is_protected_file",
    "list_protected_files",
]
