"""world_simulator/backtest.py — 回测与校准（第二十二轮 WP5）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP5。目的：**有答案才能说"更真实"**。给定一个带已知里程碑的案例，
对引擎**隐藏答案**地推进 N 步，把涌现的里程碑与真值对账，得到一组可比较
的数字；同一案例在开关 ON/OFF 下各重复 N 次，比较指标分布——这是后续
WP1/2/3 是否真的有效的唯一量化手段。

## 流程

1. `load_case()`：读 YAML 案例（起点设定、真值里程碑及年份、前置关系）。
2. `anonymize()`：名称别名化 + 年份整体平移，只把处理后的意图交给引擎。
3. `run_case()`：在**独立数据目录**里创建实例、推进 N 步（不碰用户真实
   `data/`，也不读写真实知识库）。
4. `extract_candidates()`：从历史里抽取涌现的里程碑候选——
   `capabilities_gained`、以及因果树里 `resolved` 的分支。
5. `match`：把候选与真值配对。默认规则匹配器（名称/别名子串，零成本、
   可复现）；`make_llm_matcher()` 是一次 LLM 语义匹配调用。**配对结果
   可由用户覆盖**（`rescore()`），最终裁定权在人，与 `reality_check` 一致。
6. `score_run()`：召回/精确、顺序一致性（Kendall τ-b）、区间偏差、前置
   违反数、阶段一致率；同时附上 WP4 体检的告警计数作为结构性基线。
7. 每条真值以 `reality_check.record_and_apply()` 写入该次运行数据目录
   的 `reality_checks.jsonl`（命中 → matched；范围内未命中 → diverged）。

## 只对"相对顺序与间隔"打分

引擎看到的是别名化名称 + 平移后的年份，打分只用**顺序**（τ）和**区间**
（首个命中里程碑对齐后的间隔误差），不用绝对年份，所以平移不影响分数。
`years_per_step` 是案例声明的"一步约多少年"，候选时间 = 起点 + 步数 ×
该值——这是近似；`elapsed_days`（WP1 引入）落地后应改用引擎自己记录的
每步跨度。

## 已知局限（必须如实标注，报告里每次都会带上）

- **训练数据污染只能缓解不能根除**：LLM 读过真实历史，可能背诵而非模拟。
  别名化只替换案例里列出的名称，描述性文字仍可能泄露；年份平移不影响
  对"这是哪段历史"的识别。所以**绝对分数不可信，只有同一案例、同一匹配
  方式下的 A/B 相对差异才有意义**。
- **精确率是下界**：真值列表是稀疏的，引擎合理涌现的其它里程碑会被算作
  未命中。
- **规则匹配器偏保守**（只认名称/别名子串），LLM 匹配器偏宽松；两者不要
  混着比。
- **样本量**：`run_ab()` 只给分布（n/均值/标准差/最小/最大），不做显著性
  检验；n < 5 时报告会明确写"不足以下结论"。
- 案例文件里的里程碑与年份由人（或 Claude 起草后由人）填写，**不是事实
  来源**；`verified: false` 的案例会在报告里注明。
"""

from __future__ import annotations

import copy
import json
import math
import re
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

CAVEATS: Tuple[str, ...] = (
    "训练数据污染只能缓解不能根除：绝对分数不可信，只有同一案例、同一匹配方式下的 A/B 相对差异才有参考价值。",
    "精确率是下界：真值列表稀疏，引擎合理涌现的其它里程碑会被算作未命中。",
    "候选时间由 years_per_step 近似推得，不是引擎记录的真实跨度。",
)

_YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
_STAGES = ("lab", "expert", "developer", "consumer", "cheap_at_scale", "infrastructure")

Matcher = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Optional[str]]]


class BacktestError(RuntimeError):
    """案例非法、或回测流程无法继续。"""


# ── 案例 ─────────────────────────────────────────────────────────────


@dataclass
class Milestone:
    id: str
    name: str
    year: float
    aliases: List[str] = field(default_factory=list)
    stage: Optional[str] = None
    requires: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class BacktestCase:
    id: str
    title: str
    intent: str
    milestones: List[Milestone]
    template: str = "group_evolution"
    start_year: float = 0.0
    years_per_step: float = 1.0
    steps: int = 10
    time_granularity: str = ""
    settings: Dict[str, Any] = field(default_factory=dict)
    alias_map: Dict[str, str] = field(default_factory=dict)
    year_shift: int = 0
    verified: bool = False
    disclaimer: str = ""


def _fail(case_id: str, msg: str) -> BacktestError:
    return BacktestError(f"案例 {case_id or '?'} 非法：{msg}")


def parse_case(data: Dict[str, Any]) -> BacktestCase:
    """校验并构造案例。宽松处理可选字段，对会让打分失去意义的问题直接报错
    （重复 id、前置引用不存在、前置成环、年份不是数字、没有里程碑）。"""
    if not isinstance(data, dict):
        raise BacktestError("案例必须是一个映射（YAML/JSON 对象）")
    case_id = str(data.get("id") or "").strip()
    if not case_id:
        raise _fail("", "缺少 id")
    start = data.get("start") or {}
    intent = str(start.get("intent") or "").strip()
    if not intent:
        raise _fail(case_id, "start.intent 不能为空")
    try:
        start_year = float(start.get("start_year"))
    except (TypeError, ValueError):
        raise _fail(case_id, "start.start_year 必须是数字")
    try:
        years_per_step = float(start.get("years_per_step", 1.0))
    except (TypeError, ValueError):
        raise _fail(case_id, "start.years_per_step 必须是数字")
    if years_per_step <= 0:
        raise _fail(case_id, "start.years_per_step 必须 > 0")

    raw_ms = data.get("milestones") or []
    if not isinstance(raw_ms, list) or not raw_ms:
        raise _fail(case_id, "milestones 不能为空")
    milestones: List[Milestone] = []
    seen = set()
    for i, item in enumerate(raw_ms):
        if not isinstance(item, dict):
            raise _fail(case_id, f"milestones[{i}] 必须是映射")
        mid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not mid or not name:
            raise _fail(case_id, f"milestones[{i}] 缺少 id 或 name")
        if mid in seen:
            raise _fail(case_id, f"里程碑 id 重复：{mid}")
        seen.add(mid)
        try:
            year = float(item.get("year"))
        except (TypeError, ValueError):
            raise _fail(case_id, f"里程碑 {mid} 的 year 必须是数字")
        stage = item.get("stage")
        if stage is not None and stage not in _STAGES:
            raise _fail(case_id, f"里程碑 {mid} 的 stage 必须是 {_STAGES} 之一")
        milestones.append(Milestone(
            id=mid, name=name, year=year,
            aliases=[str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()],
            stage=stage,
            requires=[str(r).strip() for r in (item.get("requires") or []) if str(r).strip()],
            note=str(item.get("note") or ""),
        ))
    ids = {m.id for m in milestones}
    for m in milestones:
        for req in m.requires:
            if req not in ids:
                raise _fail(case_id, f"里程碑 {m.id} 的前置 {req} 不存在")
            if req == m.id:
                raise _fail(case_id, f"里程碑 {m.id} 的前置不能是自己")
    _assert_acyclic(case_id, milestones)

    alias_map = {str(k): str(v) for k, v in (data.get("alias_map") or {}).items() if str(k)}
    try:
        year_shift = int(data.get("year_shift", 0) or 0)
        steps = int(data.get("steps", 10))
    except (TypeError, ValueError):
        raise _fail(case_id, "year_shift/steps 必须是整数")
    if steps < 1:
        raise _fail(case_id, "steps 必须 >= 1")
    return BacktestCase(
        id=case_id,
        title=str(data.get("title") or case_id),
        intent=intent,
        milestones=milestones,
        template=str(start.get("template") or "group_evolution"),
        start_year=start_year,
        years_per_step=years_per_step,
        steps=steps,
        time_granularity=str(start.get("time_granularity") or ""),
        settings=dict(start.get("settings") or {}),
        alias_map=alias_map,
        year_shift=year_shift,
        verified=bool(data.get("verified", False)),
        disclaimer=str(data.get("disclaimer") or ""),
    )


def _assert_acyclic(case_id: str, milestones: Sequence[Milestone]) -> None:
    graph = {m.id: list(m.requires) for m in milestones}
    state: Dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise _fail(case_id, f"前置关系成环（经过 {node}）")
        if state.get(node) == 2:
            return
        state[node] = 1
        for nxt in graph[node]:
            visit(nxt)
        state[node] = 2

    for node in graph:
        visit(node)


def load_case(path: Path) -> BacktestCase:
    """读 YAML（`.yaml/.yml`）或 JSON 案例文件。"""
    path = Path(path)
    if not path.exists():
        raise BacktestError(f"案例文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return parse_case(data)


# ── 隐藏答案：别名化 + 年份平移 ────────────────────────────────────────


def _alias(text: str, alias_map: Dict[str, str]) -> str:
    for key in sorted(alias_map, key=len, reverse=True):  # 长名优先，避免子串误替换
        text = text.replace(key, alias_map[key])
    return text


def _shift_years_in_text(text: str, shift: int) -> str:
    return _YEAR_RE.sub(lambda m: str(int(m.group(1)) + shift), text)


def anonymize(case: BacktestCase) -> Dict[str, Any]:
    """返回引擎实际看到的视图：别名化并平移后的意图，以及同样处理后的
    真值（名称/别名/年份）。原案例不被修改。"""
    intent = _shift_years_in_text(_alias(case.intent, case.alias_map), case.year_shift)
    truths = []
    for m in case.milestones:
        truths.append({
            "id": m.id,
            "name": _alias(m.name, case.alias_map),
            "aliases": [_alias(a, case.alias_map) for a in m.aliases],
            "year": m.year + case.year_shift,
            "stage": m.stage,
            "requires": list(m.requires),
        })
    return {
        "intent": intent,
        "truths": truths,
        "start_year": case.start_year + case.year_shift,
        "years_per_step": case.years_per_step,
    }


# ── 涌现里程碑的抽取 ─────────────────────────────────────────────────


def _iter_branches(branches: Any):
    if not isinstance(branches, list):
        return
    for b in branches:
        if isinstance(b, dict):
            yield b
            yield from _iter_branches(b.get("children"))
            yield from _iter_branches(b.get("sub_branches"))


def _resolved_branches(causal_lines: Any) -> Dict[Tuple[str, str], Dict[str, Any]]:
    from world_simulator import causal_tree

    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for line in causal_lines or []:
        if not isinstance(line, dict):
            continue
        lid = str(line.get("id") or "")
        tree = line.get("future_tree") or {}
        for b in _iter_branches(tree.get("branches") if isinstance(tree, dict) else None):
            if causal_tree.canonical_status(b.get("status")) == "resolved":
                out[(lid, str(b.get("id") or ""))] = b
    return out


def extract_candidates(
    history: Sequence[Any], causal_lines: Any, *, start_year: float, years_per_step: float
) -> List[Dict[str, Any]]:
    """从历史抽取涌现的里程碑候选，按步数升序：
    - `capabilities_gained` 每项一个候选（带 `maturity_stage`）；
    - 因果树里 `resolved` 的分支：首次出现 resolved 的步数取自
      `dynamic_snapshot` 链（WP0），找不到快照时退到最后一步（保守：不
      编造更早的时点）。
    候选时间 = `start_year + step × years_per_step`（近似，见模块 docstring）。
    """
    candidates: List[Dict[str, Any]] = []

    def add(step: int, text: str, source: str, stage: Optional[str] = None, extra: str = "") -> None:
        candidates.append({
            "step": int(step),
            "time": start_year + int(step) * years_per_step,
            "text": text.strip(),
            "source": source,
            "maturity_stage": stage if stage in _STAGES else None,
            "detail": extra.strip(),
        })

    for state in history:
        step = int(getattr(state, "step", 0) or 0)
        for item in getattr(state, "capabilities_gained", None) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("capability") or "").strip()
            if name:
                add(step, name, "capability", item.get("maturity_stage"),
                    str(item.get("enables") or item.get("description") or ""))

    last_step = int(getattr(history[-1], "step", 0)) if history else 0
    resolved_now = _resolved_branches(causal_lines)
    if resolved_now:
        first_resolved: Dict[Tuple[str, str], int] = {}
        for state in history:
            snap = getattr(state, "dynamic_snapshot", None)
            if isinstance(snap, dict):
                for key in _resolved_branches(snap.get("causal_lines")):
                    first_resolved.setdefault(key, int(getattr(state, "step", 0) or 0))
        for key, branch in resolved_now.items():
            step = first_resolved.get(key, last_step)
            add(step, str(branch.get("description") or branch.get("id") or ""), "tree_resolved",
                None, str(branch.get("semantic_event") or ""))

    candidates.sort(key=lambda c: (c["step"], c["source"], c["text"]))
    for i, c in enumerate(candidates, 1):
        c["id"] = f"c{i}"
    return candidates


# ── 匹配 ─────────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text).lower())


def rule_matcher(candidates: List[Dict[str, Any]], truths: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """规则匹配器：真值名称/别名与候选文本（含 detail）互为子串即命中；
    一对一、按真值顺序、每个真值取最早的未占用候选。零成本、可复现，但只
    认字面，**偏保守**。"""
    used: set = set()
    matches: Dict[str, Optional[str]] = {}
    for truth in truths:
        keys = [k for k in (_norm(truth["name"]), *(_norm(a) for a in truth.get("aliases", []))) if len(k) >= 2]
        chosen = None
        for cand in sorted(candidates, key=lambda c: (c["step"], c["id"])):
            if cand["id"] in used:
                continue
            hay = _norm(cand["text"] + cand.get("detail", ""))
            if hay and any(k in hay or (len(_norm(cand["text"])) >= 2 and _norm(cand["text"]) in k) for k in keys):
                chosen = cand["id"]
                break
        if chosen:
            used.add(chosen)
        matches[truth["id"]] = chosen
    return matches


def make_llm_matcher(cfg, workspace_root: Path) -> Matcher:
    """一次 LLM 语义匹配调用（`workflows/backtest_match.yaml`）。输出会被
    校验：只接受存在的 id，且强制一对一（同一候选被多个真值选中时保留
    第一个）。LLM 偏宽松——与规则匹配器的分数不要混着比。"""

    def matcher(candidates: List[Dict[str, Any]], truths: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
        from mini_agent.workflow.runner import WorkflowRunner
        from mini_agent.workflow.store import WorkflowStore
        from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output

        wf = WorkflowStore(Path(workspace_root)).load("backtest_match")
        if wf is None:
            raise BacktestError(f"找不到 workflow 'backtest_match'（预期 {workspace_root}/workflows/backtest_match.yaml）")
        result = WorkflowRunner(cfg).run(wf, {
            "candidates_json": json.dumps(
                [{"id": c["id"], "text": c["text"], "detail": c.get("detail", "")} for c in candidates],
                ensure_ascii=False),
            "truths_json": json.dumps(
                [{"id": t["id"], "name": t["name"], "aliases": t.get("aliases", [])} for t in truths],
                ensure_ascii=False),
        })
        if result.status != "done":
            raise BacktestError(f"backtest_match workflow 未成功：status={result.status}")
        step = next((sr for sr in result.step_results if sr.step_id == "match"), None)
        if step is None:
            raise BacktestError("backtest_match 没有产出结果")
        try:
            data = extract_agent_json_output(step.output, required_keys=["matches"])
        except AgentStepOutputError as exc:
            raise BacktestError(f"backtest_match 回复无法解析：{exc}") from exc
        return sanitize_matches(data.get("matches"), candidates, truths)

    return matcher


def sanitize_matches(raw: Any, candidates: List[Dict[str, Any]], truths: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """把 LLM 的原始 `matches` 数组规整成 `{truth_id: candidate_id|None}`：
    忽略不存在的 id、强制一对一、缺失的真值补 None。"""
    cand_ids = {c["id"] for c in candidates}
    truth_ids = [t["id"] for t in truths]
    result: Dict[str, Optional[str]] = {tid: None for tid in truth_ids}
    used: set = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("truth_id") or "")
        cid = item.get("candidate_id")
        if tid not in result or result[tid] is not None:
            continue
        if cid is None or str(cid) not in cand_ids or str(cid) in used:
            continue
        result[tid] = str(cid)
        used.add(str(cid))
    return result


def apply_overrides(
    matches: Dict[str, Optional[str]],
    overrides: Dict[str, Optional[str]],
    candidates: List[Dict[str, Any]],
    truths: List[Dict[str, Any]],
) -> Tuple[Dict[str, Optional[str]], List[str]]:
    """用户覆盖：`{truth_id: candidate_id|None}`（None = 明确判定"没有对应
    候选"）。返回 `(新匹配, 实际发生变化的 truth_id 列表)`。未知 id 报错
    而不是静默忽略——覆盖是人的裁定，不该悄悄失效。覆盖会保持一对一：
    被覆盖到的候选若原本属于别的真值，那个真值被置 None。"""
    truth_ids = {t["id"] for t in truths}
    cand_ids = {c["id"] for c in candidates}
    new = dict(matches)
    for tid, cid in overrides.items():
        if tid not in truth_ids:
            raise BacktestError(f"覆盖引用了不存在的真值 id：{tid}")
        if cid is not None and cid not in cand_ids:
            raise BacktestError(f"覆盖引用了不存在的候选 id：{cid}")
    for tid, cid in overrides.items():
        if cid is not None:
            for other, other_cid in new.items():
                if other != tid and other_cid == cid and other not in overrides:
                    new[other] = None
        new[tid] = cid
    changed = [tid for tid in matches if matches[tid] != new.get(tid)]
    return new, changed


# ── 打分 ─────────────────────────────────────────────────────────────


def _kendall_tau_b(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    n = len(pairs)
    if n < 2:
        return None
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = pairs[i][0] - pairs[j][0]
            dy = pairs[i][1] - pairs[j][1]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif (dx > 0) == (dy > 0):
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + ties_x) * (concordant + discordant + ties_y))
    return None if denom == 0 else (concordant - discordant) / denom


def score_run(
    truths: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    matches: Dict[str, Optional[str]],
    *,
    start_year: float,
    horizon_years: float,
) -> Dict[str, Any]:
    """指标全部只看**顺序与间隔**，不看绝对年份。分母为 0 时对应指标为
    `None`（区分"没有数据"和"得 0 分"）。

    - `recall`：范围内（真值年份 ≤ 起点 + 模拟跨度）的真值中被命中的比例；
      `recall_all` 含范围外（对短模拟不公平，仅供参考）。
    - `precision`：候选中被某个真值命中的比例（**下界**）。
    - `kendall_tau`：命中项上"真值时间序 vs 涌现步数序"的 τ-b，±1 = 完全
      一致/相反，<2 个命中时 None。
    - `interval_error`：命中项按真值时间排序，以第一个命中项为原点对齐后，
      各项 (涌现时间差 − 真值时间差) 的平均绝对误差（年）与带符号均值
      （正 = 引擎比真实更慢）。
    - `prerequisite_violations`：前置与后继都命中、且后继涌现步数**早于**
      前置的对数（同步不算违反）。
    - `stage`：真值声明了 `stage` 且候选带 `maturity_stage` 的命中项上，
      阶段完全一致率与平均序数差。
    """
    cand_by_id = {c["id"]: c for c in candidates}
    horizon_end = start_year + horizon_years
    in_horizon = [t for t in truths if t["year"] <= horizon_end]
    matched_all = [(t, cand_by_id[matches[t["id"]]]) for t in truths if matches.get(t["id"]) in cand_by_id]
    matched_in = [(t, c) for t, c in matched_all if t["year"] <= horizon_end]

    matched_cand_ids = {c["id"] for _t, c in matched_all}
    tau = _kendall_tau_b([(t["year"], c["step"]) for t, c in matched_all])

    ordered = sorted(matched_all, key=lambda p: p[0]["year"])
    interval: Dict[str, Any] = {"n": max(len(ordered) - 1, 0), "mean_abs_years": None, "mean_signed_years": None}
    if len(ordered) >= 2:
        t0, c0 = ordered[0]
        errs = [((c["time"] - c0["time"]) - (t["year"] - t0["year"])) for t, c in ordered[1:]]
        interval["mean_abs_years"] = sum(abs(e) for e in errs) / len(errs)
        interval["mean_signed_years"] = sum(errs) / len(errs)

    cand_of = {t["id"]: c for t, c in matched_all}
    checked = violations = 0
    for t in truths:
        for req in t.get("requires", []):
            if t["id"] in cand_of and req in cand_of:
                checked += 1
                if cand_of[t["id"]]["step"] < cand_of[req]["step"]:
                    violations += 1

    stage_pairs = [(t["stage"], c["maturity_stage"]) for t, c in matched_all if t.get("stage") and c.get("maturity_stage")]
    stage: Dict[str, Any] = {"n": len(stage_pairs), "agreement": None, "mean_abs_ordinal_diff": None}
    if stage_pairs:
        ords = [(_STAGES.index(a), _STAGES.index(b)) for a, b in stage_pairs]
        stage["agreement"] = sum(1 for a, b in ords if a == b) / len(ords)
        stage["mean_abs_ordinal_diff"] = sum(abs(a - b) for a, b in ords) / len(ords)

    return {
        "truths_total": len(truths),
        "truths_in_horizon": len(in_horizon),
        "matched": len(matched_all),
        "matched_in_horizon": len(matched_in),
        "recall": (len(matched_in) / len(in_horizon)) if in_horizon else None,
        "recall_all": (len(matched_all) / len(truths)) if truths else None,
        "candidates_total": len(candidates),
        "precision": (len(matched_cand_ids) / len(candidates)) if candidates else None,
        "kendall_tau": tau,
        "interval_error": interval,
        "prerequisite_violations": {"checked": checked, "violations": violations},
        "stage": stage,
    }


# ── 运行 ─────────────────────────────────────────────────────────────


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _record_reality_checks(
    data_dir: Path, sim_id: str, branch: str, last_step: int,
    truths: List[Dict[str, Any]], candidates: List[Dict[str, Any]],
    matches: Dict[str, Optional[str]], only: Optional[Sequence[str]] = None,
    horizon_end: float = float("inf"), model_version: str = "backtest",
) -> int:
    """把真值对账写进该次运行数据目录的 `reality_checks.jsonl`（复用
    `reality_check.record_and_apply`，verdict 沿用既有三选一）。范围外的
    真值不记（引擎没跑到那么远，不是预测错误）。失败静默：对账记录是
    旁路产物，不影响指标本身。返回写入条数。"""
    from world_simulator import reality_check

    cand_by_id = {c["id"]: c for c in candidates}
    written = 0
    for truth in truths:
        if only is not None and truth["id"] not in only:
            continue
        if truth["year"] > horizon_end:
            continue
        cand = cand_by_id.get(matches.get(truth["id"]))
        try:
            if cand is not None:
                reality_check.record_and_apply(
                    data_dir, sim_id, branch=branch, step=cand["step"],
                    predicted_summary=cand["text"], actual_outcome=truth["name"],
                    verdict="matched", model_version=model_version)
            else:
                reality_check.record_and_apply(
                    data_dir, sim_id, branch=branch, step=last_step,
                    predicted_summary="（引擎在这次运行中没有涌现对应的里程碑）",
                    actual_outcome=truth["name"], verdict="diverged", model_version=model_version)
            written += 1
        except Exception:  # noqa: BLE001 — 旁路产物，见 docstring
            continue
    return written


def _default_create(cfg, workspace_root, data_dir, *, template, intent, settings):
    from world_simulator.engine import create_simulation

    return create_simulation(cfg, workspace_root, data_dir, template=template, intent=intent, settings=settings)


def _default_advance(cfg, workspace_root, data_dir, sim_id):
    from world_simulator.engine import advance

    return advance(cfg, workspace_root, data_dir, sim_id)


def run_case(
    case: BacktestCase,
    *,
    cfg: Any,
    workspace_root: Path,
    out_dir: Path,
    matcher: Optional[Matcher] = None,
    steps: Optional[int] = None,
    settings_override: Optional[Dict[str, Any]] = None,
    label: str = "run",
    create_fn: Optional[Callable[..., Any]] = None,
    advance_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """跑一次回测并把结果落盘到 `out_dir/result.json`。

    `create_fn`/`advance_fn` 可注入（测试用桩；默认走真实引擎）。数据目录
    是 `out_dir/data`，与用户真实 `data/` 完全隔离。推进中遇到引擎错误
    （如 LLM 输出反复不合法）时停止并**带着已有历史继续打分**，
    `aborted` 字段记录原因——不因为一步失败丢掉前面 N-1 步的数据。
    """
    from world_simulator.engine.errors import SimEngineError
    from world_simulator.store import SimStore
    from world_simulator import consistency_guard

    matcher = matcher or rule_matcher
    create_fn = create_fn or _default_create
    advance_fn = advance_fn or _default_advance
    n_steps = int(steps or case.steps)
    view = anonymize(case)
    out_dir = Path(out_dir)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    settings = {**case.settings, **(settings_override or {})}
    if case.time_granularity:
        settings.setdefault("time_granularity", case.time_granularity)
    manifest = create_fn(cfg, Path(workspace_root), data_dir,
                         template=case.template, intent=view["intent"], settings=settings)
    sim_id = manifest.sim_id

    store = SimStore.for_root(data_dir, sim_id)
    aborted: Optional[str] = None
    for _ in range(n_steps):
        try:
            advance_fn(cfg, Path(workspace_root), data_dir, sim_id)
        except SimEngineError as exc:
            aborted = f"{type(exc).__name__}: {exc}"
            break
        if store.load_manifest().status == "ended":
            break

    manifest = store.load_manifest()
    branch = manifest.branch or "main"
    history = store.load_history(branch)
    causal_lines = manifest.settings.get("causal_lines")
    last_step = int(history[-1].step) if history else 0
    horizon_years = last_step * case.years_per_step

    candidates = extract_candidates(history, causal_lines,
                                    start_year=view["start_year"], years_per_step=case.years_per_step)
    matches = matcher(candidates, view["truths"])
    metrics = score_run(view["truths"], candidates, matches,
                        start_year=view["start_year"], horizon_years=horizon_years)
    health = consistency_guard.analyze_history(
        history, causal_lines=causal_lines,
        declared_causal_graph=manifest.settings.get("declared_causal_graph"))

    horizon_end = view["start_year"] + horizon_years
    recorded = _record_reality_checks(data_dir, sim_id, branch, last_step, view["truths"],
                                      candidates, matches, horizon_end=horizon_end)
    result = {
        "run_id": uuid.uuid4().hex[:10],
        "label": label,
        "case_id": case.id,
        "case_title": case.title,
        "case_verified": case.verified,
        "case_disclaimer": case.disclaimer,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sim_id": sim_id,
        "branch": branch,
        "steps_requested": n_steps,
        "steps_run": last_step,
        "aborted": aborted,
        "settings_override": copy.deepcopy(settings_override or {}),
        "matcher": getattr(matcher, "__name__", "custom"),
        "engine_intent": view["intent"],
        "start_year": view["start_year"],
        "years_per_step": case.years_per_step,
        "truths": view["truths"],
        "candidates": candidates,
        "matches": matches,
        "overrides": {},
        "metrics": metrics,
        "structural_health": {
            "warning_counts": health["warning_counts"],
            "coverage": health["coverage"],
            "c5_event_density": health["c5_event_density"],
        },
        "reality_checks_written": recorded,
        "caveats": list(CAVEATS),
    }
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def rescore(result_path: Path, overrides: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """应用用户覆盖并重算指标：读 `result.json`、改匹配、重打分、回写，
    并把**发生变化的**真值追加进该次运行的 `reality_checks.jsonl`（标记
    `model_version="backtest-override"`，与自动对账记录区分；只追加、不
    删旧记录——历史上的自动判定仍可追溯）。覆盖累积在 `overrides` 字段。"""
    result_path = Path(result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    truths, candidates = result["truths"], result["candidates"]
    new_matches, changed = apply_overrides(result["matches"], overrides, candidates, truths)
    horizon_years = result["steps_run"] * result["years_per_step"]
    result["matches"] = new_matches
    result["overrides"] = {**result.get("overrides", {}), **overrides}
    result["matcher"] = f"{result.get('matcher', 'custom')}+user_override"
    result["metrics"] = score_run(truths, candidates, new_matches,
                                  start_year=result["start_year"], horizon_years=horizon_years)
    if changed:
        _record_reality_checks(
            result_path.parent / "data", result["sim_id"], result["branch"], result["steps_run"],
            truths, candidates, new_matches, only=changed,
            horizon_end=result["start_year"] + horizon_years, model_version="backtest-override")
    result["overridden_truths"] = sorted(result["overrides"])
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ── A/B ──────────────────────────────────────────────────────────────

_METRIC_PATHS: Dict[str, Tuple[str, ...]] = {
    "recall": ("recall",),
    "precision": ("precision",),
    "kendall_tau": ("kendall_tau",),
    "interval_mean_abs_years": ("interval_error", "mean_abs_years"),
    "prerequisite_violations": ("prerequisite_violations", "violations"),
    "stage_agreement": ("stage", "agreement"),
}


def _dig(d: Dict[str, Any], path: Tuple[str, ...]) -> Optional[float]:
    for key in path:
        d = d.get(key) if isinstance(d, dict) else None
        if d is None:
            return None
    return d


def summarize_metric(values: Sequence[Optional[float]]) -> Dict[str, Any]:
    """一组重复运行的某项指标分布。`None`（无数据）不参与统计，只计入
    `n_missing`；n < 5 时 `enough_samples=False`——报告据此写"不足以下结论"。"""
    real = [float(v) for v in values if v is not None]
    return {
        "n": len(real),
        "n_missing": len(values) - len(real),
        "mean": statistics.fmean(real) if real else None,
        "stdev": statistics.stdev(real) if len(real) >= 2 else None,
        "min": min(real) if real else None,
        "max": max(real) if real else None,
        "values": real,
        "enough_samples": len(real) >= 5,
    }


def compare_arms(arm_results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """`{arm名: [run result, ...]}` → 各臂各指标的分布，以及（恰好两臂时）
    均值差 `second - first`。只给分布与差值，**不做显著性检验**。"""
    per_arm: Dict[str, Dict[str, Any]] = {}
    for arm, runs in arm_results.items():
        per_arm[arm] = {
            name: summarize_metric([_dig(r["metrics"], path) for r in runs])
            for name, path in _METRIC_PATHS.items()
        }
        per_arm[arm]["_runs"] = len(runs)
        per_arm[arm]["_aborted_runs"] = sum(1 for r in runs if r.get("aborted"))
    out: Dict[str, Any] = {"arms": per_arm, "caveats": list(CAVEATS)}
    arms = list(arm_results)
    if len(arms) == 2:
        a, b = arms
        diff = {}
        for name in _METRIC_PATHS:
            ma, mb = per_arm[a][name]["mean"], per_arm[b][name]["mean"]
            diff[name] = (mb - ma) if (ma is not None and mb is not None) else None
        out["mean_difference"] = {"from": a, "to": b, "by_metric": diff}
    small = [f"{arm}.{name}" for arm, stats in per_arm.items() for name, s in stats.items()
             if not name.startswith("_") and s["n"] < 5]
    out["insufficient_samples"] = small
    out["verdict_note"] = (
        "部分指标样本数 < 5，不足以下结论，只能当作探索性观察。" if small
        else "样本数达到 5，但未做显著性检验；差异需结合标准差判断。"
    )
    return out


def run_ab(
    case: BacktestCase,
    *,
    cfg: Any,
    workspace_root: Path,
    out_root: Path,
    arms: Dict[str, Dict[str, Any]],
    repeats: int,
    matcher: Optional[Matcher] = None,
    steps: Optional[int] = None,
    create_fn: Optional[Callable[..., Any]] = None,
    advance_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """同一案例、每个臂（`{名称: settings 覆盖}`）各重复 `repeats` 次，
    汇总指标分布并落盘 `out_root/ab_report.json`。臂与重复按顺序串行跑
    （每次都是真实 LLM 调用，成本 = 臂数 × 重复数 × 步数）。"""
    if repeats < 1:
        raise BacktestError("repeats 必须 >= 1")
    if not arms:
        raise BacktestError("至少需要一个臂")
    out_root = Path(out_root)
    results: Dict[str, List[Dict[str, Any]]] = {}
    for arm, override in arms.items():
        results[arm] = []
        for i in range(repeats):
            results[arm].append(run_case(
                case, cfg=cfg, workspace_root=workspace_root,
                out_dir=out_root / f"{arm}_{i + 1}", matcher=matcher, steps=steps,
                settings_override=override, label=f"{arm}#{i + 1}",
                create_fn=create_fn, advance_fn=advance_fn))
    report = compare_arms(results)
    report.update({
        "case_id": case.id, "case_verified": case.verified, "repeats": repeats,
        "arms_settings": copy.deepcopy(arms),
        "matcher": getattr(matcher or rule_matcher, "__name__", "custom"),
        "run_dirs": {arm: [str(out_root / f"{arm}_{i + 1}") for i in range(repeats)] for arm in arms},
    })
    (out_root / "ab_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def default_out_dir(reports_dir: Path, case_id: str, label: str = "run") -> Path:
    """`reports/backtest/<case_id>/<UTC 时间戳>-<label>/`。"""
    return Path(reports_dir) / "backtest" / case_id / f"{_now_stamp()}-{label}"
