"""
capability_engine.py
=====================
Generative-Capability Skill 的通用调度引擎（平台内置，跨 skill 复用）。

对应文档: next_doc/generative-capability-skill-plan.md
当前阶段: 阶段三 —— 在阶段一(resolve/execute骨架)、阶段二(LLM二级检索)基础上，
          接入探索子agent(explore) 与蒸馏固化(distill)，见 `explorer_runtime.py`
          与 `distiller.py`。生命周期状态机的 degraded -> 重新探索 -> trusted/dead
          闭环在本阶段打通。
          阶段六在此基础上，为状态流转（`_apply_lifecycle`/
          `_handle_reexplore_failure`）额外写入 `status_changed_at` 时间戳，
          供 `health_patrol.py` 精确计算"进入 dead 多久了"（回应阶段四"已知
          遗留"）。
          阶段七：本文件所在包从 `.claude/skills/_engine`（skill 内容目录）
          迁移到 `src/mini_agent/skills/generative_capability`（主项目正常
          子包），包内模块间引用改为相对 import，不再依赖 `sys.path` 手工
          注入；`.claude/skills/<capability-name>/` 下只保留声明式配置与
          运行时数据（capability.yaml / SKILL.md / _index.json / registry.json
          / members/），详见方案文档"9. 与静态 skill 系统的关系"与阶段七
          实施记录。唯一仍然需要 `sys.path` 的地方是加载/自测独立于本包的
          member 脚本文件（`execute()`/`distiller._sandbox_run()`），因为
          它们是运行时动态生成、按路径 `importlib` 加载的文件，不是本包的
          一部分。

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
    # [本次新增] 蒸馏失败时(distill_result.trace_context)透传的探索上下文，
    # 让"探索成功但蒸馏/自测失败"这种情况不再只剩一句摘要错误——调用方能
    # 看到完整 steps/输出，判断是否值得再修一次。见 distiller.py 同名字段。
    trace_context: Optional[dict] = None


@dataclass
class CapabilityCallResult:
    status: str                     # "success" | "fail" | "not_implemented" | "invalid_request"
    data: Optional[dict] = None
    error: Optional[str] = None
    member_id: Optional[str] = None
    resolve_reason: str = ""
    # [本次新增] 同 ExecuteResult.trace_context：仅在 status="not_implemented"
    # 且失败发生在蒸馏阶段时才可能非空，success 时探索上下文已无必要单独
    # 携带（data 就是最终产物）。
    trace_context: Optional[dict] = None


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
        llm_helper: Any = None,
        playbook_repo: Any = None,
        skill_runner: Optional[Callable[[dict, str], dict]] = None,
        enable_skill_upgrade: bool = False,
        skill_upgrade_success_threshold: int = 3,
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
        # llm_helper: [阶段二十五新增] 通常是当前 Agent.llm_helper，透传给
        # distill() 用于 script_source 缺失时的"LLM 事后总结"路径（见
        # distiller.py 文件头"三条蒸馏路径"）。未注入时该路径自动跳过。
        self.llm_helper = llm_helper
        # playbook_repo / skill_runner: [本次新增，见 skill_tier.py]
        # SKILL 档（playbook）依赖，均为可选注入——与 explore_runner/
        # tool_executor 相同的 DI 风格，未注入时 `_try_skill()` 直接跳过，
        # 不改变任何既有调用方的行为。`playbook_repo` 通常由
        # `skill_tier.build_playbook_repo(skill_dir)` 构造；`skill_runner`
        # 通常由 `skill_tier.build_skill_runner(project_root, max_turns=...)`
        # 构造，遵循与 member `run()` 完全一致的
        # `(request) -> {"status", "data", "error"}` 契约。
        self.playbook_repo = playbook_repo
        self.skill_runner = skill_runner
        # enable_skill_upgrade / skill_upgrade_success_threshold: [本次新增]
        # 见 _maybe_upgrade_skill_to_script()。默认关闭、门槛 3 次——不影响
        # 任何既有调用方（未显式开启时，即使注入了 llm_helper 也不会尝试
        # 升级，保持"新增能力默认不生效"的一贯风格）。
        self.enable_skill_upgrade = enable_skill_upgrade
        self.skill_upgrade_success_threshold = skill_upgrade_success_threshold

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

    # ---------------------- 第 0 步: 输入形状校验 ---------------------- #
    #
    # 背景：resolve() 的一级/二级匹配都假设 request 里存在特定字段
    # （如 target.op / content.text），但调用方（agent）经常猜错字段名，
    # 结果不是报错而是安静地 no_match，一路滑进 explore()，把"我传的
    # request 形状不对"和"这个能力真的还不存在，需要探索"两种完全不同的
    # 情况混成了同一个含糊的 not_implemented，agent 拿到反馈也无从纠正。
    #
    # request_formats（capability.yaml 可选字段）让 skill 作者显式声明
    # 自己接受的一种或多种 request 形状，每种给出 required_fields（点号
    # 路径列表）+ 一个可直接照抄的 example。调用前先做一次零成本的"形状"
    # 检查（只看字段是否存在且非空，不校验类型/语义），只要满足声明的
    # 任意一种形状就放行，交给 resolve()/explore() 按原有逻辑处理；一种
    # 都不满足则直接短路返回 invalid_request，把所有声明的格式连同 example
    # 一起带回去，供 agent 据此重新生成调用——不占用探索预算，也不会被
    # 误判成"这个变换需要探索"。
    #
    # 未声明 request_formats 的 skill（存量的手写 capability.yaml 忘了加）
    # 直接跳过检查，行为与阶段七之前完全一致，不会因为这个新机制而回归。

    def _check_request_format(self, request: dict) -> Optional[list[dict]]:
        """
        返回 None 表示形状合法（或该 skill 未声明 request_formats，不做检查）。
        否则返回 request_formats 的规整化列表，供上层拼成"期望格式"提示。
        """
        formats = self.capability.get("request_formats")
        if not formats:
            return None

        def _present(path: str) -> bool:
            value = self._get_field(request, path)
            return value is not None and value != "" and value != []

        for fmt in formats:
            required = fmt.get("required_fields", [])
            if all(_present(path) for path in required):
                return None  # 至少一种声明的格式满足，形状合法

        return [
            {
                "name": fmt.get("name", ""),
                "description": fmt.get("description", ""),
                "required_fields": fmt.get("required_fields", []),
                "example": fmt.get("example", {}),
            }
            for fmt in formats
        ]

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
        # 注意：member 脚本是 importlib 动态加载的独立文件（不属于本包），
        # 脚本内部写的是 flat `from tool_runtime import get_tool_executor`
        # （见 distiller.SCRIPT_TEMPLATE），所以这里仍需要把本包目录（而不是
        # 已废弃的 `.claude/skills/_engine`）塞进 sys.path，让 `tool_runtime`
        # 作为一个可被 flat import 的模块名被脚本找到；包内部各模块之间的
        # 相互引用已改为正常的相对 import，不再依赖这个 sys.path 技巧。
        import sys as _sys
        engine_dir = str(Path(__file__).resolve().parent)
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

        # schema 校验：使用 schema_validator 做完整校验（类型/结构/嵌套），
        # 而不只是阶段一遗留的"必填字段是否存在"浅层检查（阶段四补齐）。
        schema_errors = self._validate_schema(result.get("data"), entry.get("intent_schema"))
        if schema_errors:
            self._record_execution(member_id, success=False)
            return ExecuteResult(
                status="fail", member_id=member_id,
                error="返回数据未通过 intent_schema 校验: " + "; ".join(schema_errors),
            )

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
    def _validate_schema(data: Any, schema: Optional[dict]) -> list[str]:
        """完整 JSON Schema 子集校验，见 schema_validator.py（阶段四）。
        返回错误信息列表；空列表表示校验通过。"""
        from .schema_validator import validate as _validate
        return _validate(data, schema)

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
        self._refresh_available_tiers(member_id, entry)
        self._save_registry()

    # ---------------------- available_tiers（信息性字段，阶段一新增） ---------------------- #
    #
    # 对应 next_doc/generative_capability_three_tier_improvement_plan.md 阶段一。
    # 纯只读计算 + 写入，**不参与**任何现有决策逻辑（_apply_lifecycle/_try_skill/
    # explore 的触发条件均不读取这个字段）——目的只是让"该 member 当前实际
    # 具备哪些执行手段"在 registry.json 里可见，供人工排查/未来 health_patrol.py
    # 报告使用，不是"改进方向 #1 整体合并"里提到的那个真正统一的状态机。

    def _compute_available_tiers(self, member_id: str) -> list[str]:
        tiers: list[str] = []
        if (self.members_dir / member_id / "script.py").exists():
            tiers.append("script")
        if self.playbook_repo is not None:
            try:
                if self.playbook_repo.get_active_playbook(member_id) is not None:
                    tiers.append("skill")
            except Exception:  # noqa: BLE001 — 计算这个信息性字段不应打断主流程
                pass
        return tiers

    def _refresh_available_tiers(self, member_id: str, entry: Optional[dict] = None) -> None:
        if entry is None:
            entry = self.registry.get("members", {}).get(member_id)
        if entry is None:
            return
        entry["available_tiers"] = self._compute_available_tiers(member_id)

    def _apply_lifecycle(self, member_id: str, entry: dict) -> None:
        """状态机流转: probation -> trusted / (trusted|probation) -> degraded。
        degraded -> dead 的判定发生在重新探索失败之后，见 explore() 中
        `dead_after_reexplore_fail` 的处理（阶段三）。

        [本次新增] trace-replay 弱信任: `distiller.py::_atomic_persist()`
        落盘时，若该 member 是 `distill_source_kind == "trace_replay"`
        产出，会在 registry 条目里写一个更保守的
        `probation_success_threshold_override`（见该函数注释与
        next_doc/generative_capability_trace_replay_and_allowlist_plan.md
        阶段 C）。这里优先读取 member 级别的覆盖值，没有才退回
        capability.yaml 的领域默认值——script_source/llm_synthesized 产出
        的 member 不受影响，行为与阶段六完全一致。
        """
        lifecycle = self.capability.get("lifecycle", {})
        promote_at = entry.get("probation_success_threshold_override", lifecycle.get("probation_success_threshold", 3))
        degrade_at = lifecycle.get("degrade_failure_threshold", 3)

        status = entry.get("status", "probation")
        if status == "probation" and entry.get("success_count", 0) >= promote_at:
            entry["status"] = "trusted"
            entry["status_changed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        elif status in {"trusted", "probation"} and entry.get("consecutive_failures", 0) >= degrade_at:
            entry["status"] = "degraded"
            entry["status_changed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # ---------------------- SKILL 档：script 失败后、explore 之前的尝试 ---------------------- #
    #
    # [本次新增] 对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
    # 第3节，见文件头 skill_tier.py 的说明：这是"capability_engine 试点接入
    # SKILL 档"这一步，不触碰 registry.json 的 script 状态机，playbook 的
    # 版本/成功率统计完全独立记在 playbook_repo 里。

    def _try_skill(self, member_id: str, request: dict) -> Optional[ExecuteResult]:
        """尝试用 member_id 对应的 active playbook 跑一次 SKILL 档执行。
        没有配置 playbook_repo/skill_runner，或该 member 没有 active
        playbook 时，返回 None（跳过，不算失败尝试，调用方据此继续走
        explore）。"""
        if self.playbook_repo is None or self.skill_runner is None:
            return None

        active_pb = self.playbook_repo.get_active_playbook(member_id)
        if active_pb is None:
            return None

        content = self.playbook_repo.load_content(member_id, active_pb.version)
        try:
            result = self.skill_runner(request, content)
        except Exception as e:  # noqa: BLE001 — skill_runner 调用失败不应打断整体流程
            err = f"skill_runner 执行异常: {type(e).__name__}: {e}"
            self.playbook_repo.record_failure(member_id, active_pb.version, err)
            self._refresh_available_tiers(member_id)
            self._save_registry()
            return ExecuteResult(status="fail", member_id=member_id, error=err)

        if not isinstance(result, dict) or result.get("status") != "success":
            err = result.get("error") if isinstance(result, dict) else "skill_runner 返回格式非法"
            err = err or "SKILL 档执行失败（未提供具体原因）"
            from .skill_tier import SKILL_RETIRE_ERROR_PREFIX
            if isinstance(err, str) and err.startswith(SKILL_RETIRE_ERROR_PREFIX):
                # Agent 明确判定这份 playbook 根本走不通，直接退役，不走
                # consecutive_fail 计数——与 hybrid_exec 主循环里
                # `HybridExecutor._try_skill()` 对 PlaybookInvalidError 的
                # 处理原则保持一致。
                reason = err[len(SKILL_RETIRE_ERROR_PREFIX):]
                self.playbook_repo.retire(member_id, active_pb.version, f"playbook 判定为不可用：{reason}")
            else:
                self.playbook_repo.record_failure(member_id, active_pb.version, err)
            self._refresh_available_tiers(member_id)
            self._save_registry()
            return ExecuteResult(status="fail", member_id=member_id, error=err)

        entry = self.registry.get("members", {}).get(member_id, {})
        schema_errors = self._validate_schema(result.get("data"), entry.get("intent_schema"))
        if schema_errors:
            msg = "SKILL 档返回数据未通过 intent_schema 校验: " + "; ".join(schema_errors)
            self.playbook_repo.record_failure(member_id, active_pb.version, msg)
            self._refresh_available_tiers(member_id)
            self._save_registry()
            return ExecuteResult(status="fail", member_id=member_id, error=msg)

        self.playbook_repo.record_success(member_id, active_pb.version)
        self._refresh_available_tiers(member_id)
        self._save_registry()

        # [本次新增，见 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
        # 第3节 3.3b"SKILL 档执行时观察到可参数化则升级蒸馏为 script.py"]
        # 该 playbook 被证明可靠地成功了足够多次、且调用方开启了升级开关并
        # 注入了 llm_helper 时，尝试一次"把它固化成更便宜的 script.py"。
        # 升级尝试本身失败不影响本次调用结果（playbook 已经成功执行过了），
        # 只是静默放弃这次升级机会，不计入 playbook 的成败统计。
        self._maybe_upgrade_skill_to_script(member_id, request, result.get("data"), content)

        return ExecuteResult(status="success", member_id=member_id, data=result.get("data"))

    def _maybe_upgrade_skill_to_script(self, member_id: str, request: dict,
                                        result_data: Any, playbook_content: str) -> None:
        if not self.enable_skill_upgrade or self.llm_helper is None:
            return
        if (self.members_dir / member_id / "script.py").exists():
            return  # 已经有脚本了，不需要重复升级
        active_pb = self.playbook_repo.get_active_playbook(member_id)
        if active_pb is None or active_pb.success_count < self.skill_upgrade_success_threshold:
            return
        # [next_doc/generative_capability_three_tier_improvement_plan.md
        # 阶段二新增] 冷却期节流：升级持续失败时，避免每次 _try_skill 成功
        # 都重新触发一次 LLM 调用。未记录过失败尝试（从未升级过，或上次
        # 升级成功）时不受影响。
        if active_pb.last_upgrade_attempt_at:
            cooldown = self.capability.get("lifecycle", {}).get(
                "skill_upgrade_retry_cooldown_seconds", 3600
            )
            if self._seconds_since_iso(active_pb.last_upgrade_attempt_at) < cooldown:
                return
        entry = self.registry.get("members", {}).get(member_id, {})
        from .distiller import attempt_skill_upgrade
        try:
            upgraded = attempt_skill_upgrade(
                playbook_content=playbook_content, request=request, result_data=result_data,
                intent_schema=entry.get("intent_schema", {}), skill_dir=self.skill_dir,
                capability=self.capability, member_id=member_id,
                self_test_executor=self.tool_executor, llm_helper=self.llm_helper,
            )
        except Exception:  # noqa: BLE001 — 升级本身出错不应影响本次已成功的调用结果
            upgraded = False
        if upgraded:
            # 落盘改动了 registry.json/_index.json，重新加载保持内存一致。
            self.index = self._load_json(self.index_path, default={"members": []})
            self.registry = self._load_json(self.registry_path, default={"members": {}})
        else:
            # 升级失败（或过程中抛异常）：记一次尝试时间，供下次触发冷却期
            # 判断，不影响 playbook 自身的成败统计。
            try:
                self.playbook_repo.record_upgrade_attempt(member_id, active_pb.version)
            except Exception:  # noqa: BLE001 — 记录节流信息本身不应影响主流程
                pass

    @staticmethod
    def _seconds_since_iso(iso_str: str) -> float:
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(iso_str)
        except ValueError:
            return float("inf")  # 解析失败按"很久以前"处理，不阻塞升级尝试
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()

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
        # [本次新增] 让 explorer_runtime._resolve_domain_tool_names() 能够按
        # `explorer.depends_skills`（或兼容别名 base_tools）声明的静态 skill
        # 名，自动去 `<skills_root>/<name>/impl/tools_impl.py::
        # TOOL_IMPLEMENTATIONS` 读取真正实现了的原语名单，而不必再靠
        # tool_allowlist.json 手工抄一份。skills_root 约定为本 skill 目录的
        # 父目录（即 `.claude/skills/`），与 `real_tools.py::
        # load_skill_local_tool_implementations()` 既有的路径约定一致。
        explorer_cfg["_resolved_skill_dir"] = str(self.skill_dir)
        # [本次新增] 把 capability.yaml 里声明的 request_formats（run(input)
        # 的输入字段契约：哪些字段必填、字段名分别是什么）一并传给探索子agent，
        # 而不是只给它这一次触发探索的具体 request 实例。探索子agent/蒸馏
        # LLM 手头如果只有一个具体样本，天然会把样本里的具体值当成"这个字段
        # 大概率长这样"写进默认值/兜底逻辑（例如把这次的搜索关键词直接编码进
        # url 默认值）；给出显式契约后，模型才能判断"哪些字段是这次请求碰巧
        # 提供的值，哪些是接口层面本就该从 input 读取的通用字段"。
        explorer_cfg["_request_formats"] = self.capability.get("request_formats")

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
            return ExecuteResult(
                status="fail", error=distill_result.error, trace_context=distill_result.trace_context,
            )

        # 蒸馏落盘会新增/覆盖 members 目录与 index/registry，重新加载进内存保持一致。
        self.index = self._load_json(self.index_path, default={"members": []})
        self.registry = self._load_json(self.registry_path, default={"members": {}})

        return ExecuteResult(status="success", member_id=distill_result.member_id, data=distill_result.data)

    def _distill(self, request: dict, trace: Any, intent_schema: dict,
                 reexplore_member_id: Optional[str]) -> Any:
        from .distiller import distill as _distill_fn

        return _distill_fn(
            trace=trace,
            request=request,
            intent_schema=intent_schema,
            skill_dir=self.skill_dir,
            capability=self.capability,
            self_test_executor=self.tool_executor,
            reexplore_member_id=reexplore_member_id,
            llm_helper=self.llm_helper,
            # [本次新增，见 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
            # 3.3b"explore 阶段产出 playbook.md"] 未注入 playbook_repo 时
            # distill() 行为不变（三条脚本路径失败即失败）；注入后，脚本
            # 蒸馏全部失败时会退化为落一份 playbook.md，供 SKILL 档
            # （self.skill_runner）今后参照执行。
            playbook_repo=self.playbook_repo,
        )

    def _handle_reexplore_failure(self, member_id: str) -> None:
        lifecycle = self.capability.get("lifecycle", {})
        if not lifecycle.get("dead_after_reexplore_fail", True):
            return
        entry = self.registry.get("members", {}).get(member_id)
        if entry is None:
            return
        entry["status"] = "dead"
        entry["status_changed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save_registry()

    # ---------------------- 对外统一入口 ---------------------- #

    def call(self, request: dict, allow_tiers: Optional[set] = None) -> CapabilityCallResult:
        """allow_tiers: [next_doc/generative_capability_three_tier_improvement_plan.md
        阶段三新增] `{"script", "skill", "explore"}` 的子集，调用方用来主动
        跳过某些执行档位（例如已知该 member 刚被标记 degraded，跳过必然
        失败的 script 档直接尝试 skill）。默认 `None` 时行为与此前完全
        一致（三档全部按既有顺序尝试）——是纯粹的"调用方主动跳过"，不改变
        `resolve()` 的检索逻辑本身，也不影响 `registry.json`/playbook 的
        成败统计：被跳过的档位不产生任何执行尝试，自然不计入成败计数。
        """
        expected_formats = self._check_request_format(request)
        if expected_formats is not None:
            return CapabilityCallResult(
                status="invalid_request",
                error=(
                    "request 不满足该 skill 声明的任何一种 request_formats（形状/必填"
                    "字段不对），未进入 resolve/explore（不消耗探索预算）。"
                ),
                resolve_reason="invalid_request",
                data={"expected_formats": expected_formats},
            )

        resolved = self.resolve(request)
        from .capability_debug import capability_debug_log
        capability_debug_log(
            "engine_resolved",
            {"status": resolved.status, "member_ids": resolved.member_ids, "reason": resolved.reason},
            where="capability_engine.CapabilityEngine.call",
        )
        if resolved.status == "hit":
            last_failed_member_id: Optional[str] = None
            last_exec_error: Optional[str] = None
            if allow_tiers is None or "script" in allow_tiers:
                for member_id in resolved.member_ids:
                    exec_result = self.execute(member_id, request)
                    capability_debug_log(
                        "engine_execute_attempted",
                        {"member_id": member_id, "status": exec_result.status, "error": exec_result.error},
                        where="capability_engine.CapabilityEngine.call",
                    )
                    if exec_result.status == "success":
                        return CapabilityCallResult(
                            status="success",
                            data=exec_result.data,
                            member_id=member_id,
                            resolve_reason=resolved.reason,
                        )
                    last_failed_member_id = member_id
                    last_exec_error = exec_result.error
            else:
                # script 档被 allow_tiers 主动跳过：不产生任何执行尝试
                # （不计入 registry.json 成败统计），直接把命中的最后一个
                # 候选视为"待尝试 skill/explore 档"，与真实执行失败走同一
                # 条后续逻辑。
                last_failed_member_id = resolved.member_ids[-1] if resolved.member_ids else None
                last_exec_error = "script 档被 allow_tiers 主动跳过，未实际执行"

            # 命中的候选全部执行失败（或 script 档被跳过）：先看有没有更
            # 便宜的 SKILL 档（playbook）可以顶上，再决定要不要真正进入
            # 全新 explore。[本次新增]
            if last_failed_member_id is not None and (allow_tiers is None or "skill" in allow_tiers):
                skill_result = self._try_skill(last_failed_member_id, request)
                capability_debug_log(
                    "engine_skill_attempted",
                    {
                        "member_id": last_failed_member_id,
                        "attempted": skill_result is not None,
                        "status": skill_result.status if skill_result is not None else None,
                        "error": skill_result.error if skill_result is not None else None,
                    },
                    where="capability_engine.CapabilityEngine.call",
                )
                if skill_result is not None and skill_result.status == "success":
                    return CapabilityCallResult(
                        status="success",
                        data=skill_result.data,
                        member_id=last_failed_member_id,
                        resolve_reason="skill_playbook",
                    )
                if skill_result is not None and skill_result.error:
                    # SKILL 档也失败了：把这个原因也带进最终的 combined_error，
                    # 与 last_exec_error 拼在一起，不丢弃任何一段诊断信息。
                    last_exec_error = (
                        f"{last_exec_error}；SKILL 档(playbook)执行也失败: {skill_result.error}"
                        if last_exec_error else f"SKILL 档(playbook)执行也失败: {skill_result.error}"
                    )

            # 命中的候选（含 SKILL 档兜底）全部执行失败：若该 member 已被判定
            # degraded，这是一次"重新探索"（复用同一个 member_id，见状态机
            # 第 7 节）；否则按普通探索处理。
            reexplore_id = None
            if last_failed_member_id is not None:
                status = self.registry.get("members", {}).get(last_failed_member_id, {}).get("status")
                if status == "degraded":
                    reexplore_id = last_failed_member_id

            if allow_tiers is not None and "explore" not in allow_tiers:
                # explore 档被 allow_tiers 主动跳过：不消耗探索预算，直接
                # 返回 not_implemented，error 里说明是被主动跳过而非能力
                # 缺失（区别于"探索了但没找到"）。
                skip_error = "explore 档被 allow_tiers 主动跳过，未触发探索子agent"
                if last_exec_error:
                    skip_error = f"已有能力(member={last_failed_member_id})执行失败: {last_exec_error}；{skip_error}"
                return CapabilityCallResult(status="not_implemented", error=skip_error,
                                             resolve_reason=resolved.reason)

            explore_result = self.explore(request, reexplore_member_id=reexplore_id)
            if explore_result.status == "success":
                return CapabilityCallResult(status="success", data=explore_result.data,
                                             member_id=explore_result.member_id, resolve_reason="explored")
            # [阶段十九修复] 此前这里只返回 explore_result.error，命中的 member
            # 自己第一次执行失败的具体原因（比如登录墙/验证码关键词，往往比
            # 探索子agent最后超时/报告的原因信息量大得多）被直接丢弃，调用方
            # 只能看到"探索超出时间预算"这类和真实病因（登录墙）毫无关系的
            # 表面原因，完全没法据此判断该不该换 session.mode="attach"。这里
            # 把两段原因都如实带回去。
            combined_error = explore_result.error
            if last_exec_error:
                combined_error = (
                    f"已有能力(member={last_failed_member_id})执行失败: {last_exec_error}；"
                    f"随后触发的探索子agent也失败: {explore_result.error}"
                )
            capability_debug_log(
                "engine_call_not_implemented",
                {"skill_name": self.capability.get("name"), "request": request,
                 "resolve_reason": resolved.reason, "last_failed_member_id": last_failed_member_id,
                 "last_exec_error": last_exec_error, "explore_error": explore_result.error,
                 "combined_error": combined_error},
                where="capability_engine.CapabilityEngine.call",
            )
            return CapabilityCallResult(status="not_implemented", error=combined_error,
                                         resolve_reason=resolved.reason,
                                         trace_context=explore_result.trace_context)

        # miss -> 全新探索
        if allow_tiers is not None and "explore" not in allow_tiers:
            return CapabilityCallResult(
                status="not_implemented",
                error="explore 档被 allow_tiers 主动跳过，未触发探索子agent",
                resolve_reason=resolved.reason,
            )
        explore_result = self.explore(request)
        if explore_result.status == "success":
            return CapabilityCallResult(status="success", data=explore_result.data,
                                         member_id=explore_result.member_id, resolve_reason="explored")
        capability_debug_log(
            "engine_call_not_implemented",
            {"skill_name": self.capability.get("name"), "request": request,
             "resolve_reason": resolved.reason, "explore_error": explore_result.error},
            where="capability_engine.CapabilityEngine.call",
        )
        return CapabilityCallResult(status="not_implemented", error=explore_result.error,
                                     resolve_reason=resolved.reason,
                                     trace_context=explore_result.trace_context)


# --------------------------------------------------------------------------- #
# 命令行自测入口
#
# 阶段七起，本文件是正常包内模块，__main__ 场景下相对 import 不可用，
# 因此下方按需导入使用绝对包路径；建议以 `python -m
# mini_agent.skills.generative_capability.capability_engine <skill_dir> --url ...`
# 方式运行，保证包上下文正确初始化。
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
        from mini_agent.skills.generative_capability.llm_resolver import build_stub_resolver
        resolver = build_stub_resolver([args.stub_llm_hit])

    explore_runner = None
    tool_executor = None
    if args.stub_explore_success:
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreStep, build_stub_explorer
        steps = [ExploreStep(tool="browser_navigate", input={"url": args.url}, output={"ok": True})]
        explore_runner = build_stub_explorer(
            steps=steps, final_data={"results": [{"title": "桩数据", "url": args.url}]}
        )
        tool_executor = lambda name, inp: {"ok": True, "echo": inp}  # noqa: E731
    elif args.stub_explore_fail:
        from mini_agent.skills.generative_capability.explorer_runtime import build_stub_explorer
        explore_runner = build_stub_explorer(final_error=args.stub_explore_fail)
        tool_executor = lambda name, inp: {"ok": True}  # noqa: E731

    engine = CapabilityEngine(args.skill_dir, llm_resolver=resolver,
                               explore_runner=explore_runner, tool_executor=tool_executor)
    req = {"text": args.url, "target": {"url": args.url}, "query": args.query}
    result = engine.call(req)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
