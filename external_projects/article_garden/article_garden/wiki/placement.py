"""wiki/placement.py — 自动挂载提案（方案 §5.3 增量挂载、§21-2）。

流程（make_proposal）：
  1. 骨架：章程 scope.in 中还没有对应方向节点的，生成 add_node 操作（无范围时用「未分类」）。
  2. 只找出**尚未挂载**的概念（已挂载的一律不动，这是增量与 id 稳定的基础）。
  3. 分批（每批 ≤ BATCH）交给模型：每个概念挂到已有主题，或新建主题（必须落在某个方向下）。
  4. 代码校验：每个概念恰好出现一次；主题 id 存在且未锁定；方向 id 存在；新主题名不与同方向已有主题重名。
  5. 已不存在的概念（概念页被删除）生成 detach 操作。
  6. 没有任何操作 → 不生成提案（零模型调用时即是如此）。提案以 base_version 记录生成时的树版本。
应用（apply_proposal）：树版本不一致则拒绝（提案过期，需重新生成）；否则应用 ops 并保存（自动备份旧树）。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import store
from ..digest import summarize as sm
from . import model, tree

BATCH = 20


def _proposal_path(ctx):
    return ctx.path("wiki", "tree_proposal.yaml")


def load_proposal(ctx) -> Optional[Dict[str, Any]]:
    return store.read_yaml(_proposal_path(ctx), default=None)


def discard_proposal(ctx) -> None:
    p = _proposal_path(ctx)
    if p.is_file():
        p.unlink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prompt(batch, work) -> str:
    dirs = tree.nodes_at(work, tree.DIRECTION)
    topics = tree.nodes_at(work, tree.TOPIC)
    d_lines = [f"- {nid} ｜ {n['name']}" for nid, n in dirs]
    t_lines = [f"- {nid} ｜ {n['name']} ｜ 方向 {n.get('parent')}" for nid, n in topics if not n.get("pinned")]
    c_lines = [f"- {c['id']} ｜ {c['name']} ｜ {c.get('_def', '')}" for c in batch]
    return ("【阶段:挂载】把下面每个待挂载概念放入知识树：优先挂到合适的已有主题；没有合适的主题时，"
            "新建一个主题（名称简洁，且必须属于某个方向）。每个概念只能出现一次。只输出 YAML：\n"
            "placements:\n"
            "  - {concept: 概念id, topic: 已有主题id}\n"
            "  - {concept: 概念id, new_topic: 新主题名, direction: 方向id}\n\n"
            "方向（只能选这些）：\n" + ("\n".join(d_lines) or "（无）") + "\n\n"
            "已有主题（可挂入）：\n" + ("\n".join(t_lines) or "（无）") + "\n\n"
            "待挂载概念：\n" + "\n".join(c_lines))


def _validator(batch, work):
    want = [c["id"] for c in batch]
    dir_ids = {nid for nid, _ in tree.nodes_at(work, tree.DIRECTION)}
    open_topics = {nid for nid, n in tree.nodes_at(work, tree.TOPIC) if not n.get("pinned")}
    existing_names = {(n.get("parent"), n["name"]) for _, n in tree.nodes_at(work, tree.TOPIC)}

    def _v(obj: Dict[str, Any]) -> List[str]:
        items = obj.get("placements")
        if not isinstance(items, list):
            return ["placements 应为列表"]
        issues, seen = [], []
        for it in items:
            if not isinstance(it, dict) or not it.get("concept"):
                issues.append("每条挂载需含 concept")
                continue
            cid = str(it["concept"])
            seen.append(cid)
            if "topic" in it:
                if str(it["topic"]) not in open_topics:
                    issues.append(f"{cid} 挂入的主题 {it['topic']} 不存在或已锁定")
            elif "new_topic" in it:
                d = str(it.get("direction") or "")
                if d not in dir_ids:
                    issues.append(f"{cid} 的方向 {d or '（空）'} 不存在，应从方向列表中选择")
                elif not str(it["new_topic"]).strip():
                    issues.append(f"{cid} 的新主题名不能为空")
                elif (d, str(it["new_topic"]).strip()) in existing_names:
                    issues.append(f"「{it['new_topic']}」在该方向下已存在，应改用 topic: 该主题的 id")
            else:
                issues.append(f"{cid} 需给出 topic 或 new_topic")
        missing = set(want) - set(seen)
        extra = set(seen) - set(want)
        dup = {c for c in seen if seen.count(c) > 1}
        if missing:
            issues.append(f"缺少这些概念的挂载：{', '.join(sorted(missing))}")
        if extra:
            issues.append(f"出现了不在本批中的概念：{', '.join(sorted(extra))}")
        if dup:
            issues.append(f"同一概念出现多次：{', '.join(sorted(dup))}")
        return issues
    return _v


def make_proposal(ctx, llm, *, max_repair_rounds: int = 6) -> Optional[Dict[str, Any]]:
    """生成提案并保存；无变化时返回 None（不调用模型）。"""
    base = tree.load(ctx)
    work = copy.deepcopy(base)
    ops: List[Dict[str, Any]] = []

    def do(op):
        tree.apply_op(work, op)
        ops.append(op)

    scope_in = ctx.load_charter()[0].get("scope", {}).get("in") or [tree.UNCLASSIFIED]
    existing_dirs = {n["name"] for _, n in tree.nodes_at(work, tree.DIRECTION)}
    for name in scope_in:
        if name not in existing_dirs:
            do(tree.op_add_node(tree.alloc_id(work), name, tree.DIRECTION, None, origin="skeleton"))
            existing_dirs.add(name)

    concepts = model.list_concepts(ctx)
    live = {c["id"] for c in concepts}
    for cid in sorted(set(tree.placed_concepts(work)) - live):
        do(tree.op_detach(cid))

    placed = set(tree.placed_concepts(work))
    todo = []
    for c in concepts:
        if c["id"] in placed:
            continue
        page = model.read_page(ctx, c["id"])
        body = (page[1] if page else "").split("## 依据")[0]
        summary = " ".join(body.replace("#", "").split())[:80]
        todo.append({**c, "_def": summary})

    new_by_key: Dict[tuple, str] = {}
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        budget = sm.RepairBudget(max_repair_rounds)
        obj = sm.ask_validated(llm, _prompt(batch, work), _validator(batch, work), budget)
        for it in obj["placements"]:
            cid = str(it["concept"])
            if "topic" in it:
                do(tree.op_attach(cid, str(it["topic"])))
                continue
            d = str(it["direction"])
            name = str(it["new_topic"]).strip()
            key = (d, name)
            if key not in new_by_key:
                nid = tree.alloc_id(work)
                do(tree.op_add_node(nid, name, tree.TOPIC, d))
                new_by_key[key] = nid
            do(tree.op_attach(cid, new_by_key[key]))

    if not ops:
        discard_proposal(ctx)
        return None
    proposal = {"base_version": base["version"], "created_at": _now(), "ops": ops,
                "summary": {"new_topics": sum(1 for o in ops if o["op"] == "add_node" and o["level"] == tree.TOPIC),
                            "attached": sum(1 for o in ops if o["op"] == "attach"),
                            "detached": sum(1 for o in ops if o["op"] == "detach"),
                            "skeleton": sum(1 for o in ops if o["op"] == "add_node" and o["origin"] == "skeleton")}}
    store.write_yaml_atomic(_proposal_path(ctx), proposal)
    return proposal


def apply_proposal(ctx) -> Dict[str, Any]:
    p = load_proposal(ctx)
    if not p:
        raise ValueError("没有待确认的挂载提案")
    data = tree.load(ctx)
    if data["version"] != p["base_version"]:
        raise ValueError("挂载提案已过期（知识树已变化），请重新生成提案")
    tree.apply_ops(data, p["ops"])
    tree.save(ctx, data)
    discard_proposal(ctx)
    return p["summary"]
