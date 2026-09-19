"""world_simulator/retrospective.py — 模拟复盘 / 经验教训总结（阶段
三十二，`next_doc/world_simulator_realism_transparency_and_
retrospective_roadmap_v3_plan.md` 4.6 节，用户本次明确要求新增）。

设计理念：模拟推进到任意节点（尤其是模拟结束时），用户目前只能自己
翻时间线/归因报告/因果图谱/顺势逆势判断来总结"这一路走下来学到了
什么"。本模块提供一个专门的入口，把这些已经存在的结构化信息喂给
LLM，产出一份"只总结已发生内容、不做新预测"的复盘报告。

范围克制（详见 4.6 节"范围克制"一段）：
- 只对单个分支生成复盘，不做跨分支/跨实例的"元复盘"。
- 不自动把 `lessons` 写入 `knowledge_base.py`（复盘产出的是"针对这个
  用户这次具体情境的经验"，和知识库"跨模拟可复用的因果机制"是两类
  不同性质的知识，混在一起会污染知识库检索质量）。
- 不做定时/自动生成——始终是用户主动点击触发的可选功能。
- "每条结论必须有具体依据"这条约束只能通过 prompt 引导，不做代码层面
  的自动校验（同 `reality_check.py` 不做自动语义匹配判定的一贯取舍）。

落盘位置：`data/<sim_id>/retrospectives.jsonl`，与
`reality_checks.jsonl` 平级，同样的降级导入方式；允许对同一分支在
不同时间点多次生成，留存历史版本（不覆盖）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output

try:
    from mini_agent.utils.atomic_write import atomic_write_jsonl
except ImportError:  # 独立运行、未装 mini_agent 时的降级实现，同 store.py/reality_check.py

    def atomic_write_jsonl(path: Path, records: list) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
            + ("\n" if records else ""),
            encoding="utf-8",
        )


class RetrospectiveError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class RetrospectiveReport:
    """一份复盘报告的内容本身（4.6 节方案里定义的五个板块）。"""

    turning_points: List[Dict[str, Any]] = field(default_factory=list)
    what_went_well: List[Dict[str, Any]] = field(default_factory=list)
    what_to_reflect_on: List[Dict[str, Any]] = field(default_factory=list)
    lessons: List[Dict[str, Any]] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turning_points": list(self.turning_points),
            "what_went_well": list(self.what_went_well),
            "what_to_reflect_on": list(self.what_to_reflect_on),
            "lessons": list(self.lessons),
            "caveats": list(self.caveats),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetrospectiveReport":
        def _list_of_dicts(key: str) -> List[Dict[str, Any]]:
            return [x for x in (data.get(key) or []) if isinstance(x, dict)]

        return cls(
            turning_points=_list_of_dicts("turning_points"),
            what_went_well=_list_of_dicts("what_went_well"),
            what_to_reflect_on=_list_of_dicts("what_to_reflect_on"),
            lessons=_list_of_dicts("lessons"),
            caveats=[str(c) for c in (data.get("caveats") or [])],
        )


@dataclass
class RetrospectiveRecord:
    """一次复盘生成的完整记录（含元信息），对应
    `retrospectives.jsonl` 里的一行。"""

    id: str
    sim_id: str
    branch: str
    up_to_step: int
    created_at: str
    report: RetrospectiveReport

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sim_id": self.sim_id,
            "branch": self.branch,
            "up_to_step": self.up_to_step,
            "created_at": self.created_at,
            "report": self.report.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetrospectiveRecord":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            sim_id=str(data.get("sim_id") or ""),
            branch=str(data.get("branch") or "main"),
            up_to_step=int(data.get("up_to_step") or 0),
            created_at=str(data.get("created_at") or ""),
            report=RetrospectiveReport.from_dict(data.get("report") or {}),
        )


# ── 落盘路径与读写 ───────────────────────────────────────────────────


def retrospectives_path(data_dir: Path, sim_id: str) -> Path:
    return Path(data_dir) / sim_id / "retrospectives.jsonl"


def load_all(data_dir: Path, sim_id: str) -> List[RetrospectiveRecord]:
    path = retrospectives_path(data_dir, sim_id)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(RetrospectiveRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # 单行损坏不应该让整个记录不可用，跳过即可
    return items


def load_for_branch(data_dir: Path, sim_id: str, branch: str) -> List[RetrospectiveRecord]:
    """返回某个分支已经生成过的复盘记录，按生成时间倒序（最新的在前，
    方便 `app.py` 直接展示"最近一次复盘"）。"""
    items = [it for it in load_all(data_dir, sim_id) if it.branch == branch]
    items.sort(key=lambda it: it.created_at, reverse=True)
    return items


def _save_append(data_dir: Path, sim_id: str, item: RetrospectiveRecord) -> None:
    existing = load_all(data_dir, sim_id)
    existing.append(item)
    atomic_write_jsonl(retrospectives_path(data_dir, sim_id), [it.to_dict() for it in existing])


# ── 素材收集 ─────────────────────────────────────────────────────────


def _collect_materials(
    manifest, history: List[Any], branch: str
) -> Dict[str, Any]:
    """把 `manifest`/`history` 里已经存在的结构化信息整理成喂给 prompt
    的几段 JSON 文本。只读取已经存在的历史运行记录，不在复盘时临时
    发起任何新的模拟推进/新分叉。"""
    from world_simulator import attribution, causal_graph, trend

    state_history_json = json.dumps(
        [
            {
                "step": s.step,
                "summary": s.summary,
                "narrative": s.narrative,
                "chosen_option_id": s.chosen_option_id,
                "chosen_by": s.chosen_by,
                "chosen_reason": s.chosen_reason,
                "key_drivers": list(getattr(s, "key_drivers", None) or []),
                "causal_links": list(getattr(s, "causal_links", None) or []),
            }
            for s in history
        ],
        ensure_ascii=False,
    )

    try:
        edges = causal_graph.build_causal_graph(history)
        causal_graph_json = json.dumps([e.to_dict() for e in edges], ensure_ascii=False)
    except Exception:  # noqa: BLE001 — 素材收集是旁路操作，单项失败不应阻断整个复盘
        causal_graph_json = "[]"

    objectives = (manifest.settings or {}).get("objectives") or []
    attribution_items = []
    for obj in objectives:
        if isinstance(obj, dict) and obj.get("field"):
            try:
                report = attribution.summarize_contributions(history, str(obj["field"]))
                attribution_items.append(
                    {
                        "objective": obj.get("label") or obj.get("field"),
                        "field": obj.get("field"),
                        "sources": [src.to_dict() for src in report.sources],
                    }
                )
            except Exception:  # noqa: BLE001
                continue
    attribution_json = json.dumps(attribution_items, ensure_ascii=False)

    self_line_id = str((manifest.settings or {}).get("retrospective_self_line_id") or "").strip()
    trend_json = "null"
    if self_line_id and objectives:
        first_field_objective = next(
            (o for o in objectives if isinstance(o, dict) and o.get("field")), None
        )
        if first_field_objective:
            try:
                judgement = trend.classify_trend(
                    history, str(first_field_objective["field"]), self_line_id
                )
                trend_json = json.dumps(judgement.to_dict(), ensure_ascii=False)
            except Exception:  # noqa: BLE001
                trend_json = "null"

    return {
        "state_history_json": state_history_json,
        "causal_graph_json": causal_graph_json,
        "attribution_json": attribution_json,
        "trend_json": trend_json,
    }


def _collect_reality_checks(data_dir: Path, sim_id: str, branch: str) -> str:
    try:
        from world_simulator import reality_check

        items = [
            it.to_dict()
            for it in reality_check.load_all(data_dir, sim_id)
            if it.branch == branch
        ]
        return json.dumps(items, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — 旁路操作，读取失败不影响复盘本身
        return "[]"


def _collect_counterfactuals(manifest) -> str:
    """读取过去已经跑过的反事实对比分支的摘要（若有），只读取已存在
    的记录，不在复盘时临时重新推演。当前实现读取
    `manifest.settings.counterfactual_summaries`（若用户/其它模块曾经
    写入过这类摘要）；没有则为空数组，不影响复盘正常生成。"""
    summaries = (manifest.settings or {}).get("counterfactual_summaries") or []
    if isinstance(summaries, list):
        return json.dumps(summaries, ensure_ascii=False)
    return "[]"


# ── 生成入口 ─────────────────────────────────────────────────────────


def generate_retrospective(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    branch: Optional[str] = None,
) -> RetrospectiveRecord:
    """触发一次 `retrospective` workflow，生成并落盘一份复盘报告。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根。
        data_dir: 模拟数据根目录。
        sim_id: 模拟实例 id。
        branch: 要复盘的分支，默认当前活跃分支（`manifest.branch`）。

    Returns:
        新生成并已落盘的 `RetrospectiveRecord`（追加写入，不覆盖历史
        记录，允许对同一分支多次生成、留存不同时间点的复盘版本）。

    Raises:
        RetrospectiveError: workflow 执行失败，或找不到指定分支的历史。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore
    from world_simulator.store import SimNotFoundError, SimStore

    store = SimStore.for_root(data_dir, sim_id)
    try:
        manifest = store.load_manifest()
    except SimNotFoundError as exc:
        raise RetrospectiveError(f"模拟实例不存在：{sim_id}") from exc
    branch = branch or manifest.branch or "main"
    history = store.load_history(branch)
    if not history:
        raise RetrospectiveError(f"分支 {branch!r} 没有任何历史记录，无法生成复盘")

    materials = _collect_materials(manifest, history, branch)
    reality_checks_json = _collect_reality_checks(data_dir, sim_id, branch)
    counterfactual_json = _collect_counterfactuals(manifest)

    wf_store = WorkflowStore(Path(workspace_root))
    wf = wf_store.load("retrospective")
    if wf is None:
        raise RetrospectiveError(
            "找不到 workflow 定义 'retrospective'"
            f"（预期路径：{workspace_root}/workflows/retrospective.yaml）"
        )

    runner = WorkflowRunner(cfg)
    result = runner.run(
        wf,
        {
            "template": manifest.template,
            "intent": manifest.intent,
            "up_to_step": history[-1].step,
            **materials,
            "reality_checks_json": reality_checks_json,
            "counterfactual_json": counterfactual_json,
        },
    )

    if result.status != "done":
        failed = [
            f"{sr.step_id}({sr.status.value}): {sr.error}"
            for sr in result.step_results
            if sr.status.value != "done"
        ]
        raise RetrospectiveError(
            f"retrospective workflow 执行未成功：status={result.status}；" + "；".join(failed)
        )

    step_result = next(
        (sr for sr in result.step_results if sr.step_id == "retrospective"), None
    )
    if step_result is None:
        raise RetrospectiveError("retrospective 步骤没有产出结果")
    # [BUGFIX，同 agent_preview.py] `type: agent` 不支持 `result_file`/
    # `result_file_required_keys` 结果文件契约（只有 `type: script`/
    # `skill_agent` 实现了这套机制），之前判 `step_result.result_file`
    # 必然是 None，每次都报"未产出 result_file"。改为直接从
    # `step_result.output`（workflow yaml 已要求只回复一个 JSON 对象）
    # 解析。
    try:
        data = extract_agent_json_output(
            step_result.output,
            required_keys=[
                "turning_points", "what_went_well", "what_to_reflect_on",
                "lessons", "caveats",
            ],
        )
    except AgentStepOutputError as exc:
        raise RetrospectiveError(f"retrospective 步骤回复无法解析为复盘报告：{exc}") from exc
    report = RetrospectiveReport.from_dict(data)
    if not report.caveats:
        # prompt 已明确要求固定包含一条局限声明，这里做最后一道兜底，
        # 避免因 LLM 偶尔漏填导致报告缺失这条必要的免责说明。
        report.caveats = [
            "这是基于这次模拟内部记录的总结，不是对你个人能力或决策方式的心理分析，"
            "模拟本身也可能和现实有偏差。"
        ]

    record = RetrospectiveRecord(
        id=uuid.uuid4().hex[:12],
        sim_id=sim_id,
        branch=branch,
        up_to_step=history[-1].step,
        created_at=_now_iso(),
        report=report,
    )
    _save_append(data_dir, sim_id, record)
    return record
