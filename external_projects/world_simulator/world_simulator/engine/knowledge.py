"""world_simulator/engine/knowledge.py — 跨模拟知识库的安全包装。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from pathlib import Path

from world_simulator import knowledge_base, reflexivity, tree_effects, tree_grounding


def _safe_suggest_knowledge(data_dir: Path, query_text: str, *, template: str) -> str:
    """`knowledge_base.suggest_for_prompt()` 的安全包装（阶段二十，4.12
    节 3.）：检索是"锦上添花"的旁路信息，任何异常（比如知识库文件被
    手工改坏）都不应该让 `generate_scenario`/`advance()` 的核心链路
    失败，退化为"没有可参考的知识"即可，不向上抛出。
    """
    try:
        return knowledge_base.suggest_for_prompt(data_dir, query_text, template=template)
    except Exception:
        return "（暂无相关的已知因果知识）"


def _safe_record_causal_links(
    data_dir: Path, *, sim_id: str, template: str, causal_links: list, step: int = None
) -> None:
    """`knowledge_base.record_causal_links()` 的安全包装（阶段二十，
    4.12 节 2.）：写入知识库是这一步推进落盘*之后*的旁路操作，失败
    不应该让本次推进本身失败（`advance()` 的返回值/落盘结果已经产生），
    这里吞掉异常，只保留"尽力而为"的语义。

    `step` 为第八轮批次一新增的可选参数，透传给 `record_causal_
    links()` 用于拼出 `evidence` 来源引用。
    """
    try:
        knowledge_base.record_causal_links(
            data_dir, sim_id=sim_id, template=template, causal_links=causal_links, step=step
        )
    except Exception:
        pass


def _safe_record_edge_outcomes(
    data_dir: Path, *, sim_id: str, template: str, branch: str, step: int, outcomes: list
) -> None:
    """`knowledge_base.record_edge_outcomes()` 的安全包装（第二十二轮 P5b）：把因果引擎
    声明边的兑现结论回写知识库。和 `_safe_record_causal_links` 一样是推进落盘*之后*的旁路
    操作，失败不应该让本次推进失败，吞掉异常、尽力而为。"""
    if not outcomes:
        return
    try:
        knowledge_base.record_edge_outcomes(
            data_dir, sim_id=sim_id, template=template, branch=branch, step=step, outcomes=outcomes
        )
    except Exception:
        pass


def _own_after(store, branch: str):
    """分叉出来的分支，分叉点及之前的历史是从源分支拷贝来的；跨实例写入只统计分支**自己产生**的
    事件（`step > from_step`），否则同一事件会被源分支与分叉分支各写一遍。返回 `(可写, 起点)`：
    主线 `(True, None)`（全部都是自己的）；分叉分支取 `branch_meta.from_step`；取不到
    （旧数据/元信息缺失）返回 `(False, None)`——**宁可不写也不重复计数**。"""
    if branch == "main":
        return True, None
    try:
        value = (store.load_branch_meta(branch) or {}).get("from_step")
    except Exception:  # noqa: BLE001
        return False, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    return True, int(value)


def _safe_record_p9_stats(data_dir: Path, *, store, manifest, branch: str) -> None:
    """P9：把 likelihood 校准账本与树声明兑现统计写入跨实例知识库（见
    `knowledge_base.record_likelihood_calibration` / `record_tree_declaration_stats`）。

    推进落盘**之后**的旁路操作，两路各自独立兜底（一路出错不影响另一路，更不影响本次推进）。
    各自的开关/前置条件见 `tree_grounding.kb_calibration_enabled`、`tree_effects.kb_writeback_enabled`；
    都不满足时什么都不做、也不读历史。`advance()` 与 `advance_lines()` 共用本函数。
    """
    settings = getattr(manifest, "settings", None) or {}
    want_cal = tree_grounding.kb_calibration_enabled(settings)
    want_tree = tree_effects.kb_writeback_enabled(settings)
    if not (want_cal or want_tree):
        return
    try:
        ok, own_after = _own_after(store, branch)
        if not ok:
            return
        history = store.load_history(branch)
        min_samples = knowledge_base.clean_min_samples(settings.get("kb_min_samples"))
    except Exception:  # noqa: BLE001
        return
    common = dict(sim_id=manifest.sim_id, template=manifest.template, branch=branch, min_samples=min_samples)
    if want_cal:
        try:
            knowledge_base.record_likelihood_calibration(
                data_dir, tiers=tree_grounding.kb_calibration_tiers(history, own_after=own_after), **common
            )
        except Exception:  # noqa: BLE001
            pass
    if want_tree:
        try:
            knowledge_base.record_tree_declaration_stats(
                data_dir, stats=tree_effects.kb_stats(settings, history, own_after=own_after), **common
            )
        except Exception:  # noqa: BLE001
            pass


def _safe_evaluate_reflexivity(data_dir: Path, sim_id: str, *, branch: str) -> None:
    """`reflexivity.evaluate_and_annotate()` 的安全包装（阶段三十六第
    四批，2.4 节）：反身性检查是这一步推进落盘*之后*的旁路观察，失败
    不应该让本次推进本身失败，这里吞掉异常，只保留"尽力而为"的语义，
    同 `_safe_record_causal_links` 的既有约定。
    """
    try:
        reflexivity.evaluate_and_annotate(data_dir, sim_id, branch=branch)
    except Exception:
        pass
