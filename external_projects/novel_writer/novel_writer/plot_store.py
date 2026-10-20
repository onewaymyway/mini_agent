"""novel_writer/plot_store.py — 伏笔账本的小文件存储（M9 第二阶段）。

布局（目录：大纲规划/线索设定/伏笔/）：
- F001.md          每条伏笔一个文件。文件头用 `key: <JSON值>` 逐行保存全部结构化字段；正文由系统生成，仅供阅读。
- _applied/第03章.md  每章已应用的增量记录（合并前状态 + 增量本身），用于重跑时撤销。
- _index.md        自动生成的紧凑索引（每条伏笔一行），sync_plot 只读它，不再读取整本账本。

为什么这样设计：
- 每个文件很小，agent 读写容易；一个文件损坏只影响那一条记录，并且会被明确报出。
- 字段值统一用单行 JSON 编码，读写可逆，往返校验可以做到逐字段一致。
- 只有 Python 写结构化字段；agent 不直接手写格式。

迁移：旧版 `plot_tracker.json` 通过 migrate_legacy() 转换，转换后立即回读比对，一致才落盘；原文件改名为 .bak。
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from novel_writer import json_repair as JR
from novel_writer import plot_delta as PD
from novel_writer.fsutil import atomic_write_json, atomic_write_text

ITEM_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
APPLIED_DIR = "_applied"
INDEX_NAME = "_index.md"
ITEM_KEY = PD.DEFAULT_LIST_KEY


class LedgerError(PD.PlotDeltaError):
    pass


# ── 单文件编解码 ─────────────────────────────────────────────────────

def _encode_fields(fields: Dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(k)):
            raise LedgerError([f"字段名不合法：{k!r}"])
        # 单行 JSON：不含换行，保证逐行可解析
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _decode_fields(text: str, where: str) -> Dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise LedgerError([f"{where}：缺少文件头（应以 --- 开始）"])
    fields: Dict[str, Any] = {}
    for n, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return fields
        if not line.strip():
            continue
        key, sep, raw = line.partition(": ")
        if not sep:
            raise LedgerError([f"{where} 第 {n} 行格式错误（应为 key: JSON值）"])
        try:
            fields[key] = json.loads(raw)
        except ValueError as exc:
            raise LedgerError([f"{where} 第 {n} 行的 {key} 值不是合法 JSON（{exc}）"])
    raise LedgerError([f"{where}：文件头未闭合（缺少结束的 ---）"])


def _item_body(item: Dict[str, Any]) -> str:
    name = item.get("name") or item.get("id")
    lines = [f"# {item.get('id')} {name}", "", "> 本文件由系统生成，结构化字段以文件头为准，修改请改文件头。", ""]
    for p in item.get("progress") or []:
        if isinstance(p, dict):
            lines.append(f"- 第 {p.get('chapter')} 章：{p.get('note', '')}")
    return "\n".join(lines) + "\n"


# ── 目录级读写 ───────────────────────────────────────────────────────

def _item_files(base: Path) -> List[Path]:
    return sorted(p for p in base.glob("*.md") if p.name != INDEX_NAME and ITEM_ID.match(p.stem))


def _applied_files(base: Path) -> List[Path]:
    d = base / APPLIED_DIR
    return sorted(d.glob("*.md")) if d.is_dir() else []


def read_store(base: Path) -> Dict[str, Any]:
    """从目录读取账本。目录不存在返回空账本；任何一个文件损坏都抛 LedgerError（点名文件）。"""
    base = Path(base)
    items: List[Dict[str, Any]] = []
    seen: set = set()
    for f in _item_files(base):
        fields = _decode_fields(f.read_text(encoding="utf-8"), f.name)
        iid = str(fields.get("id") or "")
        if iid != f.stem:
            raise LedgerError([f"{f.name}：文件头 id={iid!r} 与文件名不一致"])
        if iid in seen:
            raise LedgerError([f"伏笔 id 重复：{iid}"])
        seen.add(iid)
        items.append(fields)
    applied: Dict[str, Any] = {}
    for f in _applied_files(base):
        fields = _decode_fields(f.read_text(encoding="utf-8"), f"{APPLIED_DIR}/{f.name}")
        ch = str(fields.get("chapter"))
        applied[ch] = {"before": fields.get("before") or {}, "delta": fields.get("delta") or {}}
    data: Dict[str, Any] = {ITEM_KEY: items}
    if applied:
        data[PD.APPLIED_KEY] = applied
    return data


def _index_text(data: Dict[str, Any]) -> str:
    items = sorted(data.get(PD.list_key(data), []), key=lambda it: str(it.get("id")))
    total = len(items)
    counts = {k: 0 for k in PD.KINDS + ("resolved",)}
    for it in items:
        counts[str(it.get("status") or "planted")] = counts.get(str(it.get("status") or "planted"), 0) + 1
    lines = [
        "# 伏笔索引（自动生成，请勿手动编辑）",
        "",
        f"共 {total} 条 · 已埋设 {counts.get('planted', 0)} · 推进中 {counts.get('advanced', 0)} · 已兑现 {counts.get('resolved', 0)}",
        "",
        "| id | 名称 | 状态 | 埋设章 | 兑现章 |",
        "|---|---|---|---|---|",
    ]
    for it in items:
        name = str(it.get("name") or "").replace("|", "／").replace("\n", " ")
        lines.append(f"| {it.get('id')} | {name} | {it.get('status') or 'planted'} | "
                     f"{it.get('plant_chapter') if it.get('plant_chapter') is not None else ''} | "
                     f"{it.get('resolve_chapter') if it.get('resolve_chapter') is not None else ''} |")
    lines += ["", "详情见同目录下各 F*.md 文件。"]
    return "\n".join(lines) + "\n"


def write_store(base: Path, data: Dict[str, Any]) -> bool:
    """把账本写入目录。只改动确实变化的文件；返回是否有任何文件变化。"""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    changed = False
    items = data.get(PD.list_key(data), [])
    wanted: Dict[str, str] = {}
    seen: set = set()
    for it in items:
        if not isinstance(it, dict) or not ITEM_ID.match(str(it.get("id") or "")):
            raise LedgerError([f"伏笔 id 不合法（只允许字母、数字、下划线、连字符）：{it.get('id') if isinstance(it, dict) else it!r}"])
        iid = str(it["id"])
        if iid in seen:
            raise LedgerError([f"伏笔 id 重复：{iid}"])
        seen.add(iid)
        wanted[f"{iid}.md"] = _encode_fields(it) + _item_body(it)
    for name, text in wanted.items():
        path = base / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)
            changed = True
    for f in _item_files(base):
        if f.name not in wanted:
            f.unlink()
            changed = True
    applied_dir = base / APPLIED_DIR
    applied = data.get(PD.APPLIED_KEY) or {}
    wanted_applied: Dict[str, str] = {}
    for ch, rec in applied.items():
        name = f"第{int(ch):02d}章.md"
        wanted_applied[name] = _encode_fields({"chapter": int(ch), "before": rec.get("before") or {},
                                               "delta": rec.get("delta") or {}}) + f"\n# 第 {ch} 章伏笔增量（系统记录）\n"
    if wanted_applied:
        applied_dir.mkdir(parents=True, exist_ok=True)
    for name, text in wanted_applied.items():
        path = applied_dir / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)
            changed = True
    if applied_dir.is_dir():
        for f in applied_dir.glob("*.md"):
            if f.name not in wanted_applied:
                f.unlink()
                changed = True
    index = _index_text(data)
    idx_path = base / INDEX_NAME
    if not idx_path.exists() or idx_path.read_text(encoding="utf-8") != index:
        atomic_write_text(idx_path, index)
        changed = True
    return changed


def is_store(base: Path) -> bool:
    return (Path(base) / INDEX_NAME).is_file()


def canonical_of(data: Dict[str, Any]) -> str:
    return PD.canonical(data)


def _normalized(data: Dict[str, Any]) -> Dict[str, Any]:
    """往返比对前的规范形态：没有已记录章节时不保留 applied 字段；伏笔按 id 排序（存储按文件名读取，顺序不具语义）。"""
    out = dict(data)
    if not out.get(PD.APPLIED_KEY):
        out.pop(PD.APPLIED_KEY, None)
    key = PD.list_key(out)
    out[key] = sorted(out.get(key, []), key=lambda it: str(it.get("id")))
    return out


# ── 迁移（含损坏旧账本的抢救与修复）────────────────────────────────

REPAIR_HINT = "伏笔条目：必须是对象，含 id（字母数字下划线连字符）、name、status（planted/advanced/resolved）、"\
              "plant_chapter、resolve_chapter，progress 为对象数组。"


def _record_problem(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return "不是对象"
    iid = str(obj.get("id") or "")
    if not ITEM_ID.match(iid):
        return f"id 不合法：{obj.get('id')!r}"
    return None


def _review_report(summary: Dict[str, Any], quarantined: List[str]) -> str:
    lines = ["# 伏笔账本迁移报告", "",
             f"- 合法条目：{summary['items']}（严格解析 {summary['origins'].get('strict', 0)}，"
             f"确定性清洗 {summary['origins'].get('lenient', 0)}，LLM 修复 {summary['origins'].get('llm', 0)}）",
             f"- 隔离条目：{len(quarantined)}（原文保存在 伏笔/_quarantine/，未进入账本）",
             f"- 已记录的章节增量：{summary['applied_chapters']}" + ("（撤销记录已丢失，重跑相关章节将无法撤销旧效果）"
                                                              if summary.get("applied_lost") else ""),
             ""]
    if summary.get("notes"):
        lines += ["## 说明"] + [f"- {n}" for n in summary["notes"]] + [""]
    if summary.get("llm_ids"):
        lines += ["## 由 LLM 修复的条目（请逐条核对）"] + [f"- {i}" for i in summary["llm_ids"]] + [""]
    if quarantined:
        lines += ["## 隔离的原文（摘要）"] + [f"- 第 {i + 1} 条：{q[:80]!r}" for i, q in enumerate(quarantined)] + [""]
    lines += ["确认无误后，以 confirm=True 重新执行迁移即可提交。"]
    return "\n".join(lines) + "\n"


def migrate_legacy(legacy_path: Path, base: Path, *, dry_run: bool = False,
                   register_path: Optional[Path] = None, repair: Optional[Callable[[str, str], Optional[str]]] = None,
                   confirm: bool = False) -> Dict[str, Any]:
    """把旧版 JSON 账本迁移为小文件存储。

    流程：抢救（严格 → 清洗 → 按条目拆分）→ 待修复条目交给 repair（LLM）→ 不合格的隔离 →
    写入临时目录并回读比对 → 落盘。

    自动提交的条件：没有 LLM 修复、没有隔离、没有丢失的撤销记录、没有结构问题。
    否则只生成复核报告（伏笔迁移报告.md），需要 confirm=True 才会提交。
    旧文件不删除，提交后改名为 .bak；隔离的原文保存在 伏笔/_quarantine/。
    """
    legacy_path, base = Path(legacy_path), Path(base)
    if not legacy_path.is_file():
        return {"status": "nothing", "message": "没有旧版伏笔账本，无需迁移"}
    if is_store(base):
        return {"status": "already", "message": "伏笔账本已是小文件格式，无需迁移"}
    text = legacy_path.read_bytes().decode("utf-8", errors="replace")
    sv = JR.salvage_ledger(text, ITEM_KEY, PD.APPLIED_KEY)
    fixed, quarantined_raw = JR.repair_pending(sv.pending, repair, REPAIR_HINT, _record_problem)

    items: List[Dict[str, Any]] = []
    origins: Dict[str, int] = {}
    llm_ids: List[str] = []
    seen: set = set()
    for obj, origin in list(sv.records) + list(fixed):
        problem = _record_problem(obj)
        if problem is None and str(obj["id"]) in seen:
            problem = f"id 重复：{obj['id']}"
        if problem is not None:
            quarantined_raw.append(json.dumps(obj, ensure_ascii=False) if isinstance(obj, dict) else str(obj))
            continue
        seen.add(str(obj["id"]))
        items.append(obj)
        origins[origin] = origins.get(origin, 0) + 1
        if origin == "llm":
            llm_ids.append(str(obj["id"]))
    notes = list(sv.notes)
    if quarantined_raw:
        notes.append(f"{len(quarantined_raw)} 条无法修复或不合格，已隔离")
    applied = sv.applied or {}
    data: Dict[str, Any] = {ITEM_KEY: items}
    if applied:
        data[PD.APPLIED_KEY] = applied

    with tempfile.TemporaryDirectory() as tmp:
        tmp_base = Path(tmp) / "伏笔"
        write_store(tmp_base, data)
        back = read_store(tmp_base)
        if PD.canonical(_normalized(back)) != PD.canonical(_normalized(data)):
            raise LedgerError(["迁移回读比对不一致，已中止（旧文件未改动）"])

    needs_review = bool(llm_ids or quarantined_raw or sv.applied_lost or notes)
    summary: Dict[str, Any] = {
        "status": "dry_run" if dry_run else ("needs_review" if needs_review and not confirm else "migrated"),
        "items": len(items), "origins": origins, "quarantined": len(quarantined_raw),
        "applied_chapters": len(applied), "applied_lost": sv.applied_lost,
        "notes": notes, "llm_ids": llm_ids, "needs_review": needs_review, "legacy": str(legacy_path),
    }
    if dry_run:
        summary["message"] = f"将迁移 {len(items)} 条伏笔；隔离 {len(quarantined_raw)} 条；" \
                             + ("需要复核后再确认提交" if needs_review else "可直接提交")
        return summary
    if needs_review and not confirm:
        report = base.parent / "伏笔迁移报告.md"
        atomic_write_text(report, _review_report(summary, quarantined_raw))
        summary["report"] = str(report)
        summary["message"] = "存在需要复核的内容（LLM 修复 / 隔离 / 结构问题），未提交。请查看报告后以 confirm=True 确认。"
        return summary

    write_store(base, data)
    qdir = base / "_quarantine"
    if quarantined_raw:
        qdir.mkdir(parents=True, exist_ok=True)
        for i, raw in enumerate(quarantined_raw, start=1):
            atomic_write_text(qdir / f"第{i:02d}条_原文.md", "# 迁移时隔离的原文（未进入账本）\n\n" + raw + "\n")
    if register_path is not None:
        atomic_write_json(Path(register_path), data)
    backup = legacy_path.with_name(legacy_path.name + ".bak")
    if backup.exists():
        backup.unlink()
    shutil.move(str(legacy_path), str(backup))
    if needs_review:
        atomic_write_text(base.parent / "伏笔迁移报告.md", _review_report(summary, quarantined_raw))
    summary["backup"] = str(backup)
    summary["message"] = f"已迁移 {len(items)} 条伏笔" + (f"，隔离 {len(quarantined_raw)} 条" if quarantined_raw else "") \
                         + f"；旧文件已备份为 {backup.name}"
    return summary
