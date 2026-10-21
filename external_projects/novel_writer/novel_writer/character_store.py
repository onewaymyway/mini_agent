"""novel_writer/character_store.py — 人物档案的小文件存储（第四阶段）。

布局：
- 大纲规划/人物设定/角色/{id}.md   每个角色一个文件。文件头用 `key: <JSON值>` 逐行保存全部结构化字段；
                                     正文由系统生成，仅供阅读。
- 大纲规划/人物设定/角色索引.md     自动生成的紧凑索引（每个角色一行）。写作时只读它与本章出场角色的文件。

写入方式：
- 同步步骤（精品档 / 草稿档）由 LLM **直接编辑本章出场角色的文件**，不再整体重写 characters.json。
- 随后的 `characters_refresh` 步骤（确定性 Python）统一校验全部角色文件，重生成正文与索引；
  内容没有实质变化则不写盘，所以下游步骤不会因为无变化而整体过期。
- 产出校验（output_check = character_files）让 LLM 在写错时带着具体问题重试。

迁移：旧版 characters.json 合法则确定性迁移（回读比对一致才提交，原文件改名为 .bak）；
损坏则由 LLM（ledger-convert）直接读取原文写出角色文件，经同一套严格校验，结果一律需要复核。
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

ITEM_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
INDEX_NAME = "角色索引.md"          # 位于角色目录之外，避免被 `角色/*.md` 的产出 glob 计入
FIELD_ORDER = ("id", "name", "role", "introduced_chapter", "identity", "psychology", "behavior_guide",
               "abilities", "arc", "state_log", "current_state", "relationships")
DICT_FIELDS = ("identity", "psychology", "behavior_guide", "abilities", "arc", "current_state", "relationships")

# 给 LLM 的统一格式说明（同步 / 建档 / 迁移共用，只此一处定义）
LAYOUT_RULES = """【角色文件格式（必须严格遵守）】
每个角色一个文件 {id}.md，结构如下（示例字段值仅供参考）：
---
id: "char_001"
name: "李凌云"
role: "protagonist"
introduced_chapter: 1
identity: {"one_line_essence":"用强大武装自己的孤独者","age":17,"appearance":"身形单薄","signature_detail":"左手旧伤疤"}
psychology: {"core_desire":"证明自身价值","core_fear":"被抛弃","speech_pattern":"话少","stress_response":"沉默独自承担"}
behavior_guide: {"will_do":["无路可退时迸发意志力"],"wont_do":["无原则向敌人求饶"]}
abilities: {"current_level":"炼气第七层","special_skills":["剑法天赋异禀"],"weaknesses":["不擅团战"]}
arc: {"type":"成长弧","start_belief":"情感是弱点","end_belief":"接受自己的脆弱","cost":"放弃自我保护壳","current_stage":"扭曲信念期"}
state_log: [{"chapter":8,"status":"晋升内门弟子","key_change":"初次展现真实实力","emotional_state":"略有松动","relationship_changes":["与王道长：陌生→微妙"]}]
current_state: {"as_of_chapter":8,"location":"青云峰","physical":"轻伤已愈","emotional":"略感希望","known_secrets":["体质特殊"],"active_goals":["突破炼气期"]}
relationships: {"char_002":{"type":"师徒","planned_evolution":"陌生→微妙→信任→揭秘","current_actual":"微妙","tension":"低","last_updated_chapter":8}}
---
（正文可留空；系统会自动生成正文。）
规则：
1. 文件头每行一个字段，格式为 `key: JSON值`。JSON 值必须写在同一行，使用合法 JSON（字符串用双引号，不要尾逗号）。
2. id 必须与文件名一致；name 必须存在，且全书唯一。
3. state_log 中同一章只能有一条：更新某章时替换该章的条目，不要追加重复条目。
4. 未涉及的字段保持原样，不要删除。"""


class CharacterStoreError(Exception):
    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


# ── 单文件编解码 ─────────────────────────────────────────────────────

def _encode(rec: Dict[str, Any]) -> str:
    try:
        return KV.encode(rec, FIELD_ORDER)
    except KV.KVFormatError as exc:
        raise CharacterStoreError(exc.problems)


def _decode(text: str, where: str) -> Dict[str, Any]:
    try:
        return KV.decode(text, where)
    except KV.KVFormatError as exc:
        raise CharacterStoreError(exc.problems)


def _body(rec: Dict[str, Any]) -> str:
    name = rec.get("name") or rec.get("id")
    lines = [f"# {name}（{rec.get('id')}）", "", "> 本文件由系统生成正文，结构化字段以文件头为准，修改请改文件头。", ""]
    if rec.get("role"):
        lines.append(f"- 角色定位：{rec['role']}")
    ident = rec.get("identity") if isinstance(rec.get("identity"), dict) else {}
    if ident.get("one_line_essence"):
        lines.append(f"- 一句话：{ident['one_line_essence']}")
    cs = rec.get("current_state") if isinstance(rec.get("current_state"), dict) else {}
    if cs:
        lines.append(f"- 当前（第 {cs.get('as_of_chapter')} 章）：{cs.get('location', '')}，{cs.get('emotional', '')}")
    for e in rec.get("state_log") or []:
        if isinstance(e, dict):
            lines.append(f"- 第 {e.get('chapter')} 章：{e.get('status', '')}｜{e.get('key_change', '')}")
    return "\n".join(lines) + "\n"


# ── 校验 ─────────────────────────────────────────────────────────────

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def record_problems(rec: Any, where: str, expected_id: Optional[str] = None) -> List[str]:
    if not isinstance(rec, dict):
        return [f"{where}：不是对象"]
    p: List[str] = []
    cid = rec.get("id")
    if not isinstance(cid, str) or not ITEM_ID.match(cid):
        p.append(f"{where}：id 不合法（只允许字母、数字、下划线、连字符）")
    elif expected_id is not None and cid != expected_id:
        p.append(f"{where}：文件头 id={cid!r} 与文件名/键 {expected_id!r} 不一致")
    if not isinstance(rec.get("name"), str) or not rec["name"].strip():
        p.append(f"{where}：缺少 name（角色名）")
    for k in DICT_FIELDS:
        if k in rec and not isinstance(rec[k], dict):
            p.append(f"{where}：{k} 应为对象")
    bg = rec.get("behavior_guide")
    if isinstance(bg, dict):
        for k in ("will_do", "wont_do"):
            if k in bg and not (isinstance(bg[k], list) and all(isinstance(x, str) for x in bg[k])):
                p.append(f"{where}：behavior_guide.{k} 应为字符串数组")
    ic = rec.get("introduced_chapter")
    if ic is not None and not (_is_int(ic) and ic >= 1):
        p.append(f"{where}：introduced_chapter 应为正整数或 null")
    cs = rec.get("current_state")
    if isinstance(cs, dict) and cs.get("as_of_chapter") is not None and not _is_int(cs.get("as_of_chapter")):
        p.append(f"{where}：current_state.as_of_chapter 应为整数")
    log = rec.get("state_log", [])
    if not isinstance(log, list):
        p.append(f"{where}：state_log 应为数组")
    else:
        seen: set = set()
        for i, e in enumerate(log, start=1):
            ch = e.get("chapter") if isinstance(e, dict) else None
            if not _is_int(ch) or ch < 1:
                p.append(f"{where}：state_log 第 {i} 条缺少合法的 chapter（正整数）")
            elif ch in seen:
                p.append(f"{where}：state_log 中第 {ch} 章出现了两次（同一章只能有一条，应替换而不是追加）")
            else:
                seen.add(ch)
    rels = rec.get("relationships")
    if isinstance(rels, dict):
        for other, val in rels.items():
            if not isinstance(val, dict):
                p.append(f"{where}：relationships.{other} 应为对象")
    return p


def _files(base: Path) -> List[Path]:
    base = Path(base)
    if not base.is_dir():
        return []
    return sorted(f for f in base.glob("*.md") if ITEM_ID.match(f.stem))


def load_records(base: Path) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """逐个读取全部角色文件，收集全部问题（不在第一个错误处停止，便于一次性反馈给 LLM）。"""
    chars: Dict[str, Dict[str, Any]] = {}
    names: Dict[str, str] = {}
    problems: List[str] = []
    for f in _files(base):
        try:
            fields = _decode(f.read_text(encoding="utf-8"), f.name)
        except CharacterStoreError as exc:
            problems.extend(exc.problems)
            continue
        probs = record_problems(fields, f.name, expected_id=f.stem)
        # 角色名查重独立于其他字段的校验：即使本文件有别的错误，同名问题也要报出来
        nm = fields.get("name").strip() if isinstance(fields.get("name"), str) else ""
        if nm and nm in names:
            probs.append(f"角色名重复：{nm!r}（{names[nm]} 与 {f.name}）")
        elif nm:
            names[nm] = f.name
        if probs:
            problems.extend(probs)
            continue
        chars[fields["id"]] = fields
    return chars, problems


def read_store(base: Path) -> Dict[str, Dict[str, Any]]:
    """读取全部角色（{id: 记录}）。任何问题都抛 CharacterStoreError，并点名文件。"""
    chars, problems = load_records(base)
    if problems:
        raise CharacterStoreError(problems)
    return chars


def problems_for_dir(base: Path) -> List[str]:
    """产出校验用：目录里必须至少有一个合格的角色文件。"""
    if not _files(base):
        return ["角色目录中没有任何角色文件"]
    _, problems = load_records(base)
    return problems


# ── 目录级写入 ───────────────────────────────────────────────────────

def _cell(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("|", "／").replace("\n", " ")


def index_text(chars: Dict[str, Dict[str, Any]]) -> str:
    items = sorted(chars.values(), key=lambda r: str(r.get("id")))
    lines = [
        "# 人物索引（自动生成，请勿手动编辑）",
        "",
        f"共 {len(items)} 个角色",
        "",
        "| id | 姓名 | 角色 | 首次出场 | 最新章 | 位置 | 情绪 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in items:
        cs = r.get("current_state") if isinstance(r.get("current_state"), dict) else {}
        cells = [r.get("id"), r.get("name"), r.get("role"), r.get("introduced_chapter"),
                 cs.get("as_of_chapter"), cs.get("location"), cs.get("emotional")]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    lines += ["", "各角色详情见同目录下的 {id}.md 文件。"]
    return "\n".join(lines) + "\n"


def write_store(base: Path, index_path: Path, chars: Dict[str, Dict[str, Any]]) -> bool:
    """把全部角色写入目录并重生成索引。只改动确实变化的文件；返回是否有任何文件变化。"""
    base, index_path = Path(base), Path(index_path)
    problems: List[str] = []
    for cid, rec in chars.items():
        problems.extend(record_problems(rec, f"角色 {cid}", expected_id=cid))
    if problems:
        raise CharacterStoreError(problems)
    base.mkdir(parents=True, exist_ok=True)
    changed = False
    for cid, rec in chars.items():
        text = _encode(rec) + _body(rec)
        path = base / f"{cid}.md"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)
            changed = True
    for f in _files(base):
        if f.stem not in chars:
            f.unlink()
            changed = True
    index = index_text(chars)
    if not index_path.exists() or index_path.read_text(encoding="utf-8") != index:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(index_path, index)
        changed = True
    return changed


def refresh(base: Path, index_path: Path) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """确定性刷新：校验全部角色文件，重生成正文与索引。返回 (角色, 是否写过盘)。"""
    chars = read_store(base)
    changed = write_store(base, index_path, chars)
    return chars, changed


def is_store(index_path: Path) -> bool:
    return Path(index_path).is_file()


def load_or_none(base: Path, index_path: Path, legacy_path: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    """读取人物档案。已迁移则读小文件；仍是旧版 characters.json 则报错提示迁移；都没有返回 None。"""
    if is_store(index_path):
        return read_store(base)
    if Path(legacy_path).is_file():
        raise CharacterStoreError(["旧版人物档案（characters.json）尚未迁移，请先运行人物档案迁移"])
    return None


# ── 写作上下文：只给出场角色的摘要 ───────────────────────────────────

def brief_for(chars: Dict[str, Dict[str, Any]], names: List[str]) -> str:
    """只输出本章出场角色的紧凑摘要（不再把整本人物档案塞进上下文）。"""
    by_name = {str(r.get("name", "")).strip(): r for r in chars.values()}
    out: List[str] = []
    for nm in names:
        r = by_name.get(nm)
        if r is None:
            out.append(f"- {nm}：（尚无档案；若为首次出场，请在本章同步时建档）")
            continue
        ident = r.get("identity") if isinstance(r.get("identity"), dict) else {}
        cs = r.get("current_state") if isinstance(r.get("current_state"), dict) else {}
        ps = r.get("psychology") if isinstance(r.get("psychology"), dict) else {}
        bg = r.get("behavior_guide") if isinstance(r.get("behavior_guide"), dict) else {}
        wont = "、".join(str(x) for x in (bg.get("wont_do") or [])[:4])
        secrets = "、".join(str(x) for x in (cs.get("known_secrets") or [])[:4])
        out.append(
            f"- {nm}（{r.get('id')}，{r.get('role', '')}）：{ident.get('one_line_essence', '')}\n"
            f"  当前（第 {cs.get('as_of_chapter')} 章）：{cs.get('location', '')}，{cs.get('emotional', '')}\n"
            f"  说话：{ps.get('speech_pattern', '')}\n"
            f"  不会：{wont or '（未记录）'}；已知：{secrets or '（未记录）'}"
        )
    return "\n".join(out)


# ── 迁移（旧版 characters.json → 小文件） ─────────────────────────────

def legacy_records(legacy_path: Path) -> Tuple[Optional[Dict[str, Dict[str, Any]]], List[str]]:
    text = Path(legacy_path).read_bytes().decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, [f"旧版 characters.json 不是合法 JSON（{exc}）"]
    if not isinstance(data, dict):
        return None, ["旧版人物档案顶层应为对象（{id: 角色}）"]
    chars: Dict[str, Dict[str, Any]] = {}
    problems: List[str] = []
    for key, rec in data.items():
        if not isinstance(rec, dict):
            problems.append(f"{key}：不是对象")
            continue
        rec = dict(rec)
        rec.setdefault("id", key)
        probs = record_problems(rec, f"角色 {key}", expected_id=key)
        if probs:
            problems.extend(probs)
            continue
        chars[key] = rec
    names: Dict[str, str] = {}
    for cid, rec in chars.items():
        nm = rec["name"].strip()
        if nm in names:
            problems.append(f"角色名重复：{nm!r}（{names[nm]} 与 {cid}）")
        names[nm] = cid
    return chars, problems


def _canon(chars: Dict[str, Any]) -> str:
    return json.dumps(chars, ensure_ascii=False, sort_keys=True)


def staging_validator(out: Path) -> Tuple[Optional[Dict[str, Dict[str, Any]]], List[str]]:
    """LLM 转换结果的校验：与正式读取使用同一套严格解析。"""
    chars, problems = load_records(out)
    if not chars and not problems:
        problems = ["没有写出任何角色文件"]
    return (None, problems) if problems else (chars, [])


def llm_convert_legacy(legacy_path: Path, workdir: Path, invoker: Callable[..., Any], max_attempts: int = 3, reuse_only: bool = False) -> Any:
    from novel_writer import llm_convert as _LCX
    if reuse_only:
        cand = _LCX.load_candidate(workdir, legacy_path, staging_validator)
        return cand or _LCX.ConvertResult(ok=False, attempts=0, errors=['没有与当前旧文件匹配的待确认转换结果（旧文件已变化，或尚未运行干跑检查）。请先点击「检查」生成转换结果，核对后再确认提交。'])
    """损坏的旧版人物档案：让 LLM（ledger-convert）直接读取原文写出角色文件，经严格校验后返回。"""
    from novel_writer import llm_convert as LC
    text = Path(legacy_path).read_bytes().decode("utf-8", errors="replace")
    res = LC.convert_with_llm(
        source=Path(legacy_path), workdir=Path(workdir), invoker=invoker, skill="ledger-convert",
        instructions=("【任务】把损坏的旧版人物档案（characters.json，顶层为 {id: 角色对象}）转换为角色文件。\n"
                      "请为每个角色在写出目录下新建一个文件 {id}.md（id 与原文一致）。\n" + LAYOUT_RULES),
        validate=staging_validator, max_attempts=max_attempts, step_id="character_convert",
    )
    if res.ok:
        hint_ids = set(LC.id_count_hint(text, r'"id"\s*:\s*"([^"]+)"'))
        if hint_ids and len(res.data or {}) < len(hint_ids):
            res.warnings.append(f"疑似遗漏：原文中出现 {len(hint_ids)} 个不同的 id，转换得到 {len(res.data or {})} 个角色")
    if res.ok:
        _LCX.save_candidate(workdir, legacy_path, res)
    return res


def migrate_legacy(legacy_path: Path, base: Path, index_path: Path, *, dry_run: bool = False,
                   convert: Optional[Callable[[Path], Any]] = None, confirm: bool = False) -> Dict[str, Any]:
    """旧版 characters.json → 角色小文件。

    - 合法：确定性迁移，回读比对一致才提交（dry_run 只报告）。
    - 损坏：convert 为空则返回 needs_llm；否则由 LLM 直接转换，结果一律需要复核，confirm=True 才提交。
    旧文件始终不删除，提交后改名为 .bak。
    """
    legacy_path, base, index_path = Path(legacy_path), Path(base), Path(index_path)
    if not legacy_path.is_file():
        return {"status": "no_legacy"}
    if is_store(index_path):
        return {"status": "already_migrated"}
    chars, problems = legacy_records(legacy_path)

    def commit(data: Dict[str, Dict[str, Any]]) -> None:
        write_store(base, index_path, data)
        legacy_path.rename(legacy_path.with_name(legacy_path.name + ".bak"))

    if not problems and chars is not None:
        tmp = Path(tempfile.mkdtemp(prefix="char_rt_"))
        try:
            write_store(tmp / "角色", tmp / INDEX_NAME, chars)
            back = read_store(tmp / "角色")
        except CharacterStoreError as exc:
            problems = exc.problems
        else:
            if _canon(back) != _canon(chars):
                return {"status": "mismatch", "problems": ["回读比对不一致，已中止，旧文件未改动"]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not problems:
            if dry_run:
                return {"status": "dry_run", "count": len(chars), "via": "deterministic"}
            commit(chars)
            return {"status": "migrated", "count": len(chars), "via": "deterministic"}

    if convert is None:
        return {"status": "needs_llm", "problems": problems}
    res = convert(legacy_path)
    if not res.ok:
        return {"status": "llm_failed", "problems": res.errors, "attempts": res.attempts}
    report = {"count": len(res.data or {}), "via": "llm", "warnings": list(res.warnings),
              "staging": str(res.staging) if res.staging else None}
    if dry_run or not confirm:
        return {"status": "review_required", **report}
    commit(res.data or {})
    return {"status": "migrated", **report}
