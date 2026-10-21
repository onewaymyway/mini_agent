"""novel_writer/blueprint_store.py — 全书蓝图的小文件存储（第五阶段）。

布局（均在 大纲规划/蓝图/ 下）：
- _meta.md        蓝图顶层信息（除 chapters 以外的全部顶层字段，如 meta、structure）。
- 第NNN章.md      每章一个文件。文件头存章节的全部字段；正文由系统生成。
- _index.md       自动生成的章节索引（每章一行），是下游步骤的依赖路径。
- _draft.json     plan_blueprint 的整本产出（LLM 写出的草稿）；由 blueprint_split 拆分成上面的文件。

读取统一通过 load_or_none()，它返回与旧版 小说蓝图.json 完全相同结构的字典，
所以各读取方只需替换读取方式，不需要改数据结构。
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer import kvfile as KV
from novel_writer.fsutil import atomic_write_text

META_FILE = "_meta.md"
INDEX_NAME = "_index.md"
CHAPTER_RE = re.compile(r"^第(\d+)章\.md$")
CHAPTER_ORDER = ("id", "title", "function", "chapter_type", "pov", "tension_level", "word_budget",
                 "characters_present")
META_ORDER = ("meta", "structure")

LAYOUT_RULES = """【蓝图文件格式（必须严格遵守）】
- _meta.md：文件头为顶层字段（除 chapters 外），例如：
  ---
  meta: {"target_length":16000}
  structure: {"total_chapters":30,"acts":[...]}
  ---
- 第NNN章.md（NNN 为三位章号，与 id 一致）：文件头为该章的全部字段，例如：
  ---
  id: 3
  title: "第3章 雨夜"
  function: "主角被迫入局"
  tension_level: 6
  word_budget: 4000
  characters_present: ["李凌云","王道长"]
  ---
- 规则：文件头每行一个字段，格式为 key: JSON值，JSON 值必须写在同一行，合法 JSON（字符串用双引号，末尾不要逗号）。
- id 必须是章号整数，全书唯一；章号与文件名必须一致。"""


class BlueprintStoreError(Exception):
    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _chapter_file(no: int) -> str:
    return f"第{no:03d}章.md"


def split_parts(bp: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """整本蓝图 -> (顶层字段, 按章号排序的章节列表)。不合格直接抛 BlueprintStoreError。"""
    if not isinstance(bp, dict):
        raise BlueprintStoreError(["蓝图顶层必须是对象"])
    chapters = bp.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise BlueprintStoreError(["蓝图缺少非空的 chapters 列表"])
    problems: List[str] = []
    seen: Dict[int, int] = {}
    for i, ch in enumerate(chapters, start=1):
        if not isinstance(ch, dict):
            problems.append(f"第 {i} 条章节条目必须是对象")
            continue
        no = ch.get("id")
        if not _is_int(no) or no < 1:
            problems.append(f"第 {i} 条章节的 id 必须是正整数，当前为 {no!r}")
            continue
        if no in seen:
            problems.append(f"章号 {no} 重复出现")
            continue
        seen[no] = i
    if problems:
        raise BlueprintStoreError(problems)
    meta = {k: v for k, v in bp.items() if k != "chapters"}
    return meta, sorted((dict(c) for c in chapters), key=lambda c: c["id"])


def _chapter_text(ch: Dict[str, Any]) -> str:
    body = [f"# 第{ch['id']}章 {ch.get('title', '')}".rstrip(), "",
            "> 本文件由系统生成正文，结构化字段以文件头为准，修改请改文件头。", ""]
    if ch.get("function"):
        body.append(f"- 功能：{ch['function']}")
    if ch.get("tension_level") is not None:
        body.append(f"- 张力：{ch['tension_level']}")
    if ch.get("word_budget") is not None:
        body.append(f"- 字数预算：{ch['word_budget']}")
    if ch.get("characters_present"):
        body.append(f"- 出场：{'、'.join(str(x) for x in ch['characters_present'])}")
    return KV.encode(ch, CHAPTER_ORDER) + "\n".join(body) + "\n"


def _meta_text(meta: Dict[str, Any]) -> str:
    return KV.encode(meta, META_ORDER) + "# 蓝图结构（自动生成）\n\n章节表见同目录的 第NNN章.md，索引见 _index.md。\n"


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("|", "／").replace("\n", " ")


def fingerprint(meta: Dict[str, Any], chapters: List[Dict[str, Any]]) -> str:
    """全书内容指纹：任何字段变化都会改变它。索引里带上它，下游的过期判断才不会漏掉非索引字段的改动。"""
    import hashlib
    raw = json.dumps({"meta": meta, "chapters": chapters}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def index_text(meta: Dict[str, Any], chapters: List[Dict[str, Any]]) -> str:
    lines = [
        "# 章节索引（自动生成，请勿手动编辑）",
        "",
        f"共 {len(chapters)} 章　内容指纹：{fingerprint(meta, chapters)}（全书任何字段变化都会改变它）",
        "",
        "| 章 | 标题 | 功能 | 字数预算 | 张力 | 出场人物 |",
        "|---|---|---|---|---|---|",
    ]
    for ch in chapters:
        present = "、".join(str(x) for x in (ch.get("characters_present") or []))
        cells = [ch["id"], ch.get("title"), (ch.get("function") or "")[:40], ch.get("word_budget"),
                 ch.get("tension_level"), present]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def _files(base: Path) -> List[Path]:
    base = Path(base)
    if not base.is_dir():
        return []
    return sorted((f for f in base.iterdir() if CHAPTER_RE.match(f.name)),
                  key=lambda f: int(CHAPTER_RE.match(f.name).group(1)))


def write_store(base: Path, index_path: Path, bp: Any) -> bool:
    """把整本蓝图拆成文件并重生成索引。只改动确实变化的文件；返回是否有任何文件变化。"""
    base, index_path = Path(base), Path(index_path)
    meta, chapters = split_parts(bp)
    base.mkdir(parents=True, exist_ok=True)
    changed = False

    def put(path: Path, text: str) -> None:
        nonlocal changed
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)
            changed = True

    put(base / META_FILE, _meta_text(meta))
    wanted = set()
    for ch in chapters:
        name = _chapter_file(ch["id"])
        wanted.add(name)
        put(base / name, _chapter_text(ch))
    for f in _files(base):
        if f.name not in wanted:
            f.unlink()
            changed = True
    put(index_path, index_text(meta, chapters))
    return changed


def read_store(base: Path) -> Dict[str, Any]:
    """读取整本蓝图，结构与旧版 小说蓝图.json 一致。任何问题都抛 BlueprintStoreError，并点名文件。"""
    base = Path(base)
    problems: List[str] = []
    meta: Dict[str, Any] = {}
    meta_path = base / META_FILE
    if not meta_path.is_file():
        raise BlueprintStoreError([f"蓝图缺少 {META_FILE}"])
    try:
        meta = KV.decode(meta_path.read_text(encoding="utf-8"), META_FILE)
    except KV.KVFormatError as exc:
        problems.extend(exc.problems)
    chapters: List[Dict[str, Any]] = []
    seen: Dict[int, str] = {}
    for f in _files(base):
        no = int(CHAPTER_RE.match(f.name).group(1))
        try:
            ch = KV.decode(f.read_text(encoding="utf-8"), f.name)
        except KV.KVFormatError as exc:
            problems.extend(exc.problems)
            continue
        if ch.get("id") != no:
            problems.append(f"{f.name}：文件头 id={ch.get('id')!r} 与文件名章号 {no} 不一致")
            continue
        if no in seen:
            problems.append(f"章号 {no} 重复（{seen[no]} 与 {f.name}）")
            continue
        seen[no] = f.name
        chapters.append(ch)
    if not chapters:
        problems.append("蓝图没有任何章节文件")
    if problems:
        raise BlueprintStoreError(problems)
    return {**meta, "chapters": chapters}


def is_store(index_path: Path) -> bool:
    return Path(index_path).is_file()


def load_or_none(base: Path, index_path: Path, legacy_path: Path) -> Optional[Dict[str, Any]]:
    """已拆分则读文件；仍是旧版 小说蓝图.json 则报错提示迁移；都没有返回 None。"""
    if is_store(index_path):
        return read_store(base)
    if Path(legacy_path).is_file():
        raise BlueprintStoreError(["旧版蓝图（小说蓝图.json）尚未迁移，请先运行蓝图迁移"])
    return None


# ── 迁移（旧版 小说蓝图.json → 拆分文件） ─────────────────────────────

def _canon(bp: Dict[str, Any]) -> str:
    """规范化后比较：章节按章号排序，键排序。"""
    meta, chapters = split_parts(bp)
    return json.dumps({"meta": meta, "chapters": chapters}, ensure_ascii=False, sort_keys=True)


def staging_validator(out: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    try:
        return read_store(out), []
    except BlueprintStoreError as exc:
        return None, exc.problems


def llm_convert_legacy(legacy_path: Path, workdir: Path, invoker: Callable[..., Any], max_attempts: int = 3, reuse_only: bool = False) -> Any:
    from novel_writer import llm_convert as _LCX
    if reuse_only:
        cand = _LCX.load_candidate(workdir, legacy_path, staging_validator)
        return cand or _LCX.ConvertResult(ok=False, attempts=0, errors=['没有与当前旧文件匹配的待确认转换结果（旧文件已变化，或尚未运行干跑检查）。请先点击「检查」生成转换结果，核对后再确认提交。'])
    """损坏的旧版蓝图：让 LLM（ledger-convert）直接读取原文写出拆分文件，经严格校验后返回。"""
    from novel_writer import llm_convert as LC
    text = Path(legacy_path).read_bytes().decode("utf-8", errors="replace")
    res = LC.convert_with_llm(
        source=Path(legacy_path), workdir=Path(workdir), invoker=invoker, skill="ledger-convert",
        instructions=("【任务】把损坏的旧版蓝图（小说蓝图.json）转换为蓝图文件。请在写出目录下写出 _meta.md 与 "
                      "各章的 第NNN章.md，不要写其它文件。不要改动章节内容与字段值。\n" + LAYOUT_RULES),
        validate=staging_validator, max_attempts=max_attempts, step_id="blueprint_convert",
    )
    if res.ok:
        hint = len(set(LC.id_count_hint(text, r'"id"\s*:\s*(\d+)')))
        if hint and len(res.data.get("chapters", [])) < hint:
            res.warnings.append(f"疑似遗漏：原文中出现 {hint} 个章号，转换得到 {len(res.data.get('chapters', []))} 章")
    if res.ok:
        _LCX.save_candidate(workdir, legacy_path, res)
    return res


def migrate_legacy(legacy_path: Path, base: Path, index_path: Path, *, dry_run: bool = False,
                   convert: Optional[Callable[[Path], Any]] = None, confirm: bool = False,
                   draft_path: Optional[Path] = None) -> Dict[str, Any]:
    """旧版 小说蓝图.json -> 拆分文件。合法则确定性拆分并回读比对；损坏则 LLM 转换，需 confirm 才提交。
    旧文件不删除，提交后改名为 .bak。"""
    legacy_path, base, index_path = Path(legacy_path), Path(base), Path(index_path)
    if not legacy_path.is_file():
        return {"status": "no_legacy"}
    if is_store(index_path):
        return {"status": "already_migrated"}

    bp = None
    problems: List[str] = []
    try:
        bp = json.loads(legacy_path.read_bytes().decode("utf-8", errors="replace"))
        split_parts(bp)
    except ValueError as exc:
        problems = [f"旧版蓝图不是合法 JSON（{exc}）"]
    except BlueprintStoreError as exc:
        problems = exc.problems

    def commit(data: Dict[str, Any]) -> None:
        write_store(base, index_path, data)
        if draft_path is not None:
            # 草稿副本：plan_blueprint 的产出存在，迁移后不会被误判为缺失而整本重跑
            atomic_write_text(Path(draft_path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        legacy_path.rename(legacy_path.with_name(legacy_path.name + ".bak"))

    if not problems:
        tmp = Path(tempfile.mkdtemp(prefix="bp_rt_"))
        try:
            write_store(tmp / "蓝图", tmp / "蓝图" / INDEX_NAME, bp)
            back = read_store(tmp / "蓝图")
        except BlueprintStoreError as exc:
            problems = exc.problems
        else:
            if _canon(back) != _canon(bp):
                return {"status": "mismatch", "problems": ["回读比对不一致，已中止，旧文件未改动"]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not problems:
            if dry_run:
                return {"status": "dry_run", "chapters": len(bp["chapters"]), "via": "deterministic"}
            commit(bp)
            return {"status": "migrated", "chapters": len(bp["chapters"]), "via": "deterministic"}

    if convert is None:
        return {"status": "needs_llm", "problems": problems}
    res = convert(legacy_path)
    if not res.ok:
        return {"status": "llm_failed", "problems": res.errors, "attempts": res.attempts}
    report = {"chapters": len(res.data.get("chapters", [])), "via": "llm", "warnings": list(res.warnings),
              "staging": str(res.staging) if res.staging else None}
    if dry_run or not confirm:
        return {"status": "review_required", **report}
    commit(res.data)
    return {"status": "migrated", **report}


# ── 与 Workspace 对接的便捷入口（读写方统一走这里） ─────────────────────

def load_for_ws(ws: Any, strict: bool) -> Optional[Dict[str, Any]]:
    """strict=True：问题直接抛出；strict=False：问题时返回 None（界面 / 状态展示用）。"""
    try:
        return load_or_none(ws.path("blueprint_dir"), ws.path("blueprint"), ws.path("blueprint_legacy"))
    except BlueprintStoreError:
        if strict:
            raise
        return None


def save_for_ws(ws: Any, bp: Dict[str, Any]) -> bool:
    return write_store(ws.path("blueprint_dir"), ws.path("blueprint"), bp)
