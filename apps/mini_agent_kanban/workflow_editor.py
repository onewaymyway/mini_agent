"""
apps/mini_agent_kanban/workflow_editor.py — 看板工作流可视化编辑器：纯函数层
（next_doc/workflow_visual_editor_plan.md §三 / §四，里程碑 M3）

本模块**只做纯计算**，不 import streamlit、不发网络请求，方便单独单元测试
（见 `tests/test_workflow_editor_graph.py`）。沿用 `diff_view.py` / `async_job_ui.py` 的拆分先例：
`app.py` 只在 `render_workflow_tab` 里挂一个入口，编辑器主体（M4 的 UI）放在本模块之上。

所有"改草稿"的函数都遵循同一约定：**不原地修改入参**，返回新的草稿（深拷贝），
草稿就是后端 `GET /v1/workflows/{name}/editor` 返回的 `draft`（原始 YAML 文档的 JSON 形式，
`include` 条目原样保留）。

内容：
  1. 图转换：draft ⇄ 节点 / 边（含 include 节点、merge_sources 虚线边、节点徽标、批次）
  2. 依赖编辑：加 / 删依赖、环检测（拒绝成环并给出路径）、画布边差集 → depends_on
  3. 节点增删复制、边上插入节点
  4. 改 id 联动：depends_on / merge_sources / condition（按 AST 只改真正的名字引用）/
     `{id.xxx}` 占位符（含 prompt_file 正文），返回可进"查看变更"的改动清单
  5. 校验结果归类：后端 errors_by_step 的规范化、本地兜底归类、编辑器错误响应解析
  6. 变更预览：两份草稿的语义比较、步骤级摘要、git 风格 unified diff（可直接喂给 diff_view）
  7. 降级渲染：graphviz DOT（未安装 streamlit-flow-component 时用 `st.graphviz_chart`）
  8. 属性面板字段清单（§4.4，按类型出表单，UI 只负责渲染）
"""
from __future__ import annotations

import ast
import copy
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ══════════════════════════════════════════════════════════════════════════
# 1. 基础：节点标识 / 类型 / 样式
# ══════════════════════════════════════════════════════════════════════════

# type → (图标, 填充色, 边框色)
STEP_TYPE_STYLES: dict[str, tuple[str, str, str]] = {
    "agent": ("🤖", "#E3F2FD", "#1E88E5"),
    "role_agent": ("🎭", "#F3E5F5", "#8E24AA"),
    "sub_workflow": ("🔗", "#E8F5E9", "#43A047"),
    "tool_call": ("🔧", "#FFF3E0", "#FB8C00"),
    "human_input": ("🙋", "#FFFDE7", "#FBC02D"),
    "script": ("📜", "#ECEFF1", "#607D8B"),
    "python_step": ("🐍", "#E0F2F1", "#00897B"),
    "skill_agent": ("🧠", "#FCE4EC", "#D81B60"),
    "foreach": ("🔁", "#EDE7F6", "#5E35B1"),
    "wait": ("⏳", "#F5F5F5", "#757575"),
    "merge": ("🧬", "#E8EAF6", "#3949AB"),
    "include": ("🧩", "#EFEBE9", "#6D4C41"),
}
_UNKNOWN_STYLE = ("❓", "#FAFAFA", "#9E9E9E")   # 插件注册的自定义类型
ERROR_BORDER = "#E53935"
WARNING_BORDER = "#F9A825"

# include 节点只允许改这两项：片段内容要去 .agent/workflow_snippets/ 维护，避免编辑器把片段摊平
INCLUDE_EDITABLE_FIELDS = ("id", "depends_on")


def node_id(step: dict) -> str:
    """画布节点 id：step 的 id；没写 id 的 include 条目用片段名（与后端错误归属口径一致）。"""
    sid = step.get("id")
    if sid not in (None, ""):
        return str(sid)
    if step.get("include"):
        return str(step["include"])
    return ""


def is_include(step: dict) -> bool:
    return bool(step.get("include"))


def effective_type(step: dict) -> str:
    """与后端 WorkflowStep.effective_type 一致：include → "include"；未写 type 时按 role 推断。"""
    if is_include(step):
        return "include"
    if step.get("type"):
        return str(step["type"])
    return "role_agent" if step.get("role") else "agent"


def type_style(step_type: str) -> tuple[str, str, str]:
    return STEP_TYPE_STYLES.get(step_type, _UNKNOWN_STYLE)


def steps_of(draft: dict) -> list[dict]:
    return [s for s in (draft.get("steps") or []) if isinstance(s, dict)]


def find_step(draft: dict, sid: str) -> Optional[dict]:
    for s in steps_of(draft):
        if node_id(s) == sid:
            return s
    return None


def step_ids(draft: dict) -> list[str]:
    return [node_id(s) for s in steps_of(draft)]


def _deps(step: dict) -> list[str]:
    return [str(d) for d in (step.get("depends_on") or [])]


def _merge_sources(step: dict) -> list[str]:
    return [str(d) for d in (step.get("merge_sources") or [])]


# ══════════════════════════════════════════════════════════════════════════
# 2. draft ⇄ 图
# ══════════════════════════════════════════════════════════════════════════

def draft_to_graph(
    draft: dict,
    errors_by_step: Optional[dict] = None,
    warnings_by_step: Optional[dict] = None,
    batches: Optional[list] = None,
) -> dict:
    """草稿 → {"nodes": [...], "edges": [...], "dangling": [...]}。

    边：`depends_on` 画实线（kind="depends"，source=被依赖者 → target=依赖者，即执行顺序方向）；
    `merge_sources` 里**不在** depends_on 里的画虚线（kind="merge"，画布上只读）。
    指向不存在节点的引用不画边，收进 dangling（{"node","missing","field"}），供面板提示。
    """
    errors_by_step = errors_by_step or {}
    warnings_by_step = warnings_by_step or {}
    batch_of: dict[str, int] = {}
    for i, layer in enumerate(batches or []):
        for nid in layer:
            batch_of.setdefault(nid, i)

    steps = steps_of(draft)
    ids = {node_id(s) for s in steps}
    nodes: list[dict] = []
    for s in steps:
        nid = node_id(s)
        stype = effective_type(s)
        icon, fill, border = type_style(stype)
        errs = list(errors_by_step.get(nid) or [])
        warns = list(warnings_by_step.get(nid) or [])
        badges: list[str] = []
        if s.get("require_approval"):
            badges.append("🔒")
        if s.get("condition"):
            badges.append("🔀")
        if stype == "include":
            badges.append("🧩")
        if errs:
            badges.append("⚠️")
        name = s.get("name") or nid
        nodes.append({
            "id": nid,
            "label": f"{icon} {name}",
            "name": str(name),
            "type": stype,
            "icon": icon,
            "fill": fill,
            "border": ERROR_BORDER if errs else (WARNING_BORDER if warns else border),
            "badges": badges,
            "is_include": stype == "include",
            "has_error": bool(errs),
            "has_warning": bool(warns),
            "errors": errs,
            "warnings": warns,
            "batch": batch_of.get(nid),
        })

    edges: list[dict] = []
    dangling: list[dict] = []
    for s in steps:
        target = node_id(s)
        deps = _deps(s)
        for d in deps:
            if d in ids:
                edges.append({"id": f"{d}->{target}", "source": d, "target": target, "kind": "depends"})
            else:
                dangling.append({"node": target, "missing": d, "field": "depends_on"})
        for m in _merge_sources(s):
            if m in deps:
                continue
            if m in ids:
                edges.append({"id": f"{m}~>{target}", "source": m, "target": target, "kind": "merge"})
            else:
                dangling.append({"node": target, "missing": m, "field": "merge_sources"})
    return {"nodes": nodes, "edges": edges, "dangling": dangling}


def dependency_edges(draft: dict) -> list[tuple[str, str]]:
    """草稿里所有 depends_on 边 (source, target)，只含两端都存在的。"""
    ids = set(step_ids(draft))
    return [(d, node_id(s)) for s in steps_of(draft) for d in _deps(s) if d in ids]


def compute_layers(draft: dict) -> list[list[str]]:
    """本地 Kahn 分层（不依赖后端批次，用于降级渲染 / 校验前的即时预览）。
    有环时返回已能分层的部分，剩余节点单独放最后一层。"""
    ids = step_ids(draft)
    indeg = {i: 0 for i in ids}
    children: dict[str, list[str]] = {i: [] for i in ids}
    for src, dst in dependency_edges(draft):
        indeg[dst] += 1
        children[src].append(dst)
    layer = [i for i in ids if indeg[i] == 0]
    layers: list[list[str]] = []
    seen: set[str] = set()
    while layer:
        layers.append(layer)
        seen.update(layer)
        nxt: list[str] = []
        for u in layer:
            for v in children[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    nxt.append(v)
        layer = nxt
    rest = [i for i in ids if i not in seen]
    if rest:
        layers.append(rest)
    return layers


# ══════════════════════════════════════════════════════════════════════════
# 3. 环检测 / 依赖编辑
# ══════════════════════════════════════════════════════════════════════════

def find_cycle(draft: dict) -> Optional[list[str]]:
    """在 depends_on 图里找一个环，返回 [a, b, c, a]（沿"依赖 → 被依赖"方向）；无环返回 None。"""
    steps = steps_of(draft)
    ids = {node_id(s) for s in steps}
    deps = {node_id(s): [d for d in _deps(s) if d in ids] for s in steps}
    color = {i: 0 for i in ids}
    stack: list[str] = []

    def dfs(u: str) -> Optional[list[str]]:
        color[u] = 1
        stack.append(u)
        for v in deps[u]:
            if color[v] == 1:
                return stack[stack.index(v):] + [v]
            if color[v] == 0:
                r = dfs(v)
                if r:
                    return r
        stack.pop()
        color[u] = 2
        return None

    for i in [node_id(s) for s in steps]:
        if color.get(i) == 0:
            r = dfs(i)
            if r:
                return r
    return None


def would_create_cycle(draft: dict, source: str, target: str) -> Optional[list[str]]:
    """加一条依赖（target 依赖 source，即边 source → target）会不会成环。
    会则返回环路径 [source, target, ..., source]（执行顺序方向），否则 None。"""
    if source == target:
        return [source, source]
    children: dict[str, list[str]] = {}
    for s, t in dependency_edges(draft):
        children.setdefault(s, []).append(t)
    # 从 target 出发沿"被依赖 → 依赖者"方向能否走到 source
    prev: dict[str, Optional[str]] = {target: None}
    queue = [target]
    while queue:
        u = queue.pop(0)
        if u == source:
            path = []
            cur: Optional[str] = u
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            path.reverse()                      # target ... source
            return [source] + path              # source → target ... → source
        for v in children.get(u, []):
            if v not in prev:
                prev[v] = u
                queue.append(v)
    return None


def add_dependency(draft: dict, source: str, target: str) -> tuple[dict, Optional[str]]:
    """让 target 依赖 source。返回 (新草稿, 错误信息)；出错时草稿原样返回。
    拒绝：节点不存在 / 自依赖 / 已存在 / 成环（信息里带环路径）。"""
    ids = set(step_ids(draft))
    if source not in ids or target not in ids:
        return draft, f"节点不存在：{source if source not in ids else target!r}"
    if source == target:
        return draft, "步骤不能依赖自己"
    t = find_step(draft, target)
    if source in _deps(t):
        return draft, f"{target!r} 已经依赖 {source!r}"
    cyc = would_create_cycle(draft, source, target)
    if cyc:
        return draft, "会形成循环依赖：" + " → ".join(cyc)
    d = copy.deepcopy(draft)
    t2 = find_step(d, target)
    t2["depends_on"] = _deps(t2) + [source]
    return d, None


def remove_dependency(draft: dict, source: str, target: str) -> dict:
    """去掉 target 对 source 的依赖；depends_on 因此为空时删掉该键（保持 YAML 干净）。"""
    d = copy.deepcopy(draft)
    t = find_step(d, target)
    if t is not None:
        left = [x for x in _deps(t) if x != source]
        if left:
            t["depends_on"] = left
        else:
            t.pop("depends_on", None)
    return d


def dependency_diff(draft: dict, canvas_edges: Iterable[tuple[str, str]]) -> tuple[list, list]:
    """画布回传的依赖边 与 草稿 depends_on 做差集 → (新增边, 删除边)，元素为 (source, target)。
    只比较 kind="depends" 的边；merge_sources 虚线边只读，不进入这里。"""
    cur = set(dependency_edges(draft))
    new = set((str(a), str(b)) for a, b in canvas_edges)
    return sorted(new - cur), sorted(cur - new)


def apply_dependency_diff(draft: dict, added: Iterable[tuple], removed: Iterable[tuple]) -> tuple[dict, list[str]]:
    """把边差集应用到草稿：先删后加，加边逐条做环检测，失败的收集成错误列表（其余照常应用）。"""
    d = copy.deepcopy(draft)
    for s, t in removed:
        d = remove_dependency(d, s, t)
    errors: list[str] = []
    for s, t in added:
        d, err = add_dependency(d, s, t)
        if err:
            errors.append(f"{s} → {t}：{err}")
    return d, errors


# ══════════════════════════════════════════════════════════════════════════
# 4. 节点增删复制 / 边上插入
# ══════════════════════════════════════════════════════════════════════════

_VALID_ID_BAD_CHARS = re.compile(r"""[\s.{}'"`:,\[\]#]""")

# 各类型的新节点骨架：只放该类型的必填键，值留空——校验会如实指出"还没填"，而不是塞占位内容
_STEP_SKELETONS: dict[str, dict] = {
    "agent": {"prompt": ""},
    "role_agent": {"type": "role_agent", "role": "", "prompt": ""},
    "sub_workflow": {"type": "sub_workflow", "workflow_name": "", "prompt": ""},
    "tool_call": {"type": "tool_call", "tool_name": "", "prompt": ""},
    "human_input": {"type": "human_input", "input_prompt": ""},
    "script": {"type": "script", "script": "", "prompt": ""},
    "python_step": {"type": "python_step", "script_path": ""},
    "skill_agent": {"type": "skill_agent", "skill_name": "", "prompt": ""},
    "foreach": {"type": "foreach", "items": [], "foreach_step": {"type": "agent", "prompt": ""}},
    "wait": {"type": "wait", "wait_seconds": 1},
    "merge": {"type": "merge", "merge_sources": []},
}


def validate_new_id(draft: dict, new_id: str, *, current: Optional[str] = None) -> tuple[bool, str]:
    """新 id 是否可用。返回 (ok, 说明)。ok=True 时说明可能是"提示"（如非合法标识符）。"""
    if not new_id or not new_id.strip():
        return False, "id 不能为空"
    if _VALID_ID_BAD_CHARS.search(new_id):
        return False, "id 不能包含空白、`.`、`{}`、引号、`:`、`,`、`[]`、`#`（会破坏占位符 / condition 引用）"
    if new_id != current and new_id in step_ids(draft):
        return False, f"id {new_id!r} 已存在"
    if not new_id.isidentifier():
        return True, "该 id 不是合法的 Python 标识符，之后无法在 condition 表达式里引用它（占位符不受影响）"
    return True, ""


def unique_step_id(draft: dict, base: str) -> str:
    ids = set(step_ids(draft))
    if base not in ids:
        return base
    n = 2
    while f"{base}_{n}" in ids:
        n += 1
    return f"{base}_{n}"


def _new_step(draft: dict, step_type: str, depends_on: Optional[list] = None) -> dict:
    base = {"agent": "step", "role_agent": "role_step", "sub_workflow": "sub", "tool_call": "tool",
            "human_input": "ask", "script": "script", "python_step": "py", "skill_agent": "skill",
            "foreach": "each", "wait": "wait", "merge": "merge"}.get(step_type, "step")
    sid = unique_step_id(draft, base)
    step: dict = {"id": sid, "name": sid}
    step.update(copy.deepcopy(_STEP_SKELETONS.get(step_type, {"type": step_type, "prompt": ""})))
    if depends_on:
        step["depends_on"] = list(depends_on)
    return step


def add_step(draft: dict, step_type: str = "agent", after: Optional[str] = None) -> tuple[dict, str]:
    """新增一个节点。after 非空时新节点依赖它（并紧跟其后排序）；否则追加到末尾且无依赖。
    返回 (新草稿, 新节点 id)。"""
    d = copy.deepcopy(draft)
    step = _new_step(d, step_type, [after] if after and after in set(step_ids(d)) else None)
    steps = d.setdefault("steps", [])
    pos = len(steps)
    if after:
        for i, s in enumerate(steps):
            if isinstance(s, dict) and node_id(s) == after:
                pos = i + 1
                break
    steps.insert(pos, step)
    return d, step["id"]


def duplicate_step(draft: dict, sid: str, prompt_files: Optional[dict] = None) -> tuple[dict, str]:
    """复制节点（依赖同源，紧跟原节点排序）。返回 (新草稿, 新 id)。
    带 prompt_file 的步骤：复制体不共享文件——有正文就内联成 prompt，没有就只去掉 prompt_file。
    include 节点复制体仍引用同一片段。"""
    d = copy.deepcopy(draft)
    src = find_step(d, sid)
    if src is None:
        return draft, ""
    dup = copy.deepcopy(src)
    new_id = unique_step_id(d, f"{sid}_copy")
    dup["id"] = new_id
    if dup.get("name") and not is_include(dup):
        dup["name"] = f"{dup['name']}（副本）"
    pf = dup.pop("prompt_file", None)
    if pf and (prompt_files or {}).get(pf) is not None:
        dup["prompt"] = prompt_files[pf]
    steps = d["steps"]
    idx = next(i for i, s in enumerate(steps) if isinstance(s, dict) and node_id(s) == sid)
    steps.insert(idx + 1, dup)
    return d, new_id


@dataclass
class DeleteReport:
    """删除节点前的影响面：谁依赖它（默认联动移除）、谁的 condition / 占位符还引用它（需人工处理）。"""
    dependents: list[str] = field(default_factory=list)          # depends_on 里有它的节点
    merge_dependents: list[str] = field(default_factory=list)    # merge_sources 里有它的节点
    other_refs: list[dict] = field(default_factory=list)         # {"step","where"} condition / 占位符引用


def analyze_delete(draft: dict, sid: str, prompt_files: Optional[dict] = None) -> DeleteReport:
    rep = DeleteReport()
    for s in steps_of(draft):
        nid = node_id(s)
        if nid == sid:
            continue
        if sid in _deps(s):
            rep.dependents.append(nid)
        if sid in _merge_sources(s):
            rep.merge_dependents.append(nid)
        for where in _reference_locations(s, sid, prompt_files):
            rep.other_refs.append({"step": nid, "where": where})
    return rep


def delete_step(draft: dict, sid: str, cleanup_refs: bool = True) -> dict:
    """删除节点；cleanup_refs=True 时同步把它从其它节点的 depends_on / merge_sources 里移除。
    condition / 占位符里的引用不擅自改写（语义不明），由校验报错提示用户处理，见 analyze_delete。"""
    d = copy.deepcopy(draft)
    d["steps"] = [s for s in d.get("steps", []) if not (isinstance(s, dict) and node_id(s) == sid)]
    if cleanup_refs:
        for s in steps_of(d):
            for key in ("depends_on", "merge_sources"):
                if sid in [str(x) for x in (s.get(key) or [])]:
                    left = [x for x in s[key] if str(x) != sid]
                    if left:
                        s[key] = left
                    else:
                        s.pop(key, None)
    return d


def insert_between(draft: dict, source: str, target: str, step_type: str = "agent") -> tuple[dict, str]:
    """在边 source → target 中间插入新节点：新节点依赖 source，target 改为依赖新节点。
    边不存在时原样返回（新 id 为空串）。"""
    t = find_step(draft, target)
    if t is None or source not in _deps(t):
        return draft, ""
    d = copy.deepcopy(draft)
    step = _new_step(d, step_type, [source])
    t2 = find_step(d, target)
    t2["depends_on"] = [step["id"] if x == source else x for x in _deps(t2)]
    steps = d["steps"]
    idx = next(i for i, s in enumerate(steps) if isinstance(s, dict) and node_id(s) == target)
    steps.insert(idx, step)
    return d, step["id"]


# ══════════════════════════════════════════════════════════════════════════
# 5. 改 id 联动
# ══════════════════════════════════════════════════════════════════════════

def _placeholder_pat(old: str) -> "re.Pattern[str]":
    # 后端占位符规则：`{id.field}`，只有带 "." 的才是 step 引用（`{param}` 是运行时 inputs，不能动）
    return re.compile(r"\{" + re.escape(old) + r"\.")


def rename_in_condition(expr: str, old: str, new: str) -> str:
    """把 condition 表达式里**作为名字引用**的 old 换成 new。基于 AST：只改 ast.Name 节点，
    不会误伤 `inputs.old` 里的属性名、字符串字面量、同前缀标识符。语法错误的表达式原样返回。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return expr
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == old and n.end_lineno == n.lineno]
    if not hits:
        return expr
    lines = expr.split("\n")
    for n in sorted(hits, key=lambda n: (n.lineno, n.col_offset), reverse=True):
        b = lines[n.lineno - 1].encode("utf-8")   # AST 列偏移是 UTF-8 字节偏移
        lines[n.lineno - 1] = (b[:n.col_offset] + new.encode("utf-8") + b[n.end_col_offset:]).decode("utf-8")
    return "\n".join(lines)


def _condition_references(expr: str, sid: str) -> bool:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False
    return any(isinstance(n, ast.Name) and n.id == sid for n in ast.walk(tree))


def _rewrite_strings(value: Any, fn) -> tuple[Any, bool]:
    """递归改写结构里所有字符串叶子，返回 (新值, 是否有变化)。"""
    if isinstance(value, str):
        nv = fn(value)
        return nv, nv != value
    if isinstance(value, dict):
        out, changed = {}, False
        for k, v in value.items():
            nv, c = _rewrite_strings(v, fn)
            out[k] = nv
            changed = changed or c
        return out, changed
    if isinstance(value, list):
        out_l, changed = [], False
        for v in value:
            nv, c = _rewrite_strings(v, fn)
            out_l.append(nv)
            changed = changed or c
        return out_l, changed
    return value, False


# 这些键不是"内容"，不参与占位符改写
_NON_TEXT_KEYS = {"id", "name", "include", "depends_on", "merge_sources", "condition", "type",
                  "role", "model", "prompt_file", "script_path", "workflow_name", "tool_name", "skill_name"}


def _contains(value: Any, pat: "re.Pattern[str]") -> bool:
    if isinstance(value, str):
        return bool(pat.search(value))
    if isinstance(value, dict):
        return any(_contains(v, pat) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, pat) for v in value)
    return False


def _reference_locations(step: dict, sid: str, prompt_files: Optional[dict]) -> list[str]:
    where: list[str] = []
    if step.get("condition") and _condition_references(str(step["condition"]), sid):
        where.append("condition")
    pat = _placeholder_pat(sid)
    for k, v in step.items():
        if k in _NON_TEXT_KEYS:
            continue
        if _contains(v, pat):
            where.append(k)
    pf = step.get("prompt_file")
    if pf and pat.search((prompt_files or {}).get(pf, "") or ""):
        where.append(f"prompt_file({pf})")
    return where


@dataclass
class RenameResult:
    draft: dict
    prompt_files: dict
    renames: dict                                        # {旧 id: 新 id}，传给后端 save/validate 保住注释
    changes: list = field(default_factory=list)          # [{"step","field","before","after"}]
    error: Optional[str] = None
    hint: str = ""


def rename_step(
    draft: dict,
    old: str,
    new: str,
    prompt_files: Optional[dict] = None,
    sync_refs: bool = True,
) -> RenameResult:
    """改 id。sync_refs=True 时联动改写其它 step 的 depends_on / merge_sources / condition，
    以及 prompt / tool_args 等文本里的 `{old.xxx}` 占位符（含 prompt_file 正文）。
    只匹配完整标识符边界（见 rename_in_condition / _placeholder_pat），不误伤同前缀标识符或
    `{param}` 形式的运行时参数。返回 RenameResult，changes 可直接进"查看变更"。"""
    pf_in = dict(prompt_files or {})
    if old == new:
        return RenameResult(draft, pf_in, {})
    if find_step(draft, old) is None:
        return RenameResult(draft, pf_in, {}, error=f"节点 {old!r} 不存在")
    ok, msg = validate_new_id(draft, new, current=old)
    if not ok:
        return RenameResult(draft, pf_in, {}, error=msg)

    d = copy.deepcopy(draft)
    pf = dict(pf_in)
    changes: list[dict] = [{"step": new, "field": "id", "before": old, "after": new}]
    find_step(d, old)["id"] = new

    if sync_refs:
        pat = _placeholder_pat(old)
        repl = "{" + new + "."
        for s in steps_of(d):
            nid = node_id(s)
            for key in ("depends_on", "merge_sources"):
                if old in [str(x) for x in (s.get(key) or [])]:
                    s[key] = [new if str(x) == old else x for x in s[key]]
                    changes.append({"step": nid, "field": key, "before": old, "after": new})
            if s.get("condition"):
                nc = rename_in_condition(str(s["condition"]), old, new)
                if nc != s["condition"]:
                    changes.append({"step": nid, "field": "condition", "before": s["condition"], "after": nc})
                    s["condition"] = nc
            for k in list(s.keys()):
                if k in _NON_TEXT_KEYS:
                    continue
                nv, changed = _rewrite_strings(s[k], lambda t: pat.sub(lambda _m: repl, t))
                if changed:
                    changes.append({"step": nid, "field": k, "before": _preview(s[k]), "after": _preview(nv)})
                    s[k] = nv
        used = {s.get("prompt_file") for s in steps_of(d) if s.get("prompt_file")}
        for rel in sorted(used):
            body = pf.get(rel)
            if body and pat.search(body):
                nb = pat.sub(lambda _m: repl, body)
                changes.append({"step": next(node_id(s) for s in steps_of(d) if s.get("prompt_file") == rel),
                                "field": f"prompt_file({rel})", "before": _preview(body), "after": _preview(nb)})
                pf[rel] = nb

    hint = "" if new.isidentifier() else "新 id 不是合法的 Python 标识符，之后无法在 condition 里引用它"
    return RenameResult(d, pf, {old: new}, changes, None, hint)


def _preview(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", "⏎")
    return text if len(text) <= limit else text[:limit] + "…"


# ══════════════════════════════════════════════════════════════════════════
# 6. 校验结果归类 / 编辑器错误响应解析
# ══════════════════════════════════════════════════════════════════════════

_STEP_IN_MSG = re.compile(r"""步骤\s*['"]([^'"]+)['"]""")


def group_messages(messages: Iterable[str], node_ids: Iterable[str]) -> tuple[dict, list[str]]:
    """本地兜底归类：按文案里的 `步骤 'x'` 把消息挂到节点上（后端没给 errors_by_step 时使用，
    与后端 editor_helpers._group_by_step 同口径，include 展开的 `<id>__xxx` 归到 include 节点）。"""
    ids = set(node_ids)
    by: dict[str, list[str]] = {}
    rest: list[str] = []
    for m in messages:
        hit = _STEP_IN_MSG.search(m)
        if not hit:
            rest.append(m)
            continue
        sid = hit.group(1)
        if sid not in ids:
            for inc in ids:
                if sid.startswith(inc + "__"):
                    sid = inc
                    break
        by.setdefault(sid, []).append(m)
    return by, rest


def normalize_validation(payload: Optional[dict], node_ids: Iterable[str] = ()) -> dict:
    """把后端 validate 的响应、或保存失败时 422 的 detail 统一成
    {"ok","errors","warnings","errors_by_step","warnings_by_step","workflow_errors","cycle","batches","total_errors"}，
    缺字段时用本地兜底补齐。payload 为 None（还没校验过）返回空结果。"""
    p = payload or {}
    errors = list(p.get("errors") or [])
    warnings = list(p.get("warnings") or [])
    ebs = p.get("errors_by_step")
    wbs = p.get("warnings_by_step")
    wf_errs = p.get("workflow_errors")
    ids = list(node_ids)
    if ebs is None:
        ebs, rest = group_messages(errors, ids)
        wf_errs = rest if wf_errs is None else wf_errs
    if wbs is None:
        wbs, _ = group_messages(warnings, ids)
    return {
        "ok": bool(p.get("ok", not errors)) if payload is not None else True,
        "errors": errors,
        "warnings": warnings,
        "errors_by_step": {k: list(v) for k, v in ebs.items()},
        "warnings_by_step": {k: list(v) for k, v in (wbs or {}).items()},
        "workflow_errors": list(wf_errs or []),
        "cycle": p.get("cycle"),
        "batches": p.get("batches"),
        "total_errors": len(errors),
    }


@dataclass
class EditorError:
    """看板对编辑器接口失败的统一理解。kind 决定 UI 走哪条分支。"""
    kind: str                     # conflict / validation / disabled / needs_confirm / not_found / network / other
    message: str
    code: str = ""
    status: Optional[int] = None
    detail: dict = field(default_factory=dict)

    @property
    def current_hash(self) -> Optional[str]:
        return self.detail.get("current_hash")


_CODE_TO_KIND = {
    "conflict": "conflict",
    "validation_failed": "validation",
    "editor_disabled": "disabled",
    "needs_confirm": "needs_confirm",
    "not_found": "not_found",
}


def parse_editor_error(resp: Any) -> Optional[EditorError]:
    """解析 client 返回值：成功（无 `_error`）返回 None，否则 EditorError。
    client 的编辑器方法会在 `_detail` 里带回完整的后端 detail 对象。"""
    if not isinstance(resp, dict) or "_error" not in resp:
        return None
    detail = resp.get("_detail") if isinstance(resp.get("_detail"), dict) else {}
    status = resp.get("_status")
    code = str(detail.get("code") or "")
    msg = str(detail.get("message") or resp.get("_error") or "未知错误")
    if code in _CODE_TO_KIND:
        kind = _CODE_TO_KIND[code]
    elif status is None:
        kind = "network"
    else:
        kind = "other"
    return EditorError(kind, msg, code, status, detail)


# ══════════════════════════════════════════════════════════════════════════
# 7. 变更预览
# ══════════════════════════════════════════════════════════════════════════

def _scalar_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def semantic_equal(a: Any, b: Any) -> bool:
    """草稿语义等价（dict 不看键顺序，1 == 1.0，bool 与数字严格区分）——与后端同一口径，
    用于判断"有未保存修改"。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(semantic_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(semantic_equal(x, y) for x, y in zip(a, b))
    return _scalar_eq(a, b)


def is_dirty(original: dict, draft: dict, original_prompts: Optional[dict] = None,
             prompts: Optional[dict] = None) -> bool:
    """草稿（或 prompt_file 正文）相对打开时是否有未保存修改。
    None 值的顶层 / step 级键视同不存在（与后端 _normalize_draft 一致），避免表单空字段造成假阳性。"""
    if not semantic_equal(_prune_none(original), _prune_none(draft)):
        return True
    if prompts is None:
        return False
    op = original_prompts or {}
    return any(prompts.get(k) != op.get(k) for k in set(op) | set(prompts))


def _prune_none(draft: dict) -> dict:
    d = {k: v for k, v in draft.items() if v is not None}
    if isinstance(d.get("steps"), list):
        d["steps"] = [{k: v for k, v in s.items() if v is not None} if isinstance(s, dict) else s
                      for s in d["steps"]]
    return d


def summarize_changes(original: dict, draft: dict, renames: Optional[dict] = None) -> dict:
    """步骤级摘要：{"added","removed","modified","reordered","top_level"}（与后端 changed_steps 同形）。"""
    renames = renames or {}
    rev = {v: k for k, v in renames.items()}
    o = {node_id(s): s for s in steps_of(_prune_none(original))}
    n = {node_id(s): s for s in steps_of(_prune_none(draft))}
    added, modified, matched = [], [], set()
    for nid, s in n.items():
        oid = nid if nid in o else rev.get(nid)
        if oid is None or oid not in o:
            added.append(nid)
            continue
        matched.add(oid)
        if oid != nid or not semantic_equal(o[oid], s):
            modified.append(nid)
    removed = [k for k in o if k not in matched]
    old_order = [k for k in o if k in matched]
    new_order = [rev.get(k, k) for k in n if rev.get(k, k) in matched]
    po, pn = _prune_none(original), _prune_none(draft)
    return {
        "added": added, "removed": removed, "modified": modified,
        "reordered": old_order != new_order,
        "top_level": sorted(k for k in set(po) | set(pn) if k != "steps" and not semantic_equal(po.get(k), pn.get(k))),
    }


def _to_text(doc: dict) -> str:
    try:
        import yaml  # type: ignore

        class _D(yaml.SafeDumper):
            pass

        _D.add_representer(str, lambda dumper, data: dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|" if "\n" in data else None))
        return yaml.dump(doc, Dumper=_D, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096)
    except Exception:
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def build_change_diff(name: str, original: dict, draft: dict,
                      original_prompts: Optional[dict] = None, prompts: Optional[dict] = None) -> str:
    """"查看变更"：把两份草稿（及有变化的 prompt_file 正文）渲染成 git 风格 unified diff 文本，
    可直接交给 `diff_view.parse_unified_diff`。这是**语义**层面的预览（规范化后的 YAML 文本对比），
    与磁盘上最终的逐行 diff 可能因格式细节略有出入，但不会漏报内容变化。无变化返回空串。"""
    parts: list[str] = []

    def _one(path: str, before: str, after: str) -> None:
        if before == after:
            return
        body = list(difflib.unified_diff(
            before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=2))
        if body:
            parts.append(f"diff --git a/{path} b/{path}\n" + "\n".join(body))

    _one(f"{name}.yaml", _to_text(_prune_none(original)), _to_text(_prune_none(draft)))
    op, np_ = original_prompts or {}, prompts or {}
    for rel in sorted(set(op) | set(np_)):
        _one(rel, op.get(rel, "") or "", np_.get(rel, "") or "")
    return "\n".join(parts) + ("\n" if parts else "")


# ══════════════════════════════════════════════════════════════════════════
# 8. 降级渲染：graphviz DOT
# ══════════════════════════════════════════════════════════════════════════

def _dot_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_dot(graph: dict, selected: Optional[str] = None) -> str:
    """把 draft_to_graph 的结果渲染成 graphviz DOT（自上而下），供 `st.graphviz_chart` 使用。
    选中节点加粗高亮；有错误的节点红框；merge_sources 虚线边。"""
    lines = [
        "digraph workflow {",
        '  rankdir=TB; bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="sans-serif", fontsize=11];',
        '  edge [color="#78909C"];',
    ]
    for n in graph["nodes"]:
        label = n["label"] + ("  " + "".join(n["badges"]) if n["badges"] else "")
        if n["name"] != n["id"]:
            label += "\n(" + n["id"] + ")"
        pen = 3 if n["id"] == selected else 1.5
        color = n["border"] if n["id"] != selected else "#000000"
        lines.append(
            f'  "{_dot_escape(n["id"])}" [label="{_dot_escape(label)}", '
            f'fillcolor="{n["fill"]}", color="{color}", penwidth={pen}];'
        )
    for e in graph["edges"]:
        style = ' [style=dashed, color="#9FA8DA"]' if e["kind"] == "merge" else ""
        lines.append(f'  "{_dot_escape(e["source"])}" -> "{_dot_escape(e["target"])}"{style};')
    lines.append("}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 9. 属性面板字段清单（§4.4）
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str           # str / text / int / float / bool / tribool / json / list / select / multiselect
    options: str = ""   # select / multiselect 的选项来源：meta 里的键（roles / tools / skills / workflows / merge_strategies / steps）
    help: str = ""


_PROMPT_REQUIRED = "校验要求非空（可用 prompt_file 代替）"

STEP_FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "agent": (
        FieldSpec("prompt", "Prompt", "text"),
        FieldSpec("model", "模型", "str", help="留空继承 workflow defaults"),
        FieldSpec("max_turns", "最大轮数", "int"),
    ),
    "role_agent": (
        FieldSpec("prompt", "Prompt", "text"),
        FieldSpec("role", "角色", "select", "roles"),
        FieldSpec("model", "模型", "str"),
        FieldSpec("max_turns", "最大轮数", "int"),
    ),
    "tool_call": (
        FieldSpec("prompt", "说明（prompt）", "text", help=_PROMPT_REQUIRED),
        FieldSpec("tool_name", "工具", "select", "tools"),
        FieldSpec("tool_args", "工具参数（JSON）", "json"),
    ),
    "sub_workflow": (
        FieldSpec("prompt", "说明（prompt）", "text", help=_PROMPT_REQUIRED),
        FieldSpec("workflow_name", "子工作流", "select", "workflows", help="不能选自身"),
    ),
    "human_input": (
        FieldSpec("input_prompt", "向用户提问", "text"),
        FieldSpec("input_key", "输入键", "str", help="mode=autonomous 时必填，否则会阻塞等待人工"),
    ),
    "script": (
        FieldSpec("prompt", "说明（prompt）", "text", help=_PROMPT_REQUIRED),
        FieldSpec("script", "shell 命令", "text", help="需 workflow.script_step_enabled=true 才会执行"),
        FieldSpec("params", "参数（JSON）", "json"),
        FieldSpec("result_file", "结果文件", "str"),
        FieldSpec("result_file_required_keys", "结果文件必含 key", "list"),
    ),
    "python_step": (
        FieldSpec("script_path", "脚本路径（相对工作流目录）", "str", help="需 workflow.python_step_enabled=true 才会执行"),
        FieldSpec("params", "参数（JSON）", "json"),
        FieldSpec("result_file", "结果文件", "str"),
        FieldSpec("result_file_required_keys", "结果文件必含 key", "list"),
    ),
    "skill_agent": (
        FieldSpec("prompt", "Prompt", "text", help=_PROMPT_REQUIRED),
        FieldSpec("skill_name", "Skill", "select", "skills"),
        FieldSpec("model", "模型", "str"),
        FieldSpec("max_turns", "最大轮数", "int"),
    ),
    "foreach": (
        FieldSpec("items", "遍历项（列表或占位符）", "json"),
        FieldSpec("foreach_step", "内层步骤（JSON）", "json", help="内层 step 在此编辑，不在图上展开；type 不能是 foreach"),
        FieldSpec("foreach_max_concurrency", "最大并发", "int"),
        FieldSpec("foreach_stop_on_error", "出错即停", "bool"),
    ),
    "wait": (FieldSpec("wait_seconds", "等待秒数", "float"),),
    "merge": (
        FieldSpec("merge_sources", "合并来源", "multiselect", "steps", help="须同时在 depends_on 中（直接或传递）声明"),
        FieldSpec("merge_strategy", "合并策略", "select", "merge_strategies"),
        FieldSpec("merge_separator", "分隔符", "str"),
        FieldSpec("merge_use_result_file", "使用 result_file", "bool"),
    ),
}

ADVANCED_FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("condition", "condition 表达式", "str", help="如 analyze.passed；引用的步骤须在 depends_on 中"),
    FieldSpec("timeout", "超时（秒）", "float"),
    FieldSpec("retry_on_error", "出错重试次数", "int"),
    FieldSpec("retry_on_gate_fail", "质检失败重试次数", "int"),
    FieldSpec("allow_parallel", "允许并发", "tribool", help="留空=继承默认"),
    FieldSpec("require_approval", "需要人工审批", "bool"),
    FieldSpec("escalate_after_n_same_failures", "连续同因失败 N 次后升级", "int"),
    FieldSpec("output_file", "输出文件", "str"),
)

WORKFLOW_LEVEL_FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("description", "描述", "text"),
    FieldSpec("version", "版本", "str"),
    FieldSpec("mode", "模式", "select", "modes"),
    FieldSpec("defaults", "默认值（JSON）", "json", help="model / timeout / retry_on_error / max_turns / allow_parallel"),
    FieldSpec("max_total_duration", "总时长上限（秒）", "float"),
    FieldSpec("max_total_tokens", "总 token 上限", "int"),
)


def fields_for(step: dict) -> tuple[tuple[FieldSpec, ...], tuple[FieldSpec, ...]]:
    """(类型专属字段, 高级字段)。include 节点没有可编辑内容字段（只能改 id / depends_on）；
    插件注册的自定义类型没有专属字段清单，只给高级字段。"""
    stype = effective_type(step)
    if stype == "include":
        return (), ()
    return STEP_FIELD_SPECS.get(stype, ()), ADVANCED_FIELD_SPECS


def runtime_switch_note(step_type: str, switches: Optional[dict]) -> str:
    """script / python_step 在对应运行期开关关闭时的面板提示（编辑器不绕过开关）。"""
    sw = switches or {}
    if step_type == "script" and not sw.get("script_step_enabled", False):
        return "当前配置 workflow.script_step_enabled=false，该类型步骤不会执行"
    if step_type == "python_step" and not sw.get("python_step_enabled", False):
        return "当前配置 workflow.python_step_enabled=false，该类型步骤不会执行"
    return ""
