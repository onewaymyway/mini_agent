"""evolution/self_narrative.py — 自我叙事生成
（next_doc/self_awareness_identity_evolution_plan.md §2.2）。

`self_profile.json` 里已有的 `identity`/`self_assessment`/`operating_state`
（perception/global_knowledge.py）都是结构化字段，回答的是"我是什么状态"，
但没有任何机制把这些字段、加上 `agent_value_profile.md`（§2.1，我倾向于
什么）、`capability_map`（我现在实际擅长什么）、`self_model_drift.py`
（§2.6，我曾经以为的和实测的差多少）、失败模式（`failure_pattern_store`，
我反复卡在哪）、子 Agent 经历（§2.4，`sub_agent_experience.py`，我委派
出去的任务经历了什么）整合成一段"我如何理解自己现在是什么样子"的连贯
第一人称叙事。这正是本模块要做的事——纯语义综合，不引入新的事实来源，
所有输入都来自已经落盘的数据。

存储策略：**追加式存档（类似日记，不覆盖旧版本）**，与
`decision_profile_builder.py`/`agent_value_profile_builder.py`的"矛盾
证据不覆盖旧模式"是同一克制原则的另一种表现形式——身份认识会演变，
但不应该悄悄抹掉"我曾经怎么看自己"这段历史，旧的叙事条目永久保留在
`self_narrative_log.jsonl` 里。

每次生成会额外提炼一句话式的 `purpose_summary`，写回
`self_profile.identity.purpose`（这是 `SelfProfile` 里唯一直接可被本
模块更新的字段——其余字段仍分别由各自的建立者维护，不在这里越权覆写）。
"""

from __future__ import annotations

import json
import time
from typing import Optional

MIN_EVIDENCE_SOURCES = 1  # 至少要有 self_profile 本身存在才值得生成叙事


def _gather_evidence(paths) -> dict:
    """汇总全部只读证据源。任一子来源读取失败都不应该让整个叙事生成
    失败——降级为该来源留空，其余照常。"""
    evidence: dict = {
        "identity": None,
        "self_assessment": None,
        "operating_state": None,
        "capability_top_domains": [],
        "agent_value_patterns": [],
        "drift_signals": [],
        "recent_failure_patterns": [],
        "recent_sub_agent_experiences": [],
        "lineage": None,
    }

    try:
        from mini_agent.perception.global_knowledge import load_self_profile

        profile = load_self_profile(paths)
        if profile is not None:
            evidence["identity"] = profile.identity.to_dict()
            evidence["self_assessment"] = profile.self_assessment.to_dict()
            evidence["operating_state"] = profile.operating_state.to_dict()
    except Exception:
        pass

    try:
        from mini_agent.evolution.consolidation import build_capability_map

        entries = build_capability_map(paths, None)
        top = sorted(entries, key=lambda e: -e.confidence)[:5]
        evidence["capability_top_domains"] = [e.to_dict() for e in top]
    except Exception:
        pass

    try:
        from mini_agent.evolution.agent_value_profile_builder import load_agent_value_profile

        evidence["agent_value_patterns"] = load_agent_value_profile(paths)[:5]
    except Exception:
        pass

    try:
        from mini_agent.evolution.self_model_drift import compute_belief_drift_signals

        evidence["drift_signals"] = [s.to_dict() for s in compute_belief_drift_signals(paths)[:5]]
    except Exception:
        pass

    try:
        # [self_awareness_identity_evolution_plan.md §2.3] 谱系视图：把
        # evolve 分支重新表述为"变体候选自己"，作为叙事的额外证据源。
        from mini_agent.evolution.lineage_view import compute_lineage_view

        evidence["lineage"] = compute_lineage_view(paths).to_dict()
    except Exception:
        pass

    try:
        from mini_agent.evolution.failure_pattern_store import load_failure_patterns

        patterns = sorted(
            load_failure_patterns(paths), key=lambda p: -int(p.get("occurrence_count", 0) or 0)
        )
        evidence["recent_failure_patterns"] = patterns[:5]
    except Exception:
        pass

    try:
        from mini_agent.evolution.sub_agent_experience import load_recent_experiences

        evidence["recent_sub_agent_experiences"] = load_recent_experiences(paths, limit=5)
    except Exception:
        pass

    return evidence


def _has_any_evidence(evidence: dict) -> bool:
    if evidence.get("identity") is None:
        return False
    # identity 存在但全空（刚 ensure_self_profile 出来的默认值）也算"没什么
    # 可写的"，避免生成空洞的叙事。
    has_content = (
        bool(evidence.get("capability_top_domains"))
        or bool(evidence.get("agent_value_patterns"))
        or bool(evidence.get("drift_signals"))
        or bool(evidence.get("recent_failure_patterns"))
        or bool(evidence.get("recent_sub_agent_experiences"))
        or bool((evidence.get("lineage") or {}).get("active_variants"))
        or bool((evidence.get("lineage") or {}).get("merged_variants"))
        or bool((evidence.get("self_assessment") or {}).get("strengths"))
        or bool((evidence.get("self_assessment") or {}).get("weak_areas"))
    )
    return has_content


def _build_narrative_prompt(evidence: dict) -> str:
    return (
        "你是一个 AI agent，下面是关于你自己的一些已经记录下来的事实"
        "（不是猜测，都是真实发生过的记录）：\n\n"
        f"【身份】{json.dumps(evidence.get('identity'), ensure_ascii=False)}\n"
        f"【历史自评】{json.dumps(evidence.get('self_assessment'), ensure_ascii=False)}\n"
        f"【当前最擅长的领域（最近实测）】"
        f"{json.dumps(evidence.get('capability_top_domains'), ensure_ascii=False)}\n"
        f"【我倾向于做出的选择（自身价值观归纳）】"
        f"{json.dumps(evidence.get('agent_value_patterns'), ensure_ascii=False)}\n"
        f"【自我认识与实测的落差（我曾经以为的 vs 最近实测的）】"
        f"{json.dumps(evidence.get('drift_signals'), ensure_ascii=False)}\n"
        f"【反复卡住的地方】"
        f"{json.dumps(evidence.get('recent_failure_patterns'), ensure_ascii=False)}\n"
        f"【最近委派给子任务、值得关注的经历】"
        f"{json.dumps(evidence.get('recent_sub_agent_experiences'), ensure_ascii=False)}\n"
        f"【我的谱系：尝试过的变体候选自己（evolve 分支，尚在验证中/已被"
        f"保留下来的）】{json.dumps(evidence.get('lineage'), ensure_ascii=False)}\n\n"
        "请你以第一人称，写一段 3-6 句的叙事，综合上面这些记录，描述"
        "\"我现在如何理解自己\"——包括我目前擅长什么、我倾向于怎么做选择、"
        "有没有发现自己过去的自我认识和实测不太一样、还有哪些地方反复"
        "卡住。只依据上面给出的记录，不要编造记录之外的内容；某一类记录"
        "为空就不用在叙事里提它，不要为了凑够句数硬扯。\n\n"
        "然后再单独提炼一句话（不超过 40 字），概括我现在这个阶段大致\"在"
        "做什么/图什么\"，作为一句简短的身份定位描述。\n\n"
        "只输出如下 JSON，不要输出任何其它文字：\n"
        "{\n"
        '  "narrative": "……",\n'
        '  "purpose_summary": "……"\n'
        "}"
    )


def _parse_narrative_response(raw: str) -> Optional[dict]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return None
    narrative = str(parsed.get("narrative", "")).strip()
    purpose_summary = str(parsed.get("purpose_summary", "")).strip()
    if not narrative:
        return None
    return {"narrative": narrative, "purpose_summary": purpose_summary}


def _append_log(paths, entry: dict) -> None:
    p = paths.self_narrative_log_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")


def generate_self_narrative(paths, *, llm_helper=None) -> Optional[dict]:
    """归纳一次自我叙事。llm_helper 为 None 时直接返回 None（语义综合
    依赖 LLM，规则层无法替代，与 decision_profile/agent_value_profile
    同一取舍）。证据不足（self_profile 不存在，或存在但没有任何有内容
    的子来源）时也返回 None，不生成空洞叙事。"""
    if llm_helper is None:
        return None

    evidence = _gather_evidence(paths)
    if not _has_any_evidence(evidence):
        return None

    prompt = _build_narrative_prompt(evidence)
    try:
        raw = llm_helper.ask(prompt)
    except Exception:
        return None

    parsed = _parse_narrative_response(raw)
    if parsed is None:
        return None

    entry = {
        "at": time.time(),
        "narrative": parsed["narrative"],
        "purpose_summary": parsed["purpose_summary"],
    }
    _append_log(paths, entry)

    if parsed["purpose_summary"]:
        try:
            from mini_agent.perception.global_knowledge import (
                ensure_self_profile,
                save_self_profile,
            )

            profile = ensure_self_profile(paths)
            profile.identity.purpose = parsed["purpose_summary"]
            save_self_profile(paths, profile)
        except Exception:
            pass  # purpose 回写失败不影响本次叙事已经写入日志的既有价值

    return entry


def load_self_narrative_history(paths, *, limit: int = 20) -> list[dict]:
    """只读加载最近 limit 条叙事日志（按时间倒序），供 `/self_narrative`
    CLI 命令或 `/self/portrait` 只读展示。"""
    p = paths.self_narrative_log_path
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    # 与 sub_agent_experience.load_recent_experiences 同样的取舍：直接
    # 倒序读取文件本身的写入顺序，不依赖 at 字段排序（避免同批快速写入
    # 时 time.time() 精度不够导致排序不稳定）。
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out
