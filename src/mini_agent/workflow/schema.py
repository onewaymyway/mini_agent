"""
workflow/schema.py — 工作流数据模型

WorkflowDef   工作流定义（由 YAML/JSON 反序列化而来）
WorkflowStep  单个步骤定义
StepStatus    步骤运行时状态
StepResult    步骤执行结果

YAML 格式示例：
  name: code_review
  description: 代码审查完整流程
  version: "1.0"
  steps:
    - id: analyze
      name: 静态分析
      prompt: |
        分析以下代码的结构和潜在问题：
        {code}
      role: null        # null = 主 Agent 执行

    - id: evaluate
      name: 质量评估
      prompt: |
        对分析结果评分：{analyze.output}
      depends_on: [analyze]
      role: evaluator   # 指定角色 Agent

    - id: report
      name: 生成报告
      prompt: 综合以上内容生成报告。分析：{analyze.output}
      depends_on: [evaluate]
      condition: "evaluate.score >= 6"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# [workflow机制改进计划.md P5] Step 类型化：把 role: Optional[str] 的隐式
# "主 Agent / 角色 Agent" 二选一显式化为一个枚举，同时新增三种类型。
# 未显式设置 type 时（旧版 YAML），由 WorkflowStep.effective_type 按
# "role 是否为空"自动推断为 "agent"/"role_agent"，保证向后兼容。
STEP_TYPES = ("agent", "role_agent", "sub_workflow", "tool_call", "human_input", "script", "skill_agent", "python_step")


def condition_referenced_names(condition: str) -> set[str]:
    """
    [P9-3 workflow_system_next_directions.md §3.2] 解析 condition 表达式，
    静态（不 eval）抽取表达式里所有形如 `xxx.yyy` 属性访问的顶层名字 `xxx`
    （如 "analyze.passed and inputs.env == 'prod'" → {"analyze", "inputs"}）。
    供 WorkflowDef.validate() 的一致性检查、以及 api_helpers.preview_workflow()
    判断"这个 condition 是否只依赖 inputs（可以静态求值）"共用，避免两处
    各写一套 ast 解析逻辑。表达式语法错误时返回空集合（语法错误本身由
    调用方各自处理，不在这里报告）。
    """
    import ast
    try:
        tree = ast.parse(condition, mode="eval")
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names.add(node.value.id)
    return names


# [workflow_mechanism_improvement_plan_p10.md §2] resume_workflow_run(
# step_overrides=...) 允许临时覆盖的字段白名单：仅限"执行参数类"字段，
# 不允许覆盖会改变 step 语义的字段（prompt/condition/tool_name 等）——
# 那类改动本质上是"改逻辑"，应该走 patch_workflow_step 留痕，不应该以
# "临时覆盖"的名义绕过定义变更。命中白名单之外的字段名时，调用方应直接
# 拒绝并报错，而不是静默忽略。
RUNTIME_OVERRIDABLE_FIELDS = frozenset({
    "timeout",
    "retry_on_error",
    "allow_parallel",
    "model",
    "escalate_after_n_same_failures",
})


class StepStatus(str, Enum):
    PENDING      = "pending"
    RUNNING      = "running"
    DONE         = "done"
    SKIPPED      = "skipped"      # condition 不满足
    FAILED       = "failed"
    GATE_FAILED  = "gate_failed"  # evaluator 评分不达标（质检门未通过）
    # ── workflow机制改进计划.md P2/P4 新增状态 ──────────────────────────────
    TIMEOUT            = "timeout"             # 硬超时被看护线程强制中断
    CANCELLED          = "cancelled"            # 收到 cancel 信号后未开始/被中止
    AWAITING_APPROVAL  = "awaiting_approval"    # 等待人工审批门放行
    REJECTED           = "rejected"             # 人工审批被拒绝
    # ── [改进方案 §4.3] 结构性/配置性错误 ────────────────────────────────
    # 与 FAILED 的区别：FAILED 里混杂了"重试可能有用"（网络超时等瞬时故障）
    # 和"重试必然无用"（prompt 占位符写错、tool_name 未注册等定义错误）两类
    # 情况；NEEDS_FIX 专指后者——runner 识别到 error_type 属于结构性异常时
    # 直接跳过 retry_on_error 重试，标记为 NEEDS_FIX，提示主 Agent 应先用
    # patch_workflow_step 修改工作流定义，再用 resume_workflow_run(force_
    # rerun_from=...) 续跑，而不是简单地重跑。
    NEEDS_FIX          = "needs_fix"


@dataclass
class WorkflowStep:
    """工作流中的单个步骤（定义层）。"""
    id: str                              # 步骤唯一标识，用于依赖引用
    name: str                            # 可读名称
    prompt: str                          # Prompt 模板，支持 {step_id.output} 占位符
    role: Optional[str] = None           # 指定角色 Agent name；None = 主 Agent
    depends_on: list[str] = field(default_factory=list)  # 依赖的步骤 id 列表
    condition: Optional[str] = None      # 执行条件表达式，如 "evaluate.score >= 6"
    # [P7-③1 workflow_mechanism_improvement_plan.md] max_turns/model/timeout/
    # retry_on_error/allow_parallel 均改为 Optional，None 表示"未显式设置，
    # 继承 WorkflowDef.defaults，defaults 里也没有则用运行时硬编码兜底值"。
    # 三层查找顺序见 runner.py::WorkflowRunner._effective_step_field()。
    max_turns: Optional[int] = None      # 该步骤允许的最大 LLM 轮数（None=继承，硬编码兜底 10）
    model: Optional[str] = None          # 覆盖 model（None = 继承 defaults/全局）
    timeout: Optional[float] = None      # 步骤超时（秒，None=继承，无硬编码兜底=不限时）
    retry_on_gate_fail: int = 0          # evaluator 不达标时，重跑前序步骤的最大次数（0=不重跑）
    # [具身改进 B3] 是否允许与同一拓扑层的其他步骤并发执行。
    # None=继承（硬编码兜底 True）；若某步骤有 depends_on 未声明的隐式副作用
    # （如读写同一外部文件/状态），可显式设为 False 强制串行，保留人工对
    # 并发风险的控制权。
    allow_parallel: Optional[bool] = None
    # [workflow机制改进计划.md P4] 普通异常（非质检门）重试次数，None=继承
    # （硬编码兜底 0=不重试）。与 retry_on_gate_fail 是两套独立机制：这个只
    # 处理 FAILED（网络超时/工具报错等），不涉及"重跑依赖步骤+带反馈"的
    # 质检门语义。
    retry_on_error: Optional[int] = None
    # [workflow机制改进计划.md P4] 是否要求人工审批门放行才能执行该步骤。
    # 需要配合 run_workflow(background=True) 使用：前台同步执行时没有其他
    # 线程能在阻塞期间调用 approve_workflow_step，审批会一直等到超时。
    require_approval: bool = False
    # [workflow_mechanism_improvement_plan_p10.md §3] 连续同类失败提前升级
    # NEEDS_FIX 的阈值：同一个 step 在 retry_on_error 重试循环里连续（中间
    # 没有成功）出现同一个 error_type 达到这个次数时，watchdog 会判定"大概率
    # 不是瞬时故障"，提前把该 step 标记 NEEDS_FIX、跳过剩余重试预算。
    # None=继承 wf.defaults["escalate_after_n_same_failures"]，再没有则用
    # 全局默认值 2（见 runner.py::_effective_step_field 三层查找）。
    escalate_after_n_same_failures: Optional[int] = None

    # ── [workflow机制改进计划.md P5] Step 类型化 ────────────────────────────
    # type 为 None 时按旧语义推断（见 effective_type）；显式设置后由
    # runner._dispatch_step 分发到对应 Executor，新增类型不影响旧 YAML。
    type: Optional[str] = None
    # sub_workflow 专用：引用的另一个已保存工作流名称
    workflow_name: Optional[str] = None
    # tool_call 专用：直接调用某个已注册工具（而不是启动一整个 Agent）
    tool_name: Optional[str] = None
    tool_args: dict = field(default_factory=dict)
    # human_input 专用：展示给人类的提示语（为空则用 prompt 本身）
    input_prompt: Optional[str] = None
    # [改进方案 §1] human_input 专用：若启动 run_workflow 时传入的 inputs
    # 字典里已经能通过该 key 找到值，直接用它填充，不进入阻塞等待。用于
    # 让同一份 human_input step 既能交互式跑（人工临场输入），也能被"所有
    # 参数在最初一次性传完"的全自动调用方式复用。
    input_key: Optional[str] = None
    # [P7-③2 workflow_mechanism_improvement_plan.md] 可复用 step 片段：
    # 引用 .agent/workflow_snippets/<include>.yaml 里的一段 steps 列表。
    # 纯加载期展开（见 store.py::expand_includes），展开后这个字段本身
    # 不会出现在最终的 WorkflowDef.steps 里（会被替换成片段里的真实 step），
    # 这里保留字段只是为了 from_dict 阶段能识别到"这是一个 include 声明"。
    include: Optional[str] = None
    # script 专用：要执行的 shell 命令（受 cfg.workflow.script_step_enabled
    # 开关保护，默认关闭，避免任意 workflow YAML 被当作命令执行入口）
    script: Optional[str] = None

    # ── [workflow_directory_mode_design.md 阶段1] 目录化 Workflow 扩展 ─────
    # prompt 模板文件路径（相对 workflow 所在目录，如 "prompts/analyze.md"）。
    # 与 prompt 二选一，有值时由 WorkflowStore 加载阶段读取文件内容覆盖
    # 填充 self.prompt；序列化时只写 prompt_file，不写展开后的文本，
    # 便于连同 prompt 文件一起迁移项目。
    prompt_file: Optional[str] = None
    # skill_agent 专用：要强制加载执行的 skill 名称（不走关键词触发判断）
    skill_name: Optional[str] = None

    # ── [next_doc/workflow_python_step_and_zhihu_publish_plan.md §A/§B] ────
    # python_step 专用：脚本文件路径（相对 workflow 所在目录解析，规则与
    # prompt_file 一致）。脚本需暴露 `def run(ctx: PyStepContext) -> str|dict`
    # 入口函数，由 PythonStepExecutor 在独立子进程里通过 runpy 加载执行。
    script_path: Optional[str] = None
    # 透传给 python_step 脚本的自定义参数（workflow.yaml 里直接写字面量），
    # 脚本内通过 ctx.params 读取，例如 {"doc_path": "..."}。
    params: dict = field(default_factory=dict)
    # [next_doc/workflow_python_step_and_zhihu_publish_plan.md §A3] 通用
    # 输出落盘契约：step 执行完成后，runner 统一把 StepResult.output 写一份
    # 到 session.output_dir / output_file，不依赖 agent/脚本自己拼路径。
    output_file: Optional[str] = None

    @property
    def effective_type(self) -> str:
        """未显式设置 type 时，按旧语义推断：role 非空 → role_agent，否则 → agent。"""
        if self.type:
            return self.type
        return "role_agent" if self.role else "agent"


@dataclass
class WorkflowDef:
    """完整的工作流定义。"""
    name: str
    steps: list[WorkflowStep]
    description: str = ""
    version: str = "1.0"
    # [workflow机制改进计划.md P3] 整体资源护栏：累计执行时长（秒）超过该值，
    # 看护线程主动请求 cancel。None=不限制，走全局配置
    # cfg.workflow.max_total_duration_seconds 作为兜底。
    max_total_duration: Optional[float] = None
    # [P7-②1 workflow_mechanism_improvement_plan.md] 整体资源护栏：累计
    # token 用量（input+output）超过该值，看护线程主动请求 cancel。
    # None=不限制，走全局配置 cfg.workflow.max_total_tokens 作为兜底。
    # 只统计 agent/skill_agent 类型 step（能拿到 Agent.stats 的类型），
    # role_agent/sub_workflow 等类型的 token 消耗暂不计入（见 runner.py
    # WorkflowRunner._execute_with_main_agent 的回填点）。
    max_total_tokens: Optional[int] = None
    # [P7-③1 workflow_mechanism_improvement_plan.md] workflow 级默认配置：
    # 为 model/timeout/retry_on_error/max_turns/allow_parallel 提供统一
    # 默认值，未显式设置的 step 级字段（值为 None）继承这里的值，这里也
    # 没写则用运行时硬编码兜底（见 WorkflowStep 各字段注释）。完全向后
    # 兼容：没写 defaults 的旧 YAML 行为不变。
    defaults: dict = field(default_factory=dict)
    # [改进方案 §1] mode="autonomous" 时，validate() 会把 human_input（无
    # input_key 兜底）/require_approval 类 step 判为校验错误，在保存期就
    # 拦住"全自动 workflow 里意外混入阻塞点"，而不是等运行到后台执行才
    # 因为没人应答而卡到超时。默认 "interactive" 保持向后兼容。
    mode: str = "interactive"
    # ── [workflow_directory_mode_design.md 阶段1] 目录化 Workflow 扩展 ─────
    # 文件夹模式下指向 workflow 所在目录（<workflows_dir>/<name>/），
    # 单文件模式下为 None。纯运行时字段，不参与 to_dict 序列化，由
    # WorkflowStore 在加载/保存时设置，用于解析 prompt_file 相对路径、
    # 拼装本地 agents/skills 目录。
    source_dir: Optional[Any] = None

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowDef":
        """从字典（YAML 解析结果）反序列化。"""
        raw_steps = data.get("steps", [])
        steps = []
        for s in raw_steps:
            if not isinstance(s, dict):
                continue
            steps.append(WorkflowStep(
                id=str(s.get("id", "")),
                name=str(s.get("name", s.get("id", ""))),
                prompt=str(s.get("prompt", "")),
                role=s.get("role"),   # None 保持 None
                depends_on=list(s.get("depends_on", [])),
                condition=s.get("condition"),
                max_turns=int(s["max_turns"]) if s.get("max_turns") is not None else None,
                model=s.get("model"),
                timeout=float(s["timeout"]) if s.get("timeout") else None,
                retry_on_gate_fail=int(s.get("retry_on_gate_fail", 0)),
                allow_parallel=bool(s["allow_parallel"]) if s.get("allow_parallel") is not None else None,
                retry_on_error=int(s["retry_on_error"]) if s.get("retry_on_error") is not None else None,
                require_approval=bool(s.get("require_approval", False)),
                escalate_after_n_same_failures=int(s["escalate_after_n_same_failures"])
                    if s.get("escalate_after_n_same_failures") is not None else None,
                type=s.get("type"),
                workflow_name=s.get("workflow_name"),
                tool_name=s.get("tool_name"),
                tool_args=dict(s.get("tool_args") or {}),
                input_prompt=s.get("input_prompt"),
                input_key=s.get("input_key"),
                script=s.get("script"),
                prompt_file=s.get("prompt_file"),
                skill_name=s.get("skill_name"),
                include=s.get("include"),
                script_path=s.get("script_path"),
                params=dict(s.get("params") or {}),
                output_file=s.get("output_file"),
            ))
        return cls(
            name=str(data.get("name", "unnamed")),
            steps=steps,
            description=str(data.get("description", "")),
            version=str(data.get("version", "1.0")),
            max_total_duration=float(data["max_total_duration"]) if data.get("max_total_duration") else None,
            max_total_tokens=int(data["max_total_tokens"]) if data.get("max_total_tokens") else None,
            defaults=dict(data.get("defaults") or {}),
            mode=str(data.get("mode", "interactive")),
        )

    def to_dict(self) -> dict:
        """序列化为字典（用于 YAML 存储）。"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            **({"max_total_duration": self.max_total_duration} if self.max_total_duration else {}),
            **({"max_total_tokens": self.max_total_tokens} if self.max_total_tokens else {}),
            **({"defaults": self.defaults} if self.defaults else {}),
            **({"mode": self.mode} if self.mode and self.mode != "interactive" else {}),
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    # [阶段1] prompt_file 有值时只写 prompt_file，不写展开后的
                    # 文本，避免迁移时重复落盘、避免编辑 prompt 文件后 YAML 里
                    # 的旧文本"假装"还生效。
                    **({"prompt_file": s.prompt_file} if s.prompt_file else {"prompt": s.prompt}),
                    **({"role": s.role} if s.role else {}),
                    **({"depends_on": s.depends_on} if s.depends_on else {}),
                    **({"condition": s.condition} if s.condition else {}),
                    # [P7-③1] None=未显式设置（继承 defaults/硬编码兜底），不写入 YAML
                    **({"max_turns": s.max_turns} if s.max_turns is not None else {}),
                    **({"model": s.model} if s.model else {}),
                    **({"timeout": s.timeout} if s.timeout else {}),
                    **({"retry_on_gate_fail": s.retry_on_gate_fail} if s.retry_on_gate_fail else {}),
                    **({"allow_parallel": s.allow_parallel} if s.allow_parallel is not None else {}),
                    **({"retry_on_error": s.retry_on_error} if s.retry_on_error is not None else {}),
                    **({"require_approval": s.require_approval} if s.require_approval else {}),
                    **({"escalate_after_n_same_failures": s.escalate_after_n_same_failures}
                       if s.escalate_after_n_same_failures is not None else {}),
                    **({"type": s.type} if s.type else {}),
                    **({"workflow_name": s.workflow_name} if s.workflow_name else {}),
                    **({"tool_name": s.tool_name} if s.tool_name else {}),
                    **({"tool_args": s.tool_args} if s.tool_args else {}),
                    **({"input_prompt": s.input_prompt} if s.input_prompt else {}),
                    **({"input_key": s.input_key} if s.input_key else {}),
                    **({"script": s.script} if s.script else {}),
                    **({"skill_name": s.skill_name} if s.skill_name else {}),
                    **({"include": s.include} if s.include else {}),
                    **({"script_path": s.script_path} if s.script_path else {}),
                    **({"params": s.params} if s.params else {}),
                    **({"output_file": s.output_file} if s.output_file else {}),
                }
                for s in self.steps
            ],
        }

    def validate(
        self,
        *,
        check_placeholders: bool = True,
        check_condition: bool = True,
        check_placeholder_depends_on: bool = True,
        role_checker=None,
    ) -> list[str]:
        """校验工作流定义，返回错误列表（空列表=合法）。

        [workflow机制改进计划.md P6] 保存前引用完整性校验：
          - check_placeholders=True 时，额外扫描 prompt 中形如
            {step_id.output}/{step_id.score} 的占位符，检查引用的 step_id
            是否存在于当前工作流（弱校验：不要求显式声明 depends_on，具体
            "运行时该依赖是否已产生结果"由 runner._resolve_prompt 的 KeyError
            兜底，这里只挡"引用了根本不存在的步骤 id"这类明显笔误）。
          - role_checker：可选的 callable(role_name) -> bool，由调用方传入
            （通常来自 role_agents dispatcher），用于校验 step.role 是否为
            当前已注册的角色 profile。不传时跳过该项检查（保持向后兼容，
            单元测试/无 dispatcher 环境下 validate() 行为不变）。
          - check_condition=True 时（默认），额外做一轮 condition 表达式的
            静态一致性检查（P9-3，见下方对应代码块注释）；可由
            cfg.workflow.condition_static_check_enabled 关闭，关闭后仍会做
            condition 的 ast 语法检查（该检查在开关判断之前，不受此参数影响）。
        """
        errors: list[str] = []
        warnings: list[str] = []
        seen_ids: set[str] = set()

        for step in self.steps:
            if not step.id:
                errors.append("存在 id 为空的步骤")
            elif step.id in seen_ids:
                errors.append(f"步骤 id 重复：{step.id!r}")
            else:
                seen_ids.add(step.id)

            # [阶段1] prompt_file 指定了外部文件时，允许内嵌 prompt 为空
            # （加载阶段会用文件内容填充 step.prompt；validate() 常在保存
            # 前调用，此时若尚未经过加载流程 prompt 可能还是空的，不应误报）。
            if (
                not step.prompt.strip()
                and not step.prompt_file
                and not step.include
                and step.effective_type not in ("human_input", "python_step")
            ):
                errors.append(f"步骤 {step.id!r} 的 prompt 为空")

            # [next_doc/workflow_python_step_and_zhihu_publish_plan.md §A1]
            # 规范建议：内联 prompt 超过阈值行数时提示改用 prompt_file，
            # 走 warning 而非 error，不阻断保存/运行（向后兼容旧 workflow）。
            if step.prompt and not step.prompt_file and step.prompt.count("\n") > 4:
                warnings.append(
                    f"步骤 {step.id!r} 的内联 prompt 超过 5 行，建议改用 prompt_file 把 "
                    f"prompt 拆到独立文件（见 next_doc/workflow_authoring_guide.md）"
                )

            # [P5] 类型专属必填字段校验
            # [P7-④1] STEP_TYPES 只是内置类型；插件通过 register_step_executor()
            # 注册的自定义类型也应视为合法，这里改为查询 executors 模块当前
            # 已注册的全部类型（懒加载，避免 schema.py 对 executors.py 产生
            # 模块级循环依赖——executors.py 反过来 import 了 schema.py）。
            etype = step.effective_type
            try:
                from .executors import get_registered_types
                valid_types = get_registered_types()
            except Exception:
                valid_types = STEP_TYPES
            if etype not in valid_types:
                errors.append(f"步骤 {step.id!r} 的 type 非法：{etype!r}（可选：{valid_types}）")
            elif etype not in STEP_TYPES:
                # 插件注册的自定义类型：内置的逐类型必填字段校验不适用，
                # 委托给对应 Executor 自己的 validate_step()。
                try:
                    from .executors import get_executor
                    errors.extend(get_executor(etype).validate_step(step) or [])
                except Exception as _e:
                    errors.append(f"步骤 {step.id!r} 的自定义类型 {etype!r} 校验失败：{_e}")
            if etype == "sub_workflow" and not step.workflow_name:
                errors.append(f"步骤 {step.id!r} 是 sub_workflow 类型但未指定 workflow_name")
            if etype == "sub_workflow" and step.workflow_name == self.name:
                errors.append(f"步骤 {step.id!r} 引用了当前工作流自身（{self.name!r}），会导致无限递归")
            if etype == "tool_call" and not step.tool_name:
                errors.append(f"步骤 {step.id!r} 是 tool_call 类型但未指定 tool_name")
            if etype == "script" and not step.script:
                errors.append(f"步骤 {step.id!r} 是 script 类型但未指定 script 命令")
            if etype == "skill_agent" and not step.skill_name:
                errors.append(f"步骤 {step.id!r} 是 skill_agent 类型但未指定 skill_name")
            if etype == "python_step" and not step.script_path:
                errors.append(f"步骤 {step.id!r} 是 python_step 类型但未指定 script_path")

            # [改进方案 §1] mode="autonomous" 时，在保存期拦截阻塞型 step：
            # human_input 且没有 input_key 兜底 → 运行时会真的阻塞等人工输入；
            # require_approval=True → 运行时会真的阻塞等人工审批。
            # 这两种在"全自动、所有输入已在最初给全"的场景下都属于设计错误。
            if self.mode == "autonomous":
                if etype == "human_input" and not step.input_key:
                    errors.append(
                        f"步骤 {step.id!r} 是 human_input 类型但未设置 input_key，"
                        f"在 mode=autonomous 的工作流中会导致运行时阻塞等待人工输入"
                    )
                if step.require_approval:
                    errors.append(
                        f"步骤 {step.id!r} 设置了 require_approval=True，"
                        f"在 mode=autonomous 的工作流中会导致运行时阻塞等待人工审批"
                    )

        # 检查依赖是否存在
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in seen_ids:
                    errors.append(f"步骤 {step.id!r} 依赖不存在的步骤 {dep!r}")

        # [P9-3 workflow_system_next_directions.md §3.2] condition 静态一致性
        # 检查：只做语法级别（ast.parse）的名字引用检查，不真的 eval —— 用
        # ast.walk 抽取表达式里所有形如 `xxx.yyy` 的属性访问，把 `xxx`
        # 当作"引用的 step_id"，检查它是否存在、以及是否在该 step 的
        # depends_on（直接或传递）里。这样一个写错 step_id、或者引用了
        # 存在但没有声明依赖的 step 的 condition，能在 save_workflow 阶段
        # 就被拦下来，而不是等真正跑到那一步、被 runner 的 except Exception
        # 吞掉、只表现为"这步被跳过了"。
        # `inputs.xxx` 是运行时始终可见的外部参数命名空间（见
        # runner.py::_eval_condition），不受 depends_on 约束，跳过检查。
        import ast

        for step in self.steps:
            if not step.condition:
                continue
            try:
                ast.parse(step.condition, mode="eval")
            except SyntaxError as e:
                errors.append(f"步骤 {step.id!r} 的 condition 表达式语法错误：{e}")

        # [P11 §1] `_transitive_deps` 由 check_condition/check_placeholders
        # 两个分支共用（原来只在 check_condition 分支里定义），提到外层避免
        # 重复实现——两处都需要"引用的 step 是否在该 step 的 depends_on
        # （直接或传递）范围内"这同一层判断。
        step_deps_map = {s.id: set(s.depends_on) for s in self.steps}

        def _transitive_deps(step_id: str, _visited: Optional[set] = None) -> set:
            _visited = _visited if _visited is not None else set()
            if step_id in _visited:
                return set()
            _visited.add(step_id)
            result = set(step_deps_map.get(step_id, ()))
            for d in list(result):
                result |= _transitive_deps(d, _visited)
            return result

        # [P9-3] 引用一致性检查（引用的 step 是否存在/是否在 depends_on 范围
        # 内）单独受 check_condition 开关控制（对应
        # cfg.workflow.condition_static_check_enabled）；语法检查本身始终
        # 执行，不受此开关影响——语法错误无论如何都不该被放过。
        if check_condition:
            for step in self.steps:
                if not step.condition:
                    continue
                try:
                    ast.parse(step.condition, mode="eval")
                except SyntaxError:
                    continue  # 语法错误已在上面记录过，这里跳过避免重复报错

                ancestors = _transitive_deps(step.id)
                referenced = condition_referenced_names(step.condition)

                for ref in sorted(referenced):
                    if ref == "inputs":
                        continue
                    if ref not in seen_ids:
                        errors.append(
                            f"步骤 {step.id!r} 的 condition 引用了不存在的步骤 {ref!r}"
                            f"（{step.condition!r}）"
                        )
                    elif ref not in ancestors:
                        errors.append(
                            f"步骤 {step.id!r} 的 condition 引用了步骤 {ref!r}，"
                            f"但未在 depends_on 中声明依赖（直接或传递），"
                            f"运行时该步骤结果可能还不存在（{step.condition!r}）"
                        )

        # [P6] role 引用校验（可选，需要调用方传入 role_checker）
        if role_checker is not None:
            for step in self.steps:
                if step.role and not role_checker(step.role):
                    errors.append(f"步骤 {step.id!r} 引用了不存在的角色 Agent profile：{step.role!r}")

        # [P6][P11 §1/§3] Prompt 占位符引用完整性校验：
        # {step_id.output}/{step_id.score}/{step_id.output_file}
        if check_placeholders:
            import re
            for step in self.steps:
                for m in re.finditer(r'\{([^}]+)\}', step.prompt or ""):
                    key = m.group(1)
                    if "." not in key:
                        continue  # {param} 形式，属于运行时 inputs，无法静态校验
                    ref_id, ref_field = key.split(".", 1)
                    if ref_id not in seen_ids:
                        errors.append(
                            f"步骤 {step.id!r} 的 prompt 引用了不存在的步骤 {ref_id!r}"
                            f"（占位符 {{{key}}}）"
                        )
                        continue
                    if ref_field not in ("output", "score", "output_file"):
                        errors.append(
                            f"步骤 {step.id!r} 的 prompt 占位符 {{{key}}} 引用了未知字段 "
                            f"{ref_field!r}（只支持 .output / .score / .output_file）"
                        )
                        continue
                    # [P11 §1] 存在但引用字段合法时，额外检查是否在 depends_on
                    # （直接或传递）范围内——与上面 condition 的一致性检查是
                    # 同一层判断，只是校验对象从 condition 表达式换成了 prompt
                    # 占位符。这类问题此前只在运行期因为该 step 尚未执行、
                    # step_results 里还没有对应 key 才会以 KeyError 暴露，
                    # 现在提前到 save_workflow 阶段拦下来。受
                    # cfg.workflow.placeholder_depends_on_check_enabled 开关
                    # 控制，默认开启；关闭后仍保留上面"引用是否存在"的检查。
                    if check_placeholder_depends_on:
                        ancestors = _transitive_deps(step.id)
                        if ref_id not in ancestors:
                            errors.append(
                                f"步骤 {step.id!r} 的 prompt 占位符 {{{key}}} 引用了步骤 "
                                f"{ref_id!r}，但未在 depends_on 中声明依赖（直接或传递），"
                                f"运行时该步骤结果可能还不存在（会在执行期抛出 KeyError）"
                            )

        # [next_doc/workflow_python_step_and_zhihu_publish_plan.md §A1]
        # 保持 validate() 原有签名/返回值不变（仍只返回 errors，向后兼容
        # generator.py/store.py/tools.py 现有调用点），warning 级建议通过
        # 实例属性暴露，CLI/工具函数按需读取展示，不参与 errors 判定。
        self.last_validate_warnings = warnings
        return errors


@dataclass
class StepResult:
    """步骤执行结果（运行时产生）。"""
    step_id: str
    status: StepStatus
    output: str = ""
    score: Optional[float] = None    # evaluator 角色才会填写
    error: Optional[str] = None
    duration_seconds: float = 0.0
    retries_used: int = 0             # retry_on_error 实际消耗的重试次数
    # ── 出错诊断信息（原来只有 error=str(e)，排查时定位不到具体是哪一行、
    # 什么类型的异常、当时这个 step 处于什么配置/输入下）──────────────────
    error_type: Optional[str] = None    # 异常类名，如 "AttributeError"
    traceback: Optional[str] = None     # traceback.format_exc() 全文
    context: dict = field(default_factory=dict)  # 出错时的 step/workflow 上下文快照，见 runner.py _build_error_context
    # [P11 §6 workflow_input_passing_and_debug_logging_improvement_plan.md]
    # 调试专用运行日志，受 cfg.workflow.debug_log_enabled 开关控制（默认
    # 关闭，避免长期运行的 workflow session 目录体积膨胀）。开启后由
    # runner 在每个 step 执行完（无论成功失败）统一填充，典型字段：
    #   resolved_prompt          — _resolve_prompt 替换占位符后的最终文本
    #   unresolved_placeholders  — inputs 里没找到对应值、被原样保留的占位符
    #   upstream_step_ids_used   — 实际引用到的上游 step_id（可与 depends_on diff）
    #   started_at / finished_at — ISO8601 时间戳
    #   thread_id / batch_index  — 并发批次执行位置，用于核对是否真的并发
    #   subprocess_stdout/stderr — python_step/script 子进程输出（成功时也保留）
    # 长文本字段按 cfg.workflow.debug_log_max_chars 截断，不无限增长。
    debug_log: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output,
            "score": self.score,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "retries_used": self.retries_used,
            "error_type": self.error_type,
            "traceback": self.traceback,
            "context": self.context,
            "debug_log": self.debug_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepResult":
        return cls(
            step_id=str(data.get("step_id", "")),
            status=StepStatus(data.get("status", "pending")),
            output=str(data.get("output", "")),
            score=data.get("score"),
            error=data.get("error"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            retries_used=int(data.get("retries_used", 0)),
            error_type=data.get("error_type"),
            traceback=data.get("traceback"),
            context=data.get("context") if isinstance(data.get("context"), dict) else {},
            debug_log=data.get("debug_log") if isinstance(data.get("debug_log"), dict) else {},
        )