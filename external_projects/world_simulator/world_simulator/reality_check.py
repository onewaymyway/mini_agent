"""world_simulator/reality_check.py — Reality Loop 完整版（阶段二十四）

设计依据：`next_doc/world_simulator_toward_universal_simulator_plan.md`
4.16 节。阶段十八落地的 `calibration_notes` 只是一段静态文本，原样拼进
prompt，模拟结束后不会跟"后来真实发生了什么"做任何对比。本模块补上
"记录预测 → 事后回填真实结果 → 反过来影响知识库可信度"这个最小但
完整的闭环。

范围克制（对照 4.16 节"范围克制"一段）：
- 不做自动数据抓取（股票价格、经济指标之类），`actual_outcome` 完全
  由用户手动填写。
- 不做自动语义匹配判定 `verdict`——`matched`/`partially_matched`/
  `diverged` 由用户自己选，避免一个不成熟的自动匹配算法给出看似
  客观实则不可靠的判断。
- 只做"手动记录 + 反向影响知识库可信度"这一步，不做参考文档第四十七
  节要求的完整版自动数据源接入。

落盘位置：`data/<sim_id>/reality_checks.jsonl`，与 `manifest.json`/
`state_history.jsonl` 平级（不按分支拆分——一次预测记录本身已经
带了 `branch` 字段，同一个实例下的记录不多，不需要为此再拆文件）。
复用 `store.py` 里已经验证过的 `atomic_write_json`/`atomic_write_jsonl`
降级导入方式，保持"没装 mini_agent 环境也能独立跑"的既有约定。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mini_agent.utils.atomic_write import atomic_write_jsonl
except ImportError:  # 独立运行、未装 mini_agent 时的降级实现，同 store.py

    def atomic_write_jsonl(path: Path, records: list) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
            + ("\n" if records else ""),
            encoding="utf-8",
        )


_VALID_VERDICTS = ("matched", "partially_matched", "diverged")


class RealityCheckError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class RealityCheck:
    """一条"模拟预测 vs 后续真实结果"的对比记录（4.16 节 1. 的字段集）。"""

    id: str
    sim_id: str
    branch: str
    step: int
    predicted_summary: str
    predicted_at: str
    actual_outcome: str = ""
    recorded_at: str = ""
    verdict: str = ""  # matched / partially_matched / diverged，未回填为空字符串

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sim_id": self.sim_id,
            "branch": self.branch,
            "step": self.step,
            "predicted_summary": self.predicted_summary,
            "predicted_at": self.predicted_at,
            "actual_outcome": self.actual_outcome,
            "recorded_at": self.recorded_at,
            "verdict": self.verdict,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RealityCheck":
        verdict = str(data.get("verdict") or "")
        if verdict and verdict not in _VALID_VERDICTS:
            verdict = ""
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            sim_id=str(data.get("sim_id") or ""),
            branch=str(data.get("branch") or "main"),
            step=int(data.get("step") or 0),
            predicted_summary=str(data.get("predicted_summary") or ""),
            predicted_at=str(data.get("predicted_at") or ""),
            actual_outcome=str(data.get("actual_outcome") or ""),
            recorded_at=str(data.get("recorded_at") or ""),
            verdict=verdict,
        )


# ── 落盘路径与读写 ───────────────────────────────────────────────────


def reality_checks_path(data_dir: Path, sim_id: str) -> Path:
    return Path(data_dir) / sim_id / "reality_checks.jsonl"


def load_all(data_dir: Path, sim_id: str) -> List[RealityCheck]:
    path = reality_checks_path(data_dir, sim_id)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(RealityCheck.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # 单行损坏不应该让整个记录不可用，跳过即可
    return items


def _save_all(data_dir: Path, sim_id: str, items: List[RealityCheck]) -> None:
    atomic_write_jsonl(reality_checks_path(data_dir, sim_id), [it.to_dict() for it in items])


# ── 记录：某一步的预测 vs 事后回填的真实结果 ───────────────────────────


def record_reality_check(
    data_dir: Path,
    sim_id: str,
    *,
    branch: str,
    step: int,
    predicted_summary: str,
    actual_outcome: str,
    verdict: str,
) -> RealityCheck:
    """记录一条"某一步的预测 vs 后来实际发生了什么"（4.16 节 2.）。

    `predicted_summary` 通常取自对应 `SimState.summary`/`narrative`
    （由调用方——`app.py`——在填表时预先带入，这里不做任何解析）；
    `verdict` 必须是 `matched`/`partially_matched`/`diverged` 三选一，
    由用户在界面上选择，**不做自动语义判定**（见模块 docstring）。

    Raises:
        RealityCheckError: `actual_outcome` 为空，或 `verdict` 不是
            合法三选一之一。
    """
    outcome = actual_outcome.strip()
    if not outcome:
        raise RealityCheckError("请填写「后来实际发生了什么」，不能为空")
    if verdict not in _VALID_VERDICTS:
        raise RealityCheckError(
            f"verdict 必须是 {_VALID_VERDICTS} 之一，收到：{verdict!r}"
        )

    item = RealityCheck(
        id=uuid.uuid4().hex[:12],
        sim_id=sim_id,
        branch=branch,
        step=step,
        predicted_summary=predicted_summary,
        predicted_at="",  # 预测发生的时间点不在本模块的职责范围内——
        # 对应 SimState 本身不记录"这一步是什么时候生成的"这种墙钟
        # 时间戳（只有 time_label 这种模拟内时间点），这里留空，展示层
        # 用 state.time_label 代替即可，不强行编一个假的墙钟时间。
        actual_outcome=outcome,
        recorded_at=_now_iso(),
        verdict=verdict,
    )
    existing = load_all(data_dir, sim_id)
    existing.append(item)
    _save_all(data_dir, sim_id, existing)
    return item


def find_for_step(
    data_dir: Path, sim_id: str, *, branch: str, step: int
) -> List[RealityCheck]:
    """返回某个具体 step 已经记录过的现实结果（可能有多条——同一步的
    预测理论上可以被回填多次，比如"先粗略记一次，后来补充细节"，不做
    "只能记一条"的限制）。供 `app.py` 判断"这一步是否已经记录过"、
    渲染已有记录列表。
    """
    return [
        it for it in load_all(data_dir, sim_id) if it.branch == branch and it.step == step
    ]


def record_and_apply(
    data_dir: Path,
    sim_id: str,
    *,
    branch: str,
    step: int,
    predicted_summary: str,
    actual_outcome: str,
    verdict: str,
    causal_links: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """`record_reality_check()` + "反向影响知识库可信度" 的组合快捷方式
    （4.16 节 3.）。

    `verdict == "diverged"` 时，对这一步的 `causal_links`（通常取自
    对应 `SimState.causal_links`，调用方带入）逐条尝试匹配知识库里的
    条目，命中的调用 `knowledge_base.record_contradiction()` 把
    `contradicted_count` 加一——这是"现实反馈 → 修正因果知识库"的
    最小闭环（见 4.16 节 3.）。`matched`/`partially_matched` 不触发
    任何知识库更新（4.16 节只要求"预测错误"这一种方向的反馈，"预测
    对了"不提升 `validated_count`——那已经由 `record_causal_links()`
    的跨模拟重复出现机制负责，重复一次成功预测不应该额外加分，避免
    双重计数）。

    知识库更新是纯旁路操作：内部用 `try/except` 兜底，任何异常都不
    应该影响这条现实记录本身是否落盘成功——记录用户填的真实结果永远
    是第一位的，知识库联动失败了大不了这次没更新到，不应该连累用户
    刚填的内容丢失。

    Returns:
        `{"reality_check": RealityCheck, "contradicted_knowledge_ids":
        List[str]}`——后者是本次实际被标记为"证伪"的知识条目 id 列表，
        供调用方展示"这次反馈影响了哪些知识"。
    """
    item = record_reality_check(
        data_dir, sim_id,
        branch=branch, step=step,
        predicted_summary=predicted_summary,
        actual_outcome=actual_outcome,
        verdict=verdict,
    )

    contradicted_ids: List[str] = []
    if verdict == "diverged" and causal_links:
        try:
            from world_simulator import knowledge_base as kb

            contradicted_ids = kb.update_confidence_from_reality_check(data_dir, causal_links)
        except Exception:  # noqa: BLE001 — 旁路操作，失败不影响现实记录本身
            pass

    return {"reality_check": item, "contradicted_knowledge_ids": contradicted_ids}
