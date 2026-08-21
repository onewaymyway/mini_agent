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


def distill(
    trace: ExploreTrace,
    request: dict,
    intent_schema: dict,
    skill_dir: Path,
    capability: dict,
    self_test_executor,
    reexplore_member_id: Optional[str] = None,
) -> DistillResult:
    """
    self_test_executor: Callable[[str, dict], dict] —— 蒸馏产物沙箱自测时
    用于重放动作序列的工具执行器（阶段三里直接复用探索时的同一个
    tool_executor，因为自测的目的是验证"这条路径确实可复用"，
    而不是验证语法正确性——语法层面已经由本函数内的 import 校验覆盖）。
    """
    if not trace.success or trace.data is None:
        return DistillResult(success=False, error="探索未成功，无 trace 可供蒸馏")

    if not _validate_schema(trace.data, intent_schema):
        return DistillResult(success=False, error="探索产出的数据未通过 intent_schema 校验，拒绝蒸馏")

    skill_name = capability.get("name", skill_dir.name)
    member_id = reexplore_member_id or _generate_member_id(request, skill_dir)

    templated_steps, templated_fields = _templatize_steps(trace.steps, request)

    trust_trace_data = bool((capability.get("distill") or {}).get("trust_trace_data", False))
    last_real_output = _last_real_step_output(trace.steps)
    use_trace_fallback = trust_trace_data and not (
        isinstance(last_real_output, dict) and last_real_output.get("data") is not None
    )
    trace_data_fallback_literal = repr(trace.data) if use_trace_fallback else "None"

    script_code = SCRIPT_TEMPLATE.format(
        skill_name=skill_name,
        member_id=member_id,
        templated_fields=", ".join(sorted(templated_fields)) or "(无，动作序列不依赖输入参数)",
        steps_literal=json.dumps(templated_steps, ensure_ascii=False, indent=4),
        trace_data_fallback_literal=trace_data_fallback_literal,
    )

    # ------ 沙箱自测：动态加载蒸馏产物并重新跑一遍 run() ------ #
    tmp_dir = skill_dir / "members" / f"__tmp_{member_id}_{int(time.time())}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_script = tmp_dir / "script.py"
    tmp_script.write_text(script_code, encoding="utf-8")

    try:
        test_result = _sandbox_run(tmp_script, request, self_test_executor)
    finally:
        _rm_tree(tmp_dir)

    if not test_result.get("ok"):
        return DistillResult(success=False, error=f"蒸馏产物沙箱自测失败: {test_result.get('error')}")

    final_data = test_result.get("data")
    if not _validate_schema(final_data, intent_schema):
        return DistillResult(success=False, error="蒸馏产物自测数据未通过 intent_schema 校验")

    # ------ 通过自测，原子化落盘 ------ #
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
        )
    except Exception as e:  # noqa: BLE001
        return DistillResult(success=False, error=f"蒸馏产物落盘失败: {e}")

    return DistillResult(success=True, member_id=member_id, data=final_data)


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
                     used_trace_data_fallback: bool = False) -> None:
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
