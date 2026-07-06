"""
产出物 Manifest（Artifacts）—— 面向「产出物看板」的统一登记/查询模块
=====================================================================

背景：命令行不便展示的产出（Word/PDF/图片等）分散在各种输出目录里，
命令行只能靠"文件路径"传达，用户体验差。本模块提供一个轻量、与工具/
Agent 实现解耦的登记方式：

    record_artifact(paths, session_id, title, files, ...)

调用后会：
1. 在 <project_root>/.agent/sessions/<session_id>/artifacts/ 下写一份
   完整 manifest（manifest_<ts>_<slug>.json）。
2. 在 <project_root>/.agent/artifacts_index.jsonl 追加一行摘要，供看板
   做全局「最近产出 / 按 session 过滤」的快速查询，无需遍历所有 session
   目录。

manifest 字段设计见 docs（或直接看 ArtifactFile / ArtifactManifest 的
docstring），核心是 `type` 字段决定看板用什么方式渲染：
    image | document | pdf | code | text | other
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from mini_agent.storage.paths import AgentPaths

# 常见后缀 -> 展示类型的映射，record_artifact 在调用方未显式指定 type 时使用。
_EXT_TYPE_MAP = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".svg": "image", ".bmp": "image",
    ".pdf": "pdf",
    ".docx": "document", ".doc": "document", ".dotx": "document",
    ".pptx": "document", ".ppt": "document", ".xlsx": "document", ".xls": "document",
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
    ".json": "code", ".java": "code", ".go": "code", ".rs": "code", ".sh": "code",
    ".md": "text", ".txt": "text", ".csv": "text", ".html": "text", ".yaml": "text", ".yml": "text",
}

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _slugify(text: str, default: str = "artifact") -> str:
    text = (text or "").strip()
    if not text:
        return default
    slug = _SLUG_RE.sub("_", text).strip("_")
    return slug[:40] or default


def _infer_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _EXT_TYPE_MAP.get(ext, "other")


@dataclass
class ArtifactFile:
    """manifest 里的单个产出文件。"""
    path: str
    type: str = "other"          # image | document | pdf | code | text | other
    title: Optional[str] = None
    mime: Optional[str] = None
    size: Optional[int] = None
    preview: str = "auto"        # auto | none | 缩略图路径


@dataclass
class ArtifactManifest:
    """一次产出登记的完整清单。"""
    manifest_id: str
    session_id: str
    created_at: str
    title: str
    files: list[ArtifactFile]
    user_id: Optional[str] = None
    description: Optional[str] = None
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_summary(self) -> dict:
        """写入全局索引 artifacts_index.jsonl 的精简摘要（不含文件明细）。"""
        return {
            "manifest_id": self.manifest_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "title": self.title,
            "file_count": len(self.files),
            "types": sorted({f.type for f in self.files}),
        }


def _normalize_files(files: list[Any]) -> list[ArtifactFile]:
    normalized: list[ArtifactFile] = []
    for f in files:
        if isinstance(f, ArtifactFile):
            normalized.append(f)
            continue
        if isinstance(f, str):
            f = {"path": f}
        if not isinstance(f, dict) or "path" not in f:
            raise ValueError(f"非法的 artifact 文件描述: {f!r}，需要 str 路径或包含 'path' 的 dict")
        path = f["path"]
        file_type = f.get("type") or _infer_type(path)
        size = f.get("size")
        if size is None:
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = None
        normalized.append(ArtifactFile(
            path=path,
            type=file_type,
            title=f.get("title") or Path(path).name,
            mime=f.get("mime"),
            size=size,
            preview=f.get("preview", "auto"),
        ))
    return normalized


def record_artifact(
    paths: AgentPaths,
    session_id: str,
    title: str,
    files: list[Any],
    *,
    user_id: Optional[str] = None,
    description: Optional[str] = None,
    source: Optional[dict[str, Any]] = None,
) -> ArtifactManifest:
    """登记一次产出物。

    files 支持三种写法（可混用）：
        ["a.png", "b.docx"]
        [{"path": "a.png", "type": "image", "title": "示意图"}]
        [ArtifactFile(...)]

    未显式指定 type 的文件会按后缀自动推断（见 _EXT_TYPE_MAP）。
    """
    if not files:
        raise ValueError("record_artifact 至少需要一个文件")

    normalized_files = _normalize_files(files)
    now = datetime.now().astimezone()
    ts = now.strftime("%Y%m%d_%H%M%S")
    manifest_id = f"{ts}_{_slugify(title)}_{uuid.uuid4().hex[:6]}"

    manifest = ArtifactManifest(
        manifest_id=manifest_id,
        session_id=session_id,
        created_at=now.isoformat(),
        title=title or "未命名产出",
        files=normalized_files,
        user_id=user_id,
        description=description,
        source=source or {},
    )

    artifacts_dir = paths.session_artifacts_dir(session_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifacts_dir / f"manifest_{manifest_id}.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    index_path = paths.artifacts_index()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(manifest.to_summary(), ensure_ascii=False) + "\n")

    return manifest


def _manifest_path_by_id(paths: AgentPaths, manifest_id: str, session_id: Optional[str] = None) -> Optional[Path]:
    """按 manifest_id 定位 manifest 文件。若已知 session_id 直接查该 session 目录，
    否则回退为遍历 sessions_dir（数量一般不大，且只在未提供 session_id 时才发生）。"""
    filename = f"manifest_{manifest_id}.json"
    if session_id:
        candidate = paths.session_artifacts_dir(session_id) / filename
        if candidate.exists():
            return candidate
        return None
    sessions_dir = paths.sessions_dir
    if not sessions_dir.exists():
        return None
    for session_path in sessions_dir.iterdir():
        candidate = session_path / "artifacts" / filename
        if candidate.exists():
            return candidate
    return None


def get_manifest(paths: AgentPaths, manifest_id: str, session_id: Optional[str] = None) -> Optional[dict]:
    """读取单个 manifest 的完整内容（含文件明细）。"""
    p = _manifest_path_by_id(paths, manifest_id, session_id)
    if not p:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_artifacts(
    paths: AgentPaths,
    *,
    session_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """列出产出物摘要，按时间倒序。

    session_id 提供时优先直接读该 session 的 artifacts 目录（不依赖全局索引，
    保证同一 session 内数据始终是最新的）；否则读全局索引 jsonl。
    """
    if session_id:
        artifacts_dir = paths.session_artifacts_dir(session_id)
        if not artifacts_dir.exists():
            return []
        summaries = []
        for manifest_file in artifacts_dir.glob("manifest_*.json"):
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            summaries.append({
                "manifest_id": data.get("manifest_id"),
                "session_id": data.get("session_id"),
                "user_id": data.get("user_id"),
                "created_at": data.get("created_at"),
                "title": data.get("title"),
                "file_count": len(data.get("files", [])),
                "types": sorted({f.get("type") for f in data.get("files", [])}),
            })
        summaries.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return summaries[offset: offset + limit]

    index_path = paths.artifacts_index()
    if not index_path.exists():
        return []
    lines: list[dict] = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    lines.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return lines[offset: offset + limit]
