"""world_simulator/hypothesis.py — Hypothesis Engine（阶段二十一）

设计依据：`next_doc/world_simulator_toward_universal_simulator_plan.md`
4.15 节。把"当前模拟里哪个变量的不确定性最大、值得单独分叉出几个
世界看"这件事从"用户手动指定对比方案"往前推一步——系统先给出建议，
用户点一下确认之后再真正分叉/推进，分叉动作本身**不自动触发**（见
4.15 节"范围克制"）。

三个函数对应"识别 → 分叉推进 → 判断稳健性"这条链路，刻意都是纯组合：
- `suggest_critical_uncertainties()`：只读 `current_state.
  uncertain_fields`/`causal_links`，不发起任何 LLM 调用、不落盘。
- `run_hypothesis_worlds()`：**没有新增任何分叉/推进机制**，完全
  委托给已有的 `autopilot.run_comparison_experiment()`——每个假设
  就是一份"只有一条 principle"的自动挡策略画像，"分叉出来跑几步"
  这件事这个函数已经做得很稳（阶段十就在用），没有理由另起一套。
  这也是设计文档 4.15 节"涉及文件"一段里"优先选后者，减少对已有
  稳定接口的改动"这个取舍的直接体现——不新增 `hypothesis_override`
  这种要橫跨分支保持一致性的 `manifest.settings` 字段（`settings`
  是整个实例共享的，不是按分支隔离的，用它承载"每条假设分支各自
  不同的锚定"反而会在分支之间互相污染），改用 `autopilot.py` 已经
  验证过的"每条分支各自独立一份 `pilot_config.json`"机制。
- `find_robust_outcomes()`：复用阶段十的 `analysis.
  aggregate_field_stats()` 算完统计摘要后，套一层简单阈值判断
  "算不算稳健"，不引入任何新的统计方法。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator import analysis
from world_simulator.state_model import SimManifest, SimState


def suggest_critical_uncertainties(
    manifest: SimManifest, current_state: SimState
) -> List[Dict[str, Any]]:
    """从当前状态的 `uncertain_fields`（阶段十一）里挑出"值得分叉出几个
    世界看看"的候选字段（4.15 节方案第 1 条）。

    只挑 `confidence == "low"` 的字段——`confidence: low` 本身就是
    "高不确定性"的现成信号（阶段十一已有的标注机制），不重新设计一套
    识别逻辑；`medium`/`high` 的字段不够"关键"，不建议为它们专门分叉
    （用户仍然可以在 UI 里手动选择任意字段跑对比实验，这里只是
    "系统主动建议"这一步的范围）。

    "是否是多条因果链的共同起点"直接复用 `current_state.causal_links`
    里 `affected_fields` 的出现次数计数——只看**当前这一步**的因果链，
    不追溯整个历史（`suggest_critical_uncertainties(manifest,
    current_state)` 的签名故意只传当前状态，不需要调用方额外传一份
    历史列表进来；如果某个字段在更早的步骤里也频繁出现，用户从建议
    列表点开分叉之后，`run_hypothesis_worlds()` 跑出来的分支本身就会
    继续体现这条因果链，不需要在"建议"这一步就做历史级别的统计）。

    Args:
        manifest: 当前保留这个参数是为了和 4.15 节的函数签名一致、
            为未来可能用到 `manifest.settings`（比如 `resource_fields`/
            `objectives`）里的取值范围信息留出扩展空间；当前实现
            没有用到它，`current_state` 已经包含判断所需的一切。

    Returns:
        候选列表，每项 `{"field", "confidence", "why", "note"}`，按
        "是多少条因果链的共同起点"降序排列（并列时保留
        `uncertain_fields` 原有顺序，`sort` 是稳定排序）。没有任何
        `confidence: low` 的字段时返回空列表。
    """
    del manifest  # 当前实现未使用，见上方 docstring 的扩展空间说明

    uncertain_fields = getattr(current_state, "uncertain_fields", None) or []
    causal_links = getattr(current_state, "causal_links", None) or []

    affected_counts: Counter = Counter()
    for link in causal_links:
        if not isinstance(link, dict):
            continue
        for f in link.get("affected_fields") or []:
            affected_counts[str(f)] += 1

    results: List[Dict[str, Any]] = []
    for item in uncertain_fields:
        if not isinstance(item, dict):
            continue
        if str(item.get("confidence") or "").strip() != "low":
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        count = affected_counts.get(field, 0)
        why_parts = ["该字段被标注为低置信度估计"]
        if count > 1:
            why_parts.append(f"且此前 causal_links 显示它是 {count} 条因果链的共同起点")
        results.append(
            {
                "field": field,
                "confidence": "low",
                "why": "，".join(why_parts),
                "note": str(item.get("note") or ""),
            }
        )

    results.sort(key=lambda r: affected_counts.get(r["field"], 0), reverse=True)
    return results


@dataclass
class HypothesisWorldResult:
    """某一个假设分叉出来的世界跑完之后的结果摘要，字段含义同
    `autopilot.ExperimentBranchResult`（本质就是它的一份重命名视图，
    见 `run_hypothesis_worlds()` 的实现说明）。"""

    hypothesis: Dict[str, Any]
    branch: str
    steps_done: int
    ended_early: bool
    error: Optional[str] = None
    final_vars: Optional[Dict[str, Any]] = None


def run_hypothesis_worlds(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    field: str,
    hypotheses: List[Dict[str, Any]],
    steps: int = 3,
    source_branch: Optional[str] = None,
    from_step: Optional[int] = None,
) -> List[HypothesisWorldResult]:
    """为每个假设分叉一条分支，把假设锚定成一条自动挡"原则"，各自独立
    推进 `steps` 步（4.15 节方案第 2 条）。

    完全委托给 `autopilot.run_comparison_experiment()`：每个假设
    `{"assumption": "快速下降", "why": "...(可选)"}` 转成一份只有一条
    `principles` 的策略画像，"锚定假设方向"这件事交给 `advance_step`
    workflow 已有的 `decision_context` 机制去理解（`decision_context`
    非空时 skill 会\"从候选分支选项里挑一个最符合给定原则的选项\"，
    这条原则里明确写了"这一步推进请保持这个假设一致"，对 `next_vars`
    里 `field` 相关字段的走向也是一种强指导，但**不是代码层面的强制
    约束**——LLM 有没有严格遵守，最终要靠 `find_robust_outcomes()`
    跑完之后回头检查该字段的分布是否真的呈现出假设描述的方向）。

    Args:
        field: 这批假设共同针对的字段路径（仅用于拼进策略画像的原则
            文本，帮助 skill 理解"哪个字段"，不做任何代码层面的字段
            校验——`vars` 对引擎而言始终是不透明的自由 JSON，同其它
            字段路径类设置的一贯设计）。
        hypotheses: 假设列表，每项至少要有 `assumption`（或退化用
            `label`）说明这个假设的具体方向，比如"快速下降"/"缓慢
            下降"/"基本不变"；可选 `why` 补充一句理由，会原样拼进
            提示文本。缺少 `assumption`/`label` 的条目会被跳过，
            对应结果项 `error` 说明原因，不中断其它假设的执行。
        source_branch/from_step: 从哪条分支的第几步开始分叉，所有
            假设共享同一个起点（这样"稳健结果"的比较才有意义）；都为
            None（默认）时取"当前活跃分支的当前步"。

    Returns:
        每个假设对应一条 `HypothesisWorldResult`，顺序与 `hypotheses`
        一致（跳过的条目 `branch="?"`、`final_vars=None`）。跑完后
        会把活跃分支切回调用前的那一条（同
        `run_comparison_experiment()` 的取舍），不会把用户留在某条
        假设分支上。
    """
    from world_simulator.autopilot import run_comparison_experiment
    from world_simulator.engine import get_simulation

    manifest, current, _history = get_simulation(data_dir, sim_id)
    resolved_source_branch = source_branch or manifest.branch
    resolved_from_step = current.step if from_step is None else from_step

    profiles: List[Dict[str, Any]] = []
    skipped: List[int] = []
    for idx, h in enumerate(hypotheses):
        assumption = str(h.get("assumption") or h.get("label") or "").strip()
        if not assumption:
            skipped.append(idx)
            continue
        why = str(h.get("why") or "").strip()
        principle = (
            f"假设「{field}」按「{assumption}」方向发展"
            + (f"（{why}）" if why else "")
            + "。之后每一步推进请保持这个假设一致，除非情境明显要求调整；"
            "这条假设只约束这一个字段的发展方向，其它候选选项仍按常理"
            "正常判断，不需要为了凑这条假设而扭曲其它内容。"
        )
        profiles.append(
            {
                "name": assumption,
                "principles": [principle],
                "risk_preference": "balanced",
                "review_mode": "silent",
                "allow_custom_options": False,
            }
        )

    branch_results = run_comparison_experiment(
        cfg, workspace_root, data_dir, sim_id,
        source_branch=resolved_source_branch, from_step=resolved_from_step,
        steps=steps, profiles=profiles,
    )

    results: List[HypothesisWorldResult] = []
    branch_iter = iter(branch_results)
    for idx, h in enumerate(hypotheses):
        if idx in skipped:
            results.append(
                HypothesisWorldResult(
                    hypothesis=h, branch="?", steps_done=0, ended_early=True,
                    error="假设缺少 assumption/label 字段，已跳过", final_vars=None,
                )
            )
            continue
        r = next(branch_iter)
        results.append(
            HypothesisWorldResult(
                hypothesis=h, branch=r.branch, steps_done=r.steps_done,
                ended_early=r.ended_early, error=r.error, final_vars=r.final_vars,
            )
        )
    return results


def find_robust_outcomes(
    data_dir: Path, sim_id: str, branch_ids: List[str], fields: List[str]
) -> Dict[str, Any]:
    """对比多条假设分支在给定结果字段上的分布，区分"稳健结果"（跨所有
    分支方向/量级基本一致）和"分歧结果"（4.15 节方案第 3 条）。

    直接复用 `analysis.aggregate_field_stats()` 算统计摘要，这里只加
    一层简单阈值判断：
    - 数值型字段：`count >= 2` 且相对标准差（`stdev / |mean|`）小于
      `0.3` 判定为稳健（阈值是经验取值，不是理论推导——这里要的是
      "跑几个假设世界，粗略看出哪些结论站得住"，不是精确统计检验）；
      `mean == 0` 时相对标准差没有意义，退化判定为"分歧"（保守处理，
      避免除零）。
    - 枚举型字段：出现频率最高的取值占比达到 `0.7` 及以上判定为
      稳健（"多数假设世界都导向同一个结果"）。
    - `missing`（所有分支都取不到这个字段的值）：不计入任何一类，
      不强行归到"分歧"里污染统计——`missing` 表达的是"这个字段路径
      本身可能写错了/不适用"，不是"分歧"。

    Args:
        branch_ids: 要参与对比的分支 id 列表（通常是
            `run_hypothesis_worlds()` 结果里每条 `HypothesisWorldResult
            .branch`，调用方自己过滤掉 `branch == "?"` 的跳过项）；
            读不到当前状态的分支（id 写错/已被删除）直接跳过，不报错
            中断整个对比。
        fields: 要检查的结果字段路径列表（同 `resource_fields` 的
            路径格式）。**这里放宽为列表**（4.15 节原始签名是单个
            `field`）：一次假设分叉通常想同时看好几个下游结果字段
            有没有跟着分化，逐个字段单独调用一次这个函数没有额外
            价值，直接一次性给出所有字段的判断更实用。

    Returns:
        `{"stats": List[FieldStats], "robust_fields": List[str],
        "fragile_fields": List[str]}`——`stats` 保留完整统计摘要供
        界面展示具体数值，`robust_fields`/`fragile_fields` 是摘要
        判断，两者按 `fields` 的顺序划分（不含 `missing` 的字段）。
    """
    from world_simulator.store import SimStore

    store = SimStore.for_root(data_dir, sim_id)
    vars_list: List[Dict[str, Any]] = []
    for branch_id in branch_ids:
        try:
            state = store.load_current_state(branch_id)
        except Exception:  # noqa: BLE001 — 单条分支读取失败不该拖垮整体对比
            state = None
        if state is not None:
            vars_list.append(state.vars)

    stats = analysis.aggregate_field_stats(vars_list, fields)
    robust_fields: List[str] = []
    fragile_fields: List[str] = []
    for s in stats:
        if s.kind == "numeric":
            if s.count >= 2 and s.mean not in (None, 0) and abs((s.stdev or 0.0) / s.mean) < 0.3:
                robust_fields.append(s.field)
            else:
                fragile_fields.append(s.field)
        elif s.kind == "categorical":
            if s.distribution and s.count > 0 and max(s.distribution.values()) / s.count >= 0.7:
                robust_fields.append(s.field)
            else:
                fragile_fields.append(s.field)
        # kind == "missing"：既不算稳健也不算分歧，见上方 docstring

    return {"stats": stats, "robust_fields": robust_fields, "fragile_fields": fragile_fields}
