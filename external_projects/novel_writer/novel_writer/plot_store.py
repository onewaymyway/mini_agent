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
from typing import Any, Callable, Dict, List, Optional, Tuple

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


# ── 迁移 ────────────────────────────────────────────────────────────

ALLOWED_STATUS = ("planted", "advanced", "resolved")

# LLM 转换的目标布局说明（写入技能提示词）。这是唯一的格式契约，校验函数与之一致。
LAYOUT_INSTRUCTIONS = """【目标布局】在写出目录下创建一个名为「伏笔」的目录，内容如下：
1. 每条伏笔一个文件 `伏笔/F<编号>.md`（编号只含字母、数字、下划线、连字符，例如 F001、F12）。
   文件格式：第一行和最后一个分隔行都是 `---`，中间每行是 `字段名: JSON值`（值必须是单行合法 JSON），然后是一个 `---` 闭合行。
   引号规则（最常见的错误来源）：字符串值必须用英文双引号包裹；字符串内容里如需引号，一律使用中文「」『』，不要使用英文双引号（不要做转义）。
   例如：name: "他说「走吧」"，而不是 name: "他说"走吧""。
   必需字段：`id`（与文件名一致，字符串）、`name`（字符串）、`status`（只能是 planted / advanced / resolved）、
   `plant_chapter`（整数章号，未知则 null）、`resolve_chapter`（整数章号或 null）、`progress`（数组，元素为 {"chapter": 整数, "note": 字符串}，可为空数组）。
   可以额外保留原文中的其它字段（如 `note`、`description`），值同样用单行 JSON。
   示例：
   ---
   id: "F001"
   name: "玉佩"
   status: "advanced"
   plant_chapter: 3
   resolve_chapter: null
   progress: [{"chapter":4,"note":"玉佩在市集出现"}]
   ---
   # F001 玉佩
2. 若原文记录了章节增量的撤销信息，可写入 `伏笔/_applied/第NN章.md`（NN 为两位章号），文件头字段为
   `chapter`（整数）、`before`（对象，记录该章变更前的各伏笔状态，未出现的伏笔值为 null）、`delta`（对象，该章增量）。无法确定则不写。
3. 不要写 `_index.md`（由系统生成）。
4. 不要遗漏原文中的任何伏笔；无法确定的字段填 null，不要编造内容。
"""


def _collect_file_problems(base: Path) -> List[str]:
    """逐个检查全部伏笔文件与章节增量文件，收集所有格式问题（一次反馈给 LLM，避免每次重试只修一个）。"""
    problems: List[str] = []
    seen: Dict[str, str] = {}
    for f in _item_files(base):
        try:
            fields = _decode_fields(f.read_text(encoding="utf-8"), f.name)
        except LedgerError as exc:
            problems.extend(exc.problems)
            continue
        iid = str(fields.get("id") or "")
        if iid != f.stem:
            problems.append(f"{f.name}：文件头 id={iid!r} 与文件名不一致")
        elif iid in seen:
            problems.append(f"伏笔 id 重复：{iid}（{seen[iid]} 与 {f.name}）")
        else:
            seen[iid] = f.name
    for f in _applied_files(base):
        try:
            _decode_fields(f.read_text(encoding="utf-8"), f"{APPLIED_DIR}/{f.name}")
        except LedgerError as exc:
            problems.extend(exc.problems)
    return problems


def _plot_stage_validator(source_text: str) -> Callable[[Path], Tuple[Optional[Dict[str, Any]], List[str]]]:
    def validate(out: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        base = Path(out) / "伏笔"
        if not base.is_dir():
            return None, [f"没有写出「伏笔」目录（应在 {out} 下创建 伏笔/）"]
        file_problems = _collect_file_problems(base)
        if file_problems:
            return None, file_problems
        try:
            data = read_store(base)
        except LedgerError as exc:
            return None, exc.problems
        errors: List[str] = []
        items = data.get(ITEM_KEY, [])
        if not items:
            errors.append("没有写出任何伏笔文件（伏笔/F*.md）")
        for it in items:
            iid = it.get("id")
            if not isinstance(it.get("name"), str):
                errors.append(f"{iid}：name 必须是字符串")
            if it.get("status") not in ALLOWED_STATUS:
                errors.append(f"{iid}：status 必须是 planted / advanced / resolved，当前为 {it.get('status')!r}")
            for ck in ("plant_chapter", "resolve_chapter"):
                if it.get(ck) is not None and not (isinstance(it.get(ck), int) and not isinstance(it.get(ck), bool)):
                    errors.append(f"{iid}：{ck} 必须是整数或 null")
            prog = it.get("progress")
            if prog is not None and (not isinstance(prog, list) or not all(
                    isinstance(p, dict) and isinstance(p.get("chapter"), int) for p in prog)):
                errors.append(f"{iid}：progress 必须是 {{chapter, note}} 对象的数组")
        return data, errors
    return validate



_LIST_KEYS = ("foreshadowing", "threads", "items")
_OUT_OF_SCOPE_SECTIONS = ("meta", "plot_threads", "tension_curve", "chapter_hooks", "health_report")


def _legacy_schema_info(legacy_path: Path) -> Tuple[Optional[str], List[str]]:
    """识别「以 id 为键的伏笔登记表」这类旧结构（如 foreshadowing_registry）。

    返回 (问题说明或 None, 不在迁移范围内的顶层章节名)。确定性迁移不猜测字段映射，
    所以这类结构只能交给 LLM 转换并复核。读取失败时返回 (None, [])，由正常的损坏路径处理。
    """
    try:
        raw = json.loads(Path(legacy_path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None, []
    if not isinstance(raw, dict) or any(isinstance(raw.get(k), list) for k in _LIST_KEYS):
        return None, []
    if not isinstance(raw.get("foreshadowing_registry"), dict):
        return None, []
    extras = [k for k in raw if k != "foreshadowing_registry" and k in _OUT_OF_SCOPE_SECTIONS]
    problem = ("旧账本使用 foreshadowing_registry（以 id 为键的字典）结构，字段与当前存储不同"
               "（如 planted_chapter、status 取值），确定性迁移不会猜测字段映射。"
               "请以 use_llm=True 由 LLM 按目标结构转换并复核。")
    return problem, extras

def migrate_legacy(legacy_path: Path, base: Path, *, dry_run: bool = False,
                   register_path: Optional[Path] = None,
                   convert: Optional[Callable[[Path], Any]] = None,
                   confirm: bool = False) -> Dict[str, Any]:
    """把旧版 JSON 账本迁移为小文件存储。

    - 旧文件合法：确定性迁移，自动提交。
    - 旧文件损坏：若提供 convert（通常是 LLM 直接转换），由它读取原文生成小文件，经严格校验后作为候选；
      这种结果一律需要复核，生成「伏笔迁移报告.md」，confirm=True 才提交。未提供 convert 则返回 needs_llm。
    旧文件始终不删除；提交后改名为 .bak。
    """
    legacy_path, base = Path(legacy_path), Path(base)
    if not legacy_path.is_file():
        return {"status": "nothing", "message": "没有旧版伏笔账本，无需迁移"}
    if is_store(base):
        return {"status": "already", "message": "伏笔账本已是小文件格式，无需迁移"}

    warnings: List[str] = []
    source = "json"
    schema_problem, extra_sections = _legacy_schema_info(legacy_path)
    try:
        data = PD.load_tracker_strict(legacy_path)
        if schema_problem:
            raise PD.PlotDeltaError([schema_problem])
    except PD.PlotDeltaError as exc:
        if convert is None:
            return {"status": "needs_llm", "legacy_problem": exc.problems,
                    "message": "旧账本已损坏，无法直接读取。请以 use_llm=True 运行迁移，由 LLM 直接读取原文并生成小文件（之后需复核）"}
        res = convert(legacy_path)
        if not res.ok or res.data is None:
            return {"status": "failed", "legacy_problem": exc.problems, "errors": res.errors,
                    "attempts": res.attempts,
                    "message": f"LLM 转换失败（已尝试 {res.attempts} 次），旧文件未改动。最后的问题：" + "；".join(res.errors[:5])}
        data = res.data
        source = "llm"
        warnings = list(res.warnings)
        if extra_sections:
            warnings.append("以下旧章节不在伏笔迁移范围内，未写入新存储（完整内容仍保留在 .bak 备份中）：" + "、".join(extra_sections))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_base = Path(tmp) / "伏笔"
        write_store(tmp_base, data)
        back = read_store(tmp_base)
        if PD.canonical(_normalized(back)) != PD.canonical(_normalized(data)):
            raise LedgerError(["迁移回读比对不一致，已中止（旧文件未改动）"])

    items = data.get(ITEM_KEY, [])
    applied = data.get(PD.APPLIED_KEY) or {}
    needs_review = source == "llm" or bool(warnings)
    summary: Dict[str, Any] = {
        "status": "dry_run" if dry_run else ("needs_review" if needs_review and not confirm else "migrated"),
        "source": source, "items": len(items), "applied_chapters": len(applied),
        "item_ids": [str(i.get("id")) for i in items], "warnings": warnings,
        "needs_review": needs_review, "legacy": str(legacy_path),
    }
    if dry_run:
        summary["message"] = f"将迁移 {len(items)} 条伏笔（来源：{'LLM 转换' if source == 'llm' else '原 JSON'}）；" \
                             + ("需要复核后确认提交" if needs_review else "可直接提交")
        return summary
    if needs_review and not confirm:
        report = base.parent / "伏笔迁移报告.md"
        lines = ["# 伏笔账本迁移报告", "",
                 f"- 来源：{'LLM 直接转换（旧文件已损坏）' if source == 'llm' else '原 JSON'}",
                 f"- 伏笔条数：{len(items)}；已记录的章节增量：{len(applied)}", ""]
        if warnings:
            lines += ["## 提示"] + [f"- {w}" for w in warnings] + [""]
        lines += ["## 转换结果（请逐条核对）"] + [f"- {i.get('id')} {i.get('name', '')}（{i.get('status')}）" for i in items]
        lines += ["", "确认无误后，以 confirm=True 重新执行迁移即可提交。"]
        atomic_write_text(report, "\n".join(lines) + "\n")
        summary["report"] = str(report)
        summary["message"] = "由 LLM 转换的结果需要复核，未提交。请查看报告后以 confirm=True 确认。"
        return summary

    write_store(base, data)
    if register_path is not None:
        atomic_write_json(Path(register_path), data)
    backup = legacy_path.with_name(legacy_path.name + ".bak")
    if backup.exists():
        backup.unlink()
    shutil.move(str(legacy_path), str(backup))
    summary["backup"] = str(backup)
    summary["message"] = f"已迁移 {len(items)} 条伏笔；旧文件已备份为 {backup.name}"
    return summary


def llm_convert_legacy(legacy_path: Path, workdir: Path, invoker: Callable[..., Any],
                       max_attempts: int = 3) -> Any:
    """供 migrate_legacy 使用的 LLM 转换：让技能 ledger-convert 直接读取损坏原文，校验后返回结果。"""
    from novel_writer import llm_convert as LC
    text = Path(legacy_path).read_bytes().decode("utf-8", errors="replace")
    res = LC.convert_with_llm(
        source=Path(legacy_path), workdir=Path(workdir), invoker=invoker, skill="ledger-convert",
        instructions=LAYOUT_INSTRUCTIONS, validate=_plot_stage_validator(text),
        max_attempts=max_attempts, step_id="ledger_convert",
    )
    if res.ok:
        hint_ids = set(LC.id_count_hint(text))
        if hint_ids and len(res.data.get(ITEM_KEY, [])) < len(hint_ids):
            res.warnings.append(f"疑似遗漏：原文中出现 {len(hint_ids)} 个不同的 id，转换得到 {len(res.data.get(ITEM_KEY, []))} 条")
    return res
