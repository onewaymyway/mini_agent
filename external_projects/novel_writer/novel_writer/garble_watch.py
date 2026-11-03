"""novel_writer/garble_watch.py — 关键文件乱码检测：注册表、只读体检、report 模式记录（G1）。

背景（见 next_doc/novel_writer_garble_post_step_check_plan.md）：
正文乱码已有 `garble_guard` 守卫，但章节总结、全书章节索引、人物档案、伏笔账本等由 agent
顺手写出的文件没有任何检测，坏内容会被下游继续当输入使用。

本模块做三件事：
  1. **关键文件注册表**（`WATCH_GLOBS`）：只检查对下游有影响的文件，排除 `_studio/`、`导出/`、
     `_drafts/`、`*.bak` 与非文本后缀；`settings.garble_check.watch_extra` 可追加。
  2. **只读体检** `scan_garble(ws)`：逐个检查注册表文件，并给出归因（最后写入者、是否可自动重跑）。
     全程 `repair=False` 读取，**不会改写任何文件**，也不调用 LLM。
  3. **report 模式**：步骤将要结束时，比较步骤前后关键文件的指纹，只对本步骤改动过的文件检测，
     命中则写运行日志与 `_studio/inbox/` 记录。不改变步骤状态，不重跑。

判定沿用 `garble.is_garbled`，不改阈值。`mode == off` 时本模块不做任何事（`snapshot` 返回空），
行为与未引入本模块时逐字节一致。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from novel_writer import settings as settings_mod
from novel_writer.fsutil import atomic_write_json
from novel_writer.garble import is_garbled
from novel_writer.textio import TextEncodingError, read_text_utf8
from novel_writer.workspace import PATHS, Workspace

# 注册表：相对工作目录的 glob。覆盖章节总结、全书章节索引、记忆摘要、上下文包、
# 人物档案、伏笔账本、蓝图与各规划文档。新增关键文件时在这里登记即可。
WATCH_GLOBS: tuple = (
    "章节总结/*.md",                        # 章节总结 + 全书章节索引
    "项目状态/memory/ch_*.json",            # 记忆摘要（每章）
    "项目状态/memory/context_ch*.md",       # 上下文包（下游写作直接读取）
    "大纲规划/蓝图/_index.md",               # 蓝图索引
    "大纲规划/蓝图/*.md",                    # 蓝图逐章文件
    "大纲规划/人物设定/角色索引.md",
    "大纲规划/人物设定/角色/*.md",
    "大纲规划/线索设定/伏笔/_index.md",
    "大纲规划/线索设定/伏笔/*.md",
    "大纲规划/线索设定/伏笔注册表.md",
    "大纲规划/线索设定/故事线状态.md",
    "大纲规划/世界观设定/world_bible.json",
    "大纲规划/主题设计.md",
    "大纲规划/节奏规划.md",
    "大纲规划/前三章开篇设计.md",
    "大纲规划/结局设计.md",
)

# 排除：路径中出现这些目录名，或文件名以这些后缀结尾
_EXCLUDED_DIRS = ("_studio", "导出", "_drafts")
_EXCLUDED_SUFFIXES = (".bak",)
_TEXT_SUFFIXES = (".md", ".json", ".txt")

INBOX_PREFIX = "garble_"


# ── 配置 ──────────────────────────────────────────────────────────────────────

def config_of(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """读取 garble_check 配置（缺失时退回默认 off）。"""
    base = dict(settings_mod.DEFAULT_SETTINGS["garble_check"])
    raw = (settings or {}).get("garble_check")
    if isinstance(raw, dict):
        base.update({k: v for k, v in raw.items() if k in base})
    return base


def mode_of(settings: Optional[Dict[str, Any]]) -> str:
    mode = config_of(settings)["mode"]
    return mode if mode in settings_mod.GARBLE_MODES else "off"


# ── 注册表展开 ────────────────────────────────────────────────────────────────

def _excluded(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(part in _EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name.endswith(_EXCLUDED_SUFFIXES):
        return True
    return not name.lower().endswith(_TEXT_SUFFIXES)


def _expand_spec(ws: Workspace, spec: str) -> List[Path]:
    """把一条注册表条目展开成文件列表。spec 可以是 PATHS 键、字面路径或 glob。"""
    spec = PATHS.get(spec, spec)
    if any(ch in spec for ch in "*?["):
        return sorted(p for p in ws.root.glob(spec) if p.is_file())
    path = ws.root / spec
    return [path] if path.is_file() else []


def watched_files(ws: Workspace, settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """当前工作目录内、需要守卫的关键文件（相对路径，排序去重）。"""
    specs: List[str] = list(WATCH_GLOBS)
    if settings is None:
        settings = ws.settings()
    specs.extend(config_of(settings).get("watch_extra") or [])
    seen: Set[str] = set()
    out: List[str] = []
    for spec in specs:
        for path in _expand_spec(ws, spec):
            rel = str(path.relative_to(ws.root))
            if rel in seen or _excluded(rel):
                continue
            seen.add(rel)
            out.append(rel)
    return sorted(out)


# ── 单文件检测（只读） ────────────────────────────────────────────────────────

def check_file(path: Path) -> List[str]:
    """返回命中的乱码原因列表（空 = 未命中）。只读，不改写文件。

    读不出来（不是 UTF-8 也不是可识别的 GBK）按乱码处理，与 chapter_ops._is_garbled_file 一致。
    文件不存在返回空列表。
    """
    try:
        text = read_text_utf8(path, repair=False)
    except FileNotFoundError:
        return []
    except TextEncodingError:
        return ["无法按 UTF-8 / GBK 读取（编码错乱）"]
    except OSError:
        return []
    return list(is_garbled(text).reasons)


def _digest(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 归因 ──────────────────────────────────────────────────────────────────────

def _known_step_ids(ws: Workspace) -> Set[str]:
    try:
        from novel_writer import pipeline as P

        return {s.id for s in P.build_plan(ws)}
    except Exception:  # noqa: BLE001 - 归因失败不影响体检结果
        return set()


def attribute(ws: Workspace, rel: str, known_steps: Optional[Set[str]] = None) -> Dict[str, Any]:
    """找出最后写入 rel 的一方，并判断能否自动重跑。

    规则（方案 3.3）：写入者是已知步骤 → 可自动重跑；author（作者手改）或查不到 → 只提示。
    """
    from novel_writer import state as st

    writer = (st.load_state(ws).get("file_writers") or {}).get(rel)
    kind = None
    last = st.read_write_history(ws, path=rel, limit=1)
    if last:
        if writer is None:
            writer = last[0].get("writer")
        if last[0].get("writer") == writer:
            kind = last[0].get("kind")
    if known_steps is None:
        known_steps = _known_step_ids(ws)
    if not writer:
        note = "查不到写入记录，无法自动定位产出者，请人工确认"
        auto = False
    elif kind == "author":
        note = "最后由作者手动修改，不自动重跑，请作者确认是否重新生成"
        auto = False
    elif writer in known_steps:
        note = f"可由步骤 {writer} 重新生成"
        auto = True
    else:
        note = f"写入者 {writer} 不是当前流水线中的步骤，无法自动重跑"
        auto = False
    return {"writer": writer, "writer_kind": kind, "auto_rerunnable": auto, "note": note}


# ── 只读体检 ──────────────────────────────────────────────────────────────────

def scan_garble(ws: Workspace) -> Dict[str, Any]:
    """逐个检查关键文件，返回命中项及归因。只读，不改任何文件，不调用 LLM。"""
    settings = ws.settings()
    files = watched_files(ws, settings)
    known = _known_step_ids(ws)
    hits: List[Dict[str, Any]] = []
    for rel in files:
        reasons = check_file(ws.root / rel)
        if not reasons:
            continue
        item = {"file": rel, "reasons": reasons}
        item.update(attribute(ws, rel, known))
        hits.append(item)
    return {
        "mode": mode_of(settings),
        "files_checked": len(files),
        "garbled_count": len(hits),
        "garbled": hits,
    }


# ── report 模式：步骤前后快照 ─────────────────────────────────────────────────

def snapshot(ws: Workspace, settings: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """步骤开始前调用。mode == off 时直接返回空字典（零开销，零行为变化）。"""
    if mode_of(settings) == "off":
        return {}
    return {rel: _digest(ws.root / rel) for rel in watched_files(ws, settings)}


def changed_since(ws: Workspace, before: Dict[str, str], settings: Optional[Dict[str, Any]]) -> List[str]:
    """步骤结束后调用：返回指纹发生变化（新增、修改、删除）的关键文件。"""
    after_files = set(watched_files(ws, settings))
    candidates = after_files | set(before)
    out = []
    for rel in sorted(candidates):
        if _digest(ws.root / rel) != before.get(rel, "MISSING"):
            out.append(rel)
    return out


def report_step(ws: Workspace, step_id: str, before: Dict[str, str],
                settings: Optional[Dict[str, Any]], extra_files: Iterable[str] = ()) -> Dict[str, Any]:
    """report 模式的步骤收尾：只检测本步骤改动过的文件；命中则写日志与收件箱记录。

    extra_files：步骤声明的产出（已展开的相对路径），即使指纹未变也纳入检测。
    不改变步骤状态，不重跑。任何异常都吞掉并返回 error（失败路径保持宽松）。
    """
    mode = mode_of(settings)
    if mode == "off":
        return {"mode": "off", "checked": 0, "garbled": []}
    try:
        from novel_writer import state as st

        touched = sorted(set(changed_since(ws, before, settings)) | set(extra_files))
        hits = []
        for rel in touched:
            path = ws.root / rel
            if not path.is_file() or _excluded(rel):
                continue
            reasons = check_file(path)
            if reasons:
                hits.append({"file": rel, "reasons": reasons})
        for hit in hits:
            st.log_line(ws, f"  ⚠ 乱码检测：{hit['file']} 疑似乱码（{'；'.join(hit['reasons'])}）")
        if hits:
            _write_inbox(ws, step_id, hits)
        return {"mode": mode, "checked": len(touched), "garbled": hits}
    except Exception as exc:  # noqa: BLE001 - 检测失败不能影响步骤结果
        return {"mode": mode, "checked": 0, "garbled": [], "error": f"{type(exc).__name__}: {exc}"}


def _write_inbox(ws: Workspace, step_id: str, hits: List[Dict[str, Any]]) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in step_id)
    path = ws.studio / "inbox" / f"{INBOX_PREFIX}{safe}.json"
    atomic_write_json(path, {"step": step_id, "mode": "report", "garbled": hits})
    return path


# ── G2：步骤内重试环（A 层，仅 rerun 模式） ────────────────────────────────────

def inline_problems(ws: Workspace, step_id: str, before: Dict[str, str],
                    settings: Optional[Dict[str, Any]], extra_files: Iterable[str] = ()) -> List[str]:
    """skill 步骤每次尝试通过其它校验后调用：返回要让同一步骤 agent 修正的问题清单。

    只检查本步骤改动过的文件（含声明产出）；命中的原文件先备份到
    `_studio/backup/garbled/<步骤>_<时间>/`，不删除。非 rerun 模式直接返回空列表。
    """
    if mode_of(settings) != "rerun":
        return []
    import shutil
    from datetime import datetime

    from novel_writer import state as st

    changed = sorted(set(changed_since(ws, before, settings)) | set(extra_files))
    hits: List[tuple] = []
    for rel in changed:
        path = ws.root / rel
        if not path.is_file() or _excluded(rel):
            continue
        reasons = check_file(path)
        if reasons:
            hits.append((rel, reasons))
    if not hits:
        return []
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in step_id)
    backup_dir = ws.studio / "backup" / "garbled" / f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    problems: List[str] = []
    for rel, reasons in hits:
        src = ws.root / rel
        try:
            shutil.copy2(src, backup_dir / src.name)
        except OSError as exc:
            st.log_line(ws, f"⚠ {step_id}：乱码文件备份失败（{exc}）：{rel}")
        st.log_line(ws, f"⚠ {step_id}：{rel} 疑似乱码（{'；'.join(reasons)}），要求 agent 覆盖重写")
        problems.append(f"`{rel}` 疑似乱码（{'；'.join(reasons)}）。请重新完整撰写该文件并覆盖写入同一路径，"
                        "文件名保持不变，不要保留乱码内容。")
    return problems


# ── G3：统一守卫（B 层，rerun 模式，所有步骤含 python 步骤） ──────────────────────
#
# 检测范围 = 本步骤改动的关键文件 ∪ 本步骤声明产出 ∪ 本步骤读取的关键文件（下游发现上游乱码）。
# 归因：改动/产出归本步骤；读取的文件按「最后写入者」归因，可能是上游步骤。
# 动作（每次只处理第一条可自动处理的命中）：
#   * 写入者是本步骤  → 本步骤标为失败（错误信息即重试提示），交给 worker 自动重跑；
#   * 写入者是上游 X → 重置 X 与本步骤为待执行，调度器按顺序先跑 X；
#   * 作者手改 / 查不到 → 只记入收件箱，不动流水线；
#   * 「步骤 × 文件」次数用尽（garble_check.max_reruns）→ 挂起 waiting_user，选项见 DECISION_OPTIONS。
# 状态存放于 pipeline_state.json 的 garble_reruns / garble_accepted / garble_pending 三个键。

DECISION_OPTIONS = ["再重新生成（再试一轮）", "我手动修改后重新核查", "接受当前内容（风险自负）"]
_DECISION_ID = "decision"


def _state_get(ws: Workspace, key: str) -> Dict[str, Any]:
    from novel_writer import state as st

    return dict(st.load_state(ws).get(key) or {})


def _state_put(ws: Workspace, updates: Dict[str, Any]) -> None:
    from novel_writer import state as st
    from novel_writer.fsutil import atomic_write_json as _write

    with st._LOCK:
        data = st.load_state(ws)
        data.update(updates)
        _write(ws.path("studio_state"), data)


def collect_hits(ws: Workspace, step_id: str, before: Dict[str, str], settings: Optional[Dict[str, Any]],
                 declared: Iterable[str], inputs: Iterable[str]) -> tuple:
    """返回 (命中列表, 本次检查过的文件列表)。已被作者「接受」且指纹未变的文件不算命中。"""
    watched = set(watched_files(ws, settings))
    changed = set(changed_since(ws, before, settings))
    own = changed | set(declared)
    read_watched = {rel for rel in inputs if rel in watched}
    checked = sorted(own | read_watched)
    accepted = _state_get(ws, "garble_accepted")
    known = _known_step_ids(ws)
    hits: List[Dict[str, Any]] = []
    for rel in checked:
        path = ws.root / rel
        if not path.is_file() or _excluded(rel):
            continue
        reasons = check_file(path)
        if not reasons or accepted.get(rel) == _digest(path):
            continue
        item: Dict[str, Any] = {"file": rel, "reasons": reasons}
        if rel in own:
            # 本步骤改动或声明的产出：归本步骤（python 步骤的写入记录要到收尾才登记）
            item.update({"writer": step_id, "writer_kind": "agent", "auto_rerunnable": True,
                         "note": f"由本步骤 {step_id} 产生"})
        else:
            item.update(attribute(ws, rel, known))
        hits.append(item)
    return hits, checked


def _decision_fields(file: str, reasons: List[str], summary: str) -> Dict[str, Any]:
    return {
        "summary": summary,
        "needs_user": [{
            "id": _DECISION_ID,
            "question": f"`{file}` 疑似乱码（{'；'.join(reasons)}）。已自动重做多次仍未通过，请选择：",
            "options": list(DECISION_OPTIONS),
        }],
    }


def guard_after_step(ws: Workspace, step_id: str, status: str, fields: Dict[str, Any], *,
                     before: Dict[str, str], settings: Optional[Dict[str, Any]],
                     declared: Iterable[str], inputs: Iterable[str]) -> tuple:
    """步骤将要结束时调用（仅 rerun 模式）。返回可能被改写的 (status, fields)。失败路径保持宽松。"""
    from novel_writer import state as st

    if mode_of(settings) != "rerun" or status not in (st.ST_PASSED, st.ST_WAITING_REVIEW):
        return status, fields
    try:
        max_reruns = int(config_of(settings)["max_reruns"])
        hits, checked = collect_hits(ws, step_id, before, settings, declared, inputs)
        counters = _state_get(ws, "garble_reruns")
        if not hits:
            cleared = {k: v for k, v in counters.items() if k.split("|", 1)[-1] not in checked}
            if len(cleared) != len(counters):
                _state_put(ws, {"garble_reruns": cleared})
            return status, fields
        manual = [h for h in hits if not h["auto_rerunnable"]]
        if manual:
            for h in manual:
                st.log_line(ws, f"⚠ 乱码检测：{h['file']} 疑似乱码，{h['note']}")
            _write_inbox(ws, step_id, manual)
        auto = [h for h in hits if h["auto_rerunnable"]]
        if not auto:
            return status, fields
        h = auto[0]
        writer, rel = h["writer"], h["file"]
        key = f"{writer}|{rel}"
        n = int(counters.get(key, 0))
        if n >= max_reruns:
            pending = _state_get(ws, "garble_pending")
            pending[step_id] = {"key": key, "writer": writer, "file": rel}
            _state_put(ws, {"garble_pending": pending})
            st.log_line(ws, f"⏸ 乱码检测：{rel} 由 {writer} 产出，已自动重做 {n} 次仍为乱码，挂起等待作者决定")
            return st.ST_WAITING_USER, _decision_fields(rel, h["reasons"], f"{rel} 多次重做仍疑似乱码，已暂停")
        counters[key] = n + 1
        _state_put(ws, {"garble_reruns": counters})
        if writer == step_id:
            st.log_line(ws, f"↻ 乱码检测：{rel} 疑似乱码（{'；'.join(h['reasons'])}），本步骤自动重做（第 {n + 1}/{max_reruns} 次）")
            return st.ST_FAILED, {"error": f"乱码检测：{rel} 疑似乱码（{'；'.join(h['reasons'])}）。"
                                           "请重新完整撰写并覆盖写入同一文件，不要保留乱码内容。"}
        st.log_line(ws, f"↻ 乱码检测：{rel} 由上游 {writer} 产出且疑似乱码，重置 {writer} 与 {step_id}（第 {n + 1}/{max_reruns} 次）")
        st.reset_step(ws, writer)
        st.reset_step(ws, step_id)
        return st.ST_PENDING, {"summary": f"上游 {writer} 产出乱码，已重置"}
    except Exception as exc:  # noqa: BLE001 - 守卫失败不能影响步骤结果
        st.log_line(ws, f"⚠ 乱码守卫异常（已忽略，步骤结果不变）：{type(exc).__name__}: {exc}")
        return status, fields


def handle_pending(ws: Workspace, step_id: str, settings: Optional[Dict[str, Any]]) -> Optional[tuple]:
    """步骤开始执行前调用：若该步骤正挂起等待乱码决定，且作者已答复，则按答复处理。

    返回 (status, fields) 表示本次不执行步骤、直接给出结果；返回 None 表示照常执行。
    """
    from novel_writer import state as st
    from novel_writer.fsutil import read_json
    from novel_writer.runner import _answers_file

    if mode_of(settings) != "rerun":
        return None
    pending_all = _state_get(ws, "garble_pending")
    pending = pending_all.get(step_id)
    if not pending:
        return None
    answers_path = _answers_file(ws, step_id)
    answers = (read_json(answers_path, default=None) or {}).get("answers") or {}
    decision = str(answers.get(_DECISION_ID) or "")
    if answers_path.exists():
        answers_path.unlink()
    pending_all.pop(step_id, None)
    _state_put(ws, {"garble_pending": pending_all})
    if not decision:
        return None                                   # 没有答复：照常执行，由守卫重新判断

    key, writer, rel = pending["key"], pending["writer"], pending["file"]
    counters = _state_get(ws, "garble_reruns")
    accepted = _state_get(ws, "garble_accepted")
    path = ws.root / rel
    if decision.startswith("接受"):
        accepted[rel] = _digest(path)
        counters.pop(key, None)
        _state_put(ws, {"garble_accepted": accepted, "garble_reruns": counters})
        st.log_line(ws, f"乱码检测：作者接受 {rel} 的风险，继续推进")
        return st.ST_PASSED, {"summary": f"作者接受 {rel} 的乱码风险"}
    if decision.startswith("我手动修改"):
        counters[key] = 0
        _state_put(ws, {"garble_reruns": counters})
        reasons = check_file(path)
        if not reasons:
            st.log_line(ws, f"乱码检测：作者手动修改后 {rel} 已通过检测")
            return st.ST_PASSED, {"summary": f"{rel} 手动修改后通过乱码检测"}
        pending_all = _state_get(ws, "garble_pending")
        pending_all[step_id] = pending
        _state_put(ws, {"garble_pending": pending_all})
        return st.ST_WAITING_USER, _decision_fields(rel, reasons, f"{rel} 仍疑似乱码")
    # 再重新生成
    counters[key] = 0
    _state_put(ws, {"garble_reruns": counters})
    if writer == step_id:
        st.log_line(ws, f"乱码检测：作者选择重新生成，本步骤重跑")
        return None
    st.reset_step(ws, writer)
    st.reset_step(ws, step_id)
    st.log_line(ws, f"乱码检测：作者选择重新生成，重置上游 {writer} 与本步骤")
    return st.ST_PENDING, {"summary": f"作者选择重新生成 {writer}"}
