"""layout_adapt.py — 把 novel_writer 的「版本目录 / 工作区」布局转换成「最终稿导出包」布局。

现象：novel_writer 的 `_versions/vNNN/`（以及工作区本身）是原始内容树：
    正文/  章节总结/  大纲规划/人物设定/{角色,人设卡,角色索引.md}  大纲规划/世界观设定/地点详情/
而导入器只认最终稿导出包：
    正文/  章节总结/  设定/人物/{角色,人设卡,角色索引.md}  设定/世界观/地点详情/
直接导入版本目录会报 MISSING_CHARACTERS（阻断）、MISSING_LOCATIONS 等。

做法（与 name_normalize 同一思路）：检测到该布局后，只复制导入需要的文件到导出包布局的副本，
文件内容逐字节不变，原目录不改动；下游读取统一指向副本（manifest.working_dir）。
目录名/文件名若同时被 #U 转义，复制时一并还原。

注意：版本目录里的正文是未清洗的原稿（可能含写作备注），不同于「导出最终稿」的清洗结果。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from .name_normalize import decode_name

# (版本目录内的相对目录, 导出包布局下的相对目录)
_DIR_MAP: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    (("正文",), ("正文",)),
    (("章节总结",), ("章节总结",)),
    (("大纲规划", "人物设定", "角色"), ("设定", "人物", "角色")),
    (("大纲规划", "人物设定", "人设卡"), ("设定", "人物", "人设卡")),
    (("大纲规划", "世界观设定", "地点详情"), ("设定", "世界观", "地点详情")),
]
# 单文件
_FILE_MAP: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    (("大纲规划", "人物设定", "角色索引.md"), ("设定", "人物", "角色索引.md")),
]


def _find(root: Path, parts: Tuple[str, ...]) -> Path | None:
    """逐级按「解码后的名字」查找（兼容 #U 转义的目录名）。"""
    cur = root
    for want in parts:
        nxt = None
        if cur.is_dir():
            for child in cur.iterdir():
                if decode_name(child.name) == want:
                    nxt = child
                    break
        if nxt is None:
            return None
        cur = nxt
    return cur


def is_workspace_layout(root: Path) -> bool:
    """有 大纲规划/ 而没有 设定/ —— 版本目录或工作区；已是导出包布局的不处理。"""
    if not root.is_dir():
        return False
    has_outline = _find(root, ("大纲规划",)) is not None
    has_export_settings = _find(root, ("设定",)) is not None
    return has_outline and not has_export_settings


def convert_workspace_layout(root: Path, dest: Path) -> Dict[str, object]:
    """把版本目录 root 里导入所需的内容复制成导出包布局到 dest（须不存在或为空）。"""
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"转换目标非空：{dest}")
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src_parts, dst_parts in _DIR_MAP:
        src = _find(root, src_parts)
        if src is None or not src.is_dir():
            continue
        out = dest.joinpath(*dst_parts)
        out.mkdir(parents=True, exist_ok=True)
        for f in sorted(src.glob("*.md")):      # 只取直接子文件，跳过 正文/_drafts 等
            if f.is_file() and not f.is_symlink():
                shutil.copyfile(f, out / decode_name(f.name))
                copied += 1
    for src_parts, dst_parts in _FILE_MAP:
        src = _find(root, src_parts)
        if src is not None and src.is_file():
            out = dest.joinpath(*dst_parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
            copied += 1
    return {"copied": copied, "layout": "workspace"}
