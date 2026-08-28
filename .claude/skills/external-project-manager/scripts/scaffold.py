#!/usr/bin/env python
"""scaffold.py — 生成一个符合 external_projects_workspace_plan.md §5.1
标准结构的外部项目骨架。

用法：
    python scaffold.py <name> [--path <dir>] [--summary "一句话目标"] \
        [--entrypoint <key>[,<key>...]] [--force]

    <name>          项目名（snake_case，用作 project.yaml 的 name 与
                    daemon 注册表 key）。
    --path          项目落地目录，默认 external_projects/<name>（相对于
                    "运行本脚本时的当前工作目录"；如果当前目录已经是
                    external_projects/ 下，会直接用 <当前目录>/<name>，
                    避免生成成 external_projects/external_projects/<name>）。
                    可以传任意绝对路径——外部项目允许放在完全不同的
                    位置（原则三：路径独立）。
    --summary       PROJECT.md 标题里的一句话目标，缺省是占位符 TODO。
    --entrypoint    要生成的 entrypoint key 列表，逗号分隔，默认只生成
                    一个 "main"。每个都会在 project.yaml 里声明一条、
                    在 entrypoints/ 下生成一个对应脚本。
    --force         目标目录已存在时，只补齐缺失文件，不覆盖已有文件
                    （不传这个参数时，目标目录非空会直接报错退出）。

生成后自动调用 mini_agent.external_projects.manifest.load_manifest()
校验一遍新生成的 project.yaml——这是"骨架生成即合规"这个承诺的唯一
验证手段，校验失败时脚本以非零退出码结束，并保留已生成文件供人工修正
（不静默吞掉错误）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _SKILL_DIR / "templates"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ScaffoldError(RuntimeError):
    pass


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ScaffoldError(
            f"项目名 '{name}' 不合法：必须是 snake_case（小写字母开头，"
            "只能包含小写字母/数字/下划线），例如 stock_watch、sentiment_monitor"
        )


def _render(template_text: str, mapping: dict) -> str:
    out = template_text
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _write_if_absent(path: Path, content: str, *, force: bool) -> bool:
    """写入文件；已存在且非 force 时跳过（返回 False），否则写入（返回 True）。

    与 register() 的"不覆盖已有注册"原则一致：force 模式下也只补齐
    缺失文件，绝不覆盖已存在的同名文件——已有文件可能是用户已经开始
    编写的业务代码，脚手架不应该有任何机会破坏它。
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _default_path_for(name: str) -> Path:
    cwd = Path.cwd()
    if cwd.name == "external_projects":
        return cwd / name
    return cwd / "external_projects" / name


def scaffold(
    name: str,
    *,
    dest: Path,
    summary: str,
    entrypoints: list[str],
    force: bool,
) -> Path:
    _validate_name(name)
    for ep in entrypoints:
        if not _NAME_RE.match(ep):
            raise ScaffoldError(f"entrypoint key '{ep}' 不合法，规则同项目名")

    dest = dest.expanduser().resolve()
    if dest.exists() and any(dest.iterdir()) and not force:
        raise ScaffoldError(
            f"目标目录 '{dest}' 已存在且非空。如果是想给已有项目补齐缺失"
            "文件，加 --force（只补缺失文件，不覆盖已有文件）；如果是"
            "想新建，换一个空目录/新路径。"
        )

    today = _dt.date.today().isoformat()
    package_name = name

    mapping = {
        "PROJECT_NAME": name,
        "PROJECT_SUMMARY": summary,
        "PACKAGE_NAME": package_name,
        "DATE": today,
    }

    created: list[Path] = []
    skipped: list[Path] = []

    def emit(rel_path: str, content: str) -> None:
        target = dest / rel_path
        if _write_if_absent(target, content, force=force):
            created.append(target)
        else:
            skipped.append(target)

    # 1. entrypoints/main.py 之外的固定骨架文件
    project_yaml_tmpl = (_TEMPLATES_DIR / "project.yaml.tmpl").read_text(encoding="utf-8")
    project_md_tmpl = (_TEMPLATES_DIR / "PROJECT.md.tmpl").read_text(encoding="utf-8")
    health_tmpl = (_TEMPLATES_DIR / "health.py.tmpl").read_text(encoding="utf-8")
    entrypoint_tmpl = (_TEMPLATES_DIR / "entrypoint.py.tmpl").read_text(encoding="utf-8")
    gitignore_tmpl = (_TEMPLATES_DIR / "gitignore.tmpl").read_text(encoding="utf-8")

    # project.yaml 的 entrypoints 块需要按 --entrypoint 列表重新生成，
    # 而不是简单字符串替换模板里的占位 "main"——当用户传了多个
    # entrypoint 时，模板里那一条 "main" 声明不够用。
    entrypoints_yaml_lines = []
    for i, ep in enumerate(entrypoints):
        entrypoints_yaml_lines.append(f"  {ep}:")
        entrypoints_yaml_lines.append(f'    cmd: "python entrypoints/{ep}.py"')
        entrypoints_yaml_lines.append("    # schedule: \"cron: 0 9 * * 1-5\"  # 按需取消注释")
        entrypoints_yaml_lines.append("    timeout_sec: 300")
    entrypoints_yaml_block = "\n".join(entrypoints_yaml_lines)

    project_yaml_content = _render(project_yaml_tmpl, mapping)
    # 用重新生成的 entrypoints 块替换模板里默认的 "main" 占位块（模板
    # 文件本身就是给单个 entrypoint 场景准备的注释详尽版本，多 entrypoint
    # 场景下这里做整体替换，保留文件其余部分的注释与说明不变）。
    marker_start = "entrypoints:\n"
    idx = project_yaml_content.index(marker_start) + len(marker_start)
    idx_end = project_yaml_content.index("\nhealth_check:")
    project_yaml_content = (
        project_yaml_content[:idx] + entrypoints_yaml_block + project_yaml_content[idx_end:]
    )

    emit("project.yaml", project_yaml_content)
    emit("PROJECT.md", _render(project_md_tmpl, mapping))
    emit("requirements.txt", "# 本项目独立依赖，按需添加\n")
    emit(".gitignore", _render(gitignore_tmpl, mapping))
    emit("entrypoints/health.py", health_tmpl)
    emit("reports/.gitkeep", "")
    emit("data/.gitkeep", "")
    emit(f"{package_name}/__init__.py", "")
    emit("tests/.gitkeep", "")
    emit("config/.gitkeep", "")

    for ep in entrypoints:
        ep_mapping = dict(mapping, ENTRYPOINT_KEY=ep)
        emit(f"entrypoints/{ep}.py", _render(entrypoint_tmpl, ep_mapping))

    (dest / ".agent").mkdir(parents=True, exist_ok=True)

    return dest


def _self_validate(dest: Path) -> None:
    """生成后立刻用框架自己的校验函数验证 project.yaml 合法。"""
    try:
        from mini_agent.external_projects.manifest import load_manifest
    except ImportError as exc:  # pragma: no cover - 环境未正确安装 mini_agent
        print(
            f"警告：无法 import mini_agent.external_projects.manifest（{exc}），"
            "跳过自动校验。请确认在能 import mini_agent 的环境下运行本脚本，"
            "或手动执行 `mini-agent projects register <path> --no-validate` "
            "后再 `mini-agent projects status <name>` 间接确认。",
            file=sys.stderr,
        )
        return

    load_manifest(dest)  # 抛异常则说明生成的 project.yaml 不合法


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="项目名（snake_case）")
    parser.add_argument("--path", help="项目落地目录，默认 external_projects/<name>")
    parser.add_argument("--summary", default="TODO：一句话说明这个项目的目标", help="项目一句话目标")
    parser.add_argument(
        "--entrypoint",
        default="main",
        help="逗号分隔的 entrypoint key 列表，默认只生成一个 'main'",
    )
    parser.add_argument("--force", action="store_true", help="目标目录非空时只补齐缺失文件")
    args = parser.parse_args(argv)

    dest = Path(args.path) if args.path else _default_path_for(args.name)
    entrypoints = [e.strip() for e in args.entrypoint.split(",") if e.strip()]

    try:
        dest = scaffold(
            args.name,
            dest=dest,
            summary=args.summary,
            entrypoints=entrypoints,
            force=args.force,
        )
        _self_validate(dest)
    except ScaffoldError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(
            f"生成的 project.yaml 未通过 load_manifest() 校验：{exc}\n"
            f"骨架文件已保留在 {dest}，请修正后重新校验（可直接手动跑：\n"
            "  python -c \"from mini_agent.external_projects.manifest import "
            f"load_manifest; load_manifest('{dest}')\"）",
            file=sys.stderr,
        )
        return 1

    print(f"骨架已生成：{dest}")
    print("下一步：")
    print(f"  1. 实现 {dest}/entrypoints/*.py 里的业务逻辑（替换 TODO）")
    print(f"  2. 补全 {dest}/PROJECT.md 里的 TODO 小节")
    print(f"  3. 本地跑通：python entrypoints/<key>.py（在 {dest} 目录下）")
    print(f"  4. 注册：mini-agent projects register {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
