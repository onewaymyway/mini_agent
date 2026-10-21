"""novel_writer/state.py — 步骤状态机、脏标记、worker 心跳与控制指令（方案 4.1 / 6.5）。

持久化文件（都在工作目录的 `_studio/` 下，见 workspace.PATHS）：
  pipeline_state.json  每个步骤的状态、尝试次数、输入哈希、错误信息
  control.json         UI/CLI → worker 的指令（run / pause / stop / run_step）
  worker.json          worker 心跳与 pid（同一本书同一时刻只允许一个 worker；心跳超时 = 孤儿）
  usage.jsonl          每次 LLM 步骤的耗时与结果

所有写入都走 `fsutil.atomic_write_json`；同进程内用锁串行化「读-改-写」，
跨进程的竞争靠\"worker 每次决策前都重新读盘\"来收敛（已知限制，见 PROJECT.md）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.steps import StepDef
from novel_writer.workspace import Workspace

SCHEMA_VERSION = 1
HEARTBEAT_TIMEOUT_SEC = 90.0

# 步骤状态
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_WAITING_REVIEW = "waiting_review"
ST_WAITING_USER = "waiting_user"
ST_PASSED = "passed"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"
ST_STALE = "stale"
ST_INTERRUPTED = "interrupted"
ST_BLOCKED = "blocked"          # 执行器尚未实现（例如 M3 之前的章节 python 步骤）

FINISHED_STATUSES = (ST_PASSED, ST_SKIPPED)
STATUSES = (
    ST_PENDING, ST_RUNNING, ST_WAITING_REVIEW, ST_WAITING_USER, ST_PASSED, ST_FAILED,
    ST_SKIPPED, ST_STALE, ST_INTERRUPTED, ST_BLOCKED,
)

_LOCK = threading.RLock()


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(text: Optional[str]) -> Optional[_dt.datetime]:
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        return None


# ── 流水线状态 ─────────────────────────────────────────────────────────────


class StateReadError(RuntimeError):
    """状态文件存在但读不出来。宁可报错，也不能当作空状态再写回去（那会清空全部进度）。"""


def load_state(ws: Workspace) -> Dict[str, Any]:
    path = ws.path("studio_state")
    data = read_json(path, default=None)
    if data is None and path.exists():
        raise StateReadError(f"状态文件无法读取（可能被占用或已损坏）：{path.name}，请稍后重试")
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("steps", {})
    return data


def get_step_state(ws: Workspace, step_id: str) -> Dict[str, Any]:
    entry = load_state(ws)["steps"].get(step_id) or {}
    return {"status": ST_PENDING, "attempts": 0, **entry}


def update_step(ws: Workspace, step_id: str, **fields: Any) -> Dict[str, Any]:
    """读-改-写单个步骤的状态（加锁，写前重新读盘）。返回更新后的步骤状态。"""
    with _LOCK:
        state = load_state(ws)
        entry = {"status": ST_PENDING, "attempts": 0, **state["steps"].get(step_id, {})}
        entry.update(fields)
        entry["updated_at"] = now_iso()
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"非法步骤状态 {fields['status']!r}")
        state["steps"][step_id] = entry
        atomic_write_json(ws.path("studio_state"), state)
        return entry


def drop_step(ws: Workspace, step_id: str) -> None:
    """删除一个步骤的状态记录（用于删除按需请求）。"""
    with _LOCK:
        state = load_state(ws)
        if state["steps"].pop(step_id, None) is not None:
            atomic_write_json(ws.path("studio_state"), state)


def reset_step(ws: Workspace, step_id: str) -> Dict[str, Any]:
    """回到 pending（用于重跑）。保留 attempts 与历史，清除错误与待答复项。"""
    return update_step(ws, step_id, status=ST_PENDING, error=None, needs_user=None)


# ── 输入哈希与脏标记（方案 5.2）────────────────────────────────────────────


def _strip_glob(spec: str) -> str:
    return spec[len("glob:"):] if spec.startswith("glob:") else spec


def expand_inputs(ws: Workspace, specs: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    for spec in specs:
        pattern = _strip_glob(spec)
        if "*" in pattern:
            files.extend(sorted(p for p in ws.root.glob(pattern) if p.is_file()))
        else:
            files.append(ws.root / pattern)
    return files


def input_hash(ws: Workspace, step: StepDef) -> str:
    """步骤输入的指纹：技能名 + 每个输入文件的相对路径与内容。文件不存在记为 MISSING。"""
    h = hashlib.sha256()
    h.update(f"{step.executor}:{step.skill}:{step.handler}".encode("utf-8"))
    for path in expand_inputs(ws, step.inputs):
        h.update(str(path.relative_to(ws.root)).encode("utf-8"))
        if path.is_file():
            h.update(path.read_bytes())
        else:
            h.update(b"MISSING")
    return h.hexdigest()


def _file_digest(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_digests(ws: Workspace, step: StepDef) -> Dict[str, str]:
    """步骤每个输入文件的指纹（相对路径 -> sha256），步骤通过时记录。"""
    out: Dict[str, str] = {}
    for path in expand_inputs(ws, step.inputs):
        out[str(path.relative_to(ws.root))] = _file_digest(path)
    return out


WRITE_HISTORY_FILE = "write_history.jsonl"     # 当前活动分段
WRITE_HISTORY_MAX_BYTES = 1_000_000            # 超过即封存为分段 write_history.NNNNNN.jsonl，再开新文件
_SEGMENT_RE = re.compile(r"^write_history\.(\d{6})\.jsonl$")


def _chapter_of_writer(writer: Optional[str]) -> Optional[int]:
    m = re.match(r"ch(\d+)\.", writer or "")
    return int(m.group(1)) if m else None


def _history_segments(ws: Workspace) -> List[Path]:
    """所有分段，从旧到新（封存分段按序号，活动分段最后）。"""
    studio = ws.studio
    if not studio.is_dir():
        return []
    segs = []
    for f in studio.iterdir():
        m = _SEGMENT_RE.match(f.name)
        if m:
            segs.append((int(m.group(1)), f))
    out = [f for _, f in sorted(segs)]
    active = studio / WRITE_HISTORY_FILE
    if active.is_file():
        out.append(active)
    return out


def _rotate_if_needed(ws: Workspace) -> None:
    """活动分段过大时封存（改名），之后的写入进入新的活动分段。调用方须持有 _LOCK。"""
    active = ws.studio / WRITE_HISTORY_FILE
    if not active.is_file() or active.stat().st_size < WRITE_HISTORY_MAX_BYTES:
        return
    next_no = 1 + max((int(_SEGMENT_RE.match(f.name).group(1)) for f in ws.studio.iterdir()
                       if _SEGMENT_RE.match(f.name)), default=0)
    active.rename(ws.studio / f"write_history.{next_no:06d}.jsonl")


def _append_history(ws: Workspace, events: List[Dict[str, Any]]) -> None:
    """追加写入历史（每行一条 JSON，只追加不改写）。调用方须持有 _LOCK。"""
    if not events:
        return
    path = ws.studio / WRITE_HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(ws)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def record_writer_paths(ws: Workspace, rel_paths: Iterable[str], writer_id: str,
                        before: Optional[Dict[str, str]] = None, kind: str = "agent") -> None:
    """记录若干文件的写入：更新「最后写入者」，并追加一条写入历史。

    before：写入前各文件的指纹（没有则为 None，如作者手改时未知）。
    kind：agent（执行器实际写入）/ declared（步骤声明的产出）/ author（作者手动修改）。
    """
    now = now_iso()
    with _LOCK:
        state = load_state(ws)
        writers = state.setdefault("file_writers", {})
        events = []
        for rel in rel_paths:
            writers[rel] = writer_id
            events.append({
                "ts": now, "path": rel, "writer": writer_id, "kind": kind,
                "chapter": _chapter_of_writer(writer_id),
                "before": (before or {}).get(rel),
                "after": _file_digest(ws.root / rel),
            })
        atomic_write_json(ws.path("studio_state"), state)
        _append_history(ws, events)


def record_writers(ws: Workspace, step: StepDef) -> None:
    """步骤产出后，记录每个声明的输出文件的写入者（步骤 id）。"""
    rels = [str(path.relative_to(ws.root)) for path in expand_inputs(ws, step.outputs)]
    if rels:
        record_writer_paths(ws, rels, step.id, kind="declared")


def read_write_history(ws: Workspace, *, path: Optional[str] = None, writer: Optional[str] = None,
                       kind: Optional[str] = None, chapter: Optional[int] = None,
                       limit: int = 200) -> List[Dict[str, Any]]:
    """写入历史，最新的在前。跨所有分段读取，可按文件路径、写入者、类型过滤。

    从最新的分段开始读，凑够 limit 条即停止，不必整体加载全部历史。
    """
    want = max(1, int(limit))
    events: List[Dict[str, Any]] = []
    for seg in reversed(_history_segments(ws)):
        lines = seg.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if path and ev.get("path") != path:
                continue
            if writer and ev.get("writer") != writer:
                continue
            if kind and ev.get("kind") != kind:
                continue
            if chapter is not None and ev.get("chapter") != chapter:
                continue
            events.append(ev)
            if len(events) >= want:
                return events
    return events


_UPSTREAM_NOTE = "上游产物已变化，需要重跑"


def _writer_index(writer: Optional[str], index: Dict[str, int]) -> Optional[int]:
    """写入者在计划中的位置。修改循环内部的写入（chNN.revise.rK 等）归到该章的修改循环。"""
    if writer is None:
        return None
    if writer in index:
        return index[writer]
    m = re.match(r"ch(\d+)\.", writer)
    if m:
        return index.get(f"ch{int(m.group(1)):02d}.revise_loop")
    return None


def _chapter_of(step: StepDef) -> int:
    return int(getattr(step, "chapter", 0) or 0)


def _writer_chapter(writer: Optional[str]) -> Optional[int]:
    """写入者所属章节：chNN.* → NN；设计/规划步骤与作者手动修改 → None。"""
    m = re.match(r"ch(\d+)\.", writer or "")
    return int(m.group(1)) if m else None


def _upstream_cause(ws: Workspace, digests: Dict[str, str], i: int, reader_chapter: int,
                    index: Dict[str, int], writers: Dict[str, str]) -> Optional[str]:
    """某步骤的输入是否发生了需要它重跑的变化。

    规则（显式、与计划顺序解耦）：
      1. 章节写作的更新只属于它所在的章节：对其他章节、以及设计/规划步骤，它是「为后续章节准备」，
         绝不回溯标记已完成的步骤（写完第 N 章后更新蓝图/伏笔/人物，不影响前面的章节和设计）。
      2. 同一章内部，按计划顺序传播（例如修改循环改了正文，本章的归档要重做）。
      3. 设计/规划步骤的产物变化，以及作者手动修改，是真正的上游变化，按依赖传播（托管挡会停下来确认）。
    """
    for rel_path, recorded_sha in digests.items():
        writer = writers.get(rel_path)
        wc = _writer_chapter(writer)
        if wc is not None and wc != reader_chapter:
            continue  # 规则 1
        owner = _writer_index(writer, index)
        if owner is not None and owner >= i:
            continue  # 规则 2：本步骤自己或之后写入的，是下游结果
        if _file_digest(ws.root / rel_path) != recorded_sha:
            return f"{writer or '未知'} 写入 {rel_path}"
    return None


def mark_stale(ws: Workspace, plan: List[StepDef]) -> List[str]:
    """已通过的步骤若「来自上游」的输入变化，标记为 stale；已误判的 stale 步骤若复核后无上游变化则自愈。

    关键规则：输入文件的变化若是由计划中排在本步骤之后（或本步骤自己）的步骤造成，不算上游变化。
    这避免了「核查 → 同步 → 核查」的回路，也避免了归档顺手改了蓝图之后，早期规划步骤被连带重跑。
    没有逐文件指纹的旧记录只补录、不标脏。返回新标脏的步骤 id 列表。
    """
    stale_ids: List[str] = []
    restored: List[str] = []
    index = {step.id: i for i, step in enumerate(plan)}
    with _LOCK:
        state = load_state(ws)
        writers = state.get("file_writers") or {}
        changed = False
        for i, step in enumerate(plan):
            entry = state["steps"].get(step.id)
            if not entry:
                continue
            status = entry.get("status")
            digests = entry.get("input_digests")
            if status == ST_STALE and digests is not None and str(entry.get("note") or "").startswith(_UPSTREAM_NOTE):
                if _upstream_cause(ws, digests, i, _chapter_of(step), index, writers) is None:
                    entry.update(status=ST_PASSED, note=None, updated_at=now_iso())
                    restored.append(step.id)
                    changed = True
                continue
            if status != ST_PASSED:
                continue
            if digests is None:
                # 旧版本的记录只有整体哈希，无法区分「上游真正变化」与「流水线自己后来的写入」。
                # 只补录逐文件指纹、不标脏：宁可漏报，也不能因升级自动重跑（蓝图等步骤会覆盖全书规划）。
                entry["input_hash"] = input_hash(ws, step)
                entry["input_digests"] = input_digests(ws, step)
                changed = True
                continue
            cause = _upstream_cause(ws, digests, i, _chapter_of(step), index, writers)
            if cause is not None:
                entry.update(status=ST_STALE, note=f"{_UPSTREAM_NOTE}（{cause}）", updated_at=now_iso())
                stale_ids.append(step.id)
                changed = True
        if changed:
            atomic_write_json(ws.path("studio_state"), state)
    if restored:
        log_line(ws, f"🩹 复核后恢复 {len(restored)} 个误判的需重跑步骤：{', '.join(restored[:8])}")
    return stale_ids


def rebaseline_stale(ws: Workspace, plan: List[StepDef], step_ids: Iterable[str]) -> List[str]:
    """作者确认这些需重跑步骤的产物无误：以当前文件状态重新基线，标为已通过（仅限「上游产物已变化」的标记）。"""
    done: List[str] = []
    by_id = {step.id: step for step in plan}
    with _LOCK:
        state = load_state(ws)
        for sid in step_ids:
            entry = state["steps"].get(sid)
            step = by_id.get(sid)
            if not entry or not step or entry.get("status") != ST_STALE:
                continue
            if not str(entry.get("note") or "").startswith(_UPSTREAM_NOTE):
                continue
            entry.update(status=ST_PASSED, note="作者确认产物无误，已重新基线", updated_at=now_iso(),
                         input_hash=input_hash(ws, step), input_digests=input_digests(ws, step))
            done.append(sid)
        if done:
            atomic_write_json(ws.path("studio_state"), state)
    if done:
        log_line(ws, f"🩹 作者确认 {len(done)} 个需重跑步骤的产物无误，已重新基线：{', '.join(done[:8])}")
    return done


# 迁移规则：旧版本的数据里，某个「锚点」步骤已通过，但它后来拆出的新步骤还没有记录。
# 判断「这是旧数据」的依据是：新步骤组中的第一个（哨兵）没有记录。新流程里哨兵一定先于锚点执行，
# 因此「锚点已通过且哨兵缺失」只可能出现在旧版本数据上。满足时，整组一起认领为已通过。
# 新增拆分步骤时在这里加一行即可。格式：(锚点步骤后缀, (新步骤后缀…), 说明)
LEGACY_ADOPTION_RULES = (
    ("best_pick", ("best_pick_prep", "best_pick_apply"), "旧版本择优为单步，已完成的择优包含机检准备与结果应用"),
)


def adopt_legacy_steps(ws: Workspace) -> List[str]:
    """按 LEGACY_ADOPTION_RULES 把旧版本已完成的工作认领给新拆出的步骤。

    幂等，可以在每轮循环、每次评估流水线时安全调用。只认领「从未出现过」的新步骤，
    不会覆盖任何真实运行过的步骤，也不会在新流程中途误认领尚未运行的步骤。
    """
    adopted: List[str] = []
    with _LOCK:
        state = load_state(ws)
        steps = state["steps"]
        for sid, entry in list(steps.items()):
            m = re.fullmatch(r"ch(\d+)\.(\w+)", sid)
            if not m or entry.get("status") != ST_PASSED:
                continue
            n, suffix = m.group(1), m.group(2)
            for anchor, group, why in LEGACY_ADOPTION_RULES:
                if suffix != anchor:
                    continue
                sentinel = f"ch{n}.{group[0]}"
                if sentinel in steps:
                    continue  # 新流程数据：哨兵已存在，不认领
                for new_suffix in group:
                    new_id = f"ch{n}.{new_suffix}"
                    if new_id not in steps:
                        steps[new_id] = {"status": ST_PASSED, "attempts": 0,
                                         "note": f"迁移：{why}", "updated_at": now_iso()}
                        adopted.append(new_id)
        adopted.extend(_adopt_legacy_plot_merge(ws, steps))
        if adopted:
            atomic_write_json(ws.path("studio_state"), state)
    if adopted:
        log_line(ws, f"🔁 迁移：{len(adopted)} 个新步骤沿用旧版本的结果：{', '.join(adopted[:6])}")
    return adopted


# 旧版本的伏笔同步直接改写整本账本，没有「伏笔增量」文件，也没有 plot_merge 步骤。
# 升级后 plot_merge 是新步骤、没有记录，会被当作「待运行」从第 1 章开始排队，而旧章节根本没有增量可合并。
_LEGACY_PLOT_MERGE_NOTE = "迁移：旧版本的伏笔同步已把本章伏笔直接写入账本，无增量文件，无需再合并"
# 新流程里「同步」步骤通过的前提是增量文件存在（产出校验），所以「同步已通过 + 增量文件不存在」只可能是旧数据。
_MISSING_DELTA_MARK = "伏笔增量"


def _adopt_legacy_plot_merge(ws: Workspace, steps: Dict[str, Any]) -> List[str]:
    """把旧版本已完成章节的 plot_merge 认领为已通过。调用方已持有锁并负责落盘。

    只认领同时满足下列条件的章节：
      1. 本章的伏笔同步步骤已通过（标准/精品档为 sync_plot，草稿档为 sync）；
      2. 本章的伏笔增量文件不存在（新流程不可能出现这种组合）；
      3. plot_merge 没有记录，或只有一条「因增量缺失而失败/未开始/中断」的记录。
    已通过、运行中、等待作者、需重跑的 plot_merge 一律不动；增量文件存在的章节（新流程数据）也不动。
    """
    from novel_writer.steps import DRAFT
    try:
        quality = ws.settings().get("quality", "standard")
    except Exception:  # noqa: BLE001 - 设置读不到时按标准档处理，宁可少认领
        quality = "standard"
    anchor = "sync" if quality == DRAFT else "sync_plot"
    adopted: List[str] = []
    for sid, entry in list(steps.items()):
        m = re.fullmatch(rf"ch(\d+)\.{anchor}", sid)
        if not m or entry.get("status") != ST_PASSED:
            continue
        n = int(m.group(1))
        merge_id = f"ch{m.group(1)}.plot_merge"
        cur = steps.get(merge_id)
        if cur is not None:
            status = cur.get("status")
            if status in (ST_PASSED, ST_RUNNING, ST_WAITING_REVIEW, ST_WAITING_USER, ST_STALE):
                continue
            if status == ST_FAILED and _MISSING_DELTA_MARK not in str(cur.get("error") or ""):
                continue  # 真实的其它失败（如账本损坏）不能被吞掉
        try:
            if ws.path("plot_delta", nn=n).exists():
                continue
        except OSError:
            continue
        steps[merge_id] = {"status": ST_PASSED, "attempts": 0, "note": _LEGACY_PLOT_MERGE_NOTE,
                           "updated_at": now_iso()}
        adopted.append(merge_id)
    return adopted


def mark_orphans_interrupted(ws: Workspace) -> List[str]:
    """worker 启动时调用（已持有 worker 锁）：遗留的 running 步骤一律视为中断。"""
    fixed: List[str] = []
    state = load_state(ws)
    for sid, entry in list(state["steps"].items()):
        if entry.get("status") == ST_RUNNING:
            update_step(ws, sid, status=ST_INTERRUPTED, error="上次运行中断（worker 心跳超时），可直接重跑")
            fixed.append(sid)
    return fixed


# ── worker 心跳 / 锁 ───────────────────────────────────────────────────────


def read_worker(ws: Workspace) -> Dict[str, Any]:
    return read_json(ws.path("studio_worker"), default={}) or {}


def worker_alive(ws: Workspace, *, now: Optional[_dt.datetime] = None, timeout: float = HEARTBEAT_TIMEOUT_SEC) -> bool:
    info = read_worker(ws)
    if info.get("state") != "running":
        return False
    beat = _parse_iso(info.get("heartbeat"))
    if beat is None:
        return False
    now = now or _dt.datetime.now().astimezone()
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=now.tzinfo)
    return (now - beat).total_seconds() < timeout


def acquire_worker(ws: Workspace, *, mode: str) -> bool:
    """尝试占用 worker 锁。已有存活的 worker 时返回 False。"""
    import os

    with _LOCK:
        if worker_alive(ws):
            return False
        from novel_writer.procs import process_create_time

        atomic_write_json(ws.path("studio_worker"), {
            "pid": os.getpid(), "pid_create_time": process_create_time(), "state": "running", "mode": mode,
            "started_at": now_iso(), "heartbeat": now_iso(), "step": None, "reason": None,
        })
        return True


def heartbeat(ws: Workspace, **fields: Any) -> None:
    with _LOCK:
        info = read_worker(ws)
        info.update(fields)
        info["heartbeat"] = now_iso()
        atomic_write_json(ws.path("studio_worker"), info)


def release_worker(ws: Workspace, reason: str) -> None:
    with _LOCK:
        info = read_worker(ws)
        info.update({"state": "stopped", "reason": reason, "step": None, "step_title": None,
                     "step_started_at": None, "heartbeat": now_iso(), "finished_at": now_iso()})
        atomic_write_json(ws.path("studio_worker"), info)


# ── 运行日志（实时输出；追加写入，不会被下一次运行覆盖，界面始终能看到上一次的结果）──


def log_line(ws: Workspace, text: str) -> None:
    """向 `_studio/run.log` 追加一行（带时间戳）。worker 与子 agent 的输出都写到这里。"""
    path = ws.path("studio_log")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {text}\n")
            f.flush()


def read_log_tail(ws: Workspace, lines: int = 120, max_bytes: int = 200_000) -> List[str]:
    path = ws.path("studio_log")
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # 丢弃可能不完整的第一行
        data = f.read().decode("utf-8", errors="replace")
    return [ln for ln in data.splitlines() if ln.strip()][-lines:]


LAST_RUN_FILE = "last_run.json"


def write_last_run(ws: Workspace, record: Dict[str, Any]) -> None:
    atomic_write_json(ws.studio / LAST_RUN_FILE, record)


def read_last_run(ws: Workspace) -> Optional[Dict[str, Any]]:
    data = read_json(ws.studio / LAST_RUN_FILE, default=None)
    return data if isinstance(data, dict) else None


# ── 控制指令 ───────────────────────────────────────────────────────────────

CONTROL_DEFAULT: Dict[str, Any] = {"command": "none", "mode": "until_gate", "run_step": None}


def read_control(ws: Workspace) -> Dict[str, Any]:
    data = read_json(ws.path("studio_control"), default=None)
    return {**CONTROL_DEFAULT, **(data if isinstance(data, dict) else {})}


def write_control(ws: Workspace, **fields: Any) -> Dict[str, Any]:
    with _LOCK:
        data = {**read_control(ws), **fields, "issued_at": now_iso()}
        atomic_write_json(ws.path("studio_control"), data)
        return data


# ── 用量留痕 ───────────────────────────────────────────────────────────────


def append_usage(ws: Workspace, record: Dict[str, Any]) -> None:
    path = ws.path("studio_usage")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": now_iso(), **record}, ensure_ascii=False)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
