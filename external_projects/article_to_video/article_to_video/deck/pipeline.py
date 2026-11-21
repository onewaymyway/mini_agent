"""新方案的一键流程：规划 → 讲稿 → 配音 → 合成 → 导出，挡位与现有方案一致。

挡位对应的人工检查点（新方案）：
  G0：规划、讲稿、终审都需要人工确认；
  G1：规划与讲稿通过校验后自动确认，只有终审需要人工；
  G2：全自动，必须设置预算上限（与现有方案相同的规则）；
  G3：完全无人值守，无检查点，终审自动通过并导出，不要求预算上限。

流程是可重入的：停在检查点后，确认相应内容再次运行即可继续；已完成且未变化的阶段会复用结果。
状态写入 deck/run_status.json，格式与现有流水线状态相近。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .. import cancel, joblog
from ..app_settings import effective_group
from ..pipeline.gates import GEARS, check_budget_requirement, manual_checkpoints
from ..planning.errors import PlanError
from ..planning.runner import confirm_spine, load_spine, plan_outline
from ..project_store import now_iso, write_json_atomic
from .compose import compose_deck_part, export_deck_video
from .script import AUTO_ATTEMPTS, MAX_ATTEMPTS, confirm_script, load_script, plan_script, script_status
from .store import confirm_deck, deck_home, deck_status, load_deck, plan_deck
from .voice import synthesize_deck

STAGES = ("plan", "script", "voice", "compose", "export")
_MANUAL = {
    "G0": {"plan", "script", "export"},
    "G1": {"export"},
    "G2": set(),
    "G3": set(),
}
STAGE_NAME = {"outline": "大纲", "plan": "幻灯片规划", "script": "讲稿", "voice": "配音", "compose": "合成", "export": "导出"}


def deck_manual_checkpoints(gear: str) -> Set[str]:
    if gear not in _MANUAL:
        raise PlanError(f"未知挡位：{gear}（可选 {' / '.join(GEARS)}）")
    return set(_MANUAL[gear])


def _parts(pdir: Path):
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        raise PlanError("请先确认大纲（C0），再运行新方案")
    return spine["parts"]


def ensure_outline(pdir: Path, gear: str, llm=None, now: Optional[datetime] = None) -> Optional[str]:
    """新方案开始前保证大纲已确认（与旧方案的大纲阶段规则一致）：
    没有大纲则按生效设置生成（模型版或离线规则）；已确认则直接通过；
    人工挡位设有 C0 检查点则返回等待原因；其余挡位（如 G3）自动确认。返回 None 表示可以继续。"""
    spine = load_spine(pdir)
    if spine is None:
        engine = effective_group(Path(pdir), "pipeline")["outline_engine"]
        if engine == "llm" and llm is None:
            raise PlanError("大纲选择了模型版，但未配置文本模型")
        joblog.bound_log(f"▶ 生成大纲（{'模型' if engine == 'llm' else '离线规则'}）")
        plan_outline(Path(pdir), engine=engine, llm=llm if engine == "llm" else None, now=now)
        spine = load_spine(pdir)
    if spine.get("confirmed"):
        return None
    if "outline" in manual_checkpoints(gear):
        return "C0 大纲待人工确认（界面：🗺️ 大纲）"
    confirm_spine(Path(pdir), now=now)                    # 未通过 V1 校验时抛出，流水线记为失败
    joblog.bound_log("大纲已自动确认（本挡位无 C0 人工检查点）")
    return None


def _save(pdir: Path, status: Dict[str, Any], now: Optional[datetime]) -> None:
    status["updated_at"] = now_iso(now)
    write_json_atomic(deck_home(pdir) / "run_status.json", status)


def run_deck_pipeline(pdir: Path, *, gear: str = "G0", until: str = "export", approve: bool = False,
                      max_images: Optional[int] = None, max_minutes: Optional[float] = None,
                      llm=None, tts=None, image=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """运行新方案的一键流程，返回状态 {state: done|waiting|failed, stage, waiting, error, steps, outputs}。"""
    pdir = Path(pdir)
    if until not in STAGES:
        raise PlanError(f"未知阶段：{until}")
    check_budget_requirement(gear, max_images, max_minutes)
    manual = deck_manual_checkpoints(gear)
    if gear == "G3":
        approve = True                       # G3：终审自动通过并导出
    parts: List[Dict[str, Any]] = []
    status: Dict[str, Any] = {"gear": gear, "until": until, "state": "running", "steps": [],
                              "waiting": None, "error": None, "outputs": [], "started_at": now_iso(now)}
    deck_home(pdir).mkdir(parents=True, exist_ok=True)
    _save(pdir, status, now)
    stop_after = STAGES.index(until)

    def done_stage(stage: str, detail: str) -> None:
        status["steps"].append({"stage": stage, "result": "ok", "detail": detail})
        _save(pdir, status, now)
        joblog.bound_log(f"✔ {STAGE_NAME[stage]}完成：{detail}", level="ok")

    def start_stage(stage: str) -> None:
        joblog.bound_log(f"▶ 开始{STAGE_NAME[stage]}（{len(parts)} 章：{'、'.join(p['id'] for p in parts)}）")

    def wait(stage: str, detail: str) -> Dict[str, Any]:
        status.update(state="waiting", waiting={"stage": stage, "detail": detail})
        status["steps"].append({"stage": stage, "result": "waiting", "detail": detail})
        _save(pdir, status, now)
        joblog.bound_log(f"新方案停在「{STAGE_NAME[stage]}」：{detail}", level="warn")
        return status

    try:
        joblog.bound_log(f"新方案开始运行：挡位 {gear}，运行到「{STAGE_NAME[until]}」"
                         + ("（终审自动通过）" if approve and gear == "G3" else ""))
        # 0. 大纲（C0）：没有则生成；G3 等挡位自动确认；人工挡位停下等待
        waiting_reason = ensure_outline(pdir, gear, llm=llm, now=now)
        if waiting_reason:
            return wait("outline", waiting_reason)
        parts = _parts(pdir)                  # 大纲的生成与确认已在日志中记录，不计入流水线步骤
        # 1. 幻灯片规划
        start_stage("plan")
        for p in parts:
            cancel.check()
            plan_deck(pdir, p["id"], llm=llm)
        if "plan" in manual:
            pending = [p["id"] for p in parts if not deck_status(pdir, p["id"])["confirmed"]]
            if pending:
                return wait("plan", f"请确认幻灯片规划：{'、'.join(pending)}")
        else:
            for p in parts:
                if not deck_status(pdir, p["id"])["confirmed"]:
                    confirm_deck(pdir, p["id"], now=now)
        done_stage("plan", f"{len(parts)} 章规划就绪")
        if STAGES.index("plan") >= stop_after:
            return _finish(pdir, status, now)

        # 2. 讲稿：全自动挡位持续修改直到通过；人工挡位次数较少，失败即停下来让人检查
        attempts = AUTO_ATTEMPTS if gear in ("G2", "G3") else MAX_ATTEMPTS
        if llm is None:
            raise PlanError("新方案需要文本模型生成讲稿（mini_agent 配置）")
        start_stage("script")
        for p in parts:
            cancel.check()
            plan_script(pdir, p["id"], llm=llm, part_title=p["title"], max_attempts=attempts)
        if "script" in manual:
            pending = [p["id"] for p in parts if not script_status(pdir, p["id"])["confirmed"]]
            if pending:
                return wait("script", f"请确认讲稿：{'、'.join(pending)}")
        else:
            for p in parts:
                if not script_status(pdir, p["id"])["confirmed"]:
                    confirm_script(pdir, p["id"], now=now)
        done_stage("script", f"{len(parts)} 章讲稿就绪")
        if STAGES.index("script") >= stop_after:
            return _finish(pdir, status, now)

        # 3. 配音
        if tts is None:
            raise PlanError("新方案需要语音引擎（配音）")
        start_stage("voice")
        for p in parts:
            cancel.check()
            synthesize_deck(pdir, p["id"], tts, now=now)
        done_stage("voice", f"{len(parts)} 章配音完成")
        if STAGES.index("voice") >= stop_after:
            return _finish(pdir, status, now)

        # 4. 合成
        start_stage("compose")
        for p in parts:
            cancel.check()
            compose_deck_part(pdir, p["id"], now=now, image=image, llm=llm)
        done_stage("compose", f"{len(parts)} 章视频合成完成")
        if STAGES.index("compose") >= stop_after:
            return _finish(pdir, status, now)

        # 5. 导出（终审）
        if "export" in manual and not approve:
            return wait("export", "终审待人工确认：确认成片后，以「确认终审」再次运行即可导出")
        joblog.bound_log("▶ 开始导出新方案成片")
        out = export_deck_video(pdir, now=now)
        status["outputs"] = [str(out.name)]
        done_stage("export", f"导出 {out.name}")
        return _finish(pdir, status, now)
    except PlanError as exc:
        status.update(state="failed", error=str(exc))
        _save(pdir, status, now)
        joblog.bound_log(f"新方案运行失败：{exc}", level="error")
        return status
    except cancel.Cancelled:
        status.update(state="stopped", error="用户请求停止")
        _save(pdir, status, now)
        return status


def _finish(pdir: Path, status: Dict[str, Any], now: Optional[datetime]) -> Dict[str, Any]:
    status["state"] = "done"
    _save(pdir, status, now)
    joblog.bound_log("新方案运行完成", level="ok")
    return status


def load_run_status(pdir: Path) -> Optional[Dict[str, Any]]:
    from ..project_store import read_json
    p = deck_home(Path(pdir)) / "run_status.json"
    return read_json(p) if p.is_file() else None


def reset_deck(pdir: Path, *, keep_audio: bool = False, confirm: bool = False) -> list:
    """清空新方案的全部产出（规划、讲稿、时间线、视频、成片、封面、运行记录）。

    只触及 deck/ 目录（与现有方案的 parts/、exports/ 平级），大纲与现有方案不受影响。
    keep_audio=True 时保留逐句配音缓存（重新运行时不必重新配音）。未确认（confirm=False）不删除任何文件。
    """
    import shutil

    if not confirm:
        raise PlanError("清空新方案数据需要确认")
    pdir = Path(pdir)
    home = deck_home(pdir)
    removed: list = []
    if not home.is_dir():
        return removed
    parts_dir = home / "parts"
    if parts_dir.is_dir():
        for part in sorted(parts_dir.iterdir()):
            if keep_audio and (part / "audio").is_dir():
                for child in list(part.iterdir()):
                    if child.name == "audio":
                        continue
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
                    removed.append(f"deck/parts/{part.name}/{child.name}")
                continue
            shutil.rmtree(part)
            removed.append(f"deck/parts/{part.name}/")
    for name in ("exports", "run_status.json", "errors.json"):
        target = home / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(f"deck/{name}/")
        elif target.is_file():
            target.unlink()
            removed.append(f"deck/{name}")
    joblog.bound_log(f"已清空新方案数据（{len(removed)} 项）" + ("，保留配音缓存" if keep_audio else ""), level="warn")
    return removed


def regenerate_deck(pdir: Path, *, keep_audio: bool = False, confirm: bool = False, **run_kwargs) -> Dict[str, Any]:
    """清空新方案的当前数据，然后按给定挡位重新运行整条流程。返回 {"removed": [...], "status": ...}。"""
    removed = reset_deck(pdir, keep_audio=keep_audio, confirm=confirm)
    status = run_deck_pipeline(pdir, **run_kwargs)
    return {"removed": removed, "status": status}
