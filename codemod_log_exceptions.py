#!/usr/bin/env python3
"""
把 src/mini_agent 下所有形如：

    except Exception:
        pass

（且 pass 是该 except 块里唯一语句，允许前面有单行注释）替换为：

    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="<module>._codemod_auto")
        pass

其中 where 会填充为该文件的模块路径（如 "mini_agent.orchestrator.task_manager"），
保留原有 `pass`（保证行为完全不变——只是新增了记录，不改变控制流），
方便后续人工再去把 where 精细化成"函数名"级别。

用法：
    python3 codemod_log_exceptions.py --apply     # 实际写入
    python3 codemod_log_exceptions.py             # 仅预览，不写入
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

SRC_ROOT = Path("/home/claude/mini_agent_analysis/mini_agent-master/src/mini_agent")

# 匹配：缩进 + "except Exception" (可跟 "as x")? + ":" + 换行 + 同样缩进+4的 "pass" 单独一行
PATTERN = re.compile(
    r"(?P<indent>[ \t]*)except Exception(?P<as_clause> as \w+)?:\n"
    r"(?P<body_indent>[ \t]+)pass[ \t]*\n"
)


def module_name_for(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT.parent)  # 相对于 src/
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def process_file(path: Path, apply: bool) -> int:
    text = path.read_text(encoding="utf-8")
    mod_name = module_name_for(path)
    count = 0

    def _repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        indent = m.group("indent")
        body_indent = m.group("body_indent")
        as_clause = m.group("as_clause")
        var = as_clause.strip().split(" ")[-1] if as_clause else "_mini_agent_exc"
        new_except_line = f"{indent}except Exception as {var}:\n"
        log_line = (
            f"{body_indent}from mini_agent.errors import log_exception\n"
            f"{body_indent}log_exception({var}, where={mod_name!r})\n"
        )
        pass_line = f"{body_indent}pass\n"
        return new_except_line + log_line + pass_line

    new_text = PATTERN.sub(_repl, text)

    if count and apply:
        path.write_text(new_text, encoding="utf-8")

    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际写入文件，默认只预览统计")
    args = parser.parse_args()

    total = 0
    files_changed = 0
    for path in sorted(SRC_ROOT.rglob("*.py")):
        n = process_file(path, apply=args.apply)
        if n:
            total += n
            files_changed += 1
            rel = path.relative_to(SRC_ROOT.parent)
            print(f"{'[APPLY]' if args.apply else '[DRY-RUN]'} {rel}: {n} 处")

    print(f"\n共 {files_changed} 个文件, {total} 处 'except Exception: pass' 已{'替换' if args.apply else '（预览，未写入，加 --apply 生效）'}")


if __name__ == "__main__":
    main()
