"""
health_patrol.py
==================
Generative-Capability 引擎的定期健康巡检（阶段四，低频后台任务）。

对应文档: next_doc/generative-capability-skill-plan.md 第 8 节安全边界(6)
          "定期健康巡检：扫描长期未调用或长期 dead 的 member，清理或提示
          人工审查，防止检索池腐化膨胀" / 实施优先级建议阶段四。

职责边界（有意保持"保守默认，只报告不擅自删除"）:
  - 巡检本身只做只读扫描 + 生成结构化报告，默认不做任何写操作。
  - 唯一允许的自动写操作是"一致性修复"（index/registry/members 目录三者
    互相不一致时，以 registry.json 为准做最小修复，见 `fix_inconsistencies`
    参数），因为这类不一致本身就是方案文档第 8 节明确列为 bug 的状态
    （"脚本能跑但检索不到"或"检索能到但脚本已被清理"），修复它不会丢失
    任何有效能力。
  - 真正的"清理"（删除长期 dead 或长期未调用的 member 目录）需要显式传入
    `apply_cleanup=True` 才会执行，且执行前会把待删除的 meta.json 备份进
    报告里，避免误删且无法审计。默认调用方式（`apply_cleanup=False`）只
    输出"建议清理"清单，交给人工审查决定，符合方案文档"清理或提示人工
    审查"里"提示"优先于"清理"的表述。

阶段六更新: `_dead_since()` 现在优先读取 `registry.json` 中的
  `status_changed_at`（由 `capability_engine.py`/`distiller.py` 在状态流转
  时写入的精确时间戳），只有存量数据缺这个字段时才退化为原来的近似算法，
  回应阶段四"已知遗留"中"`_dead_since()` 近似值可能偏早"的问题。
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None

_DEFAULT_STALE_DAYS = 30          # 超过这么多天没有任何成功/失败记录 -> 视为"长期未调用"
_DEFAULT_DEAD_RETENTION_DAYS = 14  # dead 状态保留这么多天供人工审查，超过则建议清理


@dataclass
class PatrolFinding:
    member_id: str
    kind: str      # "stale" | "dead_expired" | "index_without_registry" |
                    # "registry_without_index" | "member_dir_without_registry" |
                    # "registry_without_member_dir"
    detail: str


@dataclass
class PatrolReport:
    skill_dir: str
    generated_at: str
    findings: list[PatrolFinding] = field(default_factory=list)
    fixed_inconsistencies: list[str] = field(default_factory=list)
    cleaned_members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skill_dir": self.skill_dir,
            "generated_at": self.generated_at,
            "findings": [f.__dict__ for f in self.findings],
            "fixed_inconsistencies": self.fixed_inconsistencies,
            "cleaned_members": self.cleaned_members,
        }


def run_patrol(
    skill_dir: str | Path,
    fix_inconsistencies: bool = False,
    apply_cleanup: bool = False,
    now: Optional[float] = None,
    project_root: Optional[str | Path] = None,
) -> PatrolReport:
    skill_dir = Path(skill_dir)
    # 用于受保护文件清单判定（阶段 2 guard）。调用方通常持有真正的项目根
    # 目录，未显式传入时退化为当前工作目录——与项目里其他类似场景
    # （见 session.py / plugins.py 等 `project_root or Path.cwd()` 惯例）
    # 保持一致的兜底行为。
    project_root = Path(project_root) if project_root is not None else Path.cwd()
    capability = _load_capability(skill_dir)
    lifecycle = capability.get("lifecycle", {})
    stale_days = lifecycle.get("health_patrol_stale_days", _DEFAULT_STALE_DAYS)
    dead_retention_days = lifecycle.get("health_patrol_dead_retention_days", _DEFAULT_DEAD_RETENTION_DAYS)

    now = now if now is not None else time.time()
    registry_path = skill_dir / "registry.json"
    index_path = skill_dir / "_index.json"
    members_dir = skill_dir / "members"

    registry = _load_json(registry_path, {"members": {}})
    index = _load_json(index_path, {"members": []})

    report = PatrolReport(skill_dir=str(skill_dir), generated_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))

    registry_ids = set(registry.get("members", {}).keys())
    index_ids = {m["member_id"] for m in index.get("members", []) if "member_id" in m}
    member_dir_ids = (
        {p.name for p in members_dir.iterdir() if p.is_dir() and not p.name.startswith("__tmp_")}
        if members_dir.exists() else set()
    )

    # ---------------- 一致性检查 ---------------- #
    for mid in index_ids - registry_ids:
        report.findings.append(PatrolFinding(
            member_id=mid, kind="index_without_registry",
            detail="_index.json 中存在该 member 摘要，但 registry.json 中没有对应状态记录"))
    for mid in registry_ids - index_ids:
        report.findings.append(PatrolFinding(
            member_id=mid, kind="registry_without_index",
            detail="registry.json 中存在该 member 状态记录，但 _index.json 检索清单中没有，检索不到该能力"))
    for mid in member_dir_ids - registry_ids:
        report.findings.append(PatrolFinding(
            member_id=mid, kind="member_dir_without_registry",
            detail="members/ 目录下存在该 member 脚本，但 registry.json 中没有状态记录"))
    for mid in registry_ids - member_dir_ids:
        report.findings.append(PatrolFinding(
            member_id=mid, kind="registry_without_member_dir",
            detail="registry.json 中存在该 member 状态记录，但 members/ 目录下脚本已缺失，"
                   "命中后 execute() 会必然失败"))

    if fix_inconsistencies:
        _fix_inconsistencies(report, registry, index, registry_path, index_path, registry_ids, index_ids)

    # ---------------- 长期未调用 / dead 过期检查 ---------------- #
    for mid, entry in list(registry.get("members", {}).items()):
        status = entry.get("status")
        last_activity = _latest_timestamp(entry)

        if status != "dead" and last_activity is not None:
            age_days = (now - last_activity) / 86400
            if age_days > stale_days:
                report.findings.append(PatrolFinding(
                    member_id=mid, kind="stale",
                    detail=f"已 {age_days:.1f} 天没有任何成功/失败记录（阈值 {stale_days} 天），建议人工确认是否仍需保留"))
        elif status != "dead" and last_activity is None and entry.get("success_count", 0) == 0 \
                and entry.get("fail_count", 0) == 0:
            # 从未被调用过一次，无法用"距上次活动的天数"衡量，单独标注，不计入清理候选。
            report.findings.append(PatrolFinding(
                member_id=mid, kind="stale",
                detail="自建立以来从未被检索命中执行过，建议确认对应能力是否仍有必要"))

        if status == "dead":
            dead_since = _dead_since(skill_dir, mid, entry)
            if dead_since is not None:
                age_days = (now - dead_since) / 86400
                if age_days > dead_retention_days:
                    report.findings.append(PatrolFinding(
                        member_id=mid, kind="dead_expired",
                        detail=f"已 dead {age_days:.1f} 天（保留期 {dead_retention_days} 天已过），建议清理"))
                    if apply_cleanup:
                        _cleanup_member(skill_dir, mid, registry, index, report, project_root)

    if apply_cleanup and report.cleaned_members:
        _save_json(registry_path, registry)
        _save_json(index_path, index)

    return report


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #

def _fix_inconsistencies(report: PatrolReport, registry: dict, index: dict,
                          registry_path: Path, index_path: Path,
                          registry_ids: set, index_ids: set) -> None:
    """以 registry.json（member 的真实生命周期状态）为准做最小修复：
    - index 中多余的（registry 里没有）条目予以移除，避免检索命中一个不存在状态记录的 id。
    - registry 里有但 index 缺失的，补一条最小摘要，确保它能被检索到
      （对应方案文档"检索能到但脚本已被清理"反过来的情形："脚本/状态都在但检索不到"）。
    member_dir 与 registry 之间的不一致（脚本缺失/多余）不在这里自动修复，
    因为脚本文件本身无法凭空补出来，只能提示人工审查。
    """
    changed = False
    index_members = index.get("members", [])
    kept = [m for m in index_members if m.get("member_id") in registry_ids]
    if len(kept) != len(index_members):
        removed = index_ids - {m.get("member_id") for m in kept}
        for mid in removed:
            report.fixed_inconsistencies.append(f"从 _index.json 移除孤立摘要: {mid}")
        index["members"] = kept
        changed = True

    existing_index_ids = {m.get("member_id") for m in index.get("members", [])}
    for mid in registry_ids - existing_index_ids:
        index.setdefault("members", []).append({
            "member_id": mid,
            "description": f"[巡检自动补全摘要，建议人工完善] {mid}",
            "match": {},
        })
        report.fixed_inconsistencies.append(f"为 registry 中存在但 _index.json 缺失的 member 补全摘要: {mid}")
        changed = True

    if changed:
        _save_json(index_path, index)


def _cleanup_member(
    skill_dir: Path, member_id: str, registry: dict, index: dict, report: PatrolReport,
    project_root: str | Path,
) -> None:
    member_dir = skill_dir / "members" / member_id
    backup_meta = None
    meta_path = member_dir / "meta.json"
    if meta_path.exists():
        try:
            backup_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            backup_meta = None

    if member_dir.exists():
        from mini_agent.utils.protected_files_guard import is_protected
        if is_protected(member_dir, project_root):
            report.fixed_inconsistencies.append(
                f"member `{member_id}` 命中受保护文件清单，跳过删除（registry/index 状态仍保留，不做清理）"
            )
            return
        shutil.rmtree(member_dir)
    registry.get("members", {}).pop(member_id, None)
    index["members"] = [m for m in index.get("members", []) if m.get("member_id") != member_id]

    report.cleaned_members.append(member_id)
    report.fixed_inconsistencies.append(
        f"已清理长期 dead 的 member `{member_id}`（备份的 meta: {json.dumps(backup_meta, ensure_ascii=False)}）"
    )


def _latest_timestamp(entry: dict) -> Optional[float]:
    candidates = []
    for key in ("last_success", "last_failure"):
        ts = entry.get(key)
        if ts:
            parsed = _parse_ts(ts)
            if parsed is not None:
                candidates.append(parsed)
    return max(candidates) if candidates else None


def _dead_since(skill_dir: Path, member_id: str, entry: dict) -> Optional[float]:
    """优先使用 registry.json 中的 `status_changed_at`（阶段六新增，在
    `_apply_lifecycle`/`_handle_reexplore_failure`/蒸馏落盘时写入，是状态
    流转发生时刻的准确记录）。

    对于阶段六之前生成、registry.json 里还没有这个字段的既有数据，退化为
    旧的近似逻辑：用 last_failure（进入 dead 前的最后一次失败通常紧邻状态
    流转）作为近似；如果两者都没有，回退到 meta.json 的 mtime。这一退化
    路径保留是为了兼容存量 registry.json，而不是主路径。"""
    changed_at = entry.get("status_changed_at")
    if changed_at:
        parsed = _parse_ts(changed_at)
        if parsed is not None:
            return parsed
    ts = _latest_timestamp(entry)
    if ts is not None:
        return ts
    meta_path = skill_dir / "members" / member_id / "meta.json"
    if meta_path.exists():
        return meta_path.stat().st_mtime
    return None


def _parse_ts(ts: str) -> Optional[float]:
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _load_capability(skill_dir: Path) -> dict:
    path = skill_dir / "capability.yaml"
    if yaml is None or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# 命令行入口：供 cron / 定时任务低频调用
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="generative-capability 健康巡检（阶段四）")
    parser.add_argument("skill_dir", help="generative-capability skill 目录路径")
    parser.add_argument("--fix-inconsistencies", action="store_true",
                         help="以 registry.json 为准修复 index/registry/members 目录间的不一致")
    parser.add_argument("--apply-cleanup", action="store_true",
                         help="真正清理超过保留期的 dead member（默认只报告建议清理，不删除）")
    args = parser.parse_args()

    result = run_patrol(
        args.skill_dir,
        fix_inconsistencies=args.fix_inconsistencies,
        apply_cleanup=args.apply_cleanup,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
