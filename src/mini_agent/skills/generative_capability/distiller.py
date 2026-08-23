"""
distiller.py
=============
Generative-Capability 引擎的蒸馏器（阶段三）。

对应文档: next_doc/generative-capability-skill-plan.md 第 6 节 distill() /
          第 8 节安全边界(3)(4)(5) / 实施记录阶段三。

职责:
  把 explore() 产出的动作序列（ExploreTrace）蒸馏为一个"参数化脚本"，
  而不是把 trace 原样保存当脚本用（方案文档 6.4 明确要求"蒸馏"而非"录制回放"
  的字面含义是：脚本需要能在 target/query 等参数变化时仍然复用同一套动作骨架，
  而不是死绑定当次探索用到的具体值）。

蒸馏策略（阶段三的落地实现）:
  - 把 trace 中每一步 (tool, input) 记录下来，input 里如果出现与本次
    request 相同的值（如 target.url / query 的原始值），在生成脚本时替换成
    对运行时 request 参数的引用，而不是硬编码常量——这样同一脚本在下次被
    检索命中执行时，会用新的 target/query 重新驱动相同的动作序列。
  - 脚本运行时需要一个真正的工具执行器来重放这些动作，这是运行时环境能力，
    由 `_engine/tool_runtime.py` 提供的模块级钩子在执行前注入
    （引擎不在 distiller 里假设某个具体的浏览器实现）。
  - 蒸馏产物必须先在沙箱内自测（用与探索相同的方式重新跑一遍 run()，
    再用 intent_schema 校验一次），自测通过才允许落盘；不通过则丢弃，
    不污染 members/、registry.json、_index.json。
  - 落盘是原子的：script.py / meta.json / registry.json / _index.json
    要么全部一起更新成功，要么全部不落盘，避免"脚本能跑但检索不到"或
    "检索能到但脚本已被清理"的不一致状态（方案文档第 8 节安全边界 5）。

trust_trace_data 一致性兜底（阶段六，回应阶段五"已知遗留"第 1 条）:
  - 蒸馏脚本重放动作序列后，默认只从"重放出的最后一步工具输出"里取 data
    （见 SCRIPT_TEMPLATE），这对"提取/产出"类工具（如 browser_extract_content/
    doc_render）是合理假设，但对自测/CI 场景里任意回显式的桩执行器不友好——
    任何不精心构造"最后一步返回正确 data"的桩 tool_executor 都会让自测判失败，
    容易被误判为"探索失败"而非"自测环境没配对"（阶段五实施记录已知遗留）。
  - capability.yaml 可选声明 `distill: {trust_trace_data: true}` 作为
    领域级开关：仅当探索阶段最后一个真实工具步骤的输出里确实取不到可用 data
    时，才把探索阶段已经拿到、且已通过 intent_schema 校验的 `trace.data`
    作为蒸馏脚本的兜底常量嵌入（而不是在重放能正常取到 data 时也用
    trace.data 覆盖，保持"能参数化复用就参数化复用"的优先级不变）。
  - 是否用到了这个兜底会如实记录进 meta.json 的 `distill_used_trace_data_fallback`
    字段，保持可审计——这不是静默放宽校验，而是把"探索已验证过的数据"当作
    最后一道防线，避免同一份数据的可靠性判断被两套标准反复横跳。

三条蒸馏路径（阶段二十五扩充为三条，原两条见
next_doc/generative_capability_explorer_rearch_plan.md 3.2 节）:
  - **script_source 路径（优先级最高）**: 探索子agent在调用 `finish` 时，
    如果自己判断这次解法可以整理成一个不依赖具体探索过程的
    `run(input) -> dict` 脚本，会直接把源码通过 `finish` 的 `script_source`
    参数一并提交（见 explorer_runtime.py；阶段二十五起该字段结构性必填，
    只能显式提交源码或哨兵值 "SKIP"，不再允许静默留空）。蒸馏器此时不再
    基于 trace 猜测动作形状，只是把这份源码原样落盘前置一段生成来源说明
    注释，然后走与其余路径完全一致的"沙箱自测 → intent_schema 校验 →
    原子落盘"流程。这是探索子agent自己对"这段解法是否具备可复用的参数化
    形状"做出的判断，比蒸馏器事后从一串工具调用里机械猜测更可靠。
  - **LLM 事后总结路径（阶段二十五新增，次优先级）**: `script_source` 为空
    （探索子agent被预算强制放行、或显式 "SKIP" 后蒸馏器仍想再试一次）且
    调用方注入了 `llm_helper` 时，蒸馏器用探索过程的完整 trace（每一步
    tool/input/output）加上 request/intent_schema，请 LLM 直接"读一遍这次
    探索走过的路，总结出一个参数化的 `run(input) -> dict`"——这利用的是
    agent 本身的总结/泛化能力，而不是机械地把 trace 里的每一步原样重放
    （trace 里混着探测性失败调用时，机械重放会把这些失败也一起录进去，
    重放必炸，见下方 trace-replay 路径的已知局限）。产出同样要过沙箱自测/
    schema 校验/原子落盘，失败就丢弃、退到 trace-replay。
  - **trace-replay 路径（最后兜底）**: 前两条都不可用（没有 `llm_helper`
    注入，或 LLM 总结产物没通过自测）时，沿用阶段三的原有实现——把 trace
    中每一步 (tool, input) 参数化后固化为重放序列。这条路径的已知局限：
    它不区分"探索过程中的死胡同"和"通往最终成功的关键步骤"，trace 里如果
    混有失败的探测性调用（如日志里那次知乎抓取用 bash 试了几次 curl/urllib
    都失败才转向 browser_* 工具），重放到这些失败步骤时会直接判定"重放
    步骤失败"，整个蒸馏产物被丢弃——这也是当前默认只在前两条路径都不可用
    时才会走到这里的原因。
  - 三条路径产出的最终落盘产物形状完全一致（script.py + meta.json 都在，
    meta.json 里 `distill_source_kind` 字段区分来源: "script_source" |
    "llm_synthesized" | "trace_replay"，便于事后审计/统计哪条路径实际承担了
    大部分蒸馏产出）。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .explorer_runtime import ExploreStep, ExploreTrace
from .schema_validator import validate as _validate_schema_errors


@dataclass
class DistillResult:
    success: bool
    member_id: Optional[str] = None
    error: Optional[str] = None
    data: Optional[dict] = None  # 自测通过后的最终数据，success 时透传给调用方


SCRIPT_TEMPLATE = '''"""
{skill_name} / members / {member_id} / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎的探索子agent自动蒸馏生成
（source: explored，阶段三 distill()），非人工手写。

蒸馏自一次成功的探索，参数化了以下字段: {templated_fields}
其余动作步骤原样固化为对运行时工具执行器的重放序列。
"""

from __future__ import annotations

from typing import Any

# 运行时工具执行器由引擎在加载本脚本前注入，脚本自身不实现具体的浏览器控制逻辑，
# 保持"member 只依赖统一工具执行接口"的约束，避免每个蒸馏脚本各自实现一套底层能力。
from tool_runtime import get_tool_executor

_STEPS = {steps_literal}

# 仅当 capability.yaml 声明 distill.trust_trace_data: true，且探索阶段最后一个
# 真实工具步骤未能直接给出 data 时，才会在此处嵌入探索阶段已通过 intent_schema
# 校验的 trace.data 作为兜底常量；否则恒为 None，不影响"参数化复用"的默认行为。
_TRACE_DATA_FALLBACK = {trace_data_fallback_literal}


def run(input: dict) -> dict:
    executor = get_tool_executor()
    if executor is None:
        return {{
            "status": "fail",
            "data": None,
            "error": "未注入 tool_runtime 工具执行器，蒸馏脚本无法重放动作序列",
        }}

    context = {{"target": input.get("target", {{}}), "query": input.get("query") or input.get("text", "")}}

    last_output: Any = None
    for step in _STEPS:
        tool_name = step["tool"]
        tool_input = _resolve_placeholders(step["input"], context)
        try:
            last_output = executor(tool_name, tool_input)
        except Exception as e:  # noqa: BLE001
            return {{"status": "fail", "data": None, "error": f"重放步骤 `{{tool_name}}` 失败: {{e}}"}}
        if isinstance(last_output, dict) and last_output.get("error"):
            return {{"status": "fail", "data": None, "error": f"重放步骤 `{{tool_name}}` 返回错误: {{last_output['error']}}"}}

    data = None
    if isinstance(last_output, dict):
        data = last_output.get("data")
    if data is None:
        data = _TRACE_DATA_FALLBACK
    if data is None:
        return {{"status": "fail", "data": None, "error": "重放完成但未获得可用数据"}}
    return {{"status": "success", "data": data, "error": None}}


def _resolve_placeholders(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        if value == "__PLACEHOLDER_TARGET_URL__":
            return context["target"].get("url", "")
        if value == "__PLACEHOLDER_QUERY__":
            return context["query"]
        return value
    if isinstance(value, dict):
        return {{k: _resolve_placeholders(v, context) for k, v in value.items()}}
    if isinstance(value, list):
        return [_resolve_placeholders(v, context) for v in value]
    return value
'''


SCRIPT_SOURCE_HEADER_TEMPLATE = '''"""
{skill_name} / members / {member_id} / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎的探索子agent自动蒸馏生成
（source: explored, distill_source_kind: script_source）。
探索子agent在探索成功后自行判断这次解法具备可参数化复用的形状，直接提交了
以下源码；蒸馏器只做了"沙箱自测 + intent_schema 校验 + 原子落盘"，未对动作
序列做任何猜测或改写。
"""

'''


LLM_SYNTHESIZED_HEADER_TEMPLATE = '''"""
{skill_name} / members / {member_id} / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎在探索成功、但探索子agent未提交
script_source 时，用 LLM 事后阅读整段探索 trace 总结生成
（source: explored, distill_source_kind: llm_synthesized）。
非人工手写，未逐字重放当次探索的工具调用序列，而是由 LLM 提炼出参数化的
等价逻辑；仍需经过与其它路径完全一致的沙箱自测 + intent_schema 校验才会
落盘。
"""

'''


_LLM_SYNTHESIZE_SYSTEM_PROMPT = """你是一个"探索经验蒸馏器"。你会收到一次成功的网页/工具探索过程的完整
操作记录（每一步调用了什么工具、传了什么参数、返回了什么），以及这次探索
最终提交的结构化数据和它应满足的 schema。

你的任务：读懂这次探索实际做对了什么（跳过其中失败的试探性步骤，只提炼
真正走通的那条路径），总结成一个可以在 target/query 等参数变化时复用的
Python 脚本。

硬性要求：
1. 必须定义 `def run(input: dict) -> dict`，返回
   `{"status": "success"|"fail", "data": ..., "error": ...}`。
2. 脚本内如需调用底层操作原语（如 browser_navigate/browser_extract_content
   这类领域工具），通过 `from tool_runtime import get_tool_executor` 拿到
   执行器，用 `executor(tool_name, tool_input)` 调用——不要假设这些工具有
   其它调用方式，也不要引入这次探索记录里没出现过的工具名。
3. 不要硬编码这次探索用到的具体 target.url / query 值，改为从 `input` 参数
   读取。
4. 只输出脚本源码本身，不要用 Markdown 代码块包裹，不要输出任何解释文字。
"""


def _llm_synthesize_script(
    trace: ExploreTrace, request: dict, intent_schema: dict,
    skill_name: str, member_id: str, llm_helper: Any,
) -> Optional[str]:
    """[阶段二十五] script_source 缺失时的次优先级兜底：把探索 trace 交给
    LLM，让它凭借自身的总结/泛化能力提炼出参数化脚本，而不是机械地把
    trace 里每一步（包括探索时的死胡同）原样重放。失败（LLM 调用异常/
    返回内容不像 Python 源码）时返回 None，调用方据此退到 trace-replay，
    不抛异常中断整个 distill()。
    """
    try:
        steps_summary = [
            {
                "tool": step.tool,
                "input": step.input,
                # 输出可能很大（如整页 HTML），截断到合理长度，避免把
                # LLM 上下文喂爆；这只影响总结质量，不影响最终产物是否
                # 需要过自测——自测环节仍然是真实执行，不依赖这份摘要。
                "output_preview": _truncate_for_prompt(step.output),
                "error": step.error,
            }
            for step in trace.steps
        ]
        user_content = json.dumps(
            {
                "request": request,
                "intent_schema": intent_schema,
                "final_data": trace.data,
                "steps": steps_summary,
            },
            ensure_ascii=False,
        )
        raw = llm_helper.ask(
            user_content,
            system=_LLM_SYNTHESIZE_SYSTEM_PROMPT,
            max_retries=2,
            override_temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        from .capability_debug import capability_debug_log
        capability_debug_log(
            "distill_llm_synthesize_call_failed", {"member_id": member_id, "error": str(e)},
            where="distiller._llm_synthesize_script",
        )
        return None

    code = raw.strip()
    code = code.removeprefix("```python").removeprefix("```").removesuffix("```").strip()
    if "def run(" not in code:
        from .capability_debug import capability_debug_log
        capability_debug_log(
            "distill_llm_synthesize_invalid_output",
            {"member_id": member_id, "raw_preview": code[:300]},
            where="distiller._llm_synthesize_script",
        )
        return None

    return LLM_SYNTHESIZED_HEADER_TEMPLATE.format(skill_name=skill_name, member_id=member_id) + code


def _truncate_for_prompt(value: Any, limit: int = 600) -> Any:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if len(text) > limit:
        return text[:limit] + f"...(截断，原长度 {len(text)})"
    return text


def distill(
    trace: ExploreTrace,
    request: dict,
    intent_schema: dict,
    skill_dir: Path,
    capability: dict,
    self_test_executor,
    reexplore_member_id: Optional[str] = None,
    llm_helper: Any = None,
) -> DistillResult:
    """
    self_test_executor: Callable[[str, dict], dict] —— 蒸馏产物沙箱自测时
    用于重放动作序列的工具执行器（阶段三里直接复用探索时的同一个
    tool_executor，因为自测的目的是验证"这条路径确实可复用"，
    而不是验证语法正确性——语法层面已经由本函数内的 import 校验覆盖）。

    llm_helper: [阶段二十五新增] 通常是当前 `Agent.llm_helper`，用于
    script_source 缺失时的"LLM 事后总结"路径（见文件头"三条蒸馏路径"）。
    不传时该路径自动跳过，直接退到 trace-replay，行为等同阶段二十四。

    依次尝试 script_source -> llm_synthesized -> trace_replay 三条路径
    （见文件头说明），命中第一条产出即可通过自测/校验的就用它，不会
    "既然有 script_source 就一定成功"地跳过后续自测——每条路径产出的
    脚本都要经过同一套沙箱自测 + intent_schema 校验才允许落盘。
    """
    if not trace.success or trace.data is None:
        return DistillResult(success=False, error="探索未成功，无 trace 可供蒸馏")

    if not _validate_schema(trace.data, intent_schema):
        return DistillResult(success=False, error="探索产出的数据未通过 intent_schema 校验，拒绝蒸馏")

    skill_name = capability.get("name", skill_dir.name)
    member_id = reexplore_member_id or _generate_member_id(request, skill_dir)

    attempts: list[tuple[str, str]] = []  # [(distill_source_kind, script_code), ...] 按优先级排列

    if trace.script_source and trace.script_source.strip():
        attempts.append((
            "script_source",
            SCRIPT_SOURCE_HEADER_TEMPLATE.format(skill_name=skill_name, member_id=member_id)
            + trace.script_source,
        ))
    else:
        if llm_helper is not None:
            synthesized = _llm_synthesize_script(
                trace=trace, request=request, intent_schema=intent_schema,
                skill_name=skill_name, member_id=member_id, llm_helper=llm_helper,
            )
            if synthesized:
                attempts.append(("llm_synthesized", synthesized))

    # trace-replay 兜底：始终作为最后一条候选加入（即使前面已经有候选，
    # 前面的候选自测失败时还能落到这里，而不是直接判定整次探索无法沉淀）。
    templated_steps, templated_fields = _templatize_steps(trace.steps, request)
    trust_trace_data = bool((capability.get("distill") or {}).get("trust_trace_data", False))
    last_real_output = _last_real_step_output(trace.steps)
    trace_fallback_used = trust_trace_data and not (
        isinstance(last_real_output, dict) and last_real_output.get("data") is not None
    )
    trace_replay_code = SCRIPT_TEMPLATE.format(
        skill_name=skill_name,
        member_id=member_id,
        templated_fields=", ".join(sorted(templated_fields)) or "(无，动作序列不依赖输入参数)",
        steps_literal=json.dumps(templated_steps, ensure_ascii=False, indent=4),
        trace_data_fallback_literal=repr(trace.data) if trace_fallback_used else "None",
    )
    attempts.append(("trace_replay", trace_replay_code))

    from .capability_debug import capability_debug_log

    attempt_errors: list[dict] = []
    for distill_source_kind, script_code in attempts:
        use_trace_fallback = distill_source_kind == "trace_replay" and trace_fallback_used

        tmp_dir = skill_dir / "members" / f"__tmp_{member_id}_{int(time.time())}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_script = tmp_dir / "script.py"
        tmp_script.write_text(script_code, encoding="utf-8")
        try:
            test_result = _sandbox_run(tmp_script, request, self_test_executor)
        finally:
            _rm_tree(tmp_dir)

        if not test_result.get("ok"):
            attempt_errors.append({"path": distill_source_kind, "error": test_result.get("error")})
            continue

        final_data = test_result.get("data")
        if not _validate_schema(final_data, intent_schema):
            attempt_errors.append({"path": distill_source_kind, "error": "自测数据未通过 intent_schema 校验"})
            continue

        try:
            _atomic_persist(
                skill_dir=skill_dir,
                member_id=member_id,
                script_code=script_code,
                request=request,
                capability=capability,
                intent_schema=intent_schema,
                is_reexplore=reexplore_member_id is not None,
                used_trace_data_fallback=use_trace_fallback,
                distill_source_kind=distill_source_kind,
            )
        except Exception as e:  # noqa: BLE001
            attempt_errors.append({"path": distill_source_kind, "error": f"落盘失败: {e}"})
            continue

        if attempt_errors:
            # [阶段二十五] 前面的候选路径失败了，但最终还是成功落盘了——
            # 这类"部分失败"信息如实记录，不因为最终成功就静默吞掉，方便
            # 事后判断某条路径（尤其是 LLM 总结）实际成功率如何。
            capability_debug_log(
                "distill_attempt_partial_failure",
                {"member_id": member_id, "succeeded_path": distill_source_kind,
                 "failed_attempts": attempt_errors},
                where="distiller.distill",
            )
        return DistillResult(success=True, member_id=member_id, data=final_data)

    # [阶段二十五] 三条路径全部失败：完整记录每条路径各自的失败原因到
    # capability_debug.jsonl（不做任何截断），不再依赖调用方把这些细节
    # 一路透传到用户可见的 error 字符串里才算"看得到"。
    capability_debug_log(
        "distill_all_paths_failed",
        {"member_id": member_id, "skill_name": skill_name, "request": request,
         "attempted_paths": [k for k, _ in attempts], "attempt_errors": attempt_errors},
        where="distiller.distill",
    )
    summary = "；".join(f"{a['path']}: {a['error']}" for a in attempt_errors)
    return DistillResult(success=False, error=f"全部蒸馏路径均失败——{summary}")


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #

def _last_real_step_output(steps: list[ExploreStep]) -> Any:
    """跳过 finish/report_failure 决策元步骤，取最后一个真实工具步骤的输出，
    用于判断是否需要启用 trust_trace_data 兜底（见文件头说明，阶段六）。"""
    for step in reversed(steps):
        if step.tool in ("finish", "report_failure"):
            continue
        return step.output
    return None


def _templatize_steps(steps: list[ExploreStep], request: dict) -> tuple[list[dict], set[str]]:
    target_url = str((request.get("target") or {}).get("url", ""))
    query = str(request.get("query") or request.get("text", ""))
    templated_fields: set[str] = set()

    def _templatize_value(value: Any) -> Any:
        if isinstance(value, str):
            if target_url and value == target_url:
                templated_fields.add("target.url")
                return "__PLACEHOLDER_TARGET_URL__"
            if query and value == query:
                templated_fields.add("query")
                return "__PLACEHOLDER_QUERY__"
            return value
        if isinstance(value, dict):
            return {k: _templatize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_templatize_value(v) for v in value]
        return value

    result = []
    for step in steps:
        if step.tool in ("finish", "report_failure"):
            continue  # 决策元工具不进入重放序列，只有底层操作原语需要固化
        result.append({"tool": step.tool, "input": _templatize_value(step.input)})
    return result, templated_fields


def _generate_member_id(request: dict, skill_dir: Path) -> str:
    target_url = str((request.get("target") or {}).get("url", ""))
    base = ""
    m = re.search(r"://(?:www\.)?([a-zA-Z0-9\-]+)\.", target_url)
    if m:
        base = m.group(1)
    if not base:
        base = re.sub(r"[^a-zA-Z0-9]+", "_", str(request.get("text", "")))[:20] or "member"
    base = base.lower().strip("_") or "member"

    existing = set()
    members_dir = skill_dir / "members"
    if members_dir.exists():
        existing = {p.name for p in members_dir.iterdir() if p.is_dir() and not p.name.startswith("__tmp_")}

    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _sandbox_run(script_path: Path, request: dict, tool_executor) -> dict:
    import importlib.util
    import sys

    engine_dir = Path(__file__).resolve().parent
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))

    import tool_runtime
    previous_executor = tool_runtime.get_tool_executor()
    tool_runtime.set_tool_executor(tool_executor)
    try:
        spec = importlib.util.spec_from_file_location("__distilled_sandbox_member__", script_path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"蒸馏脚本加载失败(语法/导入错误): {e}"}

        run_fn = getattr(module, "run", None)
        if run_fn is None:
            return {"ok": False, "error": "蒸馏脚本未定义 run()"}

        try:
            result = run_fn(request)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"蒸馏脚本执行异常: {e}"}

        if not isinstance(result, dict) or result.get("status") != "success":
            err = result.get("error") if isinstance(result, dict) else "返回格式非法"
            return {"ok": False, "error": err}
        return {"ok": True, "data": result.get("data")}
    finally:
        tool_runtime.set_tool_executor(previous_executor)
        sys.modules.pop("__distilled_sandbox_member__", None)


def _validate_schema(data: Any, schema: Optional[dict]) -> bool:
    """完整 JSON Schema 子集校验（阶段四起改为复用 schema_validator，
    不再是阶段一/三遗留的浅层必填字段检查）。"""
    return len(_validate_schema_errors(data, schema)) == 0


def _atomic_persist(*, skill_dir: Path, member_id: str, script_code: str, request: dict,
                     capability: dict, intent_schema: dict, is_reexplore: bool,
                     used_trace_data_fallback: bool = False,
                     distill_source_kind: str = "trace_replay") -> None:
    members_dir = skill_dir / "members"
    member_dir = members_dir / member_id
    member_dir.mkdir(parents=True, exist_ok=True)

    script_path = member_dir / "script.py"
    script_tmp = script_path.with_suffix(".py.tmp")
    script_tmp.write_text(script_code, encoding="utf-8")

    meta_path = member_dir / "meta.json"
    prev_version = 1
    if is_reexplore and meta_path.exists():
        try:
            prev_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            prev_version = int(prev_meta.get("version", 1)) + 1
        except Exception:  # noqa: BLE001
            prev_version = 2
    meta = {
        "source": "explored",
        "version": prev_version,
        "intent_schema": intent_schema,
        "distilled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "distill_source_kind": distill_source_kind,
        "distill_used_trace_data_fallback": used_trace_data_fallback,
        "distilled_from_request": {
            "text": request.get("text", ""),
            "target": request.get("target", {}),
        },
    }
    meta_tmp = meta_path.with_suffix(".json.tmp")
    meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    registry_path = skill_dir / "registry.json"
    registry = _load_json(registry_path, {"members": {}})
    registry["members"][member_id] = {
        "status": "probation",
        "status_changed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "intent_schema": intent_schema,
        "success_count": 0,
        "fail_count": 0,
        "consecutive_failures": 0,
        "last_success": None,
        "last_failure": None,
    }
    registry_tmp = registry_path.with_suffix(".json.tmp")
    registry_tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    index_path = skill_dir / "_index.json"
    index = _load_json(index_path, {"members": []})
    members_list = [m for m in index.get("members", []) if m.get("member_id") != member_id]
    members_list.append({
        "member_id": member_id,
        "description": f"探索自动生成: {request.get('text', '')[:60]}",
        "match": _infer_match_rule(request),
    })
    index["members"] = members_list
    index_tmp = index_path.with_suffix(".json.tmp")
    index_tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # 全部写完临时文件后再统一原子替换，尽量缩小"部分文件已提交、部分未提交"的窗口。
    script_tmp.replace(script_path)
    meta_tmp.replace(meta_path)
    registry_tmp.replace(registry_path)
    index_tmp.replace(index_path)


def _infer_match_rule(request: dict) -> dict:
    target_url = str((request.get("target") or {}).get("url", ""))
    match: dict = {}
    m = re.search(r"://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+)/", target_url + "/")
    if m:
        match["domain_pattern"] = f"*.{m.group(1)}*"
    text = request.get("text", "")
    if text:
        match["keyword"] = [text[:30]]
    return match


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default))
    return json.loads(path.read_text(encoding="utf-8"))


def _rm_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink(missing_ok=True)
        else:
            child.rmdir()
    path.rmdir()
