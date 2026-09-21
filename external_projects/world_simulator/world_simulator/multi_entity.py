"""world_simulator/multi_entity.py — 多主体 Entity/Relationship：从
自由文本关系到结构化连通图（第八轮批次五）。

设计依据：`next_doc/world_simulator_c_category_precision_upgrade_
improvement_plan.md` 第 6 节。

**现状**：`multi_entity_mode` 下 `SimState.vars` 按约定组织成
`entities: Dict[str, Dict[str, Any]]`（阶段十七，见
`state_model.py::SimManifest.settings` 里 `multi_entity_mode` 的
docstring）；`manifest.settings.relationships`（`relationship.py`，
阶段三十一起步）声明主体之间的关系，但 `from`/`to` 是自由文本，**不
校验**是否真的对应一个存在的 `entities` id。这两者此前完全独立，
看不出"从 A 主体到 B 主体之间隔着几层关系"这类连通性问题。

**范围克制（重要）**：本模块只做最小可行的"连通性"问题——一个简单
无向邻接表 + BFS 最短路径。不做加权最短路径（`strength`/
`reversible` 这些字段完全不参与路径计算），不做有向图语义上的
"谁支配谁"传递闭包分析，不做任何自动数值传播（同 `relationship.py`
开头强调的"不是完整 Influence Field 引擎"一以贯之的克制）。查询结果
只提供给用户看，不反过来影响推进 prompt——如果后续证明这类路径信息
对推进有用，可以在下一批单独评估是否要喂给 `advance_step`，不在
本模块范围内。

和 `causal_graph.py`/`attribution.py` 一样，本模块是纯函数、不缓存、
不落盘。`entities` 字典在当前代码里实际存放在 `SimState.vars`
（不是 `SimManifest`），所以这里的函数直接接收调用方已经拿到手的
`relationships_raw`（`manifest.settings.get("relationships")`）和
`entities`（`state.vars.get("entities")`）两份数据，不去猜/不去
`import` 别的模块反查——`manifest`/`vars` 分别属于不同的持久化对象，
强行只接收一个 `manifest` 参数反而需要调用方额外把 `vars` 塞进
`manifest`，本模块选择更直接、更容易单测的两参数签名。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Union

from world_simulator.relationship import normalize_relationships

EntityIds = Union[Dict[str, Any], Iterable[str], None]


def _entity_id_set(entities: EntityIds) -> set:
    if entities is None:
        return set()
    if isinstance(entities, dict):
        return {str(k) for k in entities.keys()}
    return {str(x) for x in entities}


def build_entity_graph(relationships_raw: Any, entities: EntityIds) -> Dict[str, List[str]]:
    """遍历 `settings.relationships`，把 `from`/`to` 都能在 `entities`
    （`vars.entities` 字典本身，或者调用方已经取好的 id 集合/列表）
    里找到对应 id 的记录，构造一个简单无向邻接表
    `{entity_id: [相关 entity_id, ...]}`。

    `from`/`to` 匹配不上真实 entity 的记录**静默跳过**，不阻断、不
    报错——`narrative` 自由文本本来就不做强校验，结构化关系列表也
    不应该突然变严格（同 `relationship.normalize_relationships()`
    "丢弃无效条目、不报错"的一贯风格）。

    无向图：`find_relationship_path()` 只关心"A 和 B 之间隔着几层
    关系"这个连通性问题，不区分 `from`→`to` 的方向性（`kind` 是
    `rival`/`ally` 也好，路径查询都只把它当一条无方向的边）——如果
    后续需要方向敏感的查询，应该新增一个单独的函数，不应该改变这个
    函数已有的返回形状。

    同一对实体之间出现多条关系（比如既是 `rival` 又声明了别的备注）
    时只保留一条边（邻接表天然去重，多条关系不代表路径上有多条可走
    的边，图上仍然只是"这两个实体之间有关系"这一个事实）。

    Args:
        relationships_raw: `manifest.settings.get("relationships")`
            的原始值（未归一化的列表也可以，内部会调用
            `relationship.normalize_relationships()`）。
        entities: `vars.entities` 字典本身（用它的 key 集合），或者
            调用方已经取好的 entity id 可迭代对象。`None`/空都会让
            所有关系记录因为匹配不上任何 id 而被跳过，返回空字典。

    Returns:
        `{entity_id: [相关 entity_id, ...]}`，没有任何有效关系时返回
        空字典（不是 `None`——空字典是"确实没有可用的连通信息"这个
        明确状态，调用方不需要额外判空处理两种"没有"）。
    """
    entity_ids = _entity_id_set(entities)
    graph: Dict[str, List[str]] = {}
    if not entity_ids:
        return graph

    for rel in normalize_relationships(relationships_raw):
        from_id = rel["from"]
        to_id = rel["to"]
        if from_id not in entity_ids or to_id not in entity_ids:
            continue
        if from_id == to_id:
            # 自环对"连通性路径查询"没有意义，跳过（不影响 from_id
            # 自己作为图里一个孤立/有其它边的节点存在）。
            graph.setdefault(from_id, [])
            continue
        neighbors_from = graph.setdefault(from_id, [])
        if to_id not in neighbors_from:
            neighbors_from.append(to_id)
        neighbors_to = graph.setdefault(to_id, [])
        if from_id not in neighbors_to:
            neighbors_to.append(from_id)
    return graph


def find_relationship_path(
    graph: Dict[str, List[str]], start_id: str, end_id: str, *, max_depth: int = 3
) -> Optional[List[str]]:
    """在 `build_entity_graph()` 返回的邻接表上做一次简单 BFS，找
    `start_id` 到 `end_id` 之间最短的关系路径（entity id 列表，含
    起点和终点）。

    BFS 保证找到的是边数最少的路径（如果存在多条边数相同的最短路径，
    返回哪一条取决于 `graph` 里各节点邻居列表的遍历顺序，不做任何
    "哪条更好"的额外判断——不是加权最短路径，`strength`/`reversible`
    完全不参与）。

    Args:
        graph: `build_entity_graph()` 的返回值。
        start_id / end_id: 查询的两个 entity id。
        max_depth: 最多允许的边数（默认 3），超过这个深度还没找到
            则视为"找不到"，返回 `None`——避免在关系很稠密的图上
            无意义地找一条很长、参考价值不大的路径。

    Returns:
        找到路径时返回 `[start_id, ..., end_id]`；`start_id ==
        end_id` 时返回 `[start_id]`（长度为 1 的路径，边数为 0，
        天然满足任何 `max_depth >= 0`）；`start_id`/`end_id` 不在
        `graph` 里、或者在 `max_depth` 步之内确实无法互相到达时
        返回 `None`。
    """
    if start_id == end_id:
        return [start_id]
    if start_id not in graph or end_id not in graph or max_depth < 0:
        return None

    visited = {start_id}
    queue: deque = deque([(start_id, [start_id])])
    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for neighbor in graph.get(node, []):
            if neighbor in visited:
                continue
            new_path = path + [neighbor]
            if neighbor == end_id:
                return new_path
            visited.add(neighbor)
            queue.append((neighbor, new_path))
    return None
