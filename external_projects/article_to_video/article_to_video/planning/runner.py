"""planning/runner.py — 规划的落盘与状态：生成大纲（S1+S2）、保存编辑、确认（C0）。

文件：
  plan/groups.json   分节组（不含正文，正文可由源文章重算）
  plan/digests.json  分节摘要（engine、input_sha 用于跳过未变化的组）
  plan/spine.json    大纲（含 confirmed 标记）
  plan/raw_*.txt     模型两次都不合格时的原始输出
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import joblog
from ..project_store import append_progress, load_project, now_iso, read_json, write_json_atomic, write_text_atomic
from ..validators.coverage import validate_spine
from .chunks import build_groups, section_catalog
from .errors import PlanError
from .llm import llm_digest, llm_spine
from .prompts import PROMPT_VERSION
from .rules import rules_digest, rules_spine, section_claims
from .spine_math import recompute_budgets, renumber


def _context(project_dir: Path) -> Dict[str, Any]:
    lines = (project_dir / "source" / "article.md").read_text(encoding="utf-8").splitlines()
    doc = read_json(project_dir / "source" / "blocks.json")
    project = load_project(project_dir)
    return {
        "lines": lines,
        "doc": doc,
        "project": project,
        "catalog": section_catalog(lines, doc),
        "blocks_by_id": {b["id"]: b for b in doc["blocks"]},
    }


def load_spine(project_dir: Path) -> Optional[Dict[str, Any]]:
    p = Path(project_dir) / "plan" / "spine.json"
    return read_json(p) if p.is_file() else None


def load_digests(project_dir: Path) -> List[Dict[str, Any]]:
    p = Path(project_dir) / "plan" / "digests.json"
    return read_json(p)["digests"] if p.is_file() else []


def _save_raw(project_dir: Path, name: str, raw: Optional[str]) -> None:
    if raw:
        write_text_atomic(Path(project_dir) / "plan" / f"raw_{name}.txt", raw)


def _input_sha(group: Dict[str, Any], title: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{title}|{group['text']}".encode("utf-8")).hexdigest()


def _validate_or_raise(spine: Dict[str, Any], ctx: Dict[str, Any], digests: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    section_ids = [c["id"] for c in ctx["catalog"]]
    result = validate_spine(spine, section_ids, digests)
    if result["errors"]:
        raise PlanError("大纲不合格：" + "；".join(result["errors"]))
    return result


def plan_outline(project_dir: Path, *, engine: str = "llm", llm=None,
                 force: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """S1 分节摘要 + S2 大纲。engine: rules（离线）| llm（需要 llm 实例）。

    已确认的大纲不会被覆盖，除非 force=True。LLM 失败时抛 PlanError，不写入任何结果。
    """
    project_dir = Path(project_dir)
    if engine not in ("rules", "llm"):
        raise PlanError(f"未知的生成方式：{engine}")
    if engine == "llm" and llm is None:
        raise PlanError("模型生成大纲需要文本模型（未配置 mini_agent 的模型）。"
                        "如需离线生成，请改选「离线规则」，或修正模型配置后重试")

    existing = load_spine(project_dir)
    if existing and existing.get("confirmed") and not force:
        raise PlanError("大纲已确认。若要重新生成，请先取消确认或指定覆盖（force）")

    ctx = _context(project_dir)
    project = ctx["project"]
    title = project["title"]
    author = project["branding"].get("author", "")
    from ..app_settings import load_effective_project  # 延迟导入
    st = load_effective_project(project_dir)["settings"]
    meta = {"title": title, "author": author, "mode": st.get("mode", "condensed"),
            "target_minutes": float(st.get("target_minutes", 10.0))}

    groups = build_groups(ctx["lines"], ctx["doc"])
    if not groups:
        raise PlanError("文章没有可规划的内容")

    joblog.bound_log(f"开始生成大纲：{'离线规则' if engine == 'rules' else '模型'}，共 {len(groups)} 组")
    old = {d["group_id"]: d for d in load_digests(project_dir)} if engine == "llm" else {}
    digests: List[Dict[str, Any]] = []
    for g in groups:
        sha = _input_sha(g, title)
        if engine == "rules":
            digests.append(rules_digest(g, ctx["blocks_by_id"]))
        elif g["id"] in old and old[g["id"]].get("input_sha") == sha:
            joblog.bound_log(f"节组 {g['id']}：输入未变，复用已有摘要")
            digests.append(old[g["id"]])  # 断点续跑：输入未变，复用已有摘要
        else:
            try:
                d, _raw = llm_digest(g, llm, title)
            except PlanError as exc:
                _save_raw(project_dir, g["id"], exc.raw)
                raise
            d["input_sha"] = sha
            digests.append(d)

    if engine == "rules":
        spine = rules_spine(ctx["catalog"], digests, meta, section_claims(ctx["doc"]))
    else:
        try:
            spine, _raw = llm_spine(digests, ctx["catalog"], meta, llm)
        except PlanError as exc:
            _save_raw(project_dir, "spine", exc.raw)
            raise
    _validate_or_raise(spine, ctx, digests)

    stamp = now_iso(now)
    write_json_atomic(project_dir / "plan" / "groups.json", {"groups": [
        {k: v for k, v in g.items() if k != "text"} for g in groups]})
    write_json_atomic(project_dir / "plan" / "digests.json", {"engine": engine, "digests": digests})
    write_json_atomic(project_dir / "plan" / "spine.json", spine)

    joblog.bound_log(f"大纲已保存：{len(spine['parts'])} 章（待你在「大纲」页确认）", level="ok")
    project["stage"] = "P1_outlined"
    project["plan"] = {"engine": engine, "generated_at": stamp, "groups": len(groups),
                       "parts": len(spine["parts"]), "spine_confirmed": False}
    write_json_atomic(project_dir / "project.json", project)
    append_progress(project_dir, f"生成大纲：engine={engine}，{len(groups)} 组，{len(spine['parts'])} 章", now)
    return spine


def save_spine(project_dir: Path, spine: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """保存人工编辑后的大纲。校验失败不写入；保存后大纲变为未确认。"""
    project_dir = Path(project_dir)
    ctx = _context(project_dir)
    spine = dict(spine)
    spine["parts"] = [dict(p) for p in spine["parts"]]
    renumber(spine)
    recompute_budgets(spine)
    spine["confirmed"] = False
    _validate_or_raise(spine, ctx, load_digests(project_dir))
    write_json_atomic(project_dir / "plan" / "spine.json", spine)
    project = ctx["project"]
    project["plan"] = {**project.get("plan", {}), "parts": len(spine["parts"]), "spine_confirmed": False}
    project["updated_at"] = now_iso(now)
    write_json_atomic(project_dir / "project.json", project)
    append_progress(project_dir, "保存大纲编辑（未确认）", now)
    return spine


def confirm_spine(project_dir: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """C0 确认：大纲通过 V1 校验后标记为已确认。"""
    project_dir = Path(project_dir)
    spine = load_spine(project_dir)
    if spine is None:
        raise PlanError("还没有大纲，请先生成")
    ctx = _context(project_dir)
    _validate_or_raise(spine, ctx, load_digests(project_dir))
    spine["confirmed"] = True
    spine["confirmed_at"] = now_iso(now)
    write_json_atomic(project_dir / "plan" / "spine.json", spine)
    project = ctx["project"]
    project["plan"] = {**project.get("plan", {}), "spine_confirmed": True}
    write_json_atomic(project_dir / "project.json", project)
    append_progress(project_dir, "确认大纲（C0）", now)
    return spine
