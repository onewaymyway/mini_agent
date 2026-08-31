"""
evolution/protected_files_backup.py — 受保护文件定期备份 + 缺失核对（阶段 3）

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 四、
"三层防护机制" 第 3 层：定期备份 + 可还原（真正的兜底）。

背景：第 1 层（prompt 提醒）只降低 agent 犯错概率，第 2 层（代码级
guard）只拦得住框架自身的例行维护逻辑，两者都拦不住 agent 直接执行
bash 命令删除文件。第 3 层不追求"拦得住"，追求"能找回来"：每天把当前
生效的受保护路径打包快照，即使真的被删了，也能从最近一份快照恢复。

行为（对应设计文档 4. 第 3 层的具体约定）：
  - 每次运行都重新扫描当前生效的受保护路径（含清单文件自身），逐一打包
    快照到 `<project_root>/.agent/protected_backup/<generation_id>/`，
    `generation_id` 用时间戳命名。
  - 只保留最近 N 份快照（N 默认 5，`cfg.protected_files_backup_keep_count`
    可配置），超出的旧快照自动清理——备份目录本身不在受保护集合里，
    不会跟自身的清理动作冲突。
  - 缺失核对：对比"上一份快照存在、但当前受保护路径下已经不存在"的
    情况，发现即写一条 `activity_digest.jsonl` 告警（type=
    "protected_files_missing"，复用现有晨报机制），**不做任何自动恢复
    动作**——遵循项目"新功能默认保守"的一贯原则，自动恢复本身是一种
    覆盖行为，风险高于"用户手动确认恢复"。
  - 没有任何受保护路径时（清单为空或不存在），本次运行直接跳过，不
    产生空快照。

手动恢复入口（`/agent protected restore`，阶段 4）见本文件下方
`restore_from_snapshot()`，CLI 层在
`cli/commands/protected_cmd.py::handle_protected_cmd` 里调用。
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:protected_files_backup"

_BACKUP_SUBDIR = "protected_backup"          # <project_root>/.agent/protected_backup/
_DEFAULT_KEEP_COUNT = 5

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass
class BackupSummary:
    """一次备份运行的执行摘要，供本地回调 handler / 日志使用。"""

    backed_up: list[str] = field(default_factory=list)   # 本次成功打包的路径（规范化字符串）
    missing: list[str] = field(default_factory=list)      # 上一份快照存在、本次已消失的路径
    pruned_generations: list[str] = field(default_factory=list)  # 本次清理掉的旧快照 generation_id
    errors: list[str] = field(default_factory=list)
    generation_id: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def _backup_root(project_root: Path) -> Path:
    return project_root / ".agent" / _BACKUP_SUBDIR


def _list_generations(backup_root: Path) -> list[Path]:
    """按 generation_id（时间戳字符串）升序返回已有快照目录。"""
    if not backup_root.is_dir():
        return []
    gens = [p for p in backup_root.iterdir() if p.is_dir()]
    gens.sort(key=lambda p: p.name)
    return gens


def _snapshot_manifest(generation_dir: Path) -> dict[str, int]:
    """读取某一份快照下已打包的路径清单，返回 {原始路径: 打包时的
    index} 映射——manifest.txt 每行格式为 `<index>\\t<original_path>`，
    index 就是 `_safe_snapshot_name(path, index)` 用来生成快照内文件名
    的那个值，恢复时必须依赖这个显式记录的 index 定位快照内容，不能靠
    重新枚举（若打包时有条目被跳过，重新枚举的下标会跟实际文件名错位）。
    """
    manifest_path = generation_dir / "manifest.txt"
    if not manifest_path.is_file():
        return {}
    result: dict[str, int] = {}
    try:
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            idx_str, _, path = line.partition("\t")
            try:
                result[path] = int(idx_str)
            except ValueError:
                continue
    except OSError:
        return {}
    return result


def _safe_snapshot_name(original_path: str, index: int) -> str:
    """把原始绝对路径转成快照目录下的安全文件/目录名，避免路径分隔符
    冲突；用 index 前缀保证同名文件不同来源也不会互相覆盖。"""
    stem = Path(original_path).name or "root"
    return f"{index:04d}_{stem}"


def run_backup_once(
    project_root: "Path | str",
    *,
    keep_count: int = _DEFAULT_KEEP_COUNT,
    now: Optional[float] = None,
) -> BackupSummary:
    """执行一次备份 + 缺失核对，返回摘要。project_root 应为项目根目录。"""
    from scripts.protected_files import ProtectedFilesGuard

    project_root = Path(project_root)
    summary = BackupSummary()

    guard = ProtectedFilesGuard(project_root)
    entries = guard.list_entries()

    backup_root = _backup_root(project_root)
    existing_generations = _list_generations(backup_root)

    # ── 缺失核对：对比上一份快照的清单 vs 当前受保护路径 ──────────────
    if existing_generations:
        prev_manifest = _snapshot_manifest(existing_generations[-1])
        current_paths = {e.path for e in entries}
        summary.missing = sorted(p for p in prev_manifest if p not in current_paths)

    if not entries:
        # 没有任何受保护路径，不产生空快照；缺失核对已经在上面做完。
        return summary

    # ── 打包本次快照 ──────────────────────────────────────────────────
    now = now if now is not None else time.time()
    generation_id = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    summary.generation_id = generation_id
    generation_dir = backup_root / generation_id

    try:
        generation_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        summary.errors.append(f"mkdir_failed: {exc}")
        return summary

    manifest_lines: list[str] = []
    for idx, entry in enumerate(entries):
        src = Path(entry.path)
        if not src.exists():
            # 理论上不应发生（guard 扫描时刚确认存在），防御性跳过，
            # 不计入本次快照，也不算作"缺失"（缺失核对只看跨快照对比）。
            continue
        dest_name = _safe_snapshot_name(entry.path, idx)
        dest = generation_dir / dest_name
        try:
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
            manifest_lines.append(f"{idx}\t{entry.path}")
            summary.backed_up.append(entry.path)
        except OSError as exc:
            summary.errors.append(f"backup_failed({entry.path}): {exc}")

    try:
        (generation_dir / "manifest.txt").write_text(
            "\n".join(manifest_lines) + ("\n" if manifest_lines else ""),
            encoding="utf-8",
        )
    except OSError as exc:
        summary.errors.append(f"manifest_write_failed: {exc}")

    # ── 保留策略：只留最近 keep_count 份（含本次新建的这份） ──────────
    all_generations = _list_generations(backup_root)
    if keep_count > 0 and len(all_generations) > keep_count:
        to_prune = all_generations[: len(all_generations) - keep_count]
        for gen_dir in to_prune:
            try:
                shutil.rmtree(gen_dir)
                summary.pruned_generations.append(gen_dir.name)
            except OSError as exc:
                summary.errors.append(f"prune_failed({gen_dir.name}): {exc}")

    return summary


@dataclass
class RestoreSummary:
    """一次手动恢复操作的执行摘要（阶段 4）。"""

    restored: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def restore_from_snapshot(
    project_root: "Path | str",
    generation_id: str,
    *,
    paths: Optional[list[str]] = None,
) -> RestoreSummary:
    """从指定快照恢复受保护路径（阶段 4，`/agent protected restore` 的
    执行入口）。

    paths 为 None 时恢复该快照 manifest 里的全部路径；否则只恢复给定的
    这些路径（必须是该快照 manifest 里出现过的原始路径，调用方——即
    CLI 命令层——负责校验，这里不重复校验，按需覆盖式恢复）。

    还原方式：把快照目录下对应条目复制回原始绝对路径，文件用
    `shutil.copy2` 直接覆盖，目录用 `shutil.copytree(dirs_exist_ok=True)`
    合并覆盖（不会先删除目标目录，只覆盖快照里存在的文件，目标目录下
    快照没有的文件保持不动——比"先删后拷"更保守，符合"宁可少改，不可
    多删"的原则）。
    """
    project_root = Path(project_root)
    summary = RestoreSummary()

    backup_root = _backup_root(project_root)
    generation_dir = backup_root / generation_id
    if not generation_dir.is_dir():
        summary.errors.append(f"snapshot_not_found: {generation_id}")
        return summary

    index_by_path = _snapshot_manifest(generation_dir)
    targets = paths if paths is not None else sorted(index_by_path)

    for original_path in targets:
        if original_path not in index_by_path:
            summary.errors.append(f"not_in_snapshot: {original_path}")
            continue
        idx = index_by_path[original_path]
        src = generation_dir / _safe_snapshot_name(original_path, idx)
        if not src.exists():
            summary.errors.append(f"snapshot_content_missing: {original_path}")
            continue
        dest = Path(original_path)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
            summary.restored.append(original_path)
        except OSError as exc:
            summary.errors.append(f"restore_failed({original_path}): {exc}")

    return summary


def _write_missing_alert(paths: "AgentPaths", summary: BackupSummary) -> None:
    """缺失核对命中时，写一条 activity_digest.jsonl 告警（复用现有晨报
    机制）。只告警，不做任何自动恢复——见模块顶部说明。"""
    if not summary.missing:
        return
    try:
        from mini_agent.evolution.resource_arbiter import append_activity_digest
        append_activity_digest(paths, {
            "type": "protected_files_missing",
            "summary": (
                f"受保护文件核对：发现 {len(summary.missing)} 处上次备份时"
                f"还在、本次已不存在的路径，未自动恢复，需要人工确认"
            ),
            "missing_paths": summary.missing,
            "generation_id": summary.generation_id,
        })
    except Exception:
        # 告警写入失败不应该影响备份任务本身的成败判定。
        pass


def ensure_protected_files_backup_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
    keep_count: int = _DEFAULT_KEEP_COUNT,
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:protected_files_backup`（零
    LLM 成本，本地回调 handler，跟 `candidate_queue_triage.py::
    ensure_candidate_queue_triage_job` 同构）。

    返回是否是本次新注册（True=新建，False=已存在直接复用）。
    """
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="受保护文件定期备份",
        schedule="interval:86400",
        description=(
            "扫描当前生效的受保护文件清单，逐一打包快照到 "
            ".agent/protected_backup/，只保留最近若干份；发现快照间"
            "缺失时只告警、不自动恢复，零 LLM 成本。"
        ),
        tags=["maintenance", "safety"],
    )

    def _handler(job: "CronJob") -> bool:
        summary = run_backup_once(paths.project_root, keep_count=keep_count)
        _write_missing_alert(paths, summary)
        return summary.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "BackupSummary",
    "RestoreSummary",
    "run_backup_once",
    "restore_from_snapshot",
    "ensure_protected_files_backup_job",
]
