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
STEP_TYPES = ("agent", "role_agent", "sub_workflow", "tool_call", "human_input", "script", "skill_agent")


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
                type=s.get("type"),
                workflow_name=s.get("workflow_name"),
                tool_name=s.get("tool_name"),
                tool_args=dict(s.get("tool_args") or {}),
                input_prompt=s.get("input_prompt"),
                script=s.get("script"),
                prompt_file=s.get("prompt_file"),
                skill_name=s.get("skill_name"),
                include=s.get("include"),
            ))
        return cls(
            name=str(data.get("name", "unnamed")),
            steps=steps,
            description=str(data.get("description", "")),
            version=str(data.get("version", "1.0")),
            max_total_duration=float(data["max_total_duration"]) if data.get("max_total_duration") else None,
            max_total_tokens=int(data["max_total_tokens"]) if data.get("max_total_tokens") else None,
            defaults=dict(data.get("defaults") or {}),
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
                    **({"type": s.type} if s.type else {}),
                    **({"workflow_name": s.workflow_name} if s.workflow_name else {}),
                    **({"tool_name": s.tool_name} if s.tool_name else {}),
                    **({"tool_args": s.tool_args} if s.tool_args else {}),
                    **({"input_prompt": s.input_prompt} if s.input_prompt else {}),
                    **({"script": s.script} if s.script else {}),
                    **({"skill_name": s.skill_name} if s.skill_name else {}),
                    **({"include": s.include} if s.include else {}),
                }
                for s in self.steps
            ],
        }

    def validate(self, *, check_placeholders: bool = True, role_checker=None) -> list[str]:
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
        """
        errors: list[str] = []
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
            if not step.prompt.strip() and not step.prompt_file and not step.include and step.effective_type != "human_input":
                errors.append(f"步骤 {step.id!r} 的 prompt 为空")

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

        # 检查依赖是否存在
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in seen_ids:
                    errors.append(f"步骤 {step.id!r} 依赖不存在的步骤 {dep!r}")

        # [P6] role 引用校验（可选，需要调用方传入 role_checker）
        if role_checker is not None:
            for step in self.steps:
                if step.role and not role_checker(step.role):
                    errors.append(f"步骤 {step.id!r} 引用了不存在的角色 Agent profile：{step.role!r}")

        # [P6] Prompt 占位符引用完整性校验：{step_id.output}/{step_id.score}
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
                    elif ref_field not in ("output", "score"):
                        errors.append(
                            f"步骤 {step.id!r} 的 prompt 占位符 {{{key}}} 引用了未知字段 "
                            f"{ref_field!r}（只支持 .output / .score）"
                        )

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
        )