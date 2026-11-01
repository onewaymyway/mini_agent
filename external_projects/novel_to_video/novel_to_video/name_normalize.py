"""name_normalize.py — 还原被转义的导出目录与文件名。

现象：部分压缩/解压工具会把非 ASCII 文件名存成 `#U5c0f` 这样的 Unicode 码位转义文本，
导致导入器按中文名找不到 正文/、设定/ 等目录，报 MISSING_CHAPTERS 等阻断错误。

做法：检测到转义后，把整棵目录树复制为中文名的规范化副本（文件内容逐字节不变），
下游所有读取统一指向副本；原目录不做任何改动。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

ESCAPE_RE = re.compile(r"#U([0-9A-Fa-f]{4})")


def decode_name(name: str) -> str:
    return ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), name)


def has_escape(root: Path) -> bool:
    for p in root.rglob("*"):
        if ESCAPE_RE.search(p.name):
            return True
    return False


def normalize_tree(root: Path, dest: Path) -> Dict[str, object]:
    """把 root 复制到 dest，并把每一级名字解码为真实字符。返回统计与映射。

    dest 必须不存在或为空。只复制普通文件与目录；符号链接一律跳过并计数。
    """
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"规范化目标非空：{dest}")
    dest.mkdir(parents=True, exist_ok=True)
    renamed: List[Tuple[str, str]] = []
    copied = 0
    skipped_links = 0
    for src in sorted(root.rglob("*")):
        if src.is_symlink():
            skipped_links += 1
            continue
        rel_parts = [decode_name(part) for part in src.relative_to(root).parts]
        raw_parts = list(src.relative_to(root).parts)
        if rel_parts != raw_parts:
            renamed.append(("/".join(raw_parts), "/".join(rel_parts)))
        target = dest.joinpath(*rel_parts)
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif src.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            copied += 1
    return {"copied": copied, "renamed": len(renamed), "examples": renamed[:3],
            "skipped_links": skipped_links}
