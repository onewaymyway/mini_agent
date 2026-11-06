"""novel_writer/concept_revision.py — 构思阶段的整体修订：按一个新思路重新调整主题、世界观、角色等。

作者给出一个思路（例如「把复仇主线改为救赎，世界观改成蒸汽朋克，主角改为女性」），选择要调整的范围，
系统就会按这个思路重写对应产物。设计要点：

  * **复用原有步骤**：每个范围直接使用原有的步骤定义（design_theme、design_pacing、build_world、
    design_character + build_characters、design_opening、plan_blueprint），只在指令前加上新思路，
    因此输出路径、格式校验与原来完全一致。
  * **先备份，后修改**：运行前把将被改写的产物复制到 `_studio/concept_backups/<请求id>/`，
    作者可以随时「撤销，恢复修订前」。
  * **改动视为作者修改**：修订写入的文件记为 `author` 写入，因此下游已通过的步骤会按依赖标为需重跑
    （与保存人设卡的做法一致；若记为执行器写入，计划顺序的规则会把它当作下游结果而漏掉）。
  * **蓝图是全书级改动**：修改蓝图前需要作者确认（与 L4 修订一致）。蓝图改写后，依赖它的其它范围
    需要等「蓝图拆分」完成后再修订，本次请求会如实列出这些「待后续」的范围。

请求以 `_studio/requests/concept_<id>.json` 保存（见 ondemand.py），步骤 id 为 `cpt.<id>`。
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

# 范围及其展示顺序（蓝图最先，因为其它范围都依赖它）
SCOPE_ORDER: Tuple[str, ...] = ("blueprint", "theme", "pacing", "world", "characters", "opening")
SCOPE_LABELS: Dict[str, str] = {
    "blueprint": "蓝图（全书结构与章节表，全书级，需确认）",
    "theme": "主题设计",
    "pacing": "节奏规划",
    "world": "世界观（世界圣经）",
    "characters": "主要角色（人设卡与角色档案）",
    "opening": "前三章开篇设计",
}

NOTE_PREFIX = "【构思修订】作者希望围绕以下新思路整体调整本产出，这是本次修订的核心方向，优先级高于原有设定：\n"
NOTE_RULES = ("\n要求：让本产出与该思路一致，并同步调整其中受影响的部分；与思路无关的内容尽量保持原样；"
              "保持文件格式、字段与标题结构不变。\n\n")


class ConceptError(ValueError):
    pass


def validate(direction: str, scopes: List[str]) -> Tuple[str, List[str]]:
    text = str(direction or "").strip()
    if not text:
        raise ConceptError("请写下新的思路（修订方向）")
    chosen = [s for s in SCOPE_ORDER if s in set(scopes or [])]
    unknown = [s for s in (scopes or []) if s not in SCOPE_LABELS]
    if unknown:
        raise ConceptError(f"未知的修订范围：{unknown}")
    if not chosen:
        raise ConceptError("请至少选择一个修订范围")
    return text, chosen


# ---------------------------------------------------------------- 路径与快照

def scope_paths(ws: Workspace, scope: str) -> List[Tuple[Path, bool]]:
    """范围覆盖的 (绝对路径, 是否目录)。"""
    if scope == "blueprint":
        return [(ws.path("blueprint_draft"), False), (ws.path("blueprint_readable"), False)]
    if scope == "theme":
        return [(ws.path("theme_design"), False)]
    if scope == "pacing":
        return [(ws.path("pacing_design"), False)]
    if scope == "world":
        return [(ws.path("world_bible"), False)]
    if scope == "opening":
        return [(ws.path("opening_design"), False)]
    if scope == "characters":
        card_dir = ws.path("character_card", name="_").parent
        return [(ws.path("characters_dir"), True), (card_dir, True), (ws.path("characters"), False)]
    raise ConceptError(f"未知范围 {scope!r}")


def _files_under(path: Path, is_dir: bool) -> List[Path]:
    if is_dir:
        return sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
    return [path] if path.is_file() else []


def _snapshot(ws: Workspace, scopes: List[str]) -> Dict[str, str]:
    """相对路径 → 内容指纹，覆盖所有范围的文件（用于判断哪些文件真的被改动了）。"""
    out: Dict[str, str] = {}
    for scope in scopes:
        for path, is_dir in scope_paths(ws, scope):
            for f in _files_under(path, is_dir):
                out[f.relative_to(ws.root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _backup_root(ws: Workspace, rid: str) -> Path:
    return ws.studio / "concept_backups" / rid


def backup_scopes(ws: Workspace, rid: str, scopes: List[str]) -> Dict[str, Any]:
    """修订前备份：每个路径按相对工作目录的结构复制一份，并记录清单（包括修订前不存在的路径）。"""
    root = _backup_root(ws, rid)
    if root.exists():
        shutil.rmtree(root)
    entries: List[Dict[str, Any]] = []
    for scope in scopes:
        for path, is_dir in scope_paths(ws, scope):
            rel = path.relative_to(ws.root).as_posix()
            existed = path.is_dir() if is_dir else path.is_file()
            entries.append({"scope": scope, "rel": rel, "dir": is_dir, "existed": existed})
            if not existed:
                continue
            dst = root / rel
            if is_dir:
                shutil.copytree(path, dst, dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
    manifest = {"entries": entries, "scopes": list(scopes)}
    atomic_write_json(root / "manifest.json", manifest)
    return manifest


def _revision_start_ids(ws: Workspace, scopes: List[str]) -> List[str]:
    """修订范围对应的生成步骤 id：作为「修订影响」的起点（起点之后的全部流程都要重跑）。"""
    ids: List[str] = []
    for sc in scopes:
        try:
            ids += [s.id for s in _scope_steps(ws, sc, "", "")]
        except ConceptError:
            pass    # 例如角色范围在没有蓝图时无法展开：其余范围照常取得起点
    return ids


def restore(ws: Workspace, rid: str) -> Dict[str, Any]:
    """撤销一次构思修订：把备份恢复回去，修订前不存在的文件删除，并标记下游为需重跑。"""
    from novel_writer import state as st
    from novel_writer.pipeline import build_plan
    from novel_writer.ondemand import load_request, save_request

    req = load_request(ws, "concept", rid)
    root = _backup_root(ws, rid)
    manifest = read_json(root / "manifest.json", default=None)
    if not isinstance(manifest, dict):
        raise ConceptError("找不到这次修订的备份，无法撤销")
    restored_files: List[str] = []
    for e in manifest["entries"]:
        target = ws.root / e["rel"]
        if e["existed"]:
            src = root / e["rel"]
            if e["dir"]:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(src, target)
                restored_files += [p.relative_to(ws.root).as_posix() for p in target.rglob("*") if p.is_file()]
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                restored_files.append(e["rel"])
        else:
            if e["dir"] and target.is_dir():
                shutil.rmtree(target)
            elif target.is_file():
                target.unlink()
    if restored_files:
        st.record_writer_paths(ws, restored_files, "author", kind="author")
    stale = st.mark_stale(ws, build_plan(ws))
    st.mark_revision_stale(ws, build_plan(ws), _revision_start_ids(ws, list(manifest.get("scopes") or [])))  # 撤销修订：恢复后的下游全部重跑
    from novel_writer import ideation_review as IR
    st.arm_revision_autorun(ws)
    reopened = IR.reopen_after_revision(ws, note="已撤销修订，正在自动续跑到构思审核")
    req["restored"] = True
    req["result"] = {**(req.get("result") or {}),
                     "outcome": "已撤销，恢复为修订前的内容" + ("；将自动续跑到构思审核" if reopened else ""),
                     "stale": stale, "auto_rerun_to_review": reopened}
    save_request(ws, req)
    return {"restored_files": len(restored_files), "stale": stale}


# ---------------------------------------------------------------- 步骤

HEARTBEAT_SEC = 60.0


def _next_run_no(ws: Workspace, step_id: str) -> int:
    """子步骤的下一个运行序号：每次重跑用新的 run_N 目录，不覆盖上次的 prompt、回执与 agent.log。"""
    base = ws.step_dir(step_id, 1).parent
    nums = [int(p.name.split("_")[1]) for p in base.glob("run_*")
            if p.is_dir() and p.name.split("_")[-1].isdigit()] if base.is_dir() else []
    return (max(nums) + 1) if nums else 1


@contextlib.contextmanager
def _heartbeat(ws: Workspace, label: str, every: float = HEARTBEAT_SEC):
    """模型调用期间每隔一段时间写一条「仍在调用」，让运行日志能看出它还在工作、已经等了多久。"""
    from novel_writer import state as st   # 在后台线程里使用，显式导入（线程里的 NameError 会被静默吞掉）
    stop = threading.Event()
    t0 = time.monotonic()

    def beat() -> None:
        while not stop.wait(every):
            st.log_line(ws, f"  ⏳ {label}：仍在调用模型，已等待 {int(time.monotonic() - t0)} 秒")

    th = threading.Thread(target=beat, name="concept-heartbeat", daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()


def _direction_step(step: S.StepDef, direction: str, rid: str, key: str, hint: str = "") -> S.StepDef:
    """复用原步骤定义，只替换 id（每次修订唯一）并在指令前加上新思路。

    hint：上一次自动重试失败的原因。带进指令，让 agent 避免重蹈覆辙。
    """
    retry = (f"\n【上一次尝试失败，请避免同样的问题】{hint}\n") if hint else ""
    return replace(step, id=f"cpt.{rid}.{key}",
                   instructions=NOTE_PREFIX + direction + NOTE_RULES + retry + (step.instructions or ""))


def _require_blueprint(ws: Workspace) -> Dict[str, Any]:
    from novel_writer import blueprint_store as BS
    bp = BS.load_for_ws(ws, strict=False)
    if not bp:
        raise ConceptError("还没有蓝图，请先完成「生成全书蓝图」（plan_blueprint），再修订这个范围")
    return bp


def _scope_steps(ws: Workspace, scope: str, direction: str, rid: str, hint: str = "") -> List[S.StepDef]:
    """某个范围要运行的步骤（已注入新思路）。"""
    if scope == "blueprint":
        # 蓝图草稿由 plan_blueprint 产出（校验 output_check=blueprint）；拆分与下游步骤由作者之后运行
        return [_direction_step(S.plan_blueprint_step(), direction, rid, scope, hint)]
    if scope == "theme":
        return [_direction_step(S.design_theme_step(), direction, rid, scope, hint)]
    if scope == "pacing":
        return [_direction_step(S.design_pacing_step(), direction, rid, scope, hint)]
    if scope == "world":
        return [_direction_step(S.build_world_step(), direction, rid, scope, hint)]
    if scope == "opening":
        return [_direction_step(S.design_opening_step(), direction, rid, scope, hint)]
    if scope == "characters":
        from novel_writer.pipeline import main_characters
        roles = main_characters(_require_blueprint(ws))
        if not roles:
            raise ConceptError("蓝图里没有主要角色，无法修订角色")
        steps = [_direction_step(S.design_character_step(n), direction, rid, f"{scope}.{n}", hint) for n in roles]
        steps.append(_direction_step(S.build_characters_step(roles), direction, rid, f"{scope}.build", hint))
        return steps
    raise ConceptError(f"未知范围 {scope!r}")


# ---------------------------------------------------------------- 处理器

def h_concept(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from novel_writer import chapter_ops as C
    from novel_writer import state as st
    from novel_writer.ondemand import OnDemandError, load_request, save_request
    from novel_writer.pipeline import build_plan
    from novel_writer.runner import NeedsUser, _answers_file, run_skill_step

    if invoker is None:
        raise OnDemandError("构思修订需要执行器（invoker）")
    rid = step.extra["request"].split("_", 1)[1]
    req = load_request(ws, "concept", rid)
    direction, scopes = validate(req.get("direction"), req.get("scopes"))

    deferred: List[str] = []
    if "blueprint" in scopes:
        answers_path = _answers_file(ws, step.id)
        answers = (read_json(answers_path, default=None) or {}).get("answers") or {}
        decision = str(answers.get("decision") or "")
        if answers_path.exists() and decision:
            answers_path.unlink()
        if decision.startswith("取消"):
            req["result"] = {"outcome": "已取消（未改动任何内容）"}
            save_request(ws, req)
            return {"summary": "作者取消了蓝图修订"}
        if not decision.startswith("确认"):
            raise NeedsUser(
                "蓝图是全书级改动：确认后将按新思路改写蓝图草稿，依赖蓝图的步骤会标为需重跑。"
                "改写前的内容会备份，可以撤销。",
                [{"id": "decision", "question": f"修订思路：{direction[:80]}",
                  "options": ["确认，改写蓝图", "取消"]}],
            )
        deferred = [s for s in scopes if s != "blueprint"]
        scopes = ["blueprint"]

    before = {sc: _snapshot(ws, [sc]) for sc in scopes}
    backup_scopes(ws, rid, scopes)
    own = st.get_step_state(ws, step.id)
    hint = str(own.get("retry_hint") or "").strip()[:800]
    attempt = int(own.get("attempts") or 1)
    if attempt > 1:
        st.log_line(ws, f"  这是第 {attempt} 次尝试；上一次失败原因：{hint or '（未记录）'}（已带入本次提示）")
    st.log_line(ws, f"✎ 构思修订开始：范围 {'、'.join(SCOPE_LABELS[x].split('（')[0] for x in scopes)}；"
                    f"思路：{direction[:60]}")
    st.log_line(ws, "  已备份修订前的产物（可在界面撤销）")

    for scope in SCOPE_ORDER:
        if scope not in scopes:
            continue
        label = SCOPE_LABELS[scope].split("（")[0]
        for sub in _scope_steps(ws, scope, direction, rid, hint):
            st.log_line(ws, f"  ↳ {label}：{sub.title or sub.id}，调用模型中（通常需要数分钟）")
            t0 = time.monotonic()
            try:
                with _heartbeat(ws, label):
                    run_skill_step(ws, sub, invoker=invoker, settings=settings, run_no=_next_run_no(ws, sub.id))
            except Exception as exc:  # noqa: BLE001 - 原因写入运行日志后继续抛出，由运行器记录失败
                st.log_line(ws, f"  ✘ {label} 失败（用时 {int(time.monotonic() - t0)} 秒）：{type(exc).__name__}：{str(exc)[:300]}")
                raise
            st.log_line(ws, f"  ✔ {label} 已完成（用时 {int(time.monotonic() - t0)} 秒）")
        if scope == "characters":
            st.log_line(ws, "  ↳ 刷新角色索引")
            C.h_characters_refresh(ws, S.characters_refresh_step(), settings)   # 角色文件改完后刷新索引（确定性）

    after = {sc: _snapshot(ws, [sc]) for sc in scopes}
    changed_scopes = [sc for sc in scopes if before[sc] != after[sc]]
    changed_files = sorted(f for sc in changed_scopes
                           for f in set(before[sc]) | set(after[sc]) if before[sc].get(f) != after[sc].get(f))
    if changed_files:
        st.record_writer_paths(ws, changed_files, "author", kind="author")
    stale = st.mark_stale(ws, build_plan(ws))
    st.mark_revision_stale(ws, build_plan(ws), _revision_start_ids(ws, scopes))   # 修订影响范围之后的全部流程重跑
    if changed_files:
        st.arm_revision_autorun(ws)     # 修改意见产生的需重跑：托管挡自动重跑
    # 构思审核开关开启时：修订后审核自动重新打开（修改循环）
    from novel_writer import ideation_review as IR
    reopened = IR.reopen_after_revision(ws) if changed_files else False

    if "blueprint" in changed_scopes:
        outcome = "蓝图已按新思路改写"
        if deferred:
            outcome += "；其余范围需在「蓝图拆分」完成后重新修订：" + "、".join(SCOPE_LABELS[s] for s in deferred)
    else:
        outcome = "已按新思路修订" if changed_files else "运行完成，但产物没有变化"
    if reopened:
        outcome += "；将自动续跑到构思审核，之后请再次审核"
    st.log_line(ws, f"✔ 构思修订完成：{outcome}")
    if changed_scopes:
        st.log_line(ws, "  实际改动：" + "、".join(SCOPE_LABELS[x].split("（")[0] for x in changed_scopes)
                    + f"（{len(changed_files)} 个文件）；需重跑 {len(stale)} 步")
    else:
        st.log_line(ws, "  没有文件内容发生变化")
    req["result"] = {"outcome": outcome, "changed": changed_scopes, "changed_files": changed_files,
                     "deferred": deferred, "stale": stale, "auto_rerun_to_review": reopened}
    save_request(ws, req)
    C._log(ws, 0, f"构思修订（{direction[:30]}）：{outcome}；需重跑 {len(stale)} 步")
    return {"summary": f"{outcome}；下游需重跑 {len(stale)} 步"}


def handler(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """供 ondemand.HANDLERS 注册的入口（延迟导入，避免循环依赖）。"""
    return h_concept(ws, step, settings, invoker=invoker)
