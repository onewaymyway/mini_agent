"""world_forge/gaps.py — 设定缺口检测（确定性，不调用模型）。

缺口是“世界还可以补充什么”的清单，供交互式补全与自动补全使用。
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import repo


def find_gaps(wid: str) -> List[Dict[str, Any]]:
    ents = repo.load_entities(wid)
    rels = repo.load_relations(wid)
    chars = repo.load_characters(wid)
    events = repo.load_events(wid, repo.load_meta(wid)["active_branch"])
    ent_ids = {e["id"] for e in ents}
    char_ids = {c["id"] for c in chars}
    linked = {r["src"] for r in rels} | {r["dst"] for r in rels}
    gaps: List[Dict[str, Any]] = []

    for e in ents:
        if not str(e.get("summary") or "").strip():
            gaps.append({"id": f"gap_sum_{e['id']}", "kind": "missing_summary", "target": e["id"],
                         "label": f"「{e['name']}」缺少描述", "entity_type": "entity"})
        if e["kind"] == "势力" and e["id"] not in linked:
            gaps.append({"id": f"gap_rel_{e['id']}", "kind": "faction_without_relation", "target": e["id"],
                         "label": f"势力「{e['name']}」尚未与其它实体建立关系", "entity_type": "entity"})
        elif e["kind"] in ("地理", "组织") and e["id"] not in linked:
            gaps.append({"id": f"gap_iso_{e['id']}", "kind": "isolated_entity", "target": e["id"],
                         "label": f"「{e['name']}」与世界其它部分没有联系", "entity_type": "entity"})

    for c in chars:
        if not str(c.get("summary") or "").strip():
            gaps.append({"id": f"gap_csum_{c['id']}", "kind": "missing_summary", "target": c["id"],
                         "label": f"角色「{c['name']}」缺少描述", "entity_type": "character"})
        fid = c.get("faction_id")
        if fid and fid not in ent_ids:
            gaps.append({"id": f"gap_fac_{c['id']}", "kind": "dangling_reference", "target": c["id"],
                         "label": f"角色「{c['name']}」的所属势力不存在", "entity_type": "character"})
        elif not fid and c.get("attrs", {}).get("faction_hint"):
            gaps.append({"id": f"gap_hint_{c['id']}", "kind": "unlinked_faction_hint", "target": c["id"],
                         "label": f"角色「{c['name']}」的势力「{c['attrs']['faction_hint']}」尚未关联",
                         "entity_type": "character"})

    for ev in events:
        for ref in ev.get("involves") or []:
            if ref not in ent_ids and ref not in char_ids:
                gaps.append({"id": f"gap_ev_{ev['id']}_{ref}", "kind": "dangling_reference", "target": ev["id"],
                             "label": f"事件「{ev['title']}」引用了不存在的对象", "entity_type": "event"})
                break
    return gaps
