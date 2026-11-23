"""importer.py — 文章导入（方案 §5.1、§19.3 导入预览）。

流程：collect（读取文件/目录/zip，得到候选）→ preview（每篇标注 新增/重复/跳过）→ apply（确认后写入）。
  * 源文件只读：只读取字节，永远不修改、不移动源文件；入库的是复制后的副本 library/imported/<id>.md。
  * 去重按正文哈希（忽略空白差异），既对照已入库文章，也对照本次导入内部。
  * 正文不足 MIN_CHARS 字的跳过（通常是空白页或导出失败的残片）。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .. import store
from . import catalog, front_matter, hashing

TEXT_EXT = (".md", ".markdown", ".txt")
MIN_CHARS = 200


@dataclass
class Candidate:
    source: str          # 显示用来源名；zip 内成员为 "包名!成员路径"
    title: str = ""
    body: str = ""
    chars: int = 0
    content_hash: str = ""
    error: str = ""


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法解码（需要 UTF-8 或 GBK/GB18030）")


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _candidate(source: str, raw: bytes) -> Candidate:
    try:
        text = _decode(raw)
    except ValueError as exc:
        return Candidate(source=source, error=str(exc))
    meta, body = front_matter.split(text)
    body = body.strip("\n")
    title = str(meta.get("title") or "").strip() or _first_heading(body) or Path(source.split("!")[-1]).stem
    return Candidate(source=source, title=title, body=body, chars=len(hashing.normalize(body)),
                     content_hash=hashing.content_hash(body))


def _skip_member(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return any(p.startswith(".") or p == "__MACOSX" for p in parts) or name.endswith("/")


def collect(entries: Iterable[Tuple[str, bytes]]) -> List[Candidate]:
    """entries: [(文件名, 字节)]。zip 会展开其中的文本文件。"""
    out: List[Candidate] = []
    for name, raw in entries:
        lower = name.lower()
        if lower.endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile:
                out.append(Candidate(source=name, error="zip 文件无法打开"))
                continue
            with zf:
                for info in zf.infolist():
                    if _skip_member(info.filename) or not info.filename.lower().endswith(TEXT_EXT):
                        continue
                    out.append(_candidate(f"{name}!{info.filename}", zf.read(info)))
        elif lower.endswith(TEXT_EXT):
            out.append(_candidate(name, raw))
        else:
            out.append(Candidate(source=name, error="不支持的文件类型（仅 .md / .txt / .zip）"))
    return out


def collect_paths(paths: Sequence[str]) -> List[Candidate]:
    """从本机路径收集（文件、目录递归、zip）。路径不存在时给出错误候选，不抛异常。"""
    entries: List[Tuple[str, bytes]] = []
    for raw_path in paths:
        p = Path(raw_path).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not any(part.startswith(".") for part in f.relative_to(p).parts):
                    if f.suffix.lower() in TEXT_EXT or f.suffix.lower() == ".zip":
                        entries.append((f.relative_to(p.parent).as_posix(), f.read_bytes()))
        elif p.is_file():
            entries.append((p.name, p.read_bytes()))
        else:
            return [Candidate(source=str(p), error="路径不存在")]
    return collect(entries)


def preview(ctx, candidates: Sequence[Candidate]) -> List[Dict[str, Any]]:
    existing = {a["content_hash"]: a for a in catalog.list_articles(ctx)}
    rows: List[Dict[str, Any]] = []
    seen_in_batch = set()
    for c in candidates:
        row = {"source": c.source, "title": c.title, "chars": c.chars, "action": "new", "reason": ""}
        if c.error:
            row.update(action="skipped", reason=c.error)
        elif c.chars < MIN_CHARS:
            row.update(action="skipped", reason=f"正文不足 {MIN_CHARS} 字")
        elif c.content_hash in existing:
            row.update(action="duplicate", reason=f"与已有 {existing[c.content_hash]['id']} 内容相同")
        elif c.content_hash in seen_in_batch:
            row.update(action="duplicate", reason="本次导入中已有相同内容")
        else:
            seen_in_batch.add(c.content_hash)
        rows.append(row)
    return rows


def apply(ctx, candidates: Sequence[Candidate]) -> Dict[str, Any]:
    """确认导入：只导入 preview 标记为 new 的候选。返回 {imported: [id…], rows: [...]}。"""
    rows = preview(ctx, candidates)
    data = catalog.load(ctx)
    imported: List[str] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for c, row in zip(candidates, rows):
        if row["action"] != "new":
            continue
        aid = catalog.allocate_id(data)
        rel = f"library/imported/{aid}.md"
        meta = {"id": aid, "title": c.title, "origin": "imported", "status": "published",
                "content_hash": c.content_hash, "source_name": c.source, "imported_at": now, "chars": c.chars}
        store.write_text_atomic(ctx.path(rel), front_matter.dump(meta, c.body))
        data["articles"].append({"id": aid, "title": c.title, "origin": "imported", "status": "published",
                                 "content_hash": c.content_hash, "path": rel, "source_name": c.source,
                                 "chars": c.chars, "imported_at": now})
        imported.append(aid)
    catalog.save(ctx, data)
    return {"imported": imported, "rows": rows}
