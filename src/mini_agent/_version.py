"""
统一版本号读取。

版本号维护在仓库根目录的 VERSION 文件中（单一数据源），避免在多处代码里
硬编码版本字符串。这里提供 get_version()，从 VERSION 文件读取并缓存结果；
找不到文件时回退到 _FALLBACK_VERSION，保证不会因为文件缺失而报错。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# 找不到 VERSION 文件时的兜底版本号
_FALLBACK_VERSION = "0.8.1"


def _find_version_file() -> Path | None:
    """从当前文件出发，向上查找仓库根目录下的 VERSION 文件。"""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "VERSION"
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def get_version() -> str:
    """读取并返回当前版本号（结果会被缓存）。"""
    version_file = _find_version_file()
    if version_file is not None:
        try:
            content = version_file.read_text(encoding="utf-8").strip()
            if content:
                return content
        except OSError:
            pass
    return _FALLBACK_VERSION


__version__ = get_version()
