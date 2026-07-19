#!/usr/bin/env python3
"""
codemod_log_exceptions.py — 批量给"静默异常"补日志（AST 版）

旧版脚本用正则只能匹配

    except Exception:
        pass

这种"except 块里只有一句 pass"的最简单情形，遇到

    except Exception:
        continue
    except Exception:
        return None
    except Exception as e:
        # 一些注释
        self.value = default
    except:
        pass

等等就完全失效了。这一版改成基于 `ast` 做检测、按行插入做修改：
既能识别任意 except 块体（多语句、continue/return/break、纯注释+pass……），
也不会因为正则改写破坏原有格式/缩进/注释。

处理逻辑
--------
1. 用 `ast.parse` 解析每个文件，收集所有 `except` 处理器（`ExceptHandler`）。
2. 只处理"目标异常类型"命中的 handler（默认 `Exception` / 裸 `except:`，
   可用 --types 扩展，比如加上 `OSError,ValueError`）。
3. 一个 handler 被判定为"已经处理过/无需插桩"，当且仅当它的 body 里已经出现：
   - 调用 `log_exception(...)`
   - 调用 `logger.exception/error/warning/critical(...)` 或
     `logging.exception(...)`
   - `raise` 语句（重新抛出，不算吞掉异常）
   否则视为"静默异常"，需要插入日志调用。
4. 插入方式：
   - 若 handler 有 `as name`：在 body 第一条语句前插入
     `from mini_agent.errors import log_exception` +
     `log_exception(name, where="module.func")`。
   - 若 handler 没有 `as`（比如 `except Exception:`）：改写 except 行，
     补上 `as _mini_agent_exc`，再插入同样的日志调用。
   - 若 handler 是裸 `except:`：不改写异常类型（避免吞 BaseException 的行为被
     悄悄改变），用 `sys.exc_info()[1]` 拿到当前异常对象传给 log_exception，
     不需要改 except 行。
   - `where` 会尽量推导到"模块.函数名"（walk 出最近的外层函数/方法），
     而不是旧版里粗糙的整模块名。
5. 所有插入按"文件内从后往前"的顺序进行，避免前面的插入把后面 handler 的
   行号搞错。
6. 原有 body（pass / continue / return ... 等）完整保留在日志调用之后，
   保证控制流 100% 不变，只是"记了一笔"。

用法
----
    python3 codemod_log_exceptions.py                    # 预览，不写入
    python3 codemod_log_exceptions.py --apply             # 实际写入
    python3 codemod_log_exceptions.py --apply --json report.json   # 同时导出详细报告
    python3 codemod_log_exceptions.py --types Exception,OSError,ValueError
    python3 codemod_log_exceptions.py --exclude tests,scripts/legacy
    python3 codemod_log_exceptions.py --src some/other/src/pkg     # 指定扫描根目录
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_SRC_ROOT = Path(__file__).resolve().parent / "src" / "mini_agent"

# 视为"吞掉异常，什么都没记"的调用名（出现即认为该 handler 已被正确处理）
SAFE_CALL_NAMES = {
    "log_exception",
}
SAFE_ATTR_METHODS = {
    "exception",  # logger.exception(...) / logging.exception(...)
    "error",
    "critical",
    "warning",
    "warn",
}
# 这些属性访问只有落在 "logger"/"logging"/"log"/"self._log" 等常见命名上才算数，
# 避免把业务代码里恰好叫 .error()/.warning() 的无关方法（比如某个 Result 对象）
# 也当成"已处理"而漏掉。可以按需扩充。
LOGGER_ISH_NAMES = {"logger", "logging", "log", "_logger", "self", "cls"}


@dataclass
class SilentHandler:
    file: Path
    lineno: int              # except 行号
    end_lineno: int          # handler 覆盖到的最后一行（含 body）
    exc_types: str           # 打印用：异常类型文本
    has_as: bool
    as_name: Optional[str]
    is_bare: bool
    body_first_lineno: int
    body_indent: str
    where: str


def _is_target_exception(node: ast.ExceptHandler, target_types: set[str]) -> bool:
    """判断该 except 是否命中目标异常类型集合（含裸 except）。"""
    if node.type is None:
        return "bare" in target_types or "Exception" in target_types
    names = _extract_exc_names(node.type)
    return any(n in target_types for n in names)


def _extract_exc_names(node: ast.expr) -> list[str]:
    """从 `except (A, B):` / `except A:` 里提取出异常名字符串列表。"""
    if isinstance(node, ast.Tuple):
        out: list[str] = []
        for elt in node.elts:
            out.extend(_extract_exc_names(elt))
        return out
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    return []


def _is_already_handled(body: list[ast.stmt]) -> bool:
    """扫描 body，判断是否已经有日志记录或者 raise（重新抛出）。"""
    already = False

    class _Visitor(ast.NodeVisitor):
        def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
            nonlocal already
            already = True

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            nonlocal already
            func = node.func
            if isinstance(func, ast.Name) and func.id in SAFE_CALL_NAMES:
                already = True
            elif isinstance(func, ast.Attribute) and func.attr in SAFE_ATTR_METHODS:
                base = func.value
                base_name = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name and (
                    base_name in LOGGER_ISH_NAMES
                    or base_name.endswith("logger")
                    or base_name.endswith("_log")
                ):
                    already = True
            self.generic_visit(node)

    for stmt in body:
        _Visitor().visit(stmt)
    return already


def _enclosing_where(path: Path, src_root: Path, parents: dict[ast.AST, ast.AST], node: ast.AST) -> str:
    """推导 where = module.path.ClassName.func_name（尽量精确到函数级）。"""
    mod_parts = list(path.relative_to(src_root.parent).with_suffix("").parts)
    if mod_parts and mod_parts[-1] == "__init__":
        mod_parts = mod_parts[:-1]
    module_name = ".".join(mod_parts)

    chain: list[str] = []
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            chain.append(cur.name)
        cur = parents.get(cur)
    chain.reverse()
    if chain:
        return f"{module_name}.{'.'.join(chain)}"
    return module_name


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def find_silent_handlers(
    path: Path, src_root: Path, target_types: set[str]
) -> list[SilentHandler]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        print(f"[SKIP] 语法解析失败，跳过: {path}: {e}", file=sys.stderr)
        return []

    parents = _build_parent_map(tree)
    lines = text.splitlines()

    results: list[SilentHandler] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_target_exception(node, target_types):
            continue
        if not node.body:
            continue
        if _is_already_handled(node.body):
            continue

        first_stmt = node.body[0]
        body_line = lines[first_stmt.lineno - 1] if first_stmt.lineno - 1 < len(lines) else ""
        body_indent = body_line[: len(body_line) - len(body_line.lstrip())]

        is_bare = node.type is None
        exc_types_txt = "bare except" if is_bare else ast.unparse(node.type)  # type: ignore[arg-type]
        end_lineno = max((getattr(s, "end_lineno", s.lineno) or s.lineno) for s in node.body)

        results.append(
            SilentHandler(
                file=path,
                lineno=node.lineno,
                end_lineno=end_lineno,
                exc_types=exc_types_txt,
                has_as=node.name is not None,
                as_name=node.name,
                is_bare=is_bare,
                body_first_lineno=first_stmt.lineno,
                body_indent=body_indent,
                where=_enclosing_where(path, src_root, parents, node),
            )
        )

    return results


def _rewrite_except_line(line: str, new_var: str) -> str:
    """给形如 `except Exception:` / `except (A, B):  # xxx` 的行补上 `as var`。
    已经有 `as` 的行不应该走到这里（has_as=True 时不调用本函数）。
    裸 except（`except:`）也不应该走到这里。
    """
    idx = line.rfind(":")
    if idx == -1:
        # 理论上不会发生（except 一定有冒号），保底原样返回
        return line
    head, tail = line[:idx], line[idx:]
    return f"{head} as {new_var}{tail}"


def process_file(
    path: Path, src_root: Path, target_types: set[str], apply: bool, default_var: str
) -> list[SilentHandler]:
    handlers = find_silent_handlers(path, src_root, target_types)
    if not handlers:
        return []

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # 从后往前改，避免前面插入的行影响后面的行号
    for h in sorted(handlers, key=lambda x: x.body_first_lineno, reverse=True):
        var = h.as_name or default_var
        exc_arg = "sys.exc_info()[1]" if h.is_bare else var
        log_block = (
            f"{h.body_indent}from mini_agent.errors import log_exception\n"
            f"{h.body_indent}log_exception({exc_arg}, where={h.where!r})\n"
        )
        if h.is_bare:
            # 裸 except 需要 sys 模块；用局部 import 而不是改动全局 import 区，
            # 避免和已有 import 顺序/风格冲突（且 import 是幂等的，多次 import 无副作用）。
            log_block = f"{h.body_indent}import sys\n" + log_block
        insert_at = h.body_first_lineno - 1  # 0-indexed，插入到该行之前
        lines[insert_at:insert_at] = [log_block]

        if not h.has_as and not h.is_bare:
            except_line_idx = h.lineno - 1
            lines[except_line_idx] = _rewrite_except_line(lines[except_line_idx], var)

    new_text = "".join(lines)

    if apply:
        # 写入前做一次语法校验，防止边界情况（比如多行 except 签名）改坏文件
        try:
            ast.parse(new_text, filename=str(path))
        except SyntaxError as e:
            print(f"[ABORT] {path} 修改后语法错误，已跳过写入，请人工检查: {e}", file=sys.stderr)
            return []
        path.write_text(new_text, encoding="utf-8")

    return handlers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="实际写入文件，默认只预览统计")
    parser.add_argument(
        "--src",
        type=str,
        default=None,
        help=f"扫描根目录（默认 {DEFAULT_SRC_ROOT}）",
    )
    parser.add_argument(
        "--types",
        type=str,
        default="Exception,bare",
        help="需要处理的异常类型，逗号分隔，如 'Exception,OSError,ValueError'；"
        "特殊值 'bare' 代表裸 except:（默认 'Exception,bare'）",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help="逗号分隔的路径子串黑名单，命中则跳过该文件，如 'tests,scripts/legacy'",
    )
    parser.add_argument(
        "--var-name",
        type=str,
        default="_mini_agent_exc",
        help="补充 `as` 时使用的变量名（默认 _mini_agent_exc）",
    )
    parser.add_argument("--json", type=str, default=None, help="把详细报告写到指定 JSON 文件")
    args = parser.parse_args()

    src_root = Path(args.src).resolve() if args.src else DEFAULT_SRC_ROOT
    if not src_root.exists():
        print(f"[ERROR] 扫描根目录不存在: {src_root}", file=sys.stderr)
        sys.exit(1)

    target_types = {t.strip() for t in args.types.split(",") if t.strip()}
    exclude_subs = [s.strip() for s in args.exclude.split(",") if s.strip()]

    total = 0
    files_changed = 0
    report: list[dict] = []

    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root.parent)
        if any(sub in str(rel) for sub in exclude_subs):
            continue

        handlers = process_file(path, src_root, target_types, apply=args.apply, default_var=args.var_name)
        if not handlers:
            continue

        total += len(handlers)
        files_changed += 1
        tag = "[APPLY]" if args.apply else "[DRY-RUN]"
        print(f"{tag} {rel}: {len(handlers)} 处")
        for h in handlers:
            print(f"    L{h.lineno} except {h.exc_types} -> where={h.where!r}")
            report.append(
                {
                    "file": str(rel),
                    "lineno": h.lineno,
                    "exc_types": h.exc_types,
                    "is_bare": h.is_bare,
                    "where": h.where,
                }
            )

    verb = "已修复" if args.apply else "待修复（加 --apply 生效）"
    print(f"\n共 {files_changed} 个文件, {total} 处静默异常{verb}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"详细报告已写入 {args.json}")


if __name__ == "__main__":
    main()
