#!/usr/bin/env python3
"""scripts/dep_graph.py — Sprint 0（`next_doc/refactor_plan/02-executable-sprint-plan.md`）
"地基与安全网"里"依赖关系扫描"任务的产出脚本。

用途：在架构重构（`next_doc/refactor_plan/`）动任何代码之前，先量化清楚
指定模块（默认 `goal_mode`）与仓库其它模块之间的耦合程度，回答两个问题：

  1. "谁 import 了这个模块？"（对内依赖 / inbound）——决定这个模块能不能
     被安全地整体替换，还是牵一发动全身。
  2. "这个模块自己又 import 了谁？"（对外依赖 / outbound）——决定 Adapter
     接入点需要覆盖多少外部依赖面。

止损条件对照（`02-executable-sprint-plan.md` Sprint 0）：
  如果 inbound 扫描发现目标模块被 10+ 个模块直接 import 内部类
  （不只是包名，而是深入 import 具体 class/function），需要重新评估
  "这是否还是合适的第一条迁移链"。本脚本的输出会直接打印 inbound 计数，
  方便人工核对这条止损条件是否触发。

用法：
  python scripts/dep_graph.py                      # 默认分析 goal_mode
  python scripts/dep_graph.py --module tools        # 分析其它模块
  python scripts/dep_graph.py --json                # 输出机器可读 JSON
  python scripts/dep_graph.py --root /path/to/src   # 自定义 src 根目录

实现说明：只用标准库 `ast` 静态解析 import 语句，不执行任何代码、不需要
额外依赖（`grimp` 等三方包在当前环境不可用时依然能跑）。只做"谁 import
了谁"这一层依赖分析，不追踪动态 `importlib.import_module(...)` 这种
运行时才能确定目标的间接依赖（如发现目标模块大量使用动态 import，
应该在人工复核时额外补充说明，脚本本身不保证能发现这类隐藏耦合）。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


PACKAGE_NAME = "mini_agent"


@dataclass
class ImportEdge:
    """一条 import 边：`from_file` 里出现了对 `imported` 的 import。"""
    from_file: str          # 相对 src 根目录的路径，如 "goal_mode/runner.py"
    imported: str            # 被 import 的完整 dotted path，如 "mini_agent.goal_mode.runner"
    names: list = field(default_factory=list)  # `from X import a, b` 里的 a, b（class/function 级别）
    lineno: int = 0


def _module_name_from_path(root: Path, file_path: Path) -> str:
    """把 `src/mini_agent/goal_mode/runner.py` 转成 `mini_agent.goal_mode.runner`。"""
    rel = file_path.relative_to(root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative_import(current_module: str, node: ast.ImportFrom, is_init: bool) -> str:
    """把相对 import（`from . import x` / `from ..spec import y`）解析成绝对 dotted path。

    Python 的相对 import 以"当前文件所属的包"（`__package__`）为基准：
      - 对 `__init__.py` 而言，`current_module` 本身就是它所属的包
        （如 `mini_agent.goal_mode`），level=1 时基准就是它自己。
      - 对普通模块（如 `runner.py`，`current_module` 为
        `mini_agent.goal_mode.runner`）而言，它所属的包是去掉最后一段的
        `mini_agent.goal_mode`，level=1 时基准是这个包。
    在此基础上，level 每多 1，再往上退一级包。
    """
    own_package_parts = (
        current_module.split(".") if is_init else current_module.split(".")[:-1]
    )
    # level=1 用 own_package 本身；level=2 起再逐级往上退。
    extra_up = max(0, node.level - 1)
    base_parts = own_package_parts[: len(own_package_parts) - extra_up] if extra_up else own_package_parts
    if node.module:
        base_parts = base_parts + node.module.split(".")
    return ".".join(base_parts)


def scan_repo(root: Path) -> list[ImportEdge]:
    """扫描 `root`（即 `src/mini_agent`）下所有 .py 文件，收集 import 边。"""
    edges: list[ImportEdge] = []
    for file_path in sorted(root.rglob("*.py")):
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"[dep_graph] 跳过无法解析的文件 {file_path}: {exc}", file=sys.stderr)
            continue

        current_module = _module_name_from_path(root, file_path)
        rel_display = str(file_path.relative_to(root.parent))
        is_init = file_path.name == "__init__.py"

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == PACKAGE_NAME or alias.name.startswith(PACKAGE_NAME + "."):
                        edges.append(ImportEdge(rel_display, alias.name, [], node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # 相对 import：只有当前文件本来就在 mini_agent 包内才需要解析
                    # （本脚本只扫描 mini_agent 包，天然满足）。
                    resolved = _resolve_relative_import(current_module, node, is_init)
                    if resolved == PACKAGE_NAME or resolved.startswith(PACKAGE_NAME + "."):
                        names = [a.name for a in node.names]
                        edges.append(ImportEdge(rel_display, resolved, names, node.lineno))
                elif node.module and (
                    node.module == PACKAGE_NAME or node.module.startswith(PACKAGE_NAME + ".")
                ):
                    names = [a.name for a in node.names]
                    edges.append(ImportEdge(rel_display, node.module, names, node.lineno))
    return edges


def filter_for_module(edges: list[ImportEdge], module: str) -> dict:
    """从全量 edges 里筛出与 `module`（如 "goal_mode"）相关的 inbound / outbound。"""
    target_prefix_dotted = f"{PACKAGE_NAME}.{module}"
    target_dir_prefix = f"{PACKAGE_NAME}/{module}/"
    target_file_name = f"{PACKAGE_NAME}/{module}.py"

    inbound = []   # 别的文件 import 了 target 模块
    outbound = []  # target 模块自己的文件 import 了别的东西
    outbound_external = []  # target 模块 import 的、且不在 target 模块自身内部的依赖

    for e in edges:
        imports_target = e.imported == target_prefix_dotted or e.imported.startswith(target_prefix_dotted + ".")
        file_in_target = e.from_file == target_file_name or e.from_file.startswith(target_dir_prefix)

        if imports_target and not file_in_target:
            inbound.append(e)
        if file_in_target:
            outbound.append(e)
            if not imports_target:
                outbound_external.append(e)

    return {
        "module": module,
        "inbound": inbound,
        "outbound": outbound,
        "outbound_external": outbound_external,
    }


def _edge_to_dict(e: ImportEdge) -> dict:
    return {"from_file": e.from_file, "imported": e.imported, "names": e.names, "lineno": e.lineno}


def render_text_report(result: dict) -> str:
    module = result["module"]
    inbound = result["inbound"]
    outbound_external = result["outbound_external"]

    inbound_files = sorted({e.from_file for e in inbound})
    outbound_targets = sorted({e.imported for e in outbound_external})

    lines = []
    lines.append(f"=== 依赖关系扫描：{module} ===\n")
    lines.append(f"[对内依赖 / inbound] 共 {len(inbound_files)} 个文件 import 了 {module}：")
    for f in inbound_files:
        names = sorted({n for e in inbound if e.from_file == f for n in e.names})
        suffix = f"  (导入的具体符号: {', '.join(names)})" if names else ""
        lines.append(f"  - {f}{suffix}")
    if not inbound_files:
        lines.append("  (无)")

    lines.append("")
    lines.append(f"[对外依赖 / outbound] {module} 自身依赖了 {len(outbound_targets)} 个包外目标：")
    for t in outbound_targets:
        lines.append(f"  - {t}")
    if not outbound_targets:
        lines.append("  (无)")

    lines.append("")
    lines.append("[止损条件核对] Sprint 0 止损条件：inbound 直接 import 内部类/函数"
                  "（而不只是包名）的文件数 >= 10 时，需要重新评估这条迁移链。")
    deep_inbound = sorted({e.from_file for e in inbound if e.names})
    lines.append(f"  当前深度 inbound（import 了具体符号）文件数：{len(deep_inbound)}")
    if len(deep_inbound) >= 10:
        lines.append("  ⚠️  已达到/超过止损阈值，请对照 02-executable-sprint-plan.md Sprint 0 止损条件复核。")
    else:
        lines.append("  未触发止损阈值。")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", default="goal_mode", help="要分析的子模块名（相对 src/mini_agent），默认 goal_mode")
    parser.add_argument(
        "--root", default=None,
        help="src/mini_agent 的路径，默认自动推断为本脚本所在仓库的 src/mini_agent",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON 而不是文本报告")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = (Path(__file__).resolve().parent.parent / "src" / PACKAGE_NAME)

    if not root.is_dir():
        print(f"[dep_graph] 找不到目录：{root}", file=sys.stderr)
        return 2

    edges = scan_repo(root)
    result = filter_for_module(edges, args.module)

    if args.json:
        payload = {
            "module": result["module"],
            "inbound": [_edge_to_dict(e) for e in result["inbound"]],
            "outbound_external": [_edge_to_dict(e) for e in result["outbound_external"]],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text_report(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
