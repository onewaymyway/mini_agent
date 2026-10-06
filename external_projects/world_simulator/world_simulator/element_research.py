"""world_simulator/element_research.py — 元素联网证据研究（第二十四轮 A3）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.4、§8 A3、§9 风险 1。

## 流程

```
目标（重点元素）→ research_element()：调一次 element_research workflow（agent 步骤，可 web_search）
              → parse_output()：解析 JSON（畸形/缺字段 → 错误，不写任何东西）
              → apply_research()：**纯函数**——证据规整与校验、LLM 无权声明的内容改写、与已有剖面合并
              → research_and_save()：先追加证据、再保存 manifest（顺序见函数注释）
```

## 谁说了算（本模块的核心约束）

LLM 只能**提供草稿与证据**。下面这些由引擎裁决，LLM 写什么都不算数：

- **来源状态**：LLM 在草稿里写的 `basis` / 状态一律丢弃；一个字段能不能标 `sourced`，只看它引用的证据是否真的被接纳
  （有合法 `http(s)` 链接 + 非空要点）。引用不存在的证据 → 该字段回到 `llm_prior`；
- **指标现值的出处要对得上**：现值要标 `sourced`，所引证据必须**带数值**、**单位与指标一致**（指标有单位时证据必须给同样的单位）、
  **数值一致**（相对误差 ≤ 1%）。任何一条不满足就不算出处——否则"有出处"徽标会被一条风马牛不相及的链接骗到；
- **引擎状态**：路径 `status`/`resolve_at_day`、里程碑 `reached_*` 一律剥掉（A4 引擎才拥有）；
- **用户的决定**：`user_confirmed` / `user_edited` 的条目**永不被研究覆盖**。

## 合并规则（`merge_draft`）

- 草稿里**新 id** 的条目 → 加入；
- **同 id** 的已有条目：只有当新条目是 `sourced`、且已有条目不是用户确认过的，才整条替换（继承引擎状态）；
  其余情况保留已有条目——研究的作用是**补出处**，不是用无出处的新猜测覆盖旧猜测（也不会冲掉向导里用户手改的内容）；
- 已有但草稿没提的条目原样保留；
- 合并后 `anatomy_status` 重算（`anatomy.derive_status`）：研究产出永远是**未审阅**，除非每个条目都经用户确认。

## 降级（不阻断）

联网失败 / 无联网 / 超时 / 输出无法解析 → 抛 `ElementResearchError`，**不改任何状态**，元素保持 A2 的 `llm_prior` 剖面；
创建后的批量研究（`research_pending`）逐个元素各自捕获，一个失败不影响其它。
研究"成功但没找到任何出处"也算成功（`researched_at` 照写，不会反复重试），报告里 `no_evidence=True`。

## 不在 `advance()` 里自动研究（与计划的偏差，见文档）

计划写"新元素登记后补全研究接入 `pending_enrichment` 流程"。研究是**额外的、会联网的 LLM 调用**，放进 `advance()`
会让推进不可预期地变慢甚至超时（计划风险 2），所以 A3 **不在推进里自动触发**：创建后批量研究、界面"补做研究 / 刷新研究"
按钮触发；`research_on_register` 控制的是"创建之后才登记的重点元素是否算作待研究"（见 `pending_targets`）。

纯 Python（`research_element` 在函数内延迟导入 `mini_agent`，缺框架时抛 `ImportError`，由调用方处理）。
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from world_simulator import anatomy as an
from world_simulator import anatomy_templates as at
from world_simulator import element_registry as er
from world_simulator import evidence as ev

WORKFLOW_NAME = "element_research"
RESEARCH_SOURCE = "element_research"

MAX_EVIDENCE_PER_RUN = 40
MAX_UNRESOLVED = 12
MAX_UNRESOLVED_LEN = 200
DIGEST_CHARS = 2400          # 已有拆解摘要的字符预算（刷新研究时放进 prompt）
VALUE_REL_TOLERANCE = 0.01   # 现值与证据数值的相对误差上限

_USER_STATES = ("user_confirmed", "user_edited")


class ElementResearchError(RuntimeError):
    """研究无法进行/无法解析（元素不存在、workflow 失败、输出不是合法 JSON……）。**不改任何状态**。"""


# ── 目标选择 ─────────────────────────────────────────────────────────


def is_researched(line: Optional[Dict[str, Any]]) -> bool:
    """这个元素的剖面是否已经研究过（`meta.researched_at` 非空）。研究失败不写它，所以失败的会继续出现在待研究里。"""
    meta = (an.get_anatomy(line) or {}).get("meta") or {}
    return bool(str(meta.get("researched_at") or "").strip())


def pending_targets(settings: Optional[Dict[str, Any]]) -> List[str]:
    """待研究的重点元素（线 id，保持登记顺序）。

    - 剖面开关没开 → 空；
    - 只列**重点元素**且**没研究过**的；
    - `origin=discovered`（创建之后才登记的）要 `research_on_register`，其余（创建期的）要 `research_on_create`。
    手动点\"刷新研究\"不受这两个开关限制（那是用户明确的动作）。
    """
    if not an.is_enabled(settings):
        return []
    params = an.get_params(settings)
    out: List[str] = []
    for lid in an.key_elements(settings):
        line = er.resolve(settings or {}, lid)
        if line is None or is_researched(line):
            continue
        discovered = line.get("origin") == "discovered"
        if discovered and not params["research_on_register"]:
            continue
        if not discovered and not params["research_on_create"]:
            continue
        out.append(lid)
    return out


def can_research(settings: Optional[Dict[str, Any]], element_ref: Any) -> Tuple[bool, str]:
    """能不能对这个元素做研究（开关、元素存在、非领域线、存活）。返回 `(可以, 原因)`。"""
    if not an.is_enabled(settings):
        return False, "元素剖面未启用"
    line = er.resolve(settings or {}, element_ref, follow_merged=False)
    if line is None:
        return False, f"找不到元素「{element_ref}」"
    if er.line_kind(line) == "domain":
        return False, "领域线没有剖面，不能研究"
    if not er.is_alive(line):
        return False, "元素已退场或已被合并"
    return True, ""


# ── 输入构造 ─────────────────────────────────────────────────────────


def _existing_digest(anatomy: Optional[Dict[str, Any]], limit: int = DIGEST_CHARS) -> Dict[str, Any]:
    """已有拆解的精简摘要（id / 名称 / 来源状态 / 指标现值），给刷新研究用；超出字符预算时从后面砍。"""
    out: Dict[str, Any] = {}
    for part in an.PARTS:
        rows = []
        for it in (anatomy or {}).get(part) or []:
            row: Dict[str, Any] = {"id": it["id"], "state": an.basis_of(it)["state"]}
            if it.get("name"):
                row["name"] = it["name"]
            cur = it.get("current")
            if isinstance(cur, dict) and "value" in cur:
                row["current"] = {k: cur[k] for k in ("value", "as_of") if k in cur}
                row["current_state"] = an.basis_of(cur)["state"]
                if it.get("unit"):
                    row["unit"] = it["unit"]
            rows.append(row)
        if rows:
            out[part] = rows
    text = json.dumps(out, ensure_ascii=False)
    while len(text) > limit and out:
        # 从最后一个部分的最后一条砍起，直到放得下
        last = next(p for p in reversed(an.PARTS) if p in out)
        out[last].pop()
        if not out[last]:
            del out[last]
        text = json.dumps(out, ensure_ascii=False)
    return out


def build_inputs(
    settings: Dict[str, Any], element_ref: Any, *, scenario: Optional[Dict[str, Any]] = None,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """element_research workflow 的输入（全部是可 JSON 化的字符串/数字）。"""
    ok, why = can_research(settings, element_ref)
    if not ok:
        raise ElementResearchError(why)
    line = er.resolve(settings, element_ref)
    params = an.get_params(settings)
    parent = er.resolve(settings, line.get("parent")) if line.get("parent") else None
    element = {
        "id": str(line["id"]).strip(), "label": str(line.get("label") or line["id"]),
        "element_type": str(line.get("element_type") or ""),
        "aliases": [str(a) for a in line.get("aliases") or []][:8],
    }
    for key in ("why_key", "summary", "description"):
        if isinstance(line.get(key), str) and line[key].strip():
            element[key] = line[key].strip()[:300]
    if parent is not None:
        element["parent_label"] = str(parent.get("label") or parent.get("id"))
    return {
        "today": today or date.today().isoformat(),
        "element_json": json.dumps(element, ensure_ascii=False),
        "scenario_json": json.dumps(scenario or {}, ensure_ascii=False),
        "template_hint": at.slot_hint(line.get("element_type")),
        "existing_json": json.dumps(_existing_digest(an.get_anatomy(line)), ensure_ascii=False),
        "max_searches": params["research_max_searches"],
    }


# ── 输出解析 ─────────────────────────────────────────────────────────


def parse_output(data: Any) -> Dict[str, Any]:
    """workflow 最终 JSON → `{draft, evidence, unresolved}`（只做形状整理，**不校验内容**，内容校验在 `apply_research`）。

    - `anatomy_draft` 必须是对象（缺失/类型错 → 抛错：这是协议级的错）；
    - `evidence` 不是数组 → 当空；只保留对象条目，最多 `MAX_EVIDENCE_PER_RUN`；
    - `unresolved` 只保留非空字符串，限条数与长度。
    """
    if not isinstance(data, dict):
        raise ElementResearchError("研究结果不是 JSON 对象")
    draft = data.get("anatomy_draft")
    if not isinstance(draft, dict):
        raise ElementResearchError("研究结果缺少 anatomy_draft 对象")
    raw_ev = data.get("evidence")
    evidence = [e for e in raw_ev if isinstance(e, dict)][:MAX_EVIDENCE_PER_RUN] if isinstance(raw_ev, list) else []
    raw_un = data.get("unresolved")
    unresolved: List[str] = []
    for item in raw_un if isinstance(raw_un, list) else []:
        text = ev._clean_text(item, MAX_UNRESOLVED_LEN)
        if text:
            unresolved.append(text)
        if len(unresolved) >= MAX_UNRESOLVED:
            break
    return {"draft": draft, "evidence": evidence, "unresolved": unresolved}


# ── 草稿预处理：来源状态由引擎裁决 ───────────────────────────────────


def _raw_refs(carrier: Dict[str, Any]) -> List[str]:
    """草稿条目（或指标 current）里 LLM 写的证据引用（`evidence_ids` 或 `basis.evidence_ids`），去重保序。"""
    out: List[str] = []
    sources = [carrier.get("evidence_ids")]
    if isinstance(carrier.get("basis"), dict):
        sources.append(carrier["basis"].get("evidence_ids"))
    for src in sources:
        if isinstance(src, (str, int)) and not isinstance(src, bool):
            src = [src]
        for x in src if isinstance(src, list) else []:
            if isinstance(x, (str, int)) and not isinstance(x, bool):
                key = str(x).strip()
                if key and key not in out:
                    out.append(key)
    return out[: an.MAX_EVIDENCE_IDS]


def _unit_key(unit: Any) -> str:
    return "".join(str(unit or "").split()).lower()


def _close(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b))
    return scale < 1e-12 or abs(a - b) <= VALUE_REL_TOLERANCE * scale


def _check_current(
    metric: Dict[str, Any], cur: Dict[str, Any], rec: Dict[str, Any], where: str, flags: List[Dict[str, str]],
) -> bool:
    """指标现值引用这条证据是否站得住（见模块文档）。不满足时往 `flags` 里记一条说明并返回 False。"""
    value = cur.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return False  # 现值本身非法：留给规整丢弃，这里不给它出处
    if "value" not in rec:
        flags.append({"kind": "no_value", "where": where, "message": f"证据 {rec['claim'][:30]}… 没有数值，现值仍标 LLM 先验"})
        return False
    m_unit = _unit_key(metric.get("unit"))
    if m_unit:
        e_unit = _unit_key(rec.get("unit"))
        if not e_unit:
            flags.append({"kind": "unit_missing", "where": where, "message": "指标有单位而证据没给单位，无法核对，现值仍标 LLM 先验"})
            return False
        if e_unit != m_unit:
            _add_flag(rec, "unit_mismatch")
            flags.append({"kind": "unit_mismatch", "where": where, "message": f"证据单位 {rec.get('unit')} 与指标单位 {metric.get('unit')} 不一致"})
            return False
    if not _close(float(value), float(rec["value"])):
        _add_flag(rec, "value_mismatch")
        flags.append({"kind": "value_mismatch", "where": where, "message": f"现值 {value} 与证据数值 {rec['value']} 不一致"})
        return False
    bounds = metric.get("bounds") if isinstance(metric.get("bounds"), dict) else {}
    lo, hi = bounds.get("min"), bounds.get("max")
    if (isinstance(lo, (int, float)) and rec["value"] < lo) or (isinstance(hi, (int, float)) and rec["value"] > hi):
        _add_flag(rec, "out_of_bounds")
        flags.append({"kind": "out_of_bounds", "where": where, "message": f"证据数值 {rec['value']} 超出声明的 bounds {bounds}，待审"})
    return True


def _add_flag(rec: Dict[str, Any], flag: str) -> None:
    flags = list(rec.get("flags") or [])
    if flag not in flags:
        flags.append(flag)
    rec["flags"] = flags


def _set_basis(carrier: Dict[str, Any], recs: List[Dict[str, Any]]) -> None:
    """按**被接纳的**证据设置来源：有证据 → `sourced` + id + 最保守的置信度；没有 → 不写 basis（= LLM 先验）。"""
    carrier.pop("evidence_ids", None)
    carrier.pop("basis", None)
    if recs:
        carrier["basis"] = {
            "state": "sourced", "evidence_ids": [r["ev_id"] for r in recs], "confidence": ev.lowest_confidence(recs),
        }


def prepare_draft(
    draft: Dict[str, Any], accepted: Dict[str, Dict[str, Any]], flags: List[Dict[str, str]],
) -> Dict[str, Any]:
    """把 LLM 草稿里的来源声明**全部重写**成引擎裁决后的结果（返回深拷贝，不改入参）。

    `accepted`：LLM 临时编号 → 已接纳的证据记录（带最终 `ev_id`）。草稿里引用了没被接纳的编号 → 忽略该引用。
    """
    out = copy.deepcopy(draft)
    out.pop("meta", None)  # 元信息由引擎写，LLM 无权声明（重点/状态/研究时间）
    for part in an.PARTS:
        items = out.get(part)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            recs = [accepted[k] for k in _raw_refs(item) if k in accepted]
            _set_basis(item, recs)
            cur = item.get("current") if part == "metrics" else None
            if isinstance(cur, dict):
                where = f"metrics.{item.get('id') or item.get('name')}.current"
                good = [r for r in (accepted[k] for k in _raw_refs(cur) if k in accepted)
                        if _check_current(item, cur, r, where, flags)]
                _set_basis(cur, good)
    return out


def _referenced_keys(draft: Dict[str, Any]) -> List[str]:
    """草稿里被引用过的临时编号（保持首次出现顺序）。"""
    seen: Dict[str, None] = {}
    for part in an.PARTS:
        for item in draft.get(part) if isinstance(draft.get(part), list) else []:
            if not isinstance(item, dict):
                continue
            for k in _raw_refs(item):
                seen.setdefault(k, None)
            if part == "metrics" and isinstance(item.get("current"), dict):
                for k in _raw_refs(item["current"]):
                    seen.setdefault(k, None)
    return list(seen)


# ── 合并 ─────────────────────────────────────────────────────────────

_RANK_PRIOR, _RANK_SOURCED, _RANK_USER = 0, 1, 2


def _rank(item: Dict[str, Any]) -> int:
    """一个条目（含指标的 current）的\"来源等级\"：用户确认 > 有出处 > 先验。取条目与其 current 里最高的。"""
    states = [an.basis_of(item)["state"]]
    if isinstance(item.get("current"), dict):
        states.append(an.basis_of(item["current"])["state"])
    if any(s in _USER_STATES for s in states):
        return _RANK_USER
    return _RANK_SOURCED if "sourced" in states else _RANK_PRIOR


def _carry_engine_state(old: Dict[str, Any], new: Dict[str, Any], part: str) -> None:
    """替换条目时继承引擎状态（A4 才会产生；提前保护，免得刷新研究把已解决的瓶颈/已达成的里程碑冲掉）。"""
    if part == "bottlenecks":
        if old.get("status") in ("resolved", "exhausted"):
            new["status"] = old["status"]
        for f in ("resolved_step", "resolved_day", "resolved_by"):  # A4：解决记录跟着状态一起带
            if f in old:
                new[f] = old[f]
        old_paths = {p["id"]: p for p in old.get("resolution_paths") or [] if isinstance(p, dict) and "id" in p}
        for p in new.get("resolution_paths") or []:
            prev = old_paths.get(p.get("id"))
            if prev:
                for f in ("status", "resolve_at_day", "outcome", "started_day", "started_step"):
                    if f in prev:
                        p[f] = prev[f]
    elif part == "milestones":
        for f in ("reached_step", "reached_sim_day"):
            if f in old:
                new[f] = old[f]
    elif part == "adoption_gates":
        for f in ("open", "opened_step"):
            if f in old:
                new[f] = old[f]
    elif part == "components":
        if "engine" in old:  # 就绪度的推进状态；指标的 engine 故意不带（新的有出处现值 = 重新锚定）
            new["engine"] = old["engine"]


def merge_draft(
    existing: Optional[Dict[str, Any]], draft: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把（已规整、已改写来源的）草稿并进已有剖面。返回 `(合并后的剖面部分, 信息)`；**不含 meta**。

    信息：`added` / `replaced` / `kept_existing`（同 id 但没替换）/ `kept_user`（被用户确认过而保留的 `part.id` 列表）。
    """
    cur = an.normalize_anatomy(existing)
    merged: Dict[str, List[Dict[str, Any]]] = {}
    info: Dict[str, Any] = {"added": 0, "replaced": 0, "kept_existing": 0, "kept_user": []}
    for part in an.PARTS:
        base = [copy.deepcopy(x) for x in cur.get(part) or []]
        index = {x["id"]: i for i, x in enumerate(base)}
        for item in draft.get(part) or []:
            iid = item["id"]
            if iid not in index:
                base.append(copy.deepcopy(item))
                index[iid] = len(base) - 1
                info["added"] += 1
                continue
            old = base[index[iid]]
            old_rank, new_rank = _rank(old), _rank(item)
            if old_rank == _RANK_USER:
                info["kept_user"].append(f"{part}.{iid}")
                info["kept_existing"] += 1
            elif new_rank == _RANK_SOURCED:
                new = copy.deepcopy(item)
                _carry_engine_state(old, new, part)
                base[index[iid]] = new
                info["replaced"] += 1
            else:
                info["kept_existing"] += 1
        if base:
            merged[part] = base
    return merged, info


def _carrier_refs(anatomy: Dict[str, Any]) -> List[Tuple[str, str]]:
    """`[(字段标签, ev_id), ...]`：每个引用证据的位置，标签形如 `metrics.cost.current` / `components.x`。"""
    out: List[Tuple[str, str]] = []
    for part in an.PARTS:
        for item in anatomy.get(part) or []:
            for e in an.basis_of(item).get("evidence_ids") or []:
                out.append((f"{part}.{item['id']}", e))
            cur = item.get("current")
            if part == "metrics" and isinstance(cur, dict):
                for e in an.basis_of(cur).get("evidence_ids") or []:
                    out.append((f"{part}.{item['id']}.current", e))
    return out


def _with_line_copied(settings: Dict[str, Any], line: Dict[str, Any]) -> Dict[str, Any]:
    """设置的浅拷贝，其中目标线换成它的拷贝（`set_anatomy` 就地改这条拷贝，不碰入参）。

    **基于原始 `causal_lines` 按身份替换**，不用 `er.get_lines`（它会过滤掉畸形/重复 id 的条目——拿它重建列表会丢数据）。
    """
    work = dict(settings)
    work["causal_lines"] = [dict(x) if x is line else x for x in (settings.get("causal_lines") or [])]
    return work


# ── 核心：应用一次研究结果（纯函数）────────────────────────────────


def apply_research(
    settings: Dict[str, Any], element_ref: Any, parsed: Dict[str, Any], *,
    existing_records: Optional[List[Dict[str, Any]]] = None,
    run_id: Optional[str] = None, now: Optional[str] = None,
) -> Dict[str, Any]:
    """把一次研究的解析结果落成：新的 `settings`（只有目标元素那条线换了）、要追加的证据记录、要标记取代的旧证据、报告。

    **纯函数**：不改入参、不读写磁盘。返回：

    ```
    {"settings": 新设置, "element_id": str, "new_evidence": [记录...], "superseded_ids": [...], "report": {...}}
    ```
    """
    ok, why = can_research(settings, element_ref)
    if not ok:
        raise ElementResearchError(why)
    line = er.resolve(settings, element_ref)
    lid = str(line["id"]).strip()
    existing_records = list(existing_records or [])
    stamp = now or ev.now_iso()
    run = run_id or f"rr_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # 1) 证据：规整、与已有重复的复用旧 id、其余按需分配新 id（只给被草稿引用的分配）
    draft_raw = parsed.get("draft") if isinstance(parsed.get("draft"), dict) else {}
    referenced = _referenced_keys(draft_raw)
    candidates: Dict[str, Dict[str, Any]] = {}
    invalid = 0
    for raw in parsed.get("evidence") or []:
        key = str(raw.get("ev_id") or raw.get("id") or "").strip()
        rec = ev.normalize_record({
            **{k: v for k, v in raw.items() if k not in ("ev_id", "id", "status", "flags", "retrieved_at", "research_run_id", "element_id", "field_ref")},
            "ev_id": "", "element_id": lid, "retrieved_at": stamp, "research_run_id": run, "status": "active",
        })
        if rec is None or not key:
            invalid += 1
            continue
        candidates.setdefault(key, rec)
    active_old = ev.for_element(existing_records, lid)
    accepted: Dict[str, Dict[str, Any]] = {}
    fresh: List[Dict[str, Any]] = []
    reused_ids: set = set()
    number = ev.next_id_number(existing_records)
    for key in referenced:
        rec = candidates.get(key)
        if rec is None:
            continue
        dup = next((o for o in active_old if o["source_url"] == rec["source_url"] and o["claim"] == rec["claim"]), None)
        if dup is not None:
            accepted[key] = dict(dup)
            reused_ids.add(dup["ev_id"])
            continue
        rec["ev_id"] = ev.format_id(number)
        number += 1
        accepted[key] = rec
        fresh.append(rec)

    # 2) 草稿：来源由引擎裁决 → 规整 → 剥引擎状态
    flags: List[Dict[str, str]] = []
    prepared = prepare_draft(draft_raw, accepted, flags)
    norm_draft, dropped = an.normalize_anatomy_report({p: prepared[p] for p in an.PARTS if p in prepared})
    an.strip_engine_state(norm_draft)

    # 3) 合并
    existing = an.get_anatomy(line)
    merged_parts, info = merge_draft(existing, norm_draft)
    meta = dict((existing or {}).get("meta") or {})
    meta["researched_at"] = stamp
    meta["template"] = at.template_name(line.get("element_type"))
    meta.setdefault("key", True)
    merged = dict(merged_parts)
    meta["anatomy_status"] = an.derive_status(merged)
    merged["meta"] = meta
    merged = an.normalize_anatomy(merged)

    # 4) 只存**最终被剖面引用**的新证据；定 field_ref；其余算未引用
    refs = _carrier_refs(merged)
    first_ref: Dict[str, str] = {}
    for label, e in refs:
        first_ref.setdefault(e, label)
    new_evidence = []
    for rec in fresh:
        if rec["ev_id"] in first_ref:
            rec = dict(rec)
            rec["field_ref"] = first_ref[rec["ev_id"]]
            new_evidence.append(rec)
    used_new = {r["ev_id"] for r in new_evidence}
    unreferenced = sum(1 for r in fresh if r["ev_id"] not in used_new) + sum(
        1 for k, r in candidates.items() if k not in referenced
    )

    # 5) 旧证据：不再被剖面引用的 → 被取代
    still = {e for _, e in refs}
    superseded = [o["ev_id"] for o in active_old if o["ev_id"] not in still]

    # 6) 落到设置副本
    work = _with_line_copied(settings, line)
    wrote, err, _ = an.set_anatomy(work, lid, merged)
    if not wrote:
        raise ElementResearchError(err)

    stats = an.basis_stats(merged)
    report = {
        "element_id": lid, "run_id": run, "researched_at": stamp,
        "new_evidence": len(new_evidence), "reused_evidence": len(reused_ids & still),
        "superseded": len(superseded), "invalid_evidence": invalid, "unreferenced_evidence": unreferenced,
        "added": info["added"], "replaced": info["replaced"], "kept_existing": info["kept_existing"],
        "kept_user": info["kept_user"], "flags": flags, "unresolved": list(parsed.get("unresolved") or []),
        "dropped": dropped, "stats": stats,
        "no_evidence": not any(an.basis_of(c)["state"] == "sourced" for c in an._basis_carriers(merged)),
        "status": merged.get("meta", {}).get("anatomy_status"),
    }
    return {
        "settings": work, "element_id": lid, "new_evidence": new_evidence,
        "superseded_ids": superseded, "report": report,
    }


# ── 取回研究（调 workflow）──────────────────────────────────────────


def research_element(
    cfg: Any, workspace_root: Path, settings: Dict[str, Any], element_ref: Any, *,
    scenario: Optional[Dict[str, Any]] = None, today: Optional[str] = None,
) -> Dict[str, Any]:
    """调一次 `element_research` workflow，返回 `parse_output` 的结果。**不改任何状态、不落盘**。

    Raises:
        ElementResearchError: 元素不可研究、找不到 workflow、执行未成功、最终回复无法解析。
        ImportError: 没有 mini_agent 框架（由调用方处理，同 `element_discovery`）。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore
    from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output

    inputs = build_inputs(settings, element_ref, scenario=scenario, today=today)
    wf = WorkflowStore(Path(workspace_root)).load(WORKFLOW_NAME)
    if wf is None:
        raise ElementResearchError(
            f"找不到 workflow 定义 '{WORKFLOW_NAME}'（预期路径：{workspace_root}/workflows/{WORKFLOW_NAME}.yaml）"
        )
    result = WorkflowRunner(cfg).run(wf, inputs)
    if result.status != "done":
        failed = [f"{sr.step_id}({sr.status.value}): {sr.error}" for sr in result.step_results if sr.status.value != "done"]
        raise ElementResearchError(f"{WORKFLOW_NAME} workflow 执行未成功：status={result.status}；" + "；".join(failed))
    step_result = next((sr for sr in result.step_results if sr.step_id == WORKFLOW_NAME), None)
    if step_result is None:
        raise ElementResearchError(f"{WORKFLOW_NAME} 步骤没有产出结果")
    try:
        data = extract_agent_json_output(step_result.output, required_keys=["anatomy_draft"])
    except AgentStepOutputError as exc:
        raise ElementResearchError(f"{WORKFLOW_NAME} 步骤回复无法解析：{exc}") from exc
    return parse_output(data)


Fetcher = Callable[..., Dict[str, Any]]


def _scenario_of(store: Any, manifest: Any) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {"title": str(getattr(manifest, "title", "") or ""), "intent": str(getattr(manifest, "intent", "") or "")}
    try:
        state = store.load_current_state(getattr(manifest, "branch", "main") or "main")
        label = str(getattr(state, "time_label", "") or "").strip()
        if label:
            scenario["time_label"] = label
    except Exception:  # noqa: BLE001 — 背景信息拿不到不影响研究
        pass
    return {k: v for k, v in scenario.items() if v}


def research_and_save(
    cfg: Any, workspace_root: Path, data_dir: Path, sim_id: str, element_ref: Any, *,
    fetch: Optional[Fetcher] = None,
) -> Dict[str, Any]:
    """研究一个元素并落盘；返回报告。失败抛 `ElementResearchError`（**此时没有任何写入**）。

    写入顺序：**先追加证据、再保存 manifest**。反过来的话，manifest 保存成功而证据没写进去，剖面会引用不存在的证据（体检里的
    `dangling_evidence`）；现在的顺序即使第二步失败，也只留下几条没人引用的证据（无害）。
    `fetch` 供测试/调用方替换取回环节（签名同 `research_element`）。
    """
    from world_simulator.store import SimStore

    store = SimStore.for_root(Path(data_dir), sim_id)
    manifest = store.load_manifest()
    ok, why = can_research(manifest.settings, element_ref)
    if not ok:
        raise ElementResearchError(why)
    parsed = (fetch or research_element)(
        cfg, workspace_root, manifest.settings, element_ref, scenario=_scenario_of(store, manifest),
    )
    manifest = store.load_manifest()  # 取回期间用户可能改过设置：落盘前重新读
    applied = apply_research(manifest.settings, element_ref, parsed, existing_records=store.load_evidence())
    store.append_evidence(applied["new_evidence"])
    for ev_id in applied["superseded_ids"]:
        store.set_evidence_status(ev_id, "superseded")
    manifest.settings = {**manifest.settings, "causal_lines": applied["settings"]["causal_lines"]}
    store.save_manifest(manifest)
    return applied["report"]


def research_pending(
    cfg: Any, workspace_root: Path, data_dir: Path, sim_id: str, *,
    fetch: Optional[Fetcher] = None, progress: Optional[Callable[[str, int, int], None]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """对所有待研究的重点元素逐个研究（创建后调用 / 界面\"补做研究\"）。**永不抛异常**：每个元素各自捕获。

    返回 `[{element_id, ok, report | error}]`。`progress(element_id, 序号, 总数)` 在每个元素开始前回调（给界面显示进度）。
    `limit` 限制本次最多研究几个（`None` = 全部）；没处理的下次仍在待研究里。
    """
    from world_simulator.store import SimStore

    try:
        targets = pending_targets(SimStore.for_root(Path(data_dir), sim_id).load_manifest().settings)
    except Exception as exc:  # noqa: BLE001
        return [{"element_id": "", "ok": False, "error": f"读取实例失败：{exc}"}]
    if limit is not None:
        targets = targets[: max(0, int(limit))]
    results: List[Dict[str, Any]] = []
    for i, lid in enumerate(targets, start=1):
        if progress is not None:
            try:
                progress(lid, i, len(targets))
            except Exception:  # noqa: BLE001
                pass
        try:
            results.append({"element_id": lid, "ok": True, "report": research_and_save(
                cfg, workspace_root, data_dir, sim_id, lid, fetch=fetch,
            )})
        except Exception as exc:  # noqa: BLE001 — 研究是旁路功能，失败只记录
            results.append({"element_id": lid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return results


# ── 驳回证据 ─────────────────────────────────────────────────────────


def reject_in_settings(settings: Dict[str, Any], element_id: str, ev_id: str) -> Dict[str, Any]:
    """纯函数：把剖面里对某条证据的引用摘掉（`sourced` 且没有别的证据 → 回到 `llm_prior`），状态重算为未审阅/已审阅。
    返回新的 `settings` 副本；找不到元素/没有剖面 → 原样返回副本。"""
    line = er.resolve(settings, element_id)
    anatomy = an.get_anatomy(line)
    if line is None or anatomy is None:
        return dict(settings)
    new = an.strip_evidence(anatomy, [ev_id])
    meta = dict(new.get("meta") or {})
    meta["anatomy_status"] = an.derive_status(new)
    new["meta"] = meta
    work = _with_line_copied(settings, line)
    an.set_anatomy(work, str(line["id"]).strip(), new)
    return work


def reject_evidence(data_dir: Path, sim_id: str, ev_id: str) -> Dict[str, Any]:
    """用户驳回一条证据：先摘掉剖面引用并保存，再追加 `rejected` 状态事件。返回 `{element_id, ev_id}`。

    顺序同 `research_and_save` 的取舍：即使第二步失败，也只是一条没人引用的\"活跃\"证据，不会出现剖面引用已驳回证据。
    """
    from world_simulator.store import SimStore

    store = SimStore.for_root(Path(data_dir), sim_id)
    record = next((r for r in store.load_evidence() if r.get("ev_id") == ev_id), None)
    if record is None:
        raise ElementResearchError(f"找不到证据 {ev_id}")
    manifest = store.load_manifest()
    new_settings = reject_in_settings(manifest.settings, record["element_id"], ev_id)
    manifest.settings = {**manifest.settings, "causal_lines": new_settings["causal_lines"]}
    store.save_manifest(manifest)
    store.set_evidence_status(ev_id, "rejected")
    return {"element_id": record["element_id"], "ev_id": ev_id}
