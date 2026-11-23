"""wiki/tree.py — 分类知识树（方案 §4.4 L3、§5.3）：方向 → 主题 → 概念。

存储：wiki/tree.yaml（事实源）。节点 id 形如 n-0001，分配后永不改变；改名、移动、合并都不换 id。
版本：每次保存 version+1；自动挂载提案记录了生成时的 version，应用时若已变化则拒绝（防止基于旧树的操作）。
备份：每次保存前把旧树复制到 wiki/tree.prev.yaml（方案 §19.1：应用前自动备份，可回退一版）。

操作分两类：
  * 自动操作（ops）：只能通过挂载提案产生，需人工确认后由 apply_ops 应用。
  * 人工操作：改名、移动、合并、锁定、新建主题，立即生效（每次都会备份）。
锁定（pinned）的节点不接收自动挂载；人工改名/移动过的节点标记 human_edited。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import store

DIRECTION, TOPIC = "direction", "topic"
UNCLASSIFIED = "未分类"


def _path(ctx):
    return ctx.path("wiki", "tree.yaml")


def empty() -> Dict[str, Any]:
    return {"version": 0, "next_seq": 1, "nodes": {}}


def load(ctx) -> Dict[str, Any]:
    data = store.read_yaml(_path(ctx), default=None)
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
        return empty()
    data.setdefault("version", 0)
    data.setdefault("next_seq", 1)
    return data


def save(ctx, data: Dict[str, Any]) -> None:
    p = _path(ctx)
    if p.is_file():
        store.write_text_atomic(p.with_name("tree.prev.yaml"), p.read_text(encoding="utf-8"))
    data["version"] = int(data.get("version", 0)) + 1
    store.write_yaml_atomic(p, data)


def alloc_id(data: Dict[str, Any]) -> str:
    seq = int(data.get("next_seq", 1))
    data["next_seq"] = seq + 1
    return f"n-{seq:04d}"


def nodes_at(data: Dict[str, Any], level: str) -> List[tuple]:
    return sorted(((nid, n) for nid, n in data["nodes"].items() if n.get("level") == level), key=lambda x: x[0])


def children(data: Dict[str, Any], nid: str) -> List[tuple]:
    return sorted(((k, n) for k, n in data["nodes"].items() if n.get("parent") == nid), key=lambda x: x[0])


def placed_concepts(data: Dict[str, Any]) -> Dict[str, str]:
    """概念 id → 所在节点 id。"""
    out = {}
    for nid, n in data["nodes"].items():
        for cid in n.get("concepts") or []:
            out[cid] = nid
    return out


# ------------------------------------------------------------------ 自动操作（提案中的 ops）

def op_add_node(nid: str, name: str, level: str, parent: Optional[str], origin: str = "auto") -> Dict[str, Any]:
    return {"op": "add_node", "id": nid, "name": name, "level": level, "parent": parent, "origin": origin}


def op_attach(cid: str, node: str) -> Dict[str, Any]:
    return {"op": "attach", "concept": cid, "node": node}


def op_detach(cid: str) -> Dict[str, Any]:
    return {"op": "detach", "concept": cid}


def apply_op(data: Dict[str, Any], op: Dict[str, Any]) -> None:
    kind = op.get("op")
    if kind == "add_node":
        nid = op["id"]
        if nid in data["nodes"]:
            raise ValueError(f"节点 id 已存在：{nid}")
        if op.get("parent") and op["parent"] not in data["nodes"]:
            raise ValueError(f"父节点不存在：{op['parent']}")
        data["nodes"][nid] = {"name": op["name"], "level": op["level"], "parent": op.get("parent"),
                              "origin": op.get("origin", "auto"), "pinned": False, "human_edited": False,
                              "concepts": []}
        num = int(nid.split("-")[1])
        data["next_seq"] = max(int(data.get("next_seq", 1)), num + 1)
    elif kind == "attach":
        if op["node"] not in data["nodes"]:
            raise ValueError(f"挂载目标不存在：{op['node']}")
        for n in data["nodes"].values():
            if op["concept"] in (n.get("concepts") or []):
                n["concepts"].remove(op["concept"])
        data["nodes"][op["node"]].setdefault("concepts", []).append(op["concept"])
    elif kind == "detach":
        for n in data["nodes"].values():
            if op["concept"] in (n.get("concepts") or []):
                n["concepts"].remove(op["concept"])
    else:
        raise ValueError(f"未知操作：{kind}")


def apply_ops(data: Dict[str, Any], ops: List[Dict[str, Any]]) -> None:
    for op in ops:
        apply_op(data, op)


# ------------------------------------------------------------------ 人工操作（立即生效）

def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    return name


def rename(ctx, nid: str, name: str) -> None:
    data = load(ctx)
    name = _check_name(name)
    node = _get(data, nid)
    for k, n in data["nodes"].items():
        if k != nid and n.get("level") == node.get("level") and n.get("parent") == node.get("parent") \
                and n.get("name") == name:
            raise ValueError(f"同一层级已有名为「{name}」的节点")
    node["name"] = name
    node["human_edited"] = True
    save(ctx, data)


def set_pinned(ctx, nid: str, pinned: bool) -> None:
    data = load(ctx)
    _get(data, nid)["pinned"] = bool(pinned)
    save(ctx, data)


def move(ctx, nid: str, parent: str) -> None:
    data = load(ctx)
    node = _get(data, nid)
    if node.get("level") != TOPIC:
        raise ValueError("只有主题可以移动；方向是顶层")
    target = _get(data, parent)
    if target.get("level") != DIRECTION:
        raise ValueError("主题只能移动到方向下面")
    node["parent"] = parent
    node["human_edited"] = True
    save(ctx, data)


def merge(ctx, src: str, dst: str) -> None:
    """把 src 的概念与子节点并入 dst，然后删除 src（同层级才允许）。"""
    data = load(ctx)
    s, d = _get(data, src), _get(data, dst)
    if src == dst or s.get("level") != d.get("level"):
        raise ValueError("只能把同层级的另一个节点合并进来")
    for cid in s.get("concepts") or []:
        d.setdefault("concepts", []).append(cid)
    for k, n in data["nodes"].items():
        if n.get("parent") == src:
            n["parent"] = dst
    d["human_edited"] = True
    del data["nodes"][src]
    save(ctx, data)


def create_topic(ctx, name: str, direction: str) -> str:
    data = load(ctx)
    name = _check_name(name)
    if _get(data, direction).get("level") != DIRECTION:
        raise ValueError("主题必须建在方向下面")
    nid = alloc_id(data)
    apply_op(data, op_add_node(nid, name, TOPIC, direction, origin="human"))
    data["nodes"][nid]["human_edited"] = True
    save(ctx, data)
    return nid


def _get(data: Dict[str, Any], nid: str) -> Dict[str, Any]:
    if nid not in data["nodes"]:
        raise ValueError(f"节点不存在：{nid}")
    return data["nodes"][nid]
