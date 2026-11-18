"""planning/runner.py — 规划的落盘与状态：生成大纲（S1+S2）、保存编辑、确认（C0）。

文件：
  plan/groups.json   分节组（不含正文，正文可由源文章重算）
  plan/digests.json  分节摘要（engine、input_sha 用于跳过未变化的组）
  plan/digests_partial.json  R5：生成中的分节摘要，每组完成即落盘；失败后下次只重算未完成的组
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
from ..validators.fidelity import locate, numbers_supported
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


PARTIAL_FILE = "digests_partial.json"


def _partial_path(project_dir: Path) -> Path:
    return Path(project_dir) / "plan" / PARTIAL_FILE


def _load_partial(project_dir: Path) -> Dict[str, Dict[str, Any]]:
    p = _partial_path(project_dir)
    if not p.is_file():
        return {}
    try:
        return {d["group_id"]: d for d in read_json(p).get("digests", []) if isinstance(d, dict) and "group_id" in d}
    except (OSError, ValueError, KeyError):
        return {}


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


def restore_digest_item(project_dir: Path, group_id: str, index: int, fragment: str) -> Dict[str, Any]:
    """R8 手动恢复：把某个被剔除的条目，换成用户在原文中选出的片段（同样经过对齐校验）。
    成功后该条目从 dropped 中移除，片段加入该组的金句或数字。失败抛 PlanError，不写入任何结果。"""
    project_dir = Path(project_dir)
    ctx = _context(project_dir)
    groups = {g["id"]: g for g in build_groups(ctx["lines"], ctx["doc"])}
    if group_id not in groups:
        raise PlanError(f"找不到节组 {group_id}")
    data = read_json(project_dir / "plan" / "digests.json")
    digest = next((d for d in data.get("digests", []) if d.get("group_id") == group_id), None)
    if digest is None:
        raise PlanError(f"节组 {group_id} 还没有摘要")
    dropped = digest.get("dropped") or []
    if not 0 <= index < len(dropped):
        raise PlanError("被剔除条目序号越界（可能已被处理，请刷新页面）")
    item = dropped[index]
    text = fragment.strip()
    if not text:
        raise PlanError("请先输入原文中的片段")
    kind = item.get("kind", "quote")
    if kind == "number":
        if not numbers_supported(text, groups[group_id]["text"]):
            raise PlanError("原文中找不到这些数字（按完整数字比较，例如 3.5 与 35 不相同）。请从原文复制一段")
        found = text
    else:
        found = locate(text, groups[group_id]["text"], min_key=4)
    if found is None:
        raise PlanError("原文中找不到这段文字（忽略标点与大小写后仍不一致）。请从原文中复制一段")
    key = "key_numbers" if kind == "number" else "quotable"
    if found not in digest.setdefault(key, []):
        digest[key].append(found)
    digest["dropped"] = [d for i, d in enumerate(dropped) if i != index]
    write_json_atomic(project_dir / "plan" / "digests.json", data)
    joblog.log(project_dir, "outline", f"节组 {group_id}：手动恢复{'数字' if kind == 'number' else '金句'}：{found[:30]}", level="ok")
    return digest


def set_project_target(project_dir: Path, minutes: float) -> float:
    """统一的目标时长入口（大纲页与设置页共用）：写入项目设置；若已有大纲，同时更新大纲的预算。
    返回实际生效的时长（已限制在 3～60 分钟，保留一位小数）。"""
    from ..app_settings import set_field  # 延迟导入
    value = round(max(3.0, min(60.0, float(minutes))), 1)
    set_field(Path(project_dir), "settings", "target_minutes", value)
    spine = load_spine(Path(project_dir))
    if spine is not None and float(spine.get("target_minutes", 0)) != value:
        from .outline_edit import set_target_minutes  # 延迟导入，避免循环
        save_spine(Path(project_dir), set_target_minutes(spine, value))
    return value


def plan_outline(project_dir: Path, *, engine: str = "llm", llm=None,
                 force: bool = False, reuse_digests: bool = True,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """S1 分节摘要 + S2 大纲。engine: rules（离线）| llm（需要 llm 实例）。

    已确认的大纲不会被覆盖，除非 force=True。LLM 失败时抛 PlanError，不写入任何结果。
    reuse_digests：True（默认，流水线与断点续跑）时复用输入未变的分节摘要；
    False（界面的“重新生成”）时丢弃全部已有摘要与部分结果，从原文重新生成。
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
    # R5：断点续跑。已完成的组来自上次的部分结果（或上次成功的摘要），输入未变则直接复用
    old: Dict[str, Dict[str, Any]] = {}
    if engine == "llm" and not reuse_digests:
        _partial_path(project_dir).unlink(missing_ok=True)
        joblog.bound_log("重新生成：不复用已有分节摘要，全部组重新调用模型")
    elif engine == "llm":
        old = {d["group_id"]: d for d in load_digests(project_dir)}
        old.update(_load_partial(project_dir))
    digests: List[Dict[str, Any]] = []
    done: Dict[str, Dict[str, Any]] = {}
    reused = 0
    for g in groups:
        sha = _input_sha(g, title)
        if engine == "rules":
            digests.append(rules_digest(g, ctx["blocks_by_id"]))
            continue
        if g["id"] in old and old[g["id"]].get("input_sha") == sha:
            joblog.bound_log(f"节组 {g['id']}：输入未变，复用已有摘要")
            d = old[g["id"]]
            reused += 1
        else:
            try:
                d, _raw = llm_digest(g, llm, title)
            except PlanError as exc:
                _save_raw(project_dir, g["id"], exc.raw)
                if done:
                    joblog.bound_log(f"已保存 {len(done)} 组摘要；下次生成将从未完成的组继续", level="warn")
                raise
            d["input_sha"] = sha
        digests.append(d)
        done[g["id"]] = d
        if engine == "llm":
            write_json_atomic(_partial_path(project_dir), {"digests": list(done.values())})
    if reused:
        joblog.bound_log(f"摘要共 {len(groups)} 组，其中 {reused} 组复用已有结果")

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
    _partial_path(project_dir).unlink(missing_ok=True)
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
