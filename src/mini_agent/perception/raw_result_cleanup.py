"""
perception/raw_result_cleanup.py — RawResultStore 落盘产物的低频清理巡检。

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第 1.5 节。

背景：
  raw_result_store.py 落盘化之后不再在 put()/get() 路径上做同步驱逐（旧的
  内存 LRU 实现是"超限即淘汰最久未访问的单条"，磁盘场景下没必要每次写入都
  扫描整个 session 目录）。清理改为一个可被定期调用的独立巡检，风格与
  `skills/generative_capability/health_patrol.py` 一致：

  - 默认只读扫描 + 生成报告，不做任何删除。
  - 只有显式传入 apply_cleanup=True 才会真正删除，且删除前会把
    被清理的 session 目录信息计入报告，保证可审计。
  - 按 session 目录的最后修改时间判断"过期"，而不是按单个文件精细驱逐——
    raw_result 本来就是"当次 session 相关"的产物，没必要做单文件粒度的
    保留策略。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_RAW_RESULTS_SUBDIR = ".agent/raw_results"
_DEFAULT_RETENTION_DAYS = 14        # session 目录超过这么多天未修改 -> 视为可清理
_DEFAULT_MAX_TOTAL_BYTES = 500_000_000  # 所有 session 目录总大小上限（500MB），超过时优先清理最旧的


@dataclass
class CleanupFinding:
    session_id: str
    kind: str          # "stale_expired" | "over_capacity"
    detail: str
    size_bytes: int = 0


@dataclass
class CleanupReport:
    root_dir: str
    generated_at: str
    findings: "list[CleanupFinding]" = field(default_factory=list)
    cleaned_sessions: "list[str]" = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"raw_result cleanup patrol @ {self.root_dir} ({self.generated_at})"]
        if not self.findings:
            lines.append("  (no findings)")
        for f in self.findings:
            lines.append(f"  [{f.kind}] session={f.session_id} ({f.size_bytes} bytes) — {f.detail}")
        if self.cleaned_sessions:
            lines.append(f"  cleaned: {', '.join(self.cleaned_sessions)}")
        return "\n".join(lines)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _dir_mtime(path: Path) -> float:
    """session 目录的"最后活跃时间"，取目录内所有文件 mtime 的最大值。"""
    latest = path.stat().st_mtime
    for p in path.rglob("*"):
        if p.is_file():
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                pass
    return latest


def run_cleanup(
    project_root: str,
    *,
    base_dir: Optional[str] = None,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    apply_cleanup: bool = False,
) -> CleanupReport:
    """
    扫描 <project_root>/.agent/raw_results/ 下所有 session 目录：
      1. 超过 retention_days 未活跃的目录 -> 标记 stale_expired
      2. 若总大小超过 max_total_bytes，从最旧的目录开始额外标记 over_capacity，
         直到总大小回落到阈值以内
    apply_cleanup=True 时才真正删除被标记的目录；否则只返回报告供人工审查。
    """
    root = Path(base_dir) if base_dir else Path(project_root) / _RAW_RESULTS_SUBDIR
    report = CleanupReport(root_dir=str(root), generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    if not root.exists():
        return report

    now = time.time()
    session_dirs = [p for p in root.iterdir() if p.is_dir()]
    sizes: dict = {}
    mtimes: dict = {}
    for d in session_dirs:
        sizes[d.name] = _dir_size(d)
        mtimes[d.name] = _dir_mtime(d)

    marked: "set[str]" = set()

    # 1. 按保留期标记过期 session
    for d in session_dirs:
        age_days = (now - mtimes[d.name]) / 86400.0
        if age_days > retention_days:
            report.findings.append(
                CleanupFinding(
                    session_id=d.name,
                    kind="stale_expired",
                    detail=f"未活跃 {age_days:.1f} 天（阈值 {retention_days} 天）",
                    size_bytes=sizes[d.name],
                )
            )
            marked.add(d.name)

    # 2. 总容量超限时，从最旧的未标记目录开始追加标记，直到落回阈值以内
    total = sum(sizes.values())
    if total > max_total_bytes:
        remaining = [d for d in session_dirs if d.name not in marked]
        remaining.sort(key=lambda d: mtimes[d.name])
        running_total = total
        for d in remaining:
            if running_total <= max_total_bytes:
                break
            report.findings.append(
                CleanupFinding(
                    session_id=d.name,
                    kind="over_capacity",
                    detail=f"总容量 {total} 超过阈值 {max_total_bytes}，按最旧优先清理",
                    size_bytes=sizes[d.name],
                )
            )
            marked.add(d.name)
            running_total -= sizes[d.name]

    if apply_cleanup:
        for name in marked:
            target = root / name
            try:
                from mini_agent.utils.protected_files_guard import is_protected
                if is_protected(target, project_root):
                    report.findings.append(
                        CleanupFinding(
                            session_id=name,
                            kind="protected_skipped",
                            detail="命中受保护文件清单，跳过删除",
                            size_bytes=sizes.get(name, 0),
                        )
                    )
                    continue
                shutil.rmtree(target)
                report.cleaned_sessions.append(name)
            except OSError:
                pass

    return report


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="RawResultStore 落盘产物低频清理巡检")
    parser.add_argument("project_root")
    parser.add_argument("--retention-days", type=int, default=_DEFAULT_RETENTION_DAYS)
    parser.add_argument("--max-total-bytes", type=int, default=_DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--apply-cleanup", action="store_true")
    args = parser.parse_args()

    rpt = run_cleanup(
        args.project_root,
        retention_days=args.retention_days,
        max_total_bytes=args.max_total_bytes,
        apply_cleanup=args.apply_cleanup,
    )
    print(rpt.summary())
