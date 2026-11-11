"""脚本适配器：执行 sources/<id>/script.py 中的 fetch(ctx) 函数。

脚本约定：
    def fetch(ctx) -> list[dict]
    每个字典至少包含 title；可选 url、rank、heat、summary、published_at。
    ctx 提供 ctx.http_get(url) 与 ctx.browser（共享浏览器会话，可为 None）。

安全说明：
- 这是"静态护栏"，不是沙箱。validate 阶段禁止导入 os/sys/subprocess/socket 等高风险模块，
  并禁止调用 exec/eval/__import__/open。
- 脚本只在用户确认预览或确认添加后才会执行，LLM 生成的草稿本身不会被运行。
"""
from __future__ import annotations

import ast
import importlib.util
import itertools
from pathlib import Path
from typing import Any

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec

FORBIDDEN_MODULES = {"os", "sys", "subprocess", "shutil", "socket", "ctypes", "importlib",
                     "pickle", "multiprocessing", "signal", "builtins"}
FORBIDDEN_CALLS = {"exec", "eval", "__import__", "open", "compile"}
_counter = itertools.count()


def static_check(source: str) -> list[str]:
    """返回静态检查发现的问题列表（空表示通过）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"脚本语法错误：第 {exc.lineno} 行 {exc.msg}"]
    errs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES:
                    errs.append(f"禁止导入模块 {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
                errs.append(f"禁止导入模块 {node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                errs.append(f"禁止调用 {node.func.id}()")
    has_fetch = any(isinstance(n, ast.FunctionDef) and n.name == "fetch" for n in tree.body)
    if not has_fetch:
        errs.append("脚本必须定义顶层函数 fetch(ctx)")
    return sorted(set(errs))


def load_script(path: Path):
    spec = importlib.util.spec_from_file_location(f"info_hub_script_{next(_counter)}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _to_raw(row: Any, rank: int) -> RawItem:
    if not isinstance(row, dict):
        raise ValueError("脚本返回的每个条目必须是 dict")
    title = str(row.get("title", "")).strip()
    return RawItem(
        title=title,
        url=str(row.get("url", "") or ""),
        rank=int(row["rank"]) if row.get("rank") is not None else rank,
        heat=str(row["heat"]) if row.get("heat") is not None else None,
        summary=str(row.get("summary", "") or ""),
        published_at=str(row["published_at"]) if row.get("published_at") else None,
    )


class ScriptAdapter(Adapter):
    kind_name = "script"

    def validate(self, spec: SourceSpec) -> list[str]:
        path = spec.dir / "script.py"
        if not path.exists():
            return ["script 适配器需要 script.py"]
        return static_check(path.read_text(encoding="utf-8"))

    def fetch(self, spec: SourceSpec, ctx: FetchContext) -> FetchResult:
        path = spec.dir / "script.py"
        try:
            module = load_script(path)
            rows = module.fetch(ctx)
            if not isinstance(rows, list):
                return FetchResult(items=[], error="fetch(ctx) 必须返回列表")
            items = []
            for idx, row in enumerate(rows, start=1):
                raw = _to_raw(row, idx)
                if raw.title:
                    items.append(raw)
            return FetchResult(items=items[: self.max_items(spec)], meta={"script_rows": len(rows)})
        except Exception as exc:  # noqa: BLE001 - 脚本错误转为结果，不中断调度
            return FetchResult(items=[], error=f"脚本错误 {type(exc).__name__}: {exc}")
