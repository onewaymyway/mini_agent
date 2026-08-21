"""
capability_engine.py
=====================
Generative-Capability Skill 的通用调度引擎（平台内置，跨 skill 复用）。

对应文档: next_doc/generative-capability-skill-plan.md
当前阶段: 阶段三 —— 在阶段一(resolve/execute骨架)、阶段二(LLM二级检索)基础上，
          接入探索子agent(explore) 与蒸馏固化(distill)，见 `explorer_runtime.py`
          与 `distiller.py`。生命周期状态机的 degraded -> 重新探索 -> trusted/dead
          闭环在本阶段打通。

设计原则:
  - 本文件是"引擎"，任何 generative-capability 类型的 skill 都复用同一份代码。
  - skill 之间的差异全部通过各自目录下的 capability.yaml 声明，不写死在引擎里。
  - member 的统一接口: run(input: dict) -> dict
        {"status": "success" | "fail", "data": {...} | None, "error": str | None}
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class ResolveResult:
    status: str                     # "hit" | "miss"
    member_ids: list[str] = field(default_factory=list)
    reason: str = ""                # 用于诊断: "domain_match" | "keyword_match" | "llm_match" | "no_match"


@dataclass
class ExecuteResult:
    status: str                     # "success" | "fail"
    member_id: Optional[str] = None
    data: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class CapabilityCallResult:
    status: str                     # "success" | "fail" | "not_implemented"
    data: Optional[dict] = None
    error: Optional[str] = None
    member_id: Optional[str] = None
    resolve_reason: str = ""


# --------------------------------------------------------------------------- #
# 引擎主体
# --------------------------------------------------------------------------- #

class CapabilityEngine:
    """一个 CapabilityEngine 实例对应一个 generative-capability skill 目录。"""

    def __init__(
        self,
        skill_dir: str | Path,
        llm_resolver: Optional[Callable] = None,
        explore_runner: Optional[Callable[[dict, dict, dict], Any]] = None,
        tool_executor: Optional[Callable[[str, dict], dict]] = None,
    ):
        self.skill_dir = Path(skill_dir)
        self.capability = self._load_yaml(self.skill_dir / "capability.yaml")
        self.index_path = self.skill_dir / "_index.json"
        self.registry_path = self.skill_dir / "registry.json"
        self.members_dir = self.skill_dir / "members"
        self.index = self._load_json(self.index_path, default={"members": []})
        self.registry = self._load_json(self.registry_path, default={"members": {}})
        # llm_resolver: Callable[[str, list[dict]], list[str]]
        # 输入: (request_text, candidate_summaries) -> 命中的 member_id 列表
        # 未注入时，第二级 LLM 检索直接跳过，落回 no_match。
        self.llm_resolver = llm_resolver
        # explore_runner: Callable[[request, intent_schema, explorer_config], ExploreTrace]
        # 阶段三新增：探索子agent的决策循环实现，见 explorer_runtime.build_llm_explorer()。
        # 未注入时 explore() 会明确返回 not_implemented 语义，不会伪造成功。
        self.explore_runner = explore_runner
        # tool_executor: Callable[[tool_name, tool_input], dict]
        # 阶段三新增：真正执行浏览器等底层操作原语的运行时钩子，探索循环与
        # 蒸馏产物的沙箱自测都复用同一个执行器（因为两者都需要"真的能做到"）。
        self.tool_executor = tool_executor

    # ---------------------- 基础文件读写 ---------------------- #

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if yaml is None:
            raise RuntimeError("需要安装 PyYAML: pip install pyyaml --break-system-packages")
        if not path.exists():
            raise FileNotFoundError(f"capability.yaml 不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @staticmethod
    def _load_json(path: Path, default: dict) -> dict:
        if not path.exists():
            return json.loads(json.dumps(default))  # deep copy
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)  # 原子替换，避免写一半的脏文件

    def _save_index(self) -> None:
        self._save_json(self.index_path, self.index)

    def _save_registry(self) -> None:
        self._save_json(self.registry_path, self.registry)

    # ---------------------- 第 1 步: resolve ---------------------- #

    def resolve(self, request: dict) -> ResolveResult:
        """
        request 至少包含:
          {"text": "自然语言请求", "target": {...}}  # target 结构随领域而定，如 {"url": "..."}
        """
        matchers = self.capability.get("domain_matchers", [])
        candidates = [m for m in self.index.get("members", []) if self._is_active(m["member_id"])]

        # 第一级: 确定性匹配（零成本）
        for matcher in matchers:
            hit = self._match_deterministic(matcher, request, candidates)
            if hit:
                return ResolveResult(status="hit", member_ids=[hit], reason=matcher["type"] + "_match")

        # 第二级: LLM 裁决（仅在第一级未命中时触发，且必须显式注入 llm_resolver）
        if self.llm_resolver and candidates:
            summaries = [
                {"member_id": c["member_id"], "description": c.get("description", "")}
                for c in candidates
            ]
            try:
                picked = self.llm_resolver(request.get("text", ""), summaries)
            except Exception as e:  # noqa: BLE001
                # LLM 调用失败(网络/配置问题)与"语义上确实无匹配"是两种不同语义，
                # 不能混为一谈地当作 no_match 静默吞掉，需要在 reason 里如实标注，
                # 上层可据此决定是重试检索还是直接进入 explore。
                return ResolveResult(status="miss", member_ids=[], reason=f"llm_error: {e}")

            # 过滤掉候选集合之外的 id，防止模型幻觉出不存在的 member
            picked = [p for p in picked if p in {c["member_id"] for c in candidates}]
            if picked:
                return ResolveResult(status="hit", member_ids=picked, reason="llm_match")

        return ResolveResult(status="miss", member_ids=[], reason="no_match")

    def _match_deterministic(self, matcher: dict, request: dict, candidates: list[dict]) -> Optional[str]:
        m_type = matcher.get("type")
        field_path = matcher.get("field", "")
        value = self._get_field(request, field_path) or ""
        for c in candidates:
            pattern = c.get("match", {}).get(m_type)
            if not pattern:
                continue
            if m_type == "domain_pattern" and self._domain_match(pattern, value):
                return c["member_id"]
            if m_type == "keyword" and self._keyword_match(pattern, value):
                return c["member_id"]
        return None

    @staticmethod
    def _get_field(obj: dict, path: str) -> Any:
        cur: Any = obj
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur

    @staticmethod
    def _domain_match(pattern: str, value: str) -> bool:
        # 支持简单通配符 *.example.com
        regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
        try:
            return bool(re.search(regex, value))
        except re.error:
            return pattern in value

    @staticmethod
    def _keyword_match(keywords: list[str] | str, value: str) -> bool:
        if isinstance(keywords, str):
            keywords = [keywords]
        return any(k in value for k in keywords)

    def _is_active(self, member_id: str) -> bool:
        status = self.registry.get("members", {}).get(member_id, {}).get("status")
        return status in {"trusted", "probation", "degraded"}  # dead 不参与检索

    # ---------------------- 第 2 步: execute ---------------------- #

    def execute(self, member_id: str, request: dict) -> ExecuteResult:
        entry = self.registry.get("members", {}).get(member_id)
        if not entry:
            return ExecuteResult(status="fail", member_id=member_id, error="member 不在 registry 中")

        run_fn = self._load_member_run(member_id)
        if run_fn is None:
            return ExecuteResult(status="fail", member_id=member_id, error="member 脚本加载失败")

        # 探索蒸馏生成的 member 脚本依赖 tool_runtime 注入的执行器重放动作序列；
        # 人工手写的 member（如 baidu/zhihu）不依赖它，注入与否不影响其运行。
        import sys as _sys
        engine_dir = str(self.skill_dir.parent / "_engine")
        if engine_dir not in _sys.path:
            _sys.path.insert(0, engine_dir)
        try:
            import tool_runtime
            tool_runtime.set_tool_executor(self.tool_executor)
        except ImportError:
            pass

        try:
            result = run_fn(request)
        except Exception as e:  # noqa: BLE001 - 需要捕获任意 member 执行异常
            self._record_execution(member_id, success=False)
            return ExecuteResult(status="fail", member_id=member_id, error=f"执行异常: {e}")

        if not isinstance(result, dict) or result.get("status") != "success":
            self._record_execution(member_id, success=False)
            return ExecuteResult(
                status="fail",
                member_id=member_id,
                error=result.get("error") if isinstance(result, dict) else "member 返回格式非法",
            )

        # schema 校验（阶段一做最基础的结构校验，占位；完整 JSON Schema 校验见后续阶段）
        if not self._validate_schema(result.get("data"), entry.get("intent_schema")):
            self._record_execution(member_id, success=False)
            return ExecuteResult(status="fail", member_id=member_id, error="返回数据未通过 intent_schema 校验")

        self._record_execution(member_id, success=True)
        return ExecuteResult(status="success", member_id=member_id, data=result.get("data"))

    def _load_member_run(self, member_id: str) -> Optional[Callable]:
        script_path = self.members_dir / member_id / "script.py"
        if not script_path.exists():
            return None
        spec = importlib.util.spec_from_file_location(f"member_{member_id}", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)  # type: ignore
        return getattr(module, "run", None)

    @staticmethod
    def _validate_schema(data: Any, schema: Optional[dict]) -> bool:
        if not schema:
            return data is not None
        required = schema.get("required", [])
        if not isinstance(data, dict):
            return False
        return all(field_name in data for field_name in required)

    def _record_execution(self, member_id: str, success: bool) -> None:
        entry = self.registry["members"].setdefault(member_id, {})
        entry["success_count"] = entry.get("success_count", 0) + (1 if success else 0)
        entry["fail_count"] = entry.get("fail_count", 0) + (0 if success else 1)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if success:
            entry["last_success"] = now
            entry["consecutive_failures"] = 0
        else:
            entry["last_failure"] = now
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        self._apply_lifecycle(member_id, entry)
        self._save_registry()

    def _apply_lifecycle(self, member_id: str, entry: dict) -> None:
        """状态机流转: probation -> trusted / (trusted|probation) -> degraded。
        degraded -> dead 的判定发生在重新探索失败之后，见 explore() 中
        `dead_after_reexplore_fail` 的处理（阶段三）。"""
        lifecycle = self.capability.get("lifecycle", {})
        promote_at = lifecycle.get("probation_success_threshold", 3)
        degrade_at = lifecycle.get("degrade_failure_threshold", 3)

        status = entry.get("status", "probation")
        if status == "probation" and entry.get("success_count", 0) >= promote_at:
            entry["status"] = "trusted"
        elif status in {"trusted", "probation"} and entry.get("consecutive_failures", 0) >= degrade_at:
            entry["status"] = "degraded"

    # ---------------------- 第 3/4 步: explore / distill（阶段三） ---------------------- #

    def explore(self, request: dict, reexplore_member_id: Optional[str] = None) -> ExecuteResult:
        """
        探索子agent接入点（阶段三）。

        reexplore_member_id: 若不为空，说明这是针对一个已 degraded 的既有
        member 的"重新探索"（见方案文档第 7 节状态机），成功则原地升版本号
        并回到 probation；失败则按 capability.yaml 的 dead_after_reexplore_fail
        配置决定是否标记为 dead。为空则是全新领域的探索，成功后作为新 member
        落盘。
        """
        if self.explore_runner is None:
            return ExecuteResult(
                status="fail",
                error="explore_runner 未注入，无法启动探索子agent。"
                      "请通过 CapabilityEngine(explore_runner=..., tool_executor=...) "
                      "接入 explorer_runtime.build_llm_explorer() 或等价实现。",
            )

        intent_schema = self.capability.get("intent_schema_template", {})
        explorer_cfg = dict(self.capability.get("explorer", {}))
        explorer_cfg["_resolved_prompt_path"] = str(self.skill_dir / explorer_cfg.get("prompt", "explorer/prompt.md"))
        explorer_cfg["_resolved_tool_allowlist_path"] = str(
            self.skill_dir / explorer_cfg.get("tool_allowlist", "explorer/tool_allowlist.json")
        )

        try:
            trace = self.explore_runner(request, intent_schema, explorer_cfg)
        except Exception as e:  # noqa: BLE001
            return ExecuteResult(status="fail", error=f"探索子agent运行异常: {e}")

        if not getattr(trace, "success", False):
            reason = getattr(trace, "error", "探索未成功")
            if reexplore_member_id:
                self._handle_reexplore_failure(reexplore_member_id)
            return ExecuteResult(status="fail", error=reason)

        distill_result = self._distill(request, trace, intent_schema, reexplore_member_id)
        if not distill_result.success:
            if reexplore_member_id:
                self._handle_reexplore_failure(reexplore_member_id)
            return ExecuteResult(status="fail", error=distill_result.error)

        # 蒸馏落盘会新增/覆盖 members 目录与 index/registry，重新加载进内存保持一致。
        self.index = self._load_json(self.index_path, default={"members": []})
        self.registry = self._load_json(self.registry_path, default={"members": {}})

        return ExecuteResult(status="success", member_id=distill_result.member_id, data=distill_result.data)

    def _distill(self, request: dict, trace: Any, intent_schema: dict,
                 reexplore_member_id: Optional[str]) -> Any:
        # 延迟导入，避免未接入探索能力的调用方也被迫依赖 distiller 的额外条件。
        import sys as _sys
        engine_dir = str(Path(__file__).resolve().parent)
        if engine_dir not in _sys.path:
            _sys.path.insert(0, engine_dir)
        from distiller import distill as _distill_fn

        return _distill_fn(
            trace=trace,
            request=request,
            intent_schema=intent_schema,
            skill_dir=self.skill_dir,
            capability=self.capability,
            self_test_executor=self.tool_executor,
            reexplore_member_id=reexplore_member_id,
        )

    def _handle_reexplore_failure(self, member_id: str) -> None:
        lifecycle = self.capability.get("lifecycle", {})
        if not lifecycle.get("dead_after_reexplore_fail", True):
            return
        entry = self.registry.get("members", {}).get(member_id)
        if entry is None:
            return
        entry["status"] = "dead"
        self._save_registry()

    # ---------------------- 对外统一入口 ---------------------- #

    def call(self, request: dict) -> CapabilityCallResult:
        resolved = self.resolve(request)
        if resolved.status == "hit":
            last_failed_member_id: Optional[str] = None
            for member_id in resolved.member_ids:
                exec_result = self.execute(member_id, request)
                if exec_result.status == "success":
                    return CapabilityCallResult(
                        status="success",
                        data=exec_result.data,
                        member_id=member_id,
                        resolve_reason=resolved.reason,
                    )
                last_failed_member_id = member_id

            # 命中的候选全部执行失败：若该 member 已被判定 degraded，这是一次
            # "重新探索"（复用同一个 member_id，见状态机第 7 节）；否则按普通探索处理。
            reexplore_id = None
            if last_failed_member_id is not None:
                status = self.registry.get("members", {}).get(last_failed_member_id, {}).get("status")
                if status == "degraded":
                    reexplore_id = last_failed_member_id

            explore_result = self.explore(request, reexplore_member_id=reexplore_id)
            if explore_result.status == "success":
                return CapabilityCallResult(status="success", data=explore_result.data,
                                             member_id=explore_result.member_id, resolve_reason="explored")
            return CapabilityCallResult(status="not_implemented", error=explore_result.error,
                                         resolve_reason=resolved.reason)

        # miss -> 全新探索
        explore_result = self.explore(request)
        if explore_result.status == "success":
            return CapabilityCallResult(status="success", data=explore_result.data,
                                         member_id=explore_result.member_id, resolve_reason="explored")
        return CapabilityCallResult(status="not_implemented", error=explore_result.error,
                                     resolve_reason=resolved.reason)


# --------------------------------------------------------------------------- #
# 命令行自测入口
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="capability_engine 阶段三自测")
    parser.add_argument("skill_dir", help="generative-capability skill 目录路径")
    parser.add_argument("--url", required=True, help="目标 URL，用于 domain_pattern 匹配测试")
    parser.add_argument("--query", default="", help="搜索关键词，透传给 member")
    parser.add_argument("--stub-llm-hit", default=None,
                         help="调试用：注入一个固定返回该 member_id 的 LLM 桩解析器，"
                              "用于验证第二级检索的接线逻辑（不代表真实语义裁决）")
    parser.add_argument("--stub-explore-success", action="store_true",
                         help="调试用：注入固定探索成功的桩探索器 + 桩工具执行器，"
                              "验证 explore()/distill() 接线与蒸馏落盘流程")
    parser.add_argument("--stub-explore-fail", default=None,
                         help="调试用：注入固定探索失败(给定原因)的桩探索器，"
                              "验证 explore 失败、以及重新探索场景下标记 dead 的流程")
    args = parser.parse_args()

    resolver = None
    if args.stub_llm_hit:
        from llm_resolver import build_stub_resolver
        resolver = build_stub_resolver([args.stub_llm_hit])

    explore_runner = None
    tool_executor = None
    if args.stub_explore_success:
        from explorer_runtime import ExploreStep, build_stub_explorer
        steps = [ExploreStep(tool="browser_navigate", input={"url": args.url}, output={"ok": True})]
        explore_runner = build_stub_explorer(
            steps=steps, final_data={"results": [{"title": "桩数据", "url": args.url}]}
        )
        tool_executor = lambda name, inp: {"ok": True, "echo": inp}  # noqa: E731
    elif args.stub_explore_fail:
        from explorer_runtime import build_stub_explorer
        explore_runner = build_stub_explorer(final_error=args.stub_explore_fail)
        tool_executor = lambda name, inp: {"ok": True}  # noqa: E731

    engine = CapabilityEngine(args.skill_dir, llm_resolver=resolver,
                               explore_runner=explore_runner, tool_executor=tool_executor)
    req = {"text": args.url, "target": {"url": args.url}, "query": args.query}
    result = engine.call(req)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
