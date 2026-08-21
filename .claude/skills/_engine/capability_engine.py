"""
capability_engine.py
=====================
Generative-Capability Skill 的通用调度引擎（平台内置，跨 skill 复用）。

对应文档: next_doc/generative-capability-skill-plan.md
当前阶段: 阶段一 —— resolve/execute 两步 + 确定性匹配 + registry/index 读写。
          explore() / distill() 在此阶段仅提供接口占位，尚未接入探索子agent
          （见文档"实施优先级建议"阶段三）。

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

    def __init__(self, skill_dir: str | Path, llm_resolver: Optional[Callable] = None):
        self.skill_dir = Path(skill_dir)
        self.capability = self._load_yaml(self.skill_dir / "capability.yaml")
        self.index_path = self.skill_dir / "_index.json"
        self.registry_path = self.skill_dir / "registry.json"
        self.members_dir = self.skill_dir / "members"
        self.index = self._load_json(self.index_path, default={"members": []})
        self.registry = self._load_json(self.registry_path, default={"members": {}})
        # llm_resolver: Callable[[str, list[dict]], list[str]]
        # 输入: (request_text, candidate_summaries) -> 命中的 member_id 列表
        # 阶段一默认不接入真实 LLM 调用，留空即表示"未命中就是 miss"。
        self.llm_resolver = llm_resolver

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
            picked = self.llm_resolver(request.get("text", ""), summaries)
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
        """状态机流转（阶段一支持 probation -> trusted / degraded -> dead 判定；
        重新探索由阶段三的 explore()/distill() 接管）。"""
        lifecycle = self.capability.get("lifecycle", {})
        promote_at = lifecycle.get("probation_success_threshold", 3)
        degrade_at = lifecycle.get("degrade_failure_threshold", 3)

        status = entry.get("status", "probation")
        if status == "probation" and entry.get("success_count", 0) >= promote_at:
            entry["status"] = "trusted"
        elif status in {"trusted", "probation"} and entry.get("consecutive_failures", 0) >= degrade_at:
            entry["status"] = "degraded"
        # degraded -> dead 的判定发生在重新探索失败之后（explore()，阶段三实现）

    # ---------------------- 第 3/4 步: explore / distill（阶段三接入） ---------------------- #

    def explore(self, request: dict) -> ExecuteResult:
        """
        探索子agent接入点。阶段一尚未实现，先给出明确的占位行为，
        避免调用方误以为"miss 后自动能拿到结果"。
        """
        return ExecuteResult(
            status="fail",
            error="explore() 尚未实现（见 next_doc/generative-capability-skill-plan.md 阶段三）。"
                  "当前 resolve() 未命中时会直接返回 miss，请人工新增 member 或等待阶段三上线。",
        )

    def distill(self, trace: Any, intent_schema: dict) -> dict:
        raise NotImplementedError("distill() 在阶段三随 explore() 一并实现。")

    # ---------------------- 对外统一入口 ---------------------- #

    def call(self, request: dict) -> CapabilityCallResult:
        resolved = self.resolve(request)
        if resolved.status == "hit":
            for member_id in resolved.member_ids:
                exec_result = self.execute(member_id, request)
                if exec_result.status == "success":
                    return CapabilityCallResult(
                        status="success",
                        data=exec_result.data,
                        member_id=member_id,
                        resolve_reason=resolved.reason,
                    )
            # 命中的候选全部执行失败 -> 尝试探索（阶段三前会得到明确的 not_implemented 提示）
            explore_result = self.explore(request)
            if explore_result.status == "success":
                return CapabilityCallResult(status="success", data=explore_result.data,
                                             member_id=explore_result.member_id, resolve_reason="explored")
            return CapabilityCallResult(status="not_implemented", error=explore_result.error,
                                         resolve_reason=resolved.reason)

        # miss -> 探索
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

    parser = argparse.ArgumentParser(description="capability_engine 阶段一自测")
    parser.add_argument("skill_dir", help="generative-capability skill 目录路径")
    parser.add_argument("--url", required=True, help="目标 URL，用于 domain_pattern 匹配测试")
    parser.add_argument("--query", default="", help="搜索关键词，透传给 member")
    args = parser.parse_args()

    engine = CapabilityEngine(args.skill_dir)
    req = {"text": args.url, "target": {"url": args.url}, "query": args.query}
    result = engine.call(req)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
