"""地点细化 L5：一键依次执行（后台任务用）。

顺序：按区域逐个处理 → 提议场景点 → 直接写入（不再逐个勾选）→ 为新场景点生成设定图（逐张生图）；
全部区域处理完后，补齐仍缺设定图的场景点。区域记录 presplit_status=done，重复运行时跳过。
用户请求停止时在当前步骤结束后返回；请求中的模型或生图调用由 cancel 机制立即中断。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import cancel, joblog
from .. import runtime as _rt
from ..project_store import read_json, write_json_atomic
from ..providers.text import TextLLM
from .global_visual import StepError
from . import location_presplit as lp


def _doc(project_dir: Path) -> Dict[str, Any]:
    return read_json(Path(project_dir) / "global" / "locations.json")


def _spot_by_id(project_dir: Path, sid: Optional[str]) -> Optional[Dict[str, Any]]:
    if not sid:
        return None
    return next((l for l in _doc(project_dir)["locations"] if l["id"] == sid), None)


def mark_area(project_dir: Path, area_id: str, added: List[str]) -> None:
    doc = _doc(project_dir)
    area = next((l for l in doc["locations"] if l["id"] == area_id), None)
    if area is None:
        return
    area["presplit_status"] = "done"
    area["presplit_added"] = list(added)
    write_json_atomic(Path(project_dir) / "global" / "locations.json", doc)


def fill_sheets(project_dir: Path, spot_ids: List[str], text_llm: TextLLM, image) -> Dict[str, Any]:
    """为场景点提议设定图与标志物，并逐张生成图片。单个场景点失败不影响其它场景点。"""
    from . import global_visual
    from . import location_sheets as LS

    summary: Dict[str, Any] = {"done": [], "failed": [], "skipped": [], "stopped": None}
    for sid in spot_ids:
        if _rt.stop_requested(project_dir):
            summary["stopped"] = "用户请求停止"
            break
        spot = _spot_by_id(project_dir, sid)
        if spot is None:
            continue
        parent = _spot_by_id(project_dir, spot.get("parent_id"))
        try:
            sheets, markers, basis = LS.propose(project_dir, spot, parent, text_llm)
            LS.save_sheets(project_dir, sid, sheets, markers, basis)
            res = global_visual.portrait_pass(project_dir, image, only_missing=True, ids=[sid])
        except StepError as exc:
            summary["failed"].append({"id": sid, "error": str(exc)})
            joblog.log(project_dir, "assets", f"设定图失败：{spot.get('name')}：{exc}", level="error")
            continue
        summary["done"] += res.get("done") or []
        summary["failed"] += res.get("failed") or []
        summary["skipped"] += res.get("skipped") or []
        if res.get("stopped"):
            summary["stopped"] = res["stopped"]
            break
    return summary


def run_all(project_dir: Path, text_llm: TextLLM, image, *, do_areas: bool = True, do_sheets: bool = True) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    if not lp.presplit_enabled(project_dir):
        raise StepError("区域预拆解已关闭（全书素材页可重新开启）")
    summary: Dict[str, Any] = {"done": [], "failed": [], "skipped": [], "stopped": None}
    joblog.log(project_dir, "assets",
               f"区域预拆解（一键依次执行）开始：区域{'是' if do_areas else '否'}，设定图{'是' if do_sheets else '否'}")

    if do_areas:
        areas = [l for l in _doc(project_dir)["locations"] if (l.get("level") or "area") == "area"]
        for area in areas:
            if _rt.stop_requested(project_dir):
                summary["stopped"] = "用户请求停止"
                break
            if area.get("presplit_status") == "done":
                summary["skipped"].append(area["id"])
                joblog.log(project_dir, "assets", f"跳过区域「{area.get('name')}」（已预拆解）")
                continue
            try:
                joblog.log(project_dir, "assets", f"区域「{area.get('name')}」：提议场景点")
                cands = lp.propose_spots(project_dir, area["id"], text_llm)
                added = lp.confirm_spots(project_dir, area["id"], cands, text_llm) if cands else []
                mark_area(project_dir, area["id"], added)
                summary["done"].append(f"区域「{area.get('name')}」：新增 {len(added)} 个场景点")
                joblog.log(project_dir, "assets", f"区域「{area.get('name')}」：新增 {len(added)} 个场景点", level="ok")
                if do_sheets and added:
                    res = fill_sheets(project_dir, added, text_llm, image)
                    summary["done"] += [f"设定图：{x}" for x in res["done"]]
                    summary["failed"] += res["failed"]
                    summary["skipped"] += res["skipped"]
                    if res.get("stopped"):
                        summary["stopped"] = res["stopped"]
                        break
            except cancel.Cancelled:
                raise
            except StepError as exc:
                summary["failed"].append({"id": area["id"], "error": str(exc)})
                joblog.log(project_dir, "assets", f"区域「{area.get('name')}」失败：{exc}", level="error")

    if do_sheets and not summary["stopped"]:
        pending = [l["id"] for l in _doc(project_dir)["locations"] if l.get("level") == "spot" and not l.get("sheets")]
        if pending:
            joblog.log(project_dir, "assets", f"补齐设定图：{len(pending)} 个场景点尚未生成设定图")
            res = fill_sheets(project_dir, pending, text_llm, image)
            summary["done"] += [f"设定图：{x}" for x in res["done"]]
            summary["failed"] += res["failed"]
            summary["skipped"] += res["skipped"]
            summary["stopped"] = res.get("stopped")
    return summary
