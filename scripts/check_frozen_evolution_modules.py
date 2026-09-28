#!/usr/bin/env python3
"""scripts/check_frozen_evolution_modules.py — Phase 9 “冻结安全设施”核对脚本。

见 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md` “完成标志”
第 3、4 条，以及 `docs/architecture_v2/phase9-evolution-inventory.md` 第二节。

Phase 9 要求 `state_repo.py` / `workspace.py` / `validators.py` /
`eval_runner.py` 四个安全设施“在整个 Phase 过程中确实没有被修改”。此前每个
Sprint 只能对交付压缩包做 sha256 比对（压缩包不含 `.git`），而完成标志第 4 条
明确要求在真实仓库里用 `git diff` 核对。本脚本把这件事变成一条命令。

两种模式：

  1. git 模式（完成标志要求的口径）：
         python scripts/check_frozen_evolution_modules.py --base <Phase 9 起点 commit>
     对冻结文件执行 `git diff --name-only <base> -- <files>`，工作区未提交的
     改动也会被发现（与 `<base>` 比较的是工作区，不只是 HEAD）。

  2. manifest 模式（没有 `.git` 时的兜底，只能保证“从基线起未变”，
     不能证明基线之前的历史）：
         python scripts/check_frozen_evolution_modules.py --write-manifest FILE
         python scripts/check_frozen_evolution_modules.py --check-manifest FILE

退出码：0 = 全部未改动；1 = 有冻结文件被改动/缺失；2 = 无法核对（不是 git
仓库、`<base>` 不存在、manifest 不可读等）。**无法核对不会被当作通过。**

`--also-adapted` 额外检查 Sprint 9-1/9-2 承诺“只对接、不修改”的两个决策逻辑
模块（`failure_pattern_store.py` / `proposal_risk.py`）。它们不是安全设施，
默认不参与判定。
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_EVO = "src/mini_agent/evolution"

# 安全设施：绝对不能动（phase9-evolution-inventory.md 第二节）
FROZEN = [
    f"{_EVO}/state_repo.py",
    f"{_EVO}/workspace.py",
    f"{_EVO}/validators.py",
    f"{_EVO}/eval_runner.py",
]
# 决策逻辑，但 Phase 9 承诺“只新增调用方、不修改”
ADAPTED = [
    f"{_EVO}/failure_pattern_store.py",
    f"{_EVO}/proposal_risk.py",
]

EXIT_OK, EXIT_CHANGED, EXIT_UNVERIFIABLE = 0, 1, 2


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def check_git(root: Path, base: str, files: list[str]) -> tuple[int, list[str]]:
    """返回 (退出码, 输出行)。"""
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return EXIT_UNVERIFIABLE, [f"无法核对：{root} 不是 git 工作区（压缩包不含 .git？请改用 manifest 模式）"]
    if _git(root, "rev-parse", "--verify", "-q", f"{base}^{{commit}}").returncode != 0:
        return EXIT_UNVERIFIABLE, [f"无法核对：找不到 commit {base!r}"]

    lines: list[str] = []
    changed: list[str] = []
    for rel in files:
        if not (root / rel).exists() and _git(root, "cat-file", "-e", f"{base}:{rel}").returncode != 0:
            return EXIT_UNVERIFIABLE, [f"无法核对：{rel} 在工作区和 {base} 里都不存在，请确认路径"]
        diff = _git(root, "diff", "--name-only", base, "--", rel)
        if diff.returncode != 0:
            return EXIT_UNVERIFIABLE, [f"无法核对：git diff 失败：{diff.stderr.strip()}"]
        if diff.stdout.strip():
            changed.append(rel)
            lines.append(f"MODIFIED   {rel}")
        else:
            lines.append(f"unchanged  {rel}")
    lines.append(f"基线：{base}；共 {len(files)} 个文件，{len(changed)} 个被改动")
    return (EXIT_CHANGED if changed else EXIT_OK), lines


def write_manifest(root: Path, manifest: Path, files: list[str]) -> tuple[int, list[str]]:
    rows = []
    for rel in files:
        p = root / rel
        if not p.is_file():
            return EXIT_UNVERIFIABLE, [f"无法写入 manifest：{rel} 不存在"]
        rows.append(f"{_sha256(p)}  {rel}")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return EXIT_OK, [f"已写入 {manifest}（{len(rows)} 个文件）"]


def check_manifest(root: Path, manifest: Path) -> tuple[int, list[str]]:
    if not manifest.is_file():
        return EXIT_UNVERIFIABLE, [f"无法核对：manifest 不存在：{manifest}"]
    lines: list[str] = []
    bad = 0
    n = 0
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            digest, rel = raw.split(None, 1)
        except ValueError:
            return EXIT_UNVERIFIABLE, [f"无法核对：manifest 行格式错误：{raw!r}"]
        n += 1
        p = root / rel.strip()
        if not p.is_file():
            bad += 1
            lines.append(f"MISSING    {rel}")
        elif _sha256(p) != digest:
            bad += 1
            lines.append(f"MODIFIED   {rel}")
        else:
            lines.append(f"unchanged  {rel}")
    if n == 0:
        return EXIT_UNVERIFIABLE, ["无法核对：manifest 为空"]
    lines.append(f"共 {n} 个文件，{bad} 个与 manifest 不一致（仅证明“自 manifest 生成时起未变”）")
    return (EXIT_CHANGED if bad else EXIT_OK), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根目录（默认：脚本所在仓库）")
    ap.add_argument("--also-adapted", action="store_true", help="同时检查 failure_pattern_store/proposal_risk")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base", help="git 模式：Phase 9 起点 commit（或 tag/分支）")
    g.add_argument("--write-manifest", type=Path, metavar="FILE", help="manifest 模式：写入当前 sha256 基线")
    g.add_argument("--check-manifest", type=Path, metavar="FILE", help="manifest 模式：对照基线核对")
    args = ap.parse_args(argv)

    files = FROZEN + (ADAPTED if args.also_adapted else [])
    root = args.root.resolve()
    if args.base:
        code, out = check_git(root, args.base, files)
    elif args.write_manifest:
        code, out = write_manifest(root, args.write_manifest, files)
    else:
        code, out = check_manifest(root, args.check_manifest)
    print("\n".join(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
