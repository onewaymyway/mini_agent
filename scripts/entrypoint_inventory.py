#!/usr/bin/env python3
"""scripts/entrypoint_inventory.py — 对外入口盘点（Phase 10 Sprint 10-1）

对应 `next_doc/refactor_plan/11-phase10-legacy-decommission-plan.md`
Sprint 10-1 第一项任务：“列出 CLI / HTTP API / 微信 / Android 等所有对外
暴露的接口，标注每个接口当前是直接调用旧模块，还是已经过 `AgentRuntime`”。

与 Sprint 0 的 `scripts/dep_graph.py` 同一风格：纯静态（AST）、只读、
可重复运行，产出可直接贴进 `docs/architecture_v2/phase10-entrypoint-
inventory.md` 的 Markdown。

═══ 方法与局限（务必读，否则会误读结果）═══

对每个入口（斜杠命令分支 / HTTP 路由处理函数）：
  1. 收集其函数体里出现的名字（Name / Attribute / import），与“旧类名清单”
     （原方案 §43 六条映射对应的真实类/模块，见 `LEGACY_SYMBOLS`）、
     `AgentRuntime`、`mini_agent.core|runtime|actions` 三类标记比对；
  2. 沿“同文件函数调用”和“函数内/模块级 import 的函数”向下最多展开
     `MAX_DEPTH` 跳，合并被调函数里的标记。

因此这是**下限估计**：
  - 只看函数调用与名字，不做类型推断；`self.agent.xxx` 这类经实例属性
    间接到达旧模块的路径**看不到**；
  - 超过 `MAX_DEPTH` 跳的路径看不到；
  - 所以 “legacy=空” 只表示“静态浅层没发现”，**不等于**“已收敛”；反过来
    “命中旧类名”是确凿的（名字真的出现在调用链上）。

分类（`Entry.kind`）：
  - `runtime`：命中 AgentRuntime 且未命中旧类；
  - `mixed`：两者都命中；
  - `legacy`：命中旧类、未命中 AgentRuntime；
  - `none`：清单内一个都没命中（配置/会话/展示类命令居多，或路径超出静态
    可见范围）。

用法：
    python scripts/entrypoint_inventory.py                 # Markdown 到 stdout
    python scripts/entrypoint_inventory.py --format json
    python scripts/entrypoint_inventory.py --out docs/architecture_v2/phase10-entrypoint-inventory.generated.md
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

MAX_DEPTH = 3

# 原方案 §43 六条映射 → 当前代码里对应的真实旧类 / 旧模块（已逐个核实存在）。
# 注意：代码里没有 `GoalManager`，README 里那只是举例名；这里只放真实存在的。
LEGACY_SYMBOLS: dict[str, tuple[str, ...]] = {
    "旧 Goal → Adapter": (
        "GoalRunner", "GoalStateStore", "GoalStepExecutor",
        "GoalTreeDecomposer", "GoalBacklog",
    ),
    "旧 Memory → Adapter": ("MemoryStore", "MemoryBackend", "HistoryManager"),
    "旧 Workflow → Capability": ("WorkflowRunner", "WorkflowStore", "WorkflowGenerator"),
    "旧 Scheduler → Runtime adapter": (
        "CronScheduler", "CronJobExecutor", "UnifiedTaskScheduler", "AutonomousLoop",
    ),
    "旧 Advisor → Decision policy": ("next_action_advisor", "growth_advisor"),
    "旧 Objective → Goal internal step": (
        "ObjectiveExecutor", "ObjectivePersistentRunner", "ObjectiveIsolatedRunner",
    ),
}
_SYMBOL_TO_GROUP = {s: g for g, syms in LEGACY_SYMBOLS.items() for s in syms}

# 用户可见字符串里“产品概念词”（不是类名）——仅作信息统计，不计入验收判定。
CONCEPT_WORDS = ("Objective", "Cron", "Advisor", "Workflow", "Scheduler")

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "websocket", "head", "options"}


# ─────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Refs:
    legacy: set = field(default_factory=set)
    runtime: bool = False
    core: bool = False

    def merge(self, other: "Refs") -> None:
        self.legacy |= other.legacy
        self.runtime |= other.runtime
        self.core |= other.core

    def kind(self) -> str:
        if self.runtime and self.legacy:
            return "mixed"
        if self.runtime:
            return "runtime"
        if self.legacy:
            return "legacy"
        return "none"


@dataclass
class Entry:
    surface: str            # "cli" | "http"
    name: str               # "/goal" | "POST /v1/chat"
    kind: str
    legacy: list
    core: bool
    where: str              # 相对路径:行号


# ─────────────────────────────────────────────────────────────────────
# 源码读取 / 解析（带缓存）
# ─────────────────────────────────────────────────────────────────────

class Source:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pkg_root = root / "src"
        self._trees: dict[Path, Optional[ast.AST]] = {}
        self._funcs: dict[Path, dict[str, list]] = {}
        self._imports: dict[Path, dict[str, tuple]] = {}

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(p)

    def tree(self, path: Path) -> Optional[ast.AST]:
        if path not in self._trees:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                self._trees[path] = ast.parse(text)
            except (OSError, SyntaxError):
                self._trees[path] = None
        return self._trees[path]

    def functions(self, path: Path) -> dict[str, list]:
        if path not in self._funcs:
            idx: dict[str, list] = {}
            t = self.tree(path)
            if t is not None:
                for n in ast.walk(t):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        idx.setdefault(n.name, []).append(n)
            self._funcs[path] = idx
        return self._funcs[path]

    def module_imports(self, path: Path) -> dict[str, tuple]:
        """alias -> (绝对模块名, 原名)；只收模块级（含 try/if 内）ImportFrom。"""
        if path not in self._imports:
            m: dict[str, tuple] = {}
            t = self.tree(path)
            if t is not None:
                for n in ast.walk(t):
                    if isinstance(n, ast.ImportFrom):
                        mod = self.abs_module(path, n.module, n.level)
                        for a in n.names:
                            m[a.asname or a.name] = (mod, a.name)
            self._imports[path] = m
        return self._imports[path]

    def abs_module(self, cur: Path, module: Optional[str], level: int) -> str:
        if level == 0:
            return module or ""
        try:
            parts = list(cur.relative_to(self.pkg_root).with_suffix("").parts)
        except ValueError:
            return module or ""
        pkg = parts[:-1] if parts and parts[-1] != "__init__" else parts[:-1]
        base = pkg[: len(pkg) - (level - 1)] if level > 1 else pkg
        return ".".join(base + ([module] if module else []))

    def resolve_module(self, dotted: str) -> Optional[Path]:
        if not dotted.startswith("mini_agent"):
            return None
        rel = Path(*dotted.split("."))
        for cand in (self.pkg_root / rel.with_suffix(".py"), self.pkg_root / rel / "__init__.py"):
            if cand.is_file():
                return cand
        return None


# ─────────────────────────────────────────────────────────────────────
# 引用分析
# ─────────────────────────────────────────────────────────────────────

def _direct_refs(nodes: Iterable[ast.AST]) -> Refs:
    r = Refs()
    for root in nodes:
        for n in ast.walk(root):
            names: list[str] = []
            if isinstance(n, ast.Name):
                names.append(n.id)
            elif isinstance(n, ast.Attribute):
                names.append(n.attr)
            elif isinstance(n, ast.ImportFrom):
                mod = n.module or ""
                names.extend(a.name for a in n.names)
                names.extend(mod.split("."))
                if mod.startswith(("mini_agent.runtime",)):
                    r.runtime = True
                if mod.startswith(("mini_agent.core", "mini_agent.actions")):
                    r.core = True
            elif isinstance(n, ast.Import):
                names.extend(a.name.split(".")[-1] for a in n.names)
            for nm in names:
                if nm == "AgentRuntime":
                    r.runtime = True
                elif nm in _SYMBOL_TO_GROUP:
                    r.legacy.add(nm)
    return r


def _called_names(nodes: Iterable[ast.AST]) -> set[str]:
    out: set[str] = set()
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                out.add(n.func.id)
    return out


def _follow_reexport(src: Source, module: str, name: str, hops: int = 3) -> Optional[tuple]:
    """把 `from pkg import name` 解析到真正定义 `name` 的文件。

    `cli/commands/__init__.py` 这类包只做再导出（`from .x import handle_x`），
    函数并不定义在包文件里；不跟随再导出会漏掉整条调用链（首次运行就因此把
    `/goal` 误判为“未走 AgentRuntime”）。
    """
    for _ in range(hops):
        path = src.resolve_module(module)
        if path is None:
            return None
        if src.functions(path).get(name):
            return path, name
        nxt = src.module_imports(path).get(name)
        if not nxt:
            return None
        module, name = nxt
    return None


def analyze_nodes(src: Source, nodes: list, ctx: Path, depth: int, seen: set) -> Refs:
    refs = _direct_refs(nodes)
    if depth <= 0:
        return refs

    local_imports: dict[str, tuple] = {}
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.ImportFrom):
                mod = src.abs_module(ctx, n.module, n.level)
                for a in n.names:
                    local_imports[a.asname or a.name] = (mod, a.name)

    for name in _called_names(nodes):
        target_ctx, orig = ctx, name
        funcs = src.functions(ctx).get(name)
        if not funcs:
            imp = local_imports.get(name) or src.module_imports(ctx).get(name)
            if not imp:
                continue
            resolved = _follow_reexport(src, imp[0], imp[1])
            if resolved is None:
                continue
            target_ctx, orig = resolved
            funcs = src.functions(target_ctx).get(orig)
            if not funcs:
                continue
        key = (target_ctx, orig)
        if key in seen:
            continue
        seen.add(key)
        for fn in funcs:
            refs.merge(analyze_nodes(src, [fn], target_ctx, depth - 1, seen))
    return refs


# ─────────────────────────────────────────────────────────────────────
# 入口抽取
# ─────────────────────────────────────────────────────────────────────

def _str_consts(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def extract_cli_commands(src: Source, repl_path: Path) -> list[Entry]:
    """从 REPL 的 `name == "x"` / `name in (...)` 分支抽取斜杠命令。"""
    tree = src.tree(repl_path)
    if tree is None:
        return []
    by_cmd: dict[str, Refs] = {}
    first_line: dict[str, int] = {}
    for n in ast.walk(tree):
        if not isinstance(n, ast.If):
            continue
        cmds: list[str] = []
        for c in ast.walk(n.test):
            if (isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id == "name"
                    and len(c.ops) == 1 and isinstance(c.ops[0], (ast.Eq, ast.In))):
                cmds.extend(_str_consts(c.comparators[0]))
        if not cmds:
            continue
        refs = analyze_nodes(src, list(n.body), repl_path, MAX_DEPTH, set())
        for cmd in cmds:
            by_cmd.setdefault(cmd, Refs()).merge(refs)
            first_line.setdefault(cmd, n.lineno)
    rel = src.rel(repl_path)
    return [
        Entry("cli", f"/{cmd}", r.kind(), sorted(r.legacy), r.core, f"{rel}:{first_line[cmd]}")
        for cmd, r in sorted(by_cmd.items())
    ]


def extract_http_routes(src: Source, route_files: list[Path]) -> list[Entry]:
    entries: list[Entry] = []
    for path in route_files:
        tree = src.tree(path)
        if tree is None:
            continue
        prefixes: dict[str, str] = {}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and getattr(n.value.func, "id", getattr(n.value.func, "attr", "")) == "APIRouter"):
                for kw in n.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        for t in n.targets:
                            if isinstance(t, ast.Name):
                                prefixes[t.id] = str(kw.value.value)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in fn.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in _HTTP_METHODS and dec.args
                        and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
                    continue
                owner = dec.func.value.id if isinstance(dec.func.value, ast.Name) else ""
                full = prefixes.get(owner, "") + dec.args[0].value
                refs = analyze_nodes(src, [fn], path, MAX_DEPTH, set())
                entries.append(Entry(
                    "http", f"{dec.func.attr.upper()} {full}", refs.kind(),
                    sorted(refs.legacy), refs.core, f"{src.rel(path)}:{fn.lineno}",
                ))
    return sorted(entries, key=lambda e: (e.name.split(" ", 1)[1], e.name))


def scan_other_entrypoints(root: Path) -> list[dict]:
    """非 Python-CLI/HTTP 的对外入口：只做事实性盘点，不做深度分析。"""
    out: list[dict] = []
    wx = root / "weixin_bot.py"
    if wx.is_file():
        tree = ast.parse(wx.read_text(encoding="utf-8", errors="replace"))
        mods = sorted({
            n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("mini_agent")
        })
        out.append({
            "entry": "weixin_bot.py", "how": "进程内直接构造 Agent（不经 HTTP、不经 AgentRuntime）",
            "evidence": "import: " + ", ".join(mods),
        })
    for d in ("android_companion_app", "browser_extension_example", "weixin_plugin",
              "mini_agent_kanban", "mini_agent_kanban_x"):
        p = root / "apps" / d
        if not p.is_dir():
            continue
        eps: set[str] = set()
        inproc: set[str] = set()
        for f in p.rglob("*"):
            if not (f.is_file() and f.suffix in {".kt", ".js", ".ts", ".tsx", ".py", ".html"}):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            eps |= set(re.findall(r"/v1/[A-Za-z0-9_/{}$.-]*[A-Za-z0-9_}]", text))
            if f.suffix == ".py":
                inproc |= set(re.findall(r"^\s*(?:from|import)\s+(mini_agent[\w.]*)", text, re.MULTILINE))
        if eps and inproc:
            how = "同时存在 HTTP 调用（`/v1/*`）与进程内导入 `mini_agent`"
        elif eps:
            how = "HTTP 客户端（经 `/v1/*` 接口）"
        elif inproc:
            how = "进程内直接导入 `mini_agent`（不经 HTTP）"
        else:
            how = "静态扫描未发现 `/v1/*` 调用或 `mini_agent` 导入（接入方式需人工确认）"
        evid = ", ".join(sorted(eps)[:6]) + (" …" if len(eps) > 6 else "")
        if inproc:
            evid = (evid + "; " if evid else "") + "import: " + ", ".join(sorted(inproc)[:4])
        out.append({"entry": f"apps/{d}", "how": how, "evidence": evid})
    return out


# ─────────────────────────────────────────────────────────────────────
# 用户可见字符串的术语扫描（Sprint 10-1 验收标准的可度量部分）
# ─────────────────────────────────────────────────────────────────────

_CLASS_RE = re.compile(r"\b(" + "|".join(sorted(_SYMBOL_TO_GROUP, key=len, reverse=True)) + r")\b")
_CONCEPT_RE = re.compile(r"\b(" + "|".join(CONCEPT_WORDS) + r")\w*", re.IGNORECASE)


def collect_user_visible_strings(src: Source, root: Path, route_files: list[Path]) -> dict[str, list[tuple]]:
    """返回 {surface: [(位置, 文本)]}。

    - cli_menu：`ui/terminal.py::_COMMANDS` 的描述（斜杠命令补全菜单）
    - cli_args：`cli/parser.py` 的 argparse `help=`/`description=`/`epilog=`（即 `/help` 与 `--help` 输出）
    - http_docs：路由处理函数 docstring 与装饰器 summary/description（即 OpenAPI 文档）
    """
    res: dict[str, list[tuple]] = {"cli_menu": [], "cli_args": [], "http_docs": []}

    term = root / "src/mini_agent/ui/terminal.py"
    t = src.tree(term)
    if t is not None:
        for n in ast.walk(t):
            targets = n.targets if isinstance(n, ast.Assign) else ([n.target] if isinstance(n, ast.AnnAssign) else [])
            if (isinstance(n, (ast.Assign, ast.AnnAssign)) and any(isinstance(x, ast.Name) and x.id == "_COMMANDS" for x in targets)
                    and isinstance(n.value, ast.List)):
                for tup in n.value.elts:
                    if isinstance(tup, ast.Tuple) and len(tup.elts) >= 2:
                        cmd, desc = tup.elts[0], tup.elts[1]
                        if isinstance(cmd, ast.Constant) and isinstance(desc, ast.Constant):
                            res["cli_menu"].append((f"ui/terminal.py:{tup.lineno} {cmd.value}", str(desc.value)))

    parser = root / "src/mini_agent/cli/parser.py"
    t = src.tree(parser)
    if t is not None:
        for n in ast.walk(t):
            if isinstance(n, ast.Call):
                for kw in n.keywords:
                    if kw.arg in {"help", "description", "epilog"} and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        res["cli_args"].append((f"cli/parser.py:{n.lineno}", kw.value.value))

    for path in route_files:
        t = src.tree(path)
        if t is None:
            continue
        for fn in ast.walk(t):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routed = False
            for dec in fn.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in _HTTP_METHODS:
                    routed = True
                    for kw in dec.keywords:
                        if kw.arg in {"summary", "description"} and isinstance(kw.value, ast.Constant):
                            res["http_docs"].append((f"{src.rel(path)}:{fn.lineno}", str(kw.value.value)))
            doc = ast.get_docstring(fn)
            if routed and doc:
                res["http_docs"].append((f"{src.rel(path)}:{fn.lineno}", doc))
    return res


def scan_terminology(strings: dict[str, list[tuple]]) -> dict:
    out: dict = {}
    for surface, items in strings.items():
        class_hits = []
        for where, text in items:
            for term in sorted({m.group(1) for m in _CLASS_RE.finditer(text)}):
                class_hits.append({"where": where, "term": term, "excerpt": text.strip()[:100]})
        concept_count: dict[str, int] = {}
        for _where, text in items:
            for m in _CONCEPT_RE.finditer(text):
                w = next(c for c in CONCEPT_WORDS if m.group(0).lower().startswith(c.lower()))
                concept_count[w] = concept_count.get(w, 0) + 1
        out[surface] = {
            "strings": len(items), "class_hits": class_hits, "concept_word_counts": concept_count,
        }
    return out


# ─────────────────────────────────────────────────────────────────────
# 汇总与渲染
# ─────────────────────────────────────────────────────────────────────

def default_route_files(root: Path) -> list[Path]:
    api = root / "src/mini_agent/api"
    return [p for p in (api / "routes.py", api / "capability_routes.py",
                        api / "persona_candidate_routes.py", api / "server.py") if p.is_file()]


def build_report(root: Path) -> dict:
    src = Source(root)
    route_files = default_route_files(root)
    cli = extract_cli_commands(src, root / "src/mini_agent/cli/repl.py")
    http = extract_http_routes(src, route_files)

    def counts(entries: list[Entry]) -> dict:
        c = {"runtime": 0, "mixed": 0, "legacy": 0, "none": 0}
        for e in entries:
            c[e.kind] += 1
        return {"total": len(entries), **c}

    group_hits: dict[str, dict] = {}
    for surface, entries in (("cli", cli), ("http", http)):
        for e in entries:
            for sym in e.legacy:
                g = _SYMBOL_TO_GROUP[sym]
                group_hits.setdefault(g, {}).setdefault(surface, set()).add(e.name)

    return {
        "max_depth": MAX_DEPTH,
        "cli": [e.__dict__ for e in cli],
        "http": [e.__dict__ for e in http],
        "summary": {"cli": counts(cli), "http": counts(http)},
        "legacy_group_reach": {
            g: {s: sorted(v) for s, v in d.items()} for g, d in sorted(group_hits.items())
        },
        "other_entrypoints": scan_other_entrypoints(root),
        "terminology": scan_terminology(collect_user_visible_strings(src, root, route_files)),
    }


def render_markdown(rep: dict) -> str:
    L: list[str] = []
    s = rep["summary"]
    L.append("<!-- 由 scripts/entrypoint_inventory.py 生成，勿手改；重新生成见该脚本 docstring -->\n")
    L.append(f"静态分析深度 `MAX_DEPTH={rep['max_depth']}`，结果为**下限估计**（见脚本 docstring）。\n")
    L.append("### 汇总\n")
    L.append("| 入口类别 | 总数 | runtime | mixed | legacy | none |")
    L.append("|---|---|---|---|---|---|")
    for k, label in (("cli", "CLI 斜杠命令"), ("http", "HTTP 路由")):
        x = s[k]
        L.append(f"| {label} | {x['total']} | {x['runtime']} | {x['mixed']} | {x['legacy']} | {x['none']} |")
    L.append("")
    L.append("### §43 六条映射：入口直接触达旧类的情况\n")
    L.append("| 映射 | 触达的 CLI 命令数 | 触达的 HTTP 路由数 |")
    L.append("|---|---|---|")
    for g in LEGACY_SYMBOLS:
        d = rep["legacy_group_reach"].get(g, {})
        L.append(f"| {g} | {len(d.get('cli', []))} | {len(d.get('http', []))} |")
    L.append("")
    L.append("### 其它对外入口\n")
    L.append("| 入口 | 接入方式 | 依据 |")
    L.append("|---|---|---|")
    for o in rep["other_entrypoints"]:
        L.append(f"| `{o['entry']}` | {o['how']} | {o['evidence'] or '-'} |")
    L.append("")
    L.append("### 用户可见字符串的术语扫描\n")
    L.append("| 表面 | 字符串数 | 命中旧类名（验收判定项） | 产品概念词计数（仅供参考） |")
    L.append("|---|---|---|---|")
    for surf, d in rep["terminology"].items():
        cw = ", ".join(f"{k}×{v}" for k, v in sorted(d["concept_word_counts"].items())) or "-"
        L.append(f"| {surf} | {d['strings']} | {len(d['class_hits'])} | {cw} |")
    hits = [(surf, h) for surf, d in rep["terminology"].items() for h in d["class_hits"]]
    if hits:
        L.append("\n命中旧类名的用户可见字符串：\n")
        L.append("| 表面 | 位置 | 旧类名 | 文本节选 |")
        L.append("|---|---|---|---|")
        for surf, h in hits:
            # 注意：不能把带反斜杠的表达式写在 f-string 里——项目支持 Python 3.10/3.11，
            # 那里 f-string 表达式含反斜杠是 SyntaxError（3.12 才放开）。
            excerpt = h["excerpt"].replace("|", "\\|").replace("\n", " ")
            L.append(f"| {surf} | `{h['where']}` | `{h['term']}` | {excerpt} |")
    L.append("")
    for title, key in (("附录 A：CLI 斜杠命令逐条", "cli"), ("附录 B：HTTP 路由逐条", "http")):
        L.append(f"### {title}\n")
        L.append("| 入口 | 分类 | 触达的旧类 | 触达 core/actions | 位置 |")
        L.append("|---|---|---|---|---|")
        for e in rep[key]:
            L.append(f"| `{e['name']}` | {e['kind']} | {', '.join(e['legacy']) or '-'} | {'是' if e['core'] else '-'} | `{e['where']}` |")
        L.append("")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="对外入口盘点（Phase 10 Sprint 10-1）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    rep = build_report(Path(args.root))
    text = json.dumps(rep, ensure_ascii=False, indent=2) if args.format == "json" else render_markdown(rep)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
