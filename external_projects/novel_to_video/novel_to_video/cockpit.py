"""cockpit.py — 驾驶舱数据：项目总览、各章进度、待处理事项，以及“下一步”。

“下一步”按优先级依次判断，第一个命中的即为当前步骤（与设计方案的检查点顺序一致）：
  1. 没有选中章节        → 导入与配置
  2. 后台正在运行        → 显示运行状态（不推荐其他操作）
  3. 有章节失败/停止      → 章节生产（查看失败原因）
  4. 有补建角色待确认     → 补建角色审核
  5. 有角色性别待确认     → 性别确认
  6. 全书素材未就绪       → 全书素材
  7. 有镜头待逐镜审核     → 章节生产（逐镜审核）
  8. 有章节待运行/过期    → 挡位与队列（运行）
  9. 全部完成，未合成     → 全书合成（需人工确认）
 10. 成片已完成           → 完成
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from . import chapter_io as cio
from . import gender_review
from . import runlock
from . import runtime as rt
from .call_log import count_calls
from .project_store import load_project, read_json
from .shot_review import shot_states
from .steps import final_compose as fc
from .steps import global_visual as gv


def _budget_usage(project_dir: Path) -> Dict[str, Any]:
    from .batch import _minutes_used
    proj = read_json(project_dir / "project.json")
    budget = proj.get("budget") or {}
    used = {"video": count_calls(project_dir, "video"), "image": count_calls(project_dir, "image"),
            "minutes": round(_minutes_used(project_dir), 2)}
    limits = {"video": budget.get("max_video_calls"), "image": budget.get("max_image_calls"),
              "minutes": budget.get("max_minutes")}
    return {"used": used, "limits": limits}


def _chapter_rows(project_dir: Path) -> List[Dict[str, Any]]:
    rows = []
    for r in load_project(project_dir)["chapters"]:
        if not r.get("selected"):
            continue
        no = r["no"]
        states = shot_states(project_dir, no)
        c = Counter(states.values())
        cdir = cio.chapter_dir(project_dir, no)
        rows.append({
            "no": no, "title": r.get("title"), "status": r.get("status") or "pending",
            "message": r.get("message") or "",
            "shots_total": len(states), "approved": c.get("approved", 0),
            "waiting": c.get("pending", 0) + c.get("missing", 0) + c.get("stale", 0) + c.get("rejected", 0),
            "failed": c.get("failed", 0),
            "stages": cio.load_state(project_dir, no).get("stages", {}),
            "has_output": (cdir / f"chapter_{no:03d}.mp4").is_file(),
        })
    return rows


def next_step(data: Dict[str, Any]) -> Dict[str, str]:
    """根据汇总数据给出当前步骤。返回 {key, title, reason, page}。"""
    if not data["selected"]:
        return {"key": "select", "title": "选择章节", "reason": "还没有选中任何章节", "page": "📥 导入导出目录"}
    if data["run"]["busy"]:
        return {"key": "running", "title": "后台运行中", "reason": data["run"].get("message") or "队列正在运行",
                "page": "🧭 驾驶舱（实时面板）"}
    failed = [c for c in data["chapters"] if c["status"] in ("failed",)]
    if failed:
        nos = "、".join(str(c["no"]) for c in failed[:5])
        return {"key": "fix", "title": "处理失败章节", "reason": f"第 {nos} 章失败，请查看原因后修正并重跑",
                "page": "🎬 章节生产"}
    waiting_assets = [c for c in data["chapters"] if c["status"] == "waiting_assets"]
    if waiting_assets:
        return {"key": "auto_add", "title": "确认补建角色", "reason": f"{len(waiting_assets)} 章等待补建角色确认",
                "page": "🧩 补建角色审核"}
    if data["gender"]["pending"]:
        return {"key": "gender", "title": "确认角色性别",
                "reason": f"还有 {data['gender']['pending']} 个角色性别未确认（配音选音依赖性别）",
                "page": "⚧ 性别确认"}
    if not data["assets"]["ready"]:
        return {"key": "assets", "title": "完成全书素材", "reason": f"{data['assets']['not_ready']} 个实体的设定或定妆图未通过",
                "page": "🎭 全书素材"}
    reviewing = [c for c in data["chapters"] if c["status"] == "waiting_review" or c["waiting"]]
    if reviewing:
        n = sum(c["waiting"] for c in reviewing)
        return {"key": "review", "title": "逐镜审核", "reason": f"{n} 个小场景等待审核（{len(reviewing)} 章）",
                "page": "🎬 章节生产"}
    todo = [c for c in data["chapters"] if c["status"] not in ("done",)]
    if todo:
        return {"key": "run", "title": "运行队列", "reason": f"{len(todo)} 章待运行或需重跑",
                "page": "🚦 挡位与队列"}
    final = data["final"]
    if not final["exists"] or final["stale"]:
        if not final["confirmed"]:
            return {"key": "confirm", "title": "全书合成（需人工确认）", "reason": "所有章节已完成，请审看后确认合成",
                    "page": "🎞️ 全书合成"}
        return {"key": "compose", "title": "合成全书", "reason": "已确认，可以合成全书成片",
                "page": "🎞️ 全书合成"}
    return {"key": "done", "title": "已完成", "reason": "全书成片已生成", "page": "🎞️ 全书合成"}


def build_cockpit(project_dir: Path) -> Dict[str, Any]:
    proj = read_json(project_dir / "project.json")
    chapters = _chapter_rows(project_dir)
    selected = [c["no"] for c in chapters]
    vis = gv.visual_status(project_dir)
    pend = gender_review.pending(project_dir)
    chk = fc.check_chapters_ready(project_dir) if selected else {"ready": False, "problems": []}
    conf = fc.confirm_state(project_dir)
    stale_flag = (project_dir / "exports" / "stale.json").is_file()
    runst = rt.read_state(project_dir)
    busy = runst.get("state") in ("running", "stopping") or runlock.inspect(project_dir)["held"]
    data: Dict[str, Any] = {
        "title": proj.get("title"), "gear": proj.get("gear", "G0"),
        "selected": selected, "chapters": chapters,
        "done": sum(1 for c in chapters if c["status"] == "done"),
        "shots": {"total": sum(c["shots_total"] for c in chapters),
                  "approved": sum(c["approved"] for c in chapters),
                  "waiting": sum(c["waiting"] for c in chapters),
                  "failed": sum(c["failed"] for c in chapters)},
        "gender": {"pending": len(pend)},
        "assets": {"ready": vis["ready"], "not_ready": sum(1 for r in vis["rows"] if r["missing_fields"]
                                                          or r["text_status"] != "approved"
                                                          or r["portrait_status"] != "approved")},
        "final": {"exists": (project_dir / "exports" / "video.mp4").is_file(), "stale": stale_flag,
                  "confirmed": bool(conf.get("confirmed")), "ready": chk["ready"],
                  "problems": chk.get("problems", [])},
        "run": {"busy": busy, "state": runst.get("state", "idle"), "chapter": runst.get("chapter"),
                "phase": runst.get("phase"), "message": runst.get("message", ""),
                "history": runst.get("history", [])[-10:], "gear": runst.get("gear"),
                "stop_requested": rt.stop_requested(project_dir)},
        "budget": _budget_usage(project_dir),
    }
    data["next"] = next_step(data)
    return data
