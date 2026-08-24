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
    # [本次新增] 无论成功与否都尽量携带的探索上下文（steps 摘要 + 最终数据 +
    # 每条蒸馏候选路径的自测/修复历史），失败时用于让调用方（上层 agent /
    # 人工）据此判断"探索明明成功了，为什么代码复用会炸"，而不是只拿到一句
    # 摘要错误字符串；成功时也带上，方便审计修复过程。见文件头本次新增说明。
    trace_context: Optional[dict] = None
    # [本次新增，见 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
    # 第3节 3.3b"explore 阶段产出 playbook.md"] 当三条脚本蒸馏路径全部失败、
    # 但调用方注入了 playbook_repo 时，distill() 会退化为把本次探索整理成一份
    # playbook.md 落盘（而不是彻底判整次探索失败），这种情况下 success=True 但
    # playbook_only=True，member 目录下没有 script.py，只有一份可供 SKILL 档
    # （PlaybookRunner）参照执行的步骤说明——语义上是"script 挂了/写不出来，
    # 退到更鲁棒但更贵的 skill 档"，而不是"这次探索完全没有任何沉淀"。
    playbook_only: bool = False


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
5. `tool_runtime.get_tool_executor()` 只用于驱动浏览器等外部系统的领域
   原语调用；过滤空结果、判断验证码/登录墙关键词、正则清洗文本、重试这类
   纯逻辑处理，直接用标准 Python（字符串/正则/循环/条件判断）实现，不要
   为了"看起来统一"而把这些逻辑也硬塞进 `executor()` 调用——脚本主体是
   完全自由的 Python，领域原语只是其中可选的一类调用。
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


def _build_trace_context(trace: ExploreTrace) -> dict:
    """[本次新增] 把探索 trace 整理成一份结构化上下文，贯穿蒸馏全过程：
    既用于喂给"修复脚本"的 LLM 调用，也在最终失败时原样带回给调用方
    （见 DistillResult.trace_context）。这样"探索明明成功了，脚本却复用
    失败"这类落差，不再只体现为一句 attempt_errors 摘要，而是能让上层
    agent/人工对照每一步真实工具调用和输出去判断代码到底哪里想当然了。"""
    return {
        "explore_success": trace.success,
        "explore_stop_reason": trace.stop_reason,
        "explore_final_data": trace.data,
        "explore_steps": [
            {
                "tool": step.tool,
                "input": step.input,
                "output_preview": _truncate_for_prompt(step.output),
                "error": step.error,
            }
            for step in trace.steps
        ],
    }


_REPAIR_SYSTEM_PROMPT = """你是一个"蒸馏脚本修复器"。你会收到三样东西：
1. 一次成功的探索过程的完整操作记录（每一步工具/参数/返回值），这证明了
   目标网站/接口本身是可达的、这次任务是能被完成的——如果修复后的脚本还是
   拿不到东西，大概率是脚本自己的问题，而不是"这事根本做不到"。
2. 一段刚刚在沙箱自测中执行失败的 `run(input) -> dict` 脚本源码。
3. 这次自测失败的具体报错信息（异常堆栈、或返回的 status/error 字段）。

你的任务：对照探索记录里"实际生效的那条路径"，定位脚本为什么会在自测里
失败（常见原因包括但不限于：选择器/等待时机和探索时不完全一致、把探索时
的某个中间状态误当成稳定前提、异常处理过粗放导致把可重试的偶发错误也
直接判失败、硬编码了本不该硬编码的值），然后输出一份修复后的完整脚本。

硬性要求：
1. 必须定义 `def run(input: dict) -> dict`，返回
   `{"status": "success"|"fail", "data": ..., "error": ...}`。
2. 脚本内如需调用底层操作原语（如 browser_navigate/browser_extract_content
   这类领域工具），通过 `from tool_runtime import get_tool_executor` 拿到
   执行器，用 `executor(tool_name, tool_input)` 调用——不要引入探索记录里
   没出现过的工具名，也不要假设这些工具有其它调用方式。
3. 不要硬编码探索时用到的具体 target.url / query 值，改为从 `input` 参数
   读取。
4. 如果失败原因看起来是"目标站点这次请求本身被限流/拦截"这类偶发性问题
   而非脚本逻辑错误，可以在脚本里加合理的重试/退避，但不要仅仅通过"吞掉
   异常然后返回一个编造的空 success"来掩盖失败。
5. 只输出脚本源码本身，不要用 Markdown 代码块包裹，不要输出任何解释文字。
"""


def _repair_script_with_llm(
    *, script_code: str, test_error: Any, trace_context: dict,
    request: dict, intent_schema: dict, skill_name: str, member_id: str,
    llm_helper: Any, header_template: str,
) -> Optional[str]:
    """[本次新增] 蒸馏候选路径自测失败时，不直接丢弃/退到下一条路径，而是
    先把"探索上下文 + 失败脚本 + 失败原因"一起交给 LLM 尝试修复一次，修复
    产物仍会走同一套沙箱自测，不享受任何特权。失败（LLM 调用异常/输出不像
    Python 源码）时返回 None，调用方据此按原有逻辑继续走下一条路径，不会
    抛异常中断整个 distill()。"""
    try:
        user_content = json.dumps(
            {
                "request": request,
                "intent_schema": intent_schema,
                "explore_context": trace_context,
                "failing_script": script_code,
                "self_test_error": test_error,
            },
            ensure_ascii=False,
        )
        raw = llm_helper.ask(
            user_content,
            system=_REPAIR_SYSTEM_PROMPT,
            max_retries=2,
            override_temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        from .capability_debug import capability_debug_log
        capability_debug_log(
            "distill_repair_call_failed", {"member_id": member_id, "error": str(e)},
            where="distiller._repair_script_with_llm",
        )
        return None

    code = raw.strip()
    code = code.removeprefix("```python").removeprefix("```").removesuffix("```").strip()
    if "def run(" not in code:
        from .capability_debug import capability_debug_log
        capability_debug_log(
            "distill_repair_invalid_output",
            {"member_id": member_id, "raw_preview": code[:300]},
            where="distiller._repair_script_with_llm",
        )
        return None

    return header_template.format(skill_name=skill_name, member_id=member_id) + code


def distill(
    trace: ExploreTrace,
    request: dict,
    intent_schema: dict,
    skill_dir: Path,
    capability: dict,
    self_test_executor,
    reexplore_member_id: Optional[str] = None,
    llm_helper: Any = None,
    playbook_repo: Any = None,
) -> DistillResult:
    """
    self_test_executor: Callable[[str, dict], dict] —— 蒸馏产物沙箱自测时
    用于重放动作序列的工具执行器（阶段三里直接复用探索时的同一个
    tool_executor，因为自测的目的是验证"这条路径确实可复用"，
    而不是验证语法正确性——语法层面已经由本函数内的 import 校验覆盖）。

    llm_helper: [阶段二十五新增] 通常是当前 `Agent.llm_helper`，用于
    script_source 缺失时的"LLM 事后总结"路径（见文件头"三条蒸馏路径"）。
    不传时该路径自动跳过，直接退到 trace-replay，行为等同阶段二十四。

    playbook_repo: [本次新增，见 DistillResult.playbook_only 说明] 通常是
    `skill_tier.build_playbook_repo(skill_dir)` 构造的
    `hybrid_exec.playbook_repository.PlaybookRepository`。不传时行为与
    此前完全一致——三条脚本路径全部失败即直接返回失败，不做任何 playbook
    兜底（保持向后兼容，不默认改变既有调用方行为）。

    依次尝试 script_source -> llm_synthesized -> trace_replay 三条路径
    （见文件头说明），命中第一条产出即可通过自测/校验的就用它，不会
    "既然有 script_source 就一定成功"地跳过后续自测——每条路径产出的
    脚本都要经过同一套沙箱自测 + intent_schema 校验才允许落盘。
    """
    if not trace.success or trace.data is None:
        return DistillResult(success=False, error="探索未成功，无 trace 可供蒸馏")

    trace_context = _build_trace_context(trace)

    if not _validate_schema(trace.data, intent_schema):
        return DistillResult(
            success=False, error="探索产出的数据未通过 intent_schema 校验，拒绝蒸馏",
            trace_context=trace_context,
        )

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

    # [本次新增] 自测失败后允许对同一候选路径做几轮"修复重试"，而不是探索
    # 明明成功、只是自测这一步没通过就直接判该路径失败、退到下一条兜底路径
    # （trace_replay 往往更脆弱，等于白白浪费了一次已经验证可行的方案）。
    # 只对"代码是可修改的"两条路径（script_source / llm_synthesized）生效：
    # trace_replay 本身就是逐步重放，没有"脚本逻辑"可供 LLM 修复。
    # 轮数可由 capability.yaml 的 `distill.repair_attempts` 覆盖，默认 2，
    # 需要 llm_helper 才能生效（未注入时行为等同此前版本，自动跳过）。
    repair_budget = int((capability.get("distill") or {}).get("repair_attempts", 2))

    def _run_self_test(code: str) -> dict:
        tmp_dir = skill_dir / "members" / f"__tmp_{member_id}_{int(time.time() * 1000)}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_script = tmp_dir / "script.py"
        tmp_script.write_text(code, encoding="utf-8")
        try:
            return _sandbox_run(tmp_script, request, self_test_executor)
        finally:
            _rm_tree(tmp_dir)

    attempt_errors: list[dict] = []
    for distill_source_kind, script_code in attempts:
        use_trace_fallback = distill_source_kind == "trace_replay" and trace_fallback_used
        repairable = distill_source_kind in ("script_source", "llm_synthesized") and llm_helper is not None
        current_kind = distill_source_kind
        current_code = script_code
        round_errors: list[dict] = []

        for attempt_no in range(repair_budget + 1):  # 第 0 轮 = 原始候选本身
            test_result = _run_self_test(current_code)

            if test_result.get("ok"):
                final_data = test_result.get("data")
                if not _validate_schema(final_data, intent_schema):
                    round_errors.append({
                        "path": current_kind, "round": attempt_no,
                        "error": "自测数据未通过 intent_schema 校验",
                    })
                    break  # schema 不过不是"脚本能不能修"的问题，不再重复修复本候选

                # [本次新增] 结构/类型对齐之外，再做一次"是否为假数据脚本"的
                # 合理性检查（见文件末尾 _check_script_plausibility 说明），
                # schema 校验无法识别"结构对但数据是写死的"这类语义问题。
                # 只对 script_source / llm_synthesized 两条"代码是探索子agent/
                # LLM 自己写出来的"路径生效——trace_replay 路径的脚本只是把
                # 动作序列原样转发给 self_test_executor，输出是否随 input 变化
                # 取决于自测环境注入的执行器本身是否变化（例如测试/CI 里的桩
                # 执行器常常不随输入变化），这是自测环境的属性，不是脚本本身
                # 硬编码假数据的证据，不应该被这项检查误伤。
                if current_kind in ("script_source", "llm_synthesized"):
                    plausible, plausibility_reason = _check_script_plausibility(
                        script_code=current_code,
                        request=request,
                        original_data=final_data,
                        self_test_executor=self_test_executor,
                        llm_helper=llm_helper,
                        tmp_dir_factory=lambda: (
                            skill_dir / "members" / f"__tmp_plausibility_{member_id}_{int(time.time() * 1000)}"
                        ).joinpath("script.py"),
                    )
                    if not plausible:
                        round_errors.append({
                            "path": current_kind, "round": attempt_no,
                            "error": f"合理性检查未通过（疑似假数据脚本）：{plausibility_reason}",
                        })
                        break  # 假数据不是"脚本能不能修"的问题，不再重复修复本候选

                try:
                    _atomic_persist(
                        skill_dir=skill_dir,
                        member_id=member_id,
                        script_code=current_code,
                        request=request,
                        capability=capability,
                        intent_schema=intent_schema,
                        is_reexplore=reexplore_member_id is not None,
                        used_trace_data_fallback=use_trace_fallback,
                        distill_source_kind=current_kind,
                    )
                except Exception as e:  # noqa: BLE001
                    round_errors.append({"path": current_kind, "round": attempt_no, "error": f"落盘失败: {e}"})
                    break

                if attempt_errors or round_errors:
                    # [阶段二十五+本次新增] 前面的候选/修复轮次失败了，但最终
                    # 还是成功落盘——如实记录，不因为最终成功就静默吞掉，
                    # 方便事后判断"修复"实际救回了多少次原本会被浪费的探索。
                    capability_debug_log(
                        "distill_attempt_partial_failure",
                        {"member_id": member_id, "succeeded_path": current_kind,
                         "repaired": attempt_no > 0,
                         "failed_attempts": attempt_errors + round_errors},
                        where="distiller.distill",
                    )
                return DistillResult(
                    success=True, member_id=member_id, data=final_data, trace_context=trace_context,
                )

            round_errors.append({"path": current_kind, "round": attempt_no, "error": test_result.get("error")})

            if attempt_no >= repair_budget or not repairable:
                break

            repaired = _repair_script_with_llm(
                script_code=current_code,
                test_error=test_result.get("error"),
                trace_context=trace_context,
                request=request,
                intent_schema=intent_schema,
                skill_name=skill_name,
                member_id=member_id,
                llm_helper=llm_helper,
                header_template=LLM_SYNTHESIZED_HEADER_TEMPLATE,
            )
            if repaired is None:
                break  # 修复调用本身失败（非脚本问题），不再浪费剩余修复预算
            current_code = repaired
            current_kind = "llm_repaired"

        attempt_errors.extend(round_errors)

    # 三条路径（含各自的修复重试）全部失败：完整记录每条路径/每轮修复各自
    # 的失败原因到 capability_debug.jsonl（不做任何截断），并把探索上下文
    # 一并带回给调用方——不再只靠一句摘要字符串体现"探索明明成功了"这个
    # 关键落差，上层 agent/人工可以直接对照 trace_context 判断代码到底
    # 哪里想当然了、是否值得再手动修一次。
    capability_debug_log(
        "distill_all_paths_failed",
        {"member_id": member_id, "skill_name": skill_name, "request": request,
         "attempted_paths": [k for k, _ in attempts], "attempt_errors": attempt_errors,
         "repair_budget": repair_budget},
        where="distiller.distill",
    )
    summary = "；".join(f"{a['path']}#{a.get('round', 0)}: {a['error']}" for a in attempt_errors)

    # [本次新增] 三条脚本蒸馏路径全部失败，但探索本身确实成功、数据也已经
    # 通过 intent_schema 校验（见函数开头）——这次探索不是"没有任何可复用
    # 价值"，只是"这次没能固化成确定性代码"。playbook_repo 存在时，退化为
    # 把探索过程整理成一份 playbook.md，交给更鲁棒（但更贵）的 SKILL 档
    # （PlaybookRunner）今后参照执行，而不是彻底丢弃这次探索的沉淀。
    if playbook_repo is not None:
        playbook_result = _distill_to_playbook(
            trace=trace, request=request, skill_name=skill_name, member_id=member_id,
            skill_dir=skill_dir, capability=capability, intent_schema=intent_schema,
            is_reexplore=reexplore_member_id is not None, playbook_repo=playbook_repo,
            script_failure_summary=summary,
        )
        if playbook_result is not None:
            capability_debug_log(
                "distill_fallback_to_playbook",
                {"member_id": member_id, "skill_name": skill_name,
                 "script_failure_summary": summary},
                where="distiller.distill",
            )
            return playbook_result

    return DistillResult(
        success=False,
        error=f"全部蒸馏路径均失败（含修复重试）——{summary}",
        trace_context=trace_context,
    )


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


# --------------------------------------------------------------------------- #
# 脚本合理性检查（假数据检测）
#
# 对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第2节。
#
# 背景：schema 校验只保证"结构对、类型对"，天然无法识别"探索子agent把本次
# 抓取到的具体结果硬编码进脚本、对任意 input 都返回同一份静态数据"这类语义
# 问题——这类脚本可以轻松通过 intent_schema 校验，却完全不可复用。
#
# 分两级触发（与 capability_engine.resolve() 的"确定性匹配优先、命中失败
# 再上 LLM"分级触发原则一致，避免每次蒸馏都产生 LLM 调用开销）：
#   1. 规则预检（零成本）：用一个"扰动过的 request"（把 request 里的字符串
#      字段替换成不同的取值）重新跑一遍脚本，若两次输出完全相同，判定为
#      "可疑"——正常情况下换了 query/url，抓取结果不应该逐字节一致。
#   2. 只有规则预检判定"可疑"时，才发起一次独立、单一职责的 LLM 复核：
#      把脚本源码 + 本次 request 交给模型，只回答"是否把探索观察到的具体
#      数据硬编码进了源码 / 是否真实依赖 input 参数执行"。
#      未注入 llm_helper 时，规则预检判定可疑就直接判失败（保守默认——
#      没有能力确认"可疑"是否只是巧合，不能放行）。
# --------------------------------------------------------------------------- #

_PLAUSIBILITY_PROMPT_TEMPLATE = """你在审查一个由"探索子agent"自动生成、声称可参数化复用的抓取/执行脚本。
只做一件事：判断这段脚本是否把某一次探索过程中观察到的具体数据（标题/URL/
数字/人名等具体值）硬编码进了源码，而不是真正使用 input 参数重新执行、
对不同输入产生不同结果。

本次探索的 request 参数（供参考，判断脚本是否真的用到了这些参数）：
{request_json}

脚本源码：
```python
{script_code}
```

只输出一个 JSON 对象，不要有任何其它文字：
{{"hardcoded": true|false, "reason": "一句话说明判断依据"}}
"""


def _perturb_request(request: dict) -> Optional[dict]:
    """构造一个"扰动过的" request：把顶层及 target 下的字符串字段换成不同取值。
    找不到可扰动的字符串字段时返回 None（跳过规则预检，直接放行进入落盘，
    避免对不含字符串参数的领域场景误伤）。
    """
    import copy

    perturbed = copy.deepcopy(request)
    changed = False

    def _flip(d: dict) -> None:
        nonlocal changed
        for k, v in list(d.items()):
            if isinstance(v, str) and v.strip():
                d[k] = v + "__plausibility_probe__"
                changed = True
            elif isinstance(v, dict):
                _flip(v)

    _flip(perturbed)
    return perturbed if changed else None


def _check_script_plausibility(
    script_code: str,
    request: dict,
    original_data: Any,
    self_test_executor,
    llm_helper: Any,
    tmp_dir_factory,
) -> "tuple[bool, str]":
    """返回 (是否合理可信, 原因说明)。tmp_dir_factory() 返回一个可写的临时
    脚本路径（调用方负责清理），复用与自测相同的沙箱执行方式。
    """
    perturbed_request = _perturb_request(request)
    if perturbed_request is None:
        return True, "request 中无可扰动的字符串字段，跳过规则预检"

    tmp_script = tmp_dir_factory()
    tmp_script.parent.mkdir(parents=True, exist_ok=True)
    tmp_script.write_text(script_code, encoding="utf-8")
    try:
        perturbed_result = _sandbox_run(tmp_script, perturbed_request, self_test_executor)
    finally:
        _rm_tree(tmp_script.parent)

    if not perturbed_result.get("ok"):
        # 扰动后执行失败：不足以证明是"假数据脚本"（可能是真实依赖参数、
        # 扰动值不合法导致的正常失败），不在此处判定，交由后续路径处理。
        return True, f"扰动后执行失败（非假数据判定依据）：{perturbed_result.get('error')}"

    if perturbed_result.get("data") != original_data:
        return True, "扰动 request 后输出发生变化，规则预检通过"

    # 规则预检判定可疑：换了参数但输出逐字节一致
    if llm_helper is None:
        return False, "扰动 request 后输出与原输出完全一致（疑似硬编码假数据），且未注入 llm_helper 无法复核"

    from .capability_debug import capability_debug_log

    try:
        prompt = _PLAUSIBILITY_PROMPT_TEMPLATE.format(
            request_json=json.dumps(request, ensure_ascii=False, indent=2),
            script_code=script_code,
        )
        raw = llm_helper.ask(
            prompt,
            system="你是一名严谨的代码审查者，只输出 JSON，不输出多余解释。",
        )
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        verdict = json.loads(cleaned)
        hardcoded = bool(verdict.get("hardcoded"))
        reason = str(verdict.get("reason", ""))
    except Exception as e:  # noqa: BLE001
        capability_debug_log(
            "distill_plausibility_llm_check_failed", {"error": str(e)},
            where="distiller._check_script_plausibility",
        )
        # LLM 复核本身失败（非脚本问题）：保守起见按规则预检的可疑结论处理，
        # 不放行——宁可错杀一次可复用探索，也不让假数据脚本蒸混过关。
        return False, f"规则预检可疑，且 LLM 复核调用失败（保守判定为不合理）：{e}"

    if hardcoded:
        return False, f"LLM 复核判定疑似硬编码假数据：{reason}"
    return True, f"规则预检可疑，但 LLM 复核判定为合理（如输出恰好稳定）：{reason}"


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
    registry_entry = {
        "status": "probation",
        "status_changed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "intent_schema": intent_schema,
        "success_count": 0,
        "fail_count": 0,
        "consecutive_failures": 0,
        "last_success": None,
        "last_failure": None,
    }
    # [本次新增，阶段 C] trace-replay 兜底路径产出的脚本结构性脆弱（只会
    # 顺序重放动作序列，遇到探索时的探测性弯路/环境差异容易在真实调用时
    # 才炸），不应该和 script_source/llm_synthesized 产出的 member 享有
    # 同样快的"转正"速度。这里写一个 member 级别的 probation 门槛覆盖值，
    # 由 `capability_engine.py::_apply_lifecycle()` 优先读取；不覆盖
    # `lifecycle.degrade_failure_threshold`（降级速度不变，脆弱脚本该多快
    # 掉出 trusted 不受影响，只是变得更难升上去）。领域可以在
    # capability.yaml -> lifecycle.trace_replay_probation_success_threshold
    # 显式声明具体门槛；未声明时默认取"领域默认门槛的两倍"，作为一个不需要
    # 额外配置就生效的合理保守值。
    if distill_source_kind == "trace_replay":
        lifecycle_cfg = capability.get("lifecycle", {})
        default_threshold = lifecycle_cfg.get("probation_success_threshold", 3)
        override = lifecycle_cfg.get(
            "trace_replay_probation_success_threshold", default_threshold * 2
        )
        registry_entry["probation_success_threshold_override"] = override
    registry["members"][member_id] = registry_entry
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


def _build_playbook_markdown(trace: ExploreTrace, request: dict, skill_name: str, member_id: str) -> str:
    """[本次新增] 把一次成功探索的 trace 整理成人类可读的步骤说明（playbook），
    供 SKILL 档（`hybrid_exec.playbook_runner.PlaybookRunner`）驱动的轻量
    Agent 今后参照执行——不是逐动作固化重放（那是 trace_replay 脚本在做的
    事，天然脆弱），而是给出"大致该怎么做"的 SOP，允许执行时按页面实际状态
    做局部适应。刻意不把本次探索观察到的具体数据（标题/URL/数字等）写进
    步骤描述本身，只保留"用了哪个工具、传了什么参数结构、目的是什么"，
    与 explorer/prompt.md 里对 script_source 路径"禁止硬编码具体数据"的
    要求保持一致的精神（playbook 同样不该是"这次跑出来的答案的说明书"，
    而应该是"下次遇到类似请求该怎么做"的说明书）。
    """
    lines: list[str] = [
        f"# {skill_name} / playbook / {member_id}",
        "",
        "本 playbook 由 generative-capability 引擎在脚本蒸馏失败后自动整理生成"
        "（source: explored, distill_source_kind: playbook），供 SKILL 档轻量 "
        "Agent 参照执行。它描述的是一套可复用的操作步骤，不是本次探索观察到的"
        "具体结果——执行时请以当前 `input`/页面实际状态为准，选择器、文案等"
        "细节如与描述不完全一致，按目的做合理调整。",
        "",
        "## 原始请求（仅供理解意图，不代表本次调用的 input）",
        f"- text: {request.get('text', '')}",
        f"- target: {json.dumps(request.get('target', {}), ensure_ascii=False)}",
        "",
        "## 步骤",
    ]
    step_no = 0
    for step in trace.steps:
        if step.error:
            # 探索过程中的探测性失败/死胡同不写进 playbook 步骤序列——
            # playbook 描述的是"该怎么做"，不是"探索时踩过哪些坑"，与
            # trace_replay 脚本"不区分死胡同和关键路径导致重放必炸"的
            # 已知局限刻意划清界限（见文件头三条蒸馏路径说明）。
            continue
        step_no += 1
        input_desc = json.dumps(step.input, ensure_ascii=False) if step.input else "{}"
        lines.append(f"{step_no}. 调用工具 `{step.tool}`，参数结构参考: `{input_desc}`")
    if step_no == 0:
        lines.append("（本次探索未记录到非失败的具体步骤，仅可参考最终产出的数据形状。）")
    lines.extend([
        "",
        "## 预期产出形状",
        "执行完成后应产出符合本 skill `intent_schema_template` 的结构化数据"
        "（可参照下方本次探索实际取得的数据形状，具体取值仅供参考，不是"
        "固定答案）：",
        "```json",
        json.dumps(trace.data, ensure_ascii=False, indent=2),
        "```",
    ])
    return "\n".join(lines)


def _persist_playbook_member(*, skill_dir: Path, member_id: str, request: dict,
                              capability: dict, intent_schema: dict, is_reexplore: bool) -> None:
    """[本次新增] 与 `_atomic_persist()` 对称，但只登记 member 的检索元信息
    （meta.json + registry.json + _index.json），不写 `script.py`——playbook
    正文由调用方通过 `playbook_repo.save_new_version()` 单独落盘（复用
    `hybrid_exec.playbook_repository.PlaybookRepository` 的既有实现，不在
    这里重复一套 playbook 存储逻辑）。member 目录下没有 script.py 时，
    `CapabilityEngine._load_member_run()` 会返回 None，`execute()` 判该
    member "脚本加载失败"，随后 `call()` 会走到 `_try_skill()`，用同一个
    member_id 去 playbook_repo 里找 active playbook——这正是本函数存在的
    目的：让 resolve() 今后能检索到这个 member，实际执行交给 SKILL 档。
    """
    members_dir = skill_dir / "members"
    member_dir = members_dir / member_id
    member_dir.mkdir(parents=True, exist_ok=True)

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
        "distill_source_kind": "playbook",
        "distill_used_trace_data_fallback": False,
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
        # 与 script 档区分：execute() 天然会因缺少 script.py 而失败，
        # 该状态字段只是让人工/事后审计能一眼看出"这是一个只有 playbook
        # 可用的 member"，不参与任何现有状态机判断逻辑。
        "execution_tier": "skill_only",
    }
    registry_tmp = registry_path.with_suffix(".json.tmp")
    registry_tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    index_path = skill_dir / "_index.json"
    index = _load_json(index_path, {"members": []})
    members_list = [m for m in index.get("members", []) if m.get("member_id") != member_id]
    members_list.append({
        "member_id": member_id,
        "description": f"探索自动生成(playbook): {request.get('text', '')[:60]}",
        "match": _infer_match_rule(request),
    })
    index["members"] = members_list
    index_tmp = index_path.with_suffix(".json.tmp")
    index_tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    meta_tmp.replace(meta_path)
    registry_tmp.replace(registry_path)
    index_tmp.replace(index_path)


def _distill_to_playbook(*, trace: ExploreTrace, request: dict, skill_name: str, member_id: str,
                          skill_dir: Path, capability: dict, intent_schema: dict, is_reexplore: bool,
                          playbook_repo: Any, script_failure_summary: str) -> Optional["DistillResult"]:
    """[本次新增] `distill()` 三条脚本路径全部失败后的兜底：整理 playbook.md
    并落盘登记 member。任何一步出错都静默返回 None（退回调用方原有的
    "全部路径失败"错误信息，不让 playbook 兜底本身的异常掩盖真正的失败
    原因），因为这是锦上添花的兜底路径，不应该比它试图挽救的失败更显眼。
    """
    try:
        markdown = _build_playbook_markdown(trace, request, skill_name, member_id)
        playbook_repo.save_new_version(member_id, markdown, created_by="agent_explorer")
        _persist_playbook_member(
            skill_dir=skill_dir, member_id=member_id, request=request,
            capability=capability, intent_schema=intent_schema, is_reexplore=is_reexplore,
        )
    except Exception:  # noqa: BLE001 — 兜底路径本身失败时静默退回主失败结果
        return None

    trace_context = _build_trace_context(trace)
    trace_context["script_distill_failure_summary"] = script_failure_summary
    return DistillResult(
        success=True, member_id=member_id, data=trace.data,
        trace_context=trace_context, playbook_only=True,
    )


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
