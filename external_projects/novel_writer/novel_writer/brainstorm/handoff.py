"""交接（B5）：把一个定版的构思版本交接成一本新小说。

流程（一次性，整包）：
  1. 只允许定版版本（current_version ≥ 1），默认交接最新定版；
  2. 用 tool_api.create_novel 创建小说工作目录；
  3. 题材卡 → 新建向导的意图（tool_api.save_intake），进入每一步的提示词；
  4. 整个版本快照复制到 `_studio/seed/brainstorm/v<n>/`，并写 source.json 记录来源（哈希、时间）；
  5. 生成「构思种子」摘要 `_studio/seed/brainstorm_seed.md`，由运行器注入每一步的提示词，标明「仅供参考，蓝图为准」；
  6. 在构思项目的元信息里记录 linked_novels。

种子与小说之后不自动同步。种子和向导意图分开存放，重新保存向导不会删除种子。
"""

from __future__ import annotations

import datetime as _dt
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json
from novel_writer.intake import INTAKE_KEYS

SEED_NOTE_REL = "_studio/seed/brainstorm_seed.md"
SEED_DIR_REL = "_studio/seed/brainstorm"
SEED_NOTE_LIMIT = 3000


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _version_data(vdir: Path, fname: str, default: Any) -> Any:
    data = read_json(vdir / fname, default=None)
    return default if data is None else data


def build_seed_note(vdir: Path, title: str, version: int) -> str:
    """把版本内容整理成一段简短的文字摘要，供 prompt 注入。超出长度时截断，并提示完整文件位置。"""
    lines = [f"构思项目「{title}」v{version}。以下为摘要，完整内容见 {SEED_DIR_REL}/v{version}/。"]
    premise = (vdir / "premise.md")
    if premise.is_file() and premise.read_text(encoding="utf-8").strip():
        lines.append("核心命题：" + premise.read_text(encoding="utf-8").strip())
    world = _version_data(vdir, "world.json", {})
    rules = [r.get("text", "") + (f"（代价：{r['cost']}）" if r.get("cost") else "") for r in world.get("rules", [])]
    if rules:
        lines.append("世界规则：" + "；".join(rules))
    for key, label in (("locations", "地点"), ("factions", "势力"), ("taboos", "禁忌")):
        items = [x.get("text", "") if isinstance(x, dict) else str(x) for x in world.get(key, [])]
        if items:
            lines.append(f"{label}：" + "；".join(items))
    cast = _version_data(vdir, "cast.json", {})
    if cast:
        people = []
        for cid, card in cast.items():
            bit = card.get("name", cid) + (f"（{card['role']}）" if card.get("role") else "")
            if card.get("want"):
                bit += f"，想要{card['want']}"
            people.append(bit)
        lines.append("主要角色：" + "；".join(people))
    plot = _version_data(vdir, "plot.json", {"acts": [], "twists": [], "endings": []})
    for i, act in enumerate(plot.get("acts", []), 1):
        beats = "、".join(act.get("beats", []))
        lines.append(f"第{i}幕「{act.get('title', '')}」" + (f"：{beats}" if beats else ""))
    if plot.get("twists"):
        lines.append("反转候选：" + "；".join(plot["twists"]))
    if plot.get("endings"):
        lines.append("结局候选：" + "；".join(plot["endings"]))
    questions = [q["text"] for q in _version_data(vdir, "open_questions.json", []) if not q.get("closed")]
    if questions:
        lines.append("尚未决定：" + "；".join(questions))
    text = "\n".join(lines)
    if len(text) > SEED_NOTE_LIMIT:
        text = text[:SEED_NOTE_LIMIT] + f"\n……（略，完整内容见 {SEED_DIR_REL}/v{version}/）"
    return text


def handoff(bws: BrainstormWorkspace, *, version: Optional[int] = None, title: Optional[str] = None,
            novels_dir: Optional[str] = None, index_file: Optional[str] = None,
            gear: str = "G0", quality: str = "standard") -> Dict[str, Any]:
    from novel_writer import tool_api

    meta = bws.meta()
    current = int(meta.get("current_version", 0))
    if current < 1:
        raise BrainstormError("请先定版（生成至少一个版本）再交接")
    n = int(version) if version is not None else current
    vdir = bws.version_dir(n)
    if not (vdir / "version.json").is_file():
        raise BrainstormError(f"版本不存在：v{n}")
    novel_title = (title or "").strip() or meta.get("title", "未命名")

    created = tool_api.create_novel(novel_title, gear=gear, quality=quality,
                                    novels_dir=novels_dir, index_file=index_file)
    if not created.get("ok"):
        raise BrainstormError(f"创建小说失败：{created.get('error', '未知错误')}")
    root = Path(created["data"]["workspace"])

    brief = _version_data(vdir, "brief.json", {})
    intake = {k: str(brief.get(k, "") or "") for k in INTAKE_KEYS}
    saved = tool_api.save_intake(str(root), intake)
    if not saved.get("ok"):
        raise BrainstormError(f"写入向导意图失败：{saved.get('error', '未知错误')}")

    seed_dir = root / SEED_DIR_REL / f"v{n}"
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    shutil.copytree(vdir, seed_dir)
    info = read_json(vdir / "version.json", default={}) or {}
    atomic_write_json(root / SEED_DIR_REL / "source.json", {
        "brainstorm_root": str(bws.root), "brainstorm_title": meta.get("title", ""), "version": n,
        "content_hash": info.get("hash", ""), "handed_off_at": _now(),
    })
    atomic_write_text(root / SEED_NOTE_REL, build_seed_note(vdir, meta.get("title", ""), n) + "\n")

    meta = bws.meta()
    meta.setdefault("linked_novels", []).append({"novel_root": str(root), "title": novel_title,
                                                 "version": n, "at": _now()})
    bws.save_meta(meta)
    return {"novel_root": str(root), "title": novel_title, "version": n, "seed_dir": str(seed_dir)}


def linked_novels(bws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    return list(bws.meta().get("linked_novels", []))
