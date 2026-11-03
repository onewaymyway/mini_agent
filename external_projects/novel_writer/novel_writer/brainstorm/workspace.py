"""构思项目的工作目录结构（B0）。

    <root>/
      _studio/brainstorm.json   项目元信息（标记文件：有它才算构思项目）
      working/                  当前草稿：ideas.json brief.json premise.md world.json cast.json plot.json open_questions.json
      versions/v<n>/            定版快照（不可变，只增不删）
      options/                  备选方案分支（B4 启用）

与小说项目共享 fsutil 的原子写入，但路径表独立，互不影响。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import config
from novel_writer.fsutil import atomic_write_json, read_json

META_FILE = "_studio/brainstorm.json"
WORKING_DIR = "working"
VERSIONS_DIR = "versions"
OPTIONS_DIR = "options"
BRAINSTORM_ROOT_NAME = "brainstorms"


class BrainstormError(ValueError):
    """构思项目的业务错误（参数不合法、版本不存在等）。"""


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def default_root() -> Path:
    return Path(config.DATA_DIR) / BRAINSTORM_ROOT_NAME


class BrainstormWorkspace:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    # ── 路径 ──
    @property
    def meta_path(self) -> Path:
        return self.root / META_FILE

    @property
    def working(self) -> Path:
        return self.root / WORKING_DIR

    @property
    def versions(self) -> Path:
        return self.root / VERSIONS_DIR

    def version_dir(self, n: int) -> Path:
        return self.versions / f"v{int(n)}"

    # ── 元信息 ──
    def exists(self) -> bool:
        return self.meta_path.is_file()

    def meta(self) -> Dict[str, Any]:
        data = read_json(self.meta_path, default=None)
        if not isinstance(data, dict) or data.get("kind") != "brainstorm":
            raise BrainstormError(f"不是构思项目：{self.root}")
        return data

    def save_meta(self, data: Dict[str, Any]) -> None:
        atomic_write_json(self.meta_path, data)

    def create(self, title: str) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise BrainstormError("请填写构思项目名称")
        if self.exists():
            raise BrainstormError(f"该目录已是构思项目：{self.root}")
        for sub in (self.working, self.versions, self.root / OPTIONS_DIR, self.root / "_studio"):
            sub.mkdir(parents=True, exist_ok=True)
        meta = {
            "kind": "brainstorm",
            "title": title,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "current_version": 0,          # 0 = 尚未定版
            "phase": "spark",              # spark → brief → world → cast → plot → final
            "linked_novels": [],           # 交接后记录生成的小说工作目录（B5 填充）
        }
        self.save_meta(meta)
        return meta

    def touch(self) -> None:
        meta = self.meta()
        meta["updated_at"] = now_iso()
        self.save_meta(meta)


def list_brainstorms(base: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """列出构思项目（按创建时间倒序）。损坏的目录跳过，不影响列表。"""
    base = Path(base) if base else default_root()
    out: List[Dict[str, Any]] = []
    if not base.is_dir():
        return out
    for child in base.iterdir():
        ws = BrainstormWorkspace(child)
        if not child.is_dir() or not ws.exists():
            continue
        try:
            meta = ws.meta()
        except BrainstormError:
            continue
        out.append({"root": str(child), "title": meta.get("title", child.name),
                    "created_at": meta.get("created_at", ""), "current_version": meta.get("current_version", 0),
                    "phase": meta.get("phase", "")})
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out
