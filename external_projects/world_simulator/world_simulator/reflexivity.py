"""world_simulator/reflexivity.py — 反身性（Reflexivity）最小诠释
（阶段三十六第四批，`next_doc/world_simulator_event_driven_engine_
and_full_architecture_plan.md` 2.4 节）。

参考文档设想的"反身性"（模拟预测本身传播到现实世界、影响现实中人的
行为，从而反过来验证或推翻预测）在"本地单机、单用户"的工具形态下
没有对应的现实场景可以承载——本模块**不实现**那个完整设想。

这里只做范围极小的个人反身性诠释：用户看到某次模拟的复盘结论后，
如果之后创建新模拟/继续推进已有模拟时的选择模式，与复盘报告指出的
"败因/建议"方向一致（比如复盘说"过度冒险是主要败因"，用户后续的
选择明显更保守），就在跨模拟知识库里对应的知识条目上追加一条
`[stated]` 风格的标注——**只记录一个观察到的相关性，不断言因果**，
也不用于任何自动化的行为调整（不会因为记录了"用户变保守了"就自动
调整后续 prompt 的策略画像或建议倾向，那是 Agent Preview 反馈闭环
的范畴，不是本模块该做的事）。

范围克制：
- 不做任何基于这个标注的自动化行为。
- 不做跨模拟、跨用户的反身性统计。
- "是否一致"的判断只用 `ChoiceOption.risk_level`（low/medium/high，
  已经存在的字段）算一个粗粒度的风险均值对比，不引入任何新的语义
  理解模型——足够验证"记录到了、有没有用"这个最基本的闭环。

纯旁路操作：调用方（`engine.advance()`）应该用安全包装（同
`engine/knowledge.py::_safe_record_causal_links` 的既有约定）调用
本模块，任何异常都不应该影响本次推进本身。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from world_simulator import knowledge_base, retrospective

# 粗粒度风险打分：仅用于比较"之前"和"之后"选择的平均风险高低，
# 不代表任何精确的量化含义。
_RISK_SCORE = {"low": 1, "medium": 2, "high": 3}

# 判断复盘报告的建议方向所用的关键词——同 `knowledge_base.py` 一贯的
# "关键词重叠，不引入语义模型"取舍。落在 `_REDUCE_RISK_KEYWORDS`
# 里的词出现在复盘的 `lessons`/`what_to_reflect_on` 文本里，意味着
# "建议后续更保守/减少冒险"；`_INCREASE_RISK_KEYWORDS` 相反。
_REDUCE_RISK_KEYWORDS = ("冒险", "激进", "孤注一掷", "过度扩张", "鲁莽", "太快")
_INCREASE_RISK_KEYWORDS = ("过于保守", "太保守", "犹豫", "错失", "畏缩", "太谨慎")

# 判断"是否发生了方向一致的变化"所需要的最小样本数——只有一两次选择
# 不足以说明是"选择模式"的变化，容易把偶然波动误判为一致。
_MIN_SAMPLES = 2
# 判断"变化足够明显"的均值差阈值（1~3 分的量表上）。
_MIN_SHIFT = 0.5


def _extract_text(report: retrospective.RetrospectiveReport) -> str:
    parts: List[str] = []
    for item in report.lessons:
        parts.append(str(item.get("point") or item.get("lesson") or ""))
    for item in report.what_to_reflect_on:
        parts.append(str(item.get("point") or ""))
    return " ".join(p for p in parts if p)


def detect_suggested_direction(report: retrospective.RetrospectiveReport) -> Optional[str]:
    """从复盘报告的 `lessons`/`what_to_reflect_on` 文本里，用关键词判断
    这份复盘是不是在建议"后续更保守"或"后续更敢冒险"。

    Returns:
        `"reduce_risk"` / `"increase_risk"` / `None`（判断不出明确方向，
        比如两类关键词都没出现，或者同时出现——不勉强给结论）。
    """
    text = _extract_text(report)
    if not text:
        return None
    reduce_hit = any(kw in text for kw in _REDUCE_RISK_KEYWORDS)
    increase_hit = any(kw in text for kw in _INCREASE_RISK_KEYWORDS)
    if reduce_hit and not increase_hit:
        return "reduce_risk"
    if increase_hit and not reduce_hit:
        return "increase_risk"
    return None


def _risk_scores_for_range(
    history: list, *, after_step: int = -1, up_to_step: Optional[int] = None
) -> List[int]:
    """收集步数落在 `(after_step, up_to_step]` 区间内（`up_to_step`
    为 `None` 表示不设上界）的每一步 `chosen_option_id` 对应的
    `risk_level` 打分（读取*上一步* `options` 里那个被选中的选项，
    因为 `risk_level` 声明在提出选项的那一步，不在选择结果落盘的那一
    步）。找不到对应选项、或该选项没声明 `risk_level` 的步骤跳过，不
    计入样本。"""
    scores: List[int] = []
    by_step = {s.step: s for s in history}
    for state in history:
        if state.step <= after_step:
            continue
        if up_to_step is not None and state.step > up_to_step:
            continue
        if not state.chosen_option_id:
            continue
        prev = by_step.get(state.step - 1)
        if prev is None:
            continue
        option = next(
            (o for o in (prev.options or []) if o.id == state.chosen_option_id), None
        )
        if option is None or not option.risk_level:
            continue
        score = _RISK_SCORE.get(option.risk_level)
        if score is not None:
            scores.append(score)
    return scores


def evaluate_and_annotate(
    data_dir: Path,
    sim_id: str,
    *,
    branch: Optional[str] = None,
) -> Optional[str]:
    """检查这个分支最近一次尚未处理过的复盘报告，判断复盘之后的选择
    模式是否和复盘建议的方向一致；一致则在知识库里追加一条观察标注，
    并把这份复盘标记为"已处理"（避免同一份复盘反复触发标注）。

    Returns:
        实际写入的标注文本；判断不一致、样本不足、或没有待处理的复盘
        报告时返回 `None`，不追加任何标注。
    """
    from world_simulator.store import SimNotFoundError, SimStore

    try:
        store = SimStore.for_root(data_dir, sim_id)
        manifest = store.load_manifest()
    except SimNotFoundError:
        return None
    branch = branch or manifest.branch or "main"

    records = retrospective.load_for_branch(data_dir, sim_id, branch)
    pending = [r for r in records if not r.reflexivity_annotated]
    if not pending:
        return None
    # 只处理最新一份待处理的复盘——更旧的几份如果之前判断过"样本还不
    # 够"，等下次有更多历史后，仍然会被最新一次调用重新纳入 pending
    # （因为还没被标记为 annotated），不会被这里的"只看最新一份"漏掉。
    record = pending[0]

    direction = detect_suggested_direction(record.report)
    if direction is None:
        retrospective.mark_reflexivity_annotated(data_dir, sim_id, record.id)
        return None

    history = store.load_history(branch)
    after_scores = _risk_scores_for_range(history, after_step=record.up_to_step)
    if len(after_scores) < _MIN_SAMPLES:
        return None  # 样本还不够，暂不下结论，也不标记为已处理，留给下次调用

    # "之前"取复盘覆盖范围内的选择（第 1 步到 up_to_step，含）。
    before_scores = _risk_scores_for_range(history, up_to_step=record.up_to_step)

    after_avg = sum(after_scores) / len(after_scores)
    consistent = False
    if before_scores:
        before_avg = sum(before_scores) / len(before_scores)
        if direction == "reduce_risk" and (before_avg - after_avg) >= _MIN_SHIFT:
            consistent = True
        elif direction == "increase_risk" and (after_avg - before_avg) >= _MIN_SHIFT:
            consistent = True
    else:
        # 没有"之前"的样本可比较时，退化为看"之后"的绝对水平是否明显
        # 偏向建议方向（比如全是 low，或全是 high）。
        if direction == "reduce_risk" and after_avg <= 1.5:
            consistent = True
        elif direction == "increase_risk" and after_avg >= 2.5:
            consistent = True

    retrospective.mark_reflexivity_annotated(data_dir, sim_id, record.id)
    if not consistent:
        return None

    note = (
        "用户在看到这条复盘后，后续表现出更保守的选择倾向"
        if direction == "reduce_risk"
        else "用户在看到这条复盘后，后续表现出更敢于冒险的选择倾向"
    )
    query_text = _extract_text(record.report)
    annotated = knowledge_base.annotate_reflexivity_observation(
        data_dir, query_text=query_text, note=note
    )
    return note if annotated else None
