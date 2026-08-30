"""evolution/self_narrative.py — 自我叙事生成
（next_doc/self_awareness_identity_evolution_plan.md §2.2，
next_doc/self_narrative_incremental_evolution_plan.md 阶段一重构）。

`self_profile.json` 里已有的 `identity`/`self_assessment`/`operating_state`
（perception/global_knowledge.py）都是结构化字段，回答的是"我是什么状态"，
但没有任何机制把这些字段、加上 `agent_value_profile.md`（§2.1，我倾向于
什么）、`capability_map`（我现在实际擅长什么）、`self_model_drift.py`
（§2.6，我曾经以为的和实测的差多少）、失败模式（`failure_pattern_store`，
我反复卡在哪）、子 Agent 经历（§2.4，`sub_agent_experience.py`，我委派
出去的任务经历了什么）整合成一段"我如何理解自己现在是什么样子"的连贯
第一人称叙事。这正是本模块要做的事——纯语义综合，不引入新的事实来源，
所有输入都来自已经落盘的数据。

存储策略：**追加式存档（类似日记，不覆盖旧版本）**，旧的叙事条目永久
保留在 `self_narrative_log.jsonl` 里，与 `decision_profile_builder.py`/
`agent_value_profile_builder.py` 的"矛盾证据不覆盖旧模式"是同一克制
原则的另一种表现形式。

**增量编辑式生成（阶段一重构）**：早期实现每次都"全量重新综合"，导致
两次生成之间证据窗口高度重叠时，叙事内容同质化重复，且从不参考"上一次
我是怎么理解自己的"，退化成一堆时间戳快照而非真正的演化主线。重构后：
  - 每条记录新增 `evidence_cursor`（本次生成所依据的时间点）和
    `snapshot_fingerprint`（本次快照型证据的指纹），供下一次生成判断
    "自上次以来有没有新证据"。
  - 证据分两类：**可追加型**（`sub_agent_experience`/`failure_pattern`/
    `agent_value_profile`，各自有天然的时间戳，按 `evidence_cursor` 过滤
    delta）和**快照型**（`identity`/`self_assessment`/
    `capability_top_domains`/`lineage`/`drift_signals`，代表"现状"而非
    事件流，每次都给最新全量，通过指纹判断是否变化）。
  - 若上一版存在且本次 delta 为空、快照指纹未变，直接跳过，不生成新
    版本——延续"没有摩擦和洞察就不写"的克制原则，避免版本数量本身
    失控增长。
  - 若上一版存在且确有新证据，prompt 从"写一段全新叙事"改为"编辑上一版"：
    传入上一版全文 + 本次 delta + 当前快照，要求 LLM 保留仍然成立的
    内容、融入新变化、对被推翻的旧判断做修正式措辞（"我曾经认为…，
    现在看来…"），而不是重写。这样"必要的历史信息保留多少"不需要额外
    的压缩算法，直接由 LLM 在编辑时自然取舍。
  - 首次生成（无历史版本）时退化为原有行为：全量证据、独立生成。

消费方式：任何需要"我现在是什么样"的场景只应取最新一条（见
`get_current_narrative()`），历史版本只用于人工/看板回溯"自我认识经历了
哪些阶段"，不参与日常消费路径。

每次生成会额外提炼一句话式的 `purpose_summary`，写回
`self_profile.identity.purpose`（这是 `SelfProfile` 里唯一直接可被本
模块更新的字段——其余字段仍分别由各自的建立者维护，不在这里越权覆写）。

`capability_focus_suggestions`（阶段二新增，可选、允许为空）是叙事综合
后提炼出的"值得针对性学习/补强的方向"，供 `persona_candidates.py` 作为
第四路信号接入候选池——见 next_doc/self_narrative_incremental_evolution_
plan.md §2.5。本模块只负责在叙事生成时顺带产出这个字段，不做任何候选
落盘或采纳决策，保持自我叙事"仅作为观察者"的定位。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

MIN_EVIDENCE_SOURCES = 1  # 至少要有 self_profile 本身存在才值得生成叙事


def _gather_evidence(paths, *, since_cursor: float = 0.0) -> dict:
    """汇总全部只读证据源。任一子来源读取失败都不应该让整个叙事生成
    失败——降级为该来源留空，其余照常。

    `since_cursor` > 0 时，对"可追加型"来源（有天然时间戳的事件流）按
    时间过滤只保留新增部分；"快照型"来源（代表现状而非事件流）不受
    影响，始终给最新全量。`since_cursor == 0`（首次生成或未提供）时
    等价于原有行为，全部来源不过滤。
    """
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

    # 快照型：capability_map 代表"现在实际擅长什么"，不是事件流，不过滤。
    try:
        from mini_agent.evolution.consolidation import build_capability_map

        entries = build_capability_map(paths, None)
        top = sorted(entries, key=lambda e: -e.confidence)[:5]
        evidence["capability_top_domains"] = [e.to_dict() for e in top]
    except Exception:
        pass

    # 可追加型：agent_value_profile 每条模式有 first_observed/last_reinforced
    # （"%Y-%m-%d" 日期字符串，字典序即时间序），按 since_cursor 过滤。
    try:
        from mini_agent.evolution.agent_value_profile_builder import load_agent_value_profile

        patterns = load_agent_value_profile(paths)
        if since_cursor > 0:
            cursor_date = time.strftime("%Y-%m-%d", time.localtime(since_cursor))
            patterns = [
                p for p in patterns
                if str(p.get("last_reinforced") or p.get("first_observed") or "") >= cursor_date
            ]
        evidence["agent_value_patterns"] = patterns[:5]
    except Exception:
        pass

    # 快照型：漂移信号是"当前信念 vs 当前实测"的实时比较结果，不是事件流，
    # 每次都是最新对比，不做时间过滤。
    try:
        from mini_agent.evolution.self_model_drift import compute_belief_drift_signals

        evidence["drift_signals"] = [s.to_dict() for s in compute_belief_drift_signals(paths)[:5]]
    except Exception:
        pass

    # 快照型：谱系视图是"当前存在哪些变体/已合并哪些"的现状汇总，不过滤。
    try:
        # [self_awareness_identity_evolution_plan.md §2.3] 谱系视图：把
        # evolve 分支重新表述为"变体候选自己"，作为叙事的额外证据源。
        from mini_agent.evolution.lineage_view import compute_lineage_view

        evidence["lineage"] = compute_lineage_view(paths).to_dict()
    except Exception:
        pass

    # 可追加型：失败模式有 last_seen（epoch），按 since_cursor 过滤后再
    # 按 occurrence_count 取 top 5。
    try:
        from mini_agent.evolution.failure_pattern_store import load_failure_patterns

        patterns = load_failure_patterns(paths)
        if since_cursor > 0:
            patterns = [p for p in patterns if float(p.get("last_seen", 0) or 0) > since_cursor]
        patterns = sorted(patterns, key=lambda p: -int(p.get("occurrence_count", 0) or 0))
        evidence["recent_failure_patterns"] = patterns[:5]
    except Exception:
        pass

    # 可追加型：子 Agent 经历有 at（epoch），按 since_cursor 过滤。
    try:
        from mini_agent.evolution.sub_agent_experience import load_recent_experiences

        experiences = load_recent_experiences(paths, limit=20 if since_cursor > 0 else 5)
        if since_cursor > 0:
            experiences = [e for e in experiences if float(e.get("at", 0) or 0) > since_cursor][:5]
        evidence["recent_sub_agent_experiences"] = experiences
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


def _snapshot_fingerprint(evidence: dict) -> str:
    """对"快照型"证据（不受 since_cursor 过滤影响的部分）算一个指纹，
    供下一次生成时判断"现状相比上一版有没有变化"。只覆盖快照型字段，
    可追加型字段的变化已经由 delta 是否为空单独判断，不重复计入。

    注意：`identity` 故意不计入指纹——`identity.purpose` 是本模块自己
    在每次生成结束时写回的字段（见 `generate_self_narrative` 末尾），
    把自己写回的结果当作"外部变化"的判断依据会形成死循环：每次生成后
    purpose 必然变化，导致指纹必然不同，"没有新证据就跳过"永远失效。"""
    payload = json.dumps(
        {
            "self_assessment": evidence.get("self_assessment"),
            "capability_top_domains": evidence.get("capability_top_domains"),
            "drift_signals": evidence.get("drift_signals"),
            "lineage": evidence.get("lineage"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _delta_is_empty(evidence: dict) -> bool:
    """判断"可追加型"证据本次过滤后是否为空（即自上一版以来没有新的
    事件型记录）。"""
    return not (
        evidence.get("agent_value_patterns")
        or evidence.get("recent_failure_patterns")
        or evidence.get("recent_sub_agent_experiences")
    )


def _build_narrative_prompt(evidence: dict, *, previous: Optional[dict] = None) -> str:
    """previous 为 None 时走首次生成的独立综合 prompt；否则走编辑式
    prompt——传入上一版全文，要求编辑更新而不是重写。"""
    evidence_block = (
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
        f"保留下来的）】{json.dumps(evidence.get('lineage'), ensure_ascii=False)}\n"
    )

    output_format = (
        "只输出如下 JSON，不要输出任何其它文字：\n"
        "{\n"
        '  "narrative": "……",\n'
        '  "purpose_summary": "……",\n'
        '  "capability_focus_suggestions": ["……"]\n'
        "}\n"
        "其中 capability_focus_suggestions 是可选的，基于以上记录判断"
        "\"有没有值得针对性学习/补强的方向\"，没有就给空数组，不要为了凑"
        "数量强行给出。"
    )

    if previous is None:
        return (
            "你是一个 AI agent，下面是关于你自己的一些已经记录下来的事实"
            "（不是猜测，都是真实发生过的记录）：\n\n"
            f"{evidence_block}\n"
            "请你以第一人称，写一段 3-6 句的叙事，综合上面这些记录，描述"
            "\"我现在如何理解自己\"——包括我目前擅长什么、我倾向于怎么做"
            "选择、有没有发现自己过去的自我认识和实测不太一样、还有哪些"
            "地方反复卡住。只依据上面给出的记录，不要编造记录之外的内容；"
            "某一类记录为空就不用在叙事里提它，不要为了凑够句数硬扯。\n\n"
            "然后再单独提炼一句话（不超过 40 字），概括我现在这个阶段"
            "大致\"在做什么/图什么\"，作为一句简短的身份定位描述。\n\n"
            f"{output_format}"
        )

    return (
        "你是一个 AI agent。下面是你上一次写下的、关于\"我现在如何理解"
        "自己\"的叙事：\n\n"
        f"【上一版叙事】{previous.get('narrative', '')}\n\n"
        "自那以后，新增了下面这些关于你自己的记录（同样不是猜测，都是"
        "真实发生过的）：\n\n"
        f"{evidence_block}\n"
        "请你以第一人称，**编辑更新**上一版叙事，而不是重新写一段——"
        "保留上一版里依然成立、没有被新记录推翻的内容；把新记录里真正"
        "值得纳入自我理解的部分融入进去；如果新记录和上一版某个判断"
        "冲突或已经不再准确，用类似\"我曾经认为…，现在看来…\"这样的"
        "措辞体现这个修正，而不是直接删掉不提。如果新记录里没有什么"
        "实质变化，允许输出和上一版基本一致的内容，不要为了显得\"有"
        "更新\"而生硬改写。最终仍然是一段 3-6 句的第一人称叙事。\n\n"
        "然后再单独提炼一句话（不超过 40 字），概括我现在这个阶段大致"
        "\"在做什么/图什么\"，作为一句简短的身份定位描述（可以延续上一版"
        "的措辞，也可以根据新记录调整）。\n\n"
        f"{output_format}"
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
    suggestions_raw = parsed.get("capability_focus_suggestions", [])
    suggestions = []
    if isinstance(suggestions_raw, list):
        suggestions = [str(s).strip() for s in suggestions_raw if str(s).strip()]
    return {
        "narrative": narrative,
        "purpose_summary": purpose_summary,
        "capability_focus_suggestions": suggestions,
    }


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
    的子来源）时也返回 None，不生成空洞叙事。

    若已有历史版本且自上一版以来没有任何新证据（可追加型来源均为空、
    快照型来源指纹未变），也返回 None，不生成同质化的重复版本。"""
    if llm_helper is None:
        return None

    previous_list = load_self_narrative_history(paths, limit=1)
    previous = previous_list[0] if previous_list else None
    since_cursor = float(previous.get("evidence_cursor", 0) or 0) if previous else 0.0

    evidence = _gather_evidence(paths, since_cursor=since_cursor)
    if not _has_any_evidence(evidence):
        return None

    fingerprint = _snapshot_fingerprint(evidence)
    if previous is not None:
        snapshot_unchanged = fingerprint == previous.get("snapshot_fingerprint")
        if _delta_is_empty(evidence) and snapshot_unchanged:
            return None

    prompt = _build_narrative_prompt(evidence, previous=previous)
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
        "capability_focus_suggestions": parsed["capability_focus_suggestions"],
        "evidence_cursor": time.time(),
        "snapshot_fingerprint": fingerprint,
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
    CLI 命令或 `/self/portrait` 只读展示，也供 `generate_self_narrative`
    内部读取"上一版"。"""
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


def get_current_narrative(paths) -> Optional[dict]:
    """便捷函数：取"当前状态"——即最新一条叙事记录。任何需要"我现在是
    什么样"的消费场景（看板展示、persona_candidates 第四路信号等）都应该
    调用这个函数，而不是自己重复"取最后一条"的逻辑，也不应该遍历/展示
    全部历史当作"当前状态"使用（历史版本只用于人工回溯）。"""
    history = load_self_narrative_history(paths, limit=1)
    return history[0] if history else None
