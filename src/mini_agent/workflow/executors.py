"""
workflow/executors.py — Step 类型化执行器（workflow机制改进计划.md P5）

背景：改造前 runner._execute_step 用 `if step.role: ... else: ...` 隐式二选一
（主 Agent / 角色 Agent）。新增一种 step 类型（子工作流、外部脚本、人工输入、
直接调用工具）不应该要求改动 runner 核心循环——本模块把"怎么执行某种
类型的 step"抽成独立的 StepExecutor 子类，runner._dispatch_step() 只做
"按 step.effective_type 查表分发"，新增类型只需在这里加一个类 + 在
_EXECUTORS 里注册一行。

每个 Executor.execute(runner, step, prompt) 返回该 step 的输出文本（str），
调用方（runner._execute_step）在拿到文本后统一处理 evaluator 评分提取/
质检门判断——这部分逻辑与"用什么方式产生了这段输出"无关，继续留在
runner 里，不下沉到每个 Executor，避免重复。

各类型的语义与保护措施：
  agent        — 独立主 Agent 实例执行（原有行为，见 runner._execute_with_main_agent）
  role_agent   — 指定角色 Agent 执行（原有行为，见 runner._execute_with_role_agent）
  sub_workflow — 把另一个已保存的工作流当作一个 step 执行，递归深度受
                 cfg.workflow.max_sub_workflow_depth 保护，避免 A→B→A 循环
  tool_call    — 直接调用一个已注册工具（不启动整个 Agent 会话），属于
                 "高风险"类型：默认沿用 step.require_approval 语义，
                 cfg.workflow.tool_call_step_auto_approve=False（默认）时，
                 runner 会在 step 未显式声明 require_approval 时依然建议
                 走审批门（见 runner._effective_require_approval）
  human_input  — 阻塞等待外部调用 provide_workflow_step_input 工具送入文本，
                 复用 registry.ControlState 的等待模式（与审批门是两套独立信号）
  script       — 执行 step.script 指定的 shell 命令，默认关闭
                 （cfg.workflow.script_step_enabled=False），避免任意
                 workflow YAML 变成命令执行入口
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .schema import WorkflowStep, StepResult, StepStatus

if TYPE_CHECKING:
    from .runner import WorkflowRunner


def build_mock_step_results(mock_step_results: "Optional[dict]") -> "dict[str, StepResult]":
    """
    [workflow_mechanism_improvement_plan_p10.md §1] 把 test_workflow_step
    工具接收到的 mock 上游数据（JSON 反序列化后的
    `{step_id: {"output": "...", "score": ..., "passed": ...}}`）转换成真实
    的 `StepResult` 对象，供 `WorkflowRunner._resolve_prompt()` /
    `WorkflowRunner._eval_condition()` 直接复用——两者本来就接受
    `dict[str, StepResult]`，不需要为沙箱测试单独写一套命名空间构造逻辑，
    只要把 mock 数据"伪装"成真实 StepResult 即可完全复用现有的占位符替换
    和 condition 求值代码路径。

    字段含义：
      output  — 该 step 的模拟输出文本（{step_id.output} 占位符会替换成这个）
      score   — 0~1 的浮点评分（{step_id.score} 占位符输出 int(score*100)）
      passed  — 是否视为"成功"，决定 status 是 DONE 还是 FAILED
                （condition 里 `xxx.passed` 会读到这个值）
      status  — 显式指定状态字符串（如 "gate_failed"），优先级高于 passed
    """
    result: dict[str, StepResult] = {}
    for step_id, raw in (mock_step_results or {}).items():
        if not isinstance(raw, dict):
            continue
        status_str = raw.get("status")
        if status_str:
            try:
                status = StepStatus(status_str)
            except ValueError:
                status = StepStatus.DONE
        else:
            status = StepStatus.DONE if raw.get("passed", True) else StepStatus.FAILED
        result[step_id] = StepResult(
            step_id=step_id,
            status=status,
            output=str(raw.get("output", "")),
            score=raw.get("score"),
        )
    return result


class StepExecutor:
    """
    单个 Step 类型的执行器基类，是 workflow 系统的公开扩展点
    （[P7-④1 workflow_mechanism_improvement_plan.md]）。

    外部插件（如 myplugins/ 下的模块）新增一种 step 类型时，继承本类实现
    execute()（必选）和 validate_step()（可选），再调用模块级
    register_step_executor(type_name, executor实例) 完成注册，不需要改动
    本包源码，也不需要改动 runner.py 的核心循环——runner._execute_step()
    只按 step.effective_type 查表分发。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        """执行该 step，返回输出文本。子类必须实现。"""
        raise NotImplementedError

    def validate_step(self, step: WorkflowStep) -> list[str]:
        """
        校验该 step 定义是否合法，返回错误信息列表（空列表=合法）。

        仅对通过 register_step_executor() 注册的自定义类型生效——内置的
        7 种类型的必填字段校验写死在 schema.py::WorkflowDef.validate() 里，
        不经过这个钩子。自定义类型若有专属必填字段（例如"调用外部 HTTP
        API 的 step 必须填 url"），在这里检查并返回错误文案。
        """
        return []


class AgentStepExecutor(StepExecutor):
    """type=agent（或未设置 type 且 role 为空）：独立主 Agent 实例执行。"""

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        return runner._execute_with_main_agent(step, prompt)


class RoleAgentStepExecutor(StepExecutor):
    """type=role_agent（或未设置 type 且 role 非空）：指定角色 Agent 执行。"""

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        return runner._execute_with_role_agent(step, prompt)


class SubWorkflowStepExecutor(StepExecutor):
    """type=sub_workflow：把 step.workflow_name 引用的另一个已保存工作流当作一个 step 执行。

    子工作流拿到的 inputs 是 `{"input": <本 step 已解析好占位符的 prompt>}`，
    子工作流内部的 step prompt 若要引用它，写 `{input}` 即可；子工作流的
    `final_output`（最后一个成功步骤的输出）作为本 step 的输出返回。

    深度保护：通过 runner._sub_workflow_depth 记录当前递归深度，超过
    `cfg.workflow.max_sub_workflow_depth` 时直接拒绝执行，防止
    A → B → A 这类循环引用导致无限递归/线程耗尽。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        from pathlib import Path
        from .store import WorkflowStore

        depth = getattr(runner, "_sub_workflow_depth", 0)
        wf_cfg = getattr(runner._cfg, "workflow", None)
        max_depth = int(getattr(wf_cfg, "max_sub_workflow_depth", 3))
        if depth >= max_depth:
            raise RuntimeError(
                f"sub_workflow 嵌套深度已达上限（max_sub_workflow_depth={max_depth}），"
                f"拒绝执行 step {step.id!r} 引用的工作流 {step.workflow_name!r}，"
                "请检查是否存在循环引用（A 引用 B，B 又引用 A 之类）"
            )

        store = WorkflowStore(Path(runner._cfg.project_root))
        sub_wf = store.load(step.workflow_name or "")
        if sub_wf is None:
            raise ValueError(f"sub_workflow 引用的工作流不存在：{step.workflow_name!r}")

        parent_wf_session = getattr(runner, "_current_wf_session", None)
        sub_session_id = None
        if parent_wf_session is not None:
            sub_session_id = f"{parent_wf_session.workflow_session_id}_sub_{step.id}"

        # 延迟导入避免模块级循环依赖（runner.py 也会导入本模块）
        from .runner import WorkflowRunner as _Runner
        sub_runner = _Runner(runner._cfg)
        sub_runner._sub_workflow_depth = depth + 1
        result = sub_runner.run(sub_wf, inputs={"input": prompt}, workflow_session_id=sub_session_id)
        return result.final_output or result.to_summary()


class ToolCallStepExecutor(StepExecutor):
    """type=tool_call：直接调用一个已注册工具，而不是启动一整个 Agent 会话。

    step.tool_args 为空时，把已解析占位符的 prompt 作为唯一实参，按工具
    函数签名的第一个参数名传入（适合"单参数字符串工具"场景，如
    web_search(query) 这类；参数更复杂时应显式填写 tool_args）。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        from mini_agent.tools import get_default_registry

        registry = get_default_registry()
        tool_def = registry.get(step.tool_name or "")
        if tool_def is None:
            raise ValueError(f"tool_call 引用的工具不存在：{step.tool_name!r}")

        tool_input = dict(step.tool_args or {})
        if not tool_input:
            try:
                import inspect
                sig = inspect.signature(tool_def.fn)
                params = [p for p in sig.parameters if p != "self"]
                if params:
                    tool_input = {params[0]: prompt}
            except (TypeError, ValueError):
                tool_input = {}
        else:
            # [workflow_mechanism_improvement_plan_p12.md Phase 2] tool_args
            # 里的字符串值支持 {step_id.output} 等占位符，复用
            # _resolve_prompt 的替换逻辑（通过 _resolve_value 递归到嵌套
            # dict/list 的字符串叶子节点）。没有占位符的字面量值（原有
            # 用法）经过 _resolve_prompt 处理后原样返回，完全向后兼容。
            step_results = getattr(runner, "_current_step_results", None) or {}
            inputs = getattr(runner, "_current_inputs", None) or {}
            tool_input = runner._resolve_value(tool_input, step_results, inputs)

        result = registry.call(step.tool_name, tool_input)
        return result if isinstance(result, str) else str(result)


class HumanInputStepExecutor(StepExecutor):
    """type=human_input：阻塞等待外部调用 provide_workflow_step_input 工具送入文本。

    与人工审批门（require_approval）是两套独立机制：审批门是"允许/拒绝
    该 step 是否执行"，human_input 是"该 step 本身就是在向人类要一段
    文本输入"，返回值直接作为该 step 的 output，可被后续 step 用
    `{step_id.output}` 引用。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        import mini_agent.ui.renderer as R
        from . import registry as wf_registry

        control = getattr(runner, "_current_control", None)
        wf_session = getattr(runner, "_current_wf_session", None)
        paths = getattr(runner, "_current_paths", None)
        wf_cfg = getattr(runner._cfg, "workflow", None)
        poll_interval = float(getattr(wf_cfg, "approval_poll_interval_seconds", 3.0))
        wait_timeout = getattr(wf_cfg, "human_input_wait_timeout_seconds", 1800.0)

        prompt_to_show = step.input_prompt or prompt

        # [改进方案 §1] input_key 命中 run_workflow(inputs=...) 里的值时，
        # 直接使用，不进入阻塞等待——用于让全自动 workflow 复用同一份
        # human_input step 定义，所有输入在启动时一次性给全。
        if step.input_key:
            current_inputs = getattr(runner, "_current_inputs", None) or {}
            if step.input_key in current_inputs:
                value = str(current_inputs[step.input_key])
                if wf_session is not None and paths is not None:
                    wf_session.append_event(paths, "human_input_prefilled", {
                        "step_id": step.id, "input_key": step.input_key,
                    })
                return value

        if control is None:
            # 没有 registry 上下文（如单测直接调用 _execute_step），无法真的
            # 等待外部输入，直接把展示文本原样返回，避免破坏现有测试假设。
            return prompt_to_show

        control.pending_input_step = step.id
        control.input_provided.clear()
        control.provided_input_text = ""
        if wf_session is not None and paths is not None:
            wf_session.append_event(paths, "human_input_requested", {
                "step_id": step.id, "prompt": prompt_to_show,
            })

        R.print_warning(
            f"[Workflow] ⌨️ 步骤 {step.id} 需要人工输入，等待 provide_workflow_step_input"
            f"（workflow_session_id={getattr(wf_session, 'workflow_session_id', '?')}）\n"
            f"提示：{prompt_to_show}"
        )

        waited = 0.0
        while True:
            if control.cancel_requested.is_set():
                raise RuntimeError("等待人工输入期间收到 cancel 信号")
            if control.input_provided.is_set():
                break
            if wait_timeout and waited >= wait_timeout:
                raise TimeoutError(f"步骤 {step.id} 等待人工输入超时（{wait_timeout}s）")
            time.sleep(poll_interval)
            waited += poll_interval

        control.pending_input_step = None
        text = control.provided_input_text
        if wf_session is not None and paths is not None:
            wf_session.append_event(paths, "human_input_provided", {"step_id": step.id})
        return text


class ScriptStepExecutor(StepExecutor):
    """type=script：执行 step.script 指定的 shell 命令。

    默认被 cfg.workflow.script_step_enabled=False 关闭——工作流 YAML 可能
    来自 LLM 生成或他人分享，把"执行任意 shell 命令"设为默认开启会引入
    明显的供应链风险；需要显式在 agent_config.json 里打开开关才能使用。
    超时优先取 step.timeout，否则取 cfg.workflow.script_step_timeout_seconds。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        wf_cfg = getattr(runner._cfg, "workflow", None)
        if not bool(getattr(wf_cfg, "script_step_enabled", False)):
            raise PermissionError(
                "script 类型 step 已被禁用（cfg.workflow.script_step_enabled=False）。"
                "如需启用，请在 agent_config.json 的 workflow 节里设置 "
                "\"script_step_enabled\": true 后重试。"
            )
        if not step.script:
            raise ValueError(f"步骤 {step.id!r} 是 script 类型但未指定 script 命令")

        timeout = step.timeout or float(getattr(wf_cfg, "script_step_timeout_seconds", 60.0))
        _is_windows = sys.platform == "win32"
        _popen_kwargs = {
            "shell": True,
            "cwd": str(runner._cfg.project_root),
            "capture_output": True,
            "text": True,
            # [编码健壮性] 不用 subprocess 默认的"按宿主机 locale 解码"
            # （Windows 上常是 GBK），命令输出里只要出现 emoji/生僻字就会在
            # 解码阶段直接抛 UnicodeDecodeError，把一个本来跑成功的命令
            # 判定为失败。显式固定 UTF-8 + errors="replace"：正常 UTF-8
            # 输出精确解码，非 UTF-8 的极端情况也只是把个别字符替换成
            # U+FFFD，不会让整个 step 因为编码问题而中断。
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
        }
        if _is_windows:
            # Windows: use CREATE_NEW_PROCESS_GROUP for proper process tree termination
            _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Unix: use start_new_session for proper process group handling
            _popen_kwargs["start_new_session"] = True
        proc = subprocess.run(step.script, **_popen_kwargs)
        # [P11 §6] 无论成功/失败都把 stdout/stderr 挂到 runner 实例属性上，
        # 供 _execute_step 合并进 StepResult.debug_log——之前只有失败时
        # 才能在异常消息里看到，成功时直接丢弃。
        runner._last_subprocess_debug = {
            "subprocess_stdout": proc.stdout,
            "subprocess_stderr": proc.stderr,
        }
        if proc.returncode != 0:
            raise RuntimeError(
                f"脚本执行失败（returncode={proc.returncode}）：\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc.stdout


class PythonStepExecutor(StepExecutor):
    """type=python_step（[计划 §B4]）：在独立子进程里执行 step.script_path
    指定的 Python 脚本（约定入口函数 `run(ctx) -> str | dict`），脚本内可
    通过 ctx.llm.ask()/ask_json() 调用 LLM（转发到 LLMHelper，与主 Agent
    同一套 provider/重试/fallback 机制），也可通过 ctx.run_agent_turn()
    临时起一个最小 Agent 处理需要判断力的子任务（与 skill_agent 类型共用
    agent_spawn.build_minimal_agent 构造逻辑）。

    默认被 cfg.workflow.python_step_enabled=False 关闭，语义与
    script_step_enabled 一致——防止分享出去的 workflow YAML 变成任意
    Python 代码执行入口。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        wf_cfg = getattr(runner._cfg, "workflow", None)
        if not bool(getattr(wf_cfg, "python_step_enabled", False)):
            raise PermissionError(
                "python_step 类型 step 已被禁用（cfg.workflow.python_step_enabled=False）。"
                "如需启用，请在 agent_config.json 的 workflow 节里设置 "
                "\"python_step_enabled\": true 后重试。"
            )
        if not step.script_path:
            raise ValueError(f"步骤 {step.id!r} 是 python_step 类型但未指定 script_path")

        timeout = step.timeout or float(getattr(wf_cfg, "python_step_timeout_seconds", 120.0))

        wf_session = getattr(runner, "_current_wf_session", None)
        paths = getattr(runner, "_current_paths", None)
        if wf_session is not None and paths is not None:
            session_dir = paths.workflow_session_dir(wf_session.workflow_session_id)
            output_dir = paths.ensure_workflow_session_output_dir(wf_session.workflow_session_id)
        else:
            session_dir = Path(runner._cfg.project_root)
            output_dir = session_dir

        # 上游 step 结果按占位符协议序列化传给子进程（只传纯数据，不传对象）。
        # [B4] runner._execute_step() 在派发 executor.execute() 之前会把当前
        # 的 step_results 赋给 self._current_step_results，供这里读取——
        # python_step 是唯一需要拿到"全部上游结果字典"的 executor（其它
        # 类型都是走 prompt 占位符替换，不需要原始 dict）。
        #
        # [P11 §4] 默认按 step.depends_on 过滤：脚本只能读到显式声明过依赖
        # 的上游 step 结果，不能"偷看"未声明依赖的 step——依赖关系应该在
        # workflow 定义里可见，不应该靠脚本内容里读字典绕过，这也是拓扑
        # 分批"同层并发安全"假设成立的前提之一。受
        # cfg.workflow.python_step_inputs_filtered_by_depends_on 开关控制，
        # 默认开启；关闭后回退到旧版本"传全部已跑完 step"的行为，供还没来得
        # 及给旧脚本补全 depends_on 声明的用户过渡期临时兼容。
        upstream = getattr(runner, "_current_step_results", None) or {}
        filter_by_deps = bool(getattr(wf_cfg, "python_step_inputs_filtered_by_depends_on", True))
        _undeclared_py_deps: list[str] = []
        if filter_by_deps:
            upstream = {sid: r for sid, r in upstream.items() if sid in set(step.depends_on)}
        else:
            # [P11 §6.4] 关闭过滤开关后，脚本能读到的全部上游结果里，
            # 未在 depends_on 声明过的那部分记下来，供 runner 侧 diff 后
            # 上报 watchdog（不改变本次执行行为，只做记录）。
            _undeclared_py_deps = sorted(set(upstream.keys()) - set(step.depends_on))
        inputs_payload = {
            sid: {
                "status": getattr(r.status, "value", str(r.status)),
                "output": r.output,
                "score": r.score,
                "result_file": getattr(r, "result_file", None),
            }
            for sid, r in upstream.items()
        }

        wf = getattr(runner, "_current_wf", None)
        workflow_dir = str(wf.source_dir) if wf is not None and getattr(wf, "source_dir", None) else None

        request = {
            "step_id": step.id,
            "session_dir": str(session_dir),
            "output_dir": str(output_dir),
            "inputs": inputs_payload,
            "params": step.params or {},
            "script_path": step.script_path,
            "workflow_dir": workflow_dir,
            "app_cfg": {
                "project_root": str(runner._cfg.project_root),
                "sandbox": runner._cfg.sandbox,
                "model": runner._effective_step_field(step, "model", None) or runner._cfg.model,
                "llm_provider": runner._cfg.llm_provider,
                "llm_base_url": runner._cfg.llm_base_url,
                # [P11 §5] api_key 不再写进这里——落盘的 request.json 明文
                # 保存密钥即使目录会自动清理，仍有窗口期被同机进程/崩溃
                # 转储读到的风险。改为通过环境变量 MINI_AGENT_STEP_API_KEY
                # 传给子进程（见下方 subprocess.run 的 env 参数），
                # py_step_runner.py 优先读该环境变量。
                "debug_llm": getattr(runner._cfg, "debug_llm", False),
                "debug_llm_console": getattr(runner._cfg, "debug_llm_console", False),
                "skills_dir": str(getattr(runner._cfg, "skills_dir", "") or "") or None,
            },
        }

        with tempfile.TemporaryDirectory(prefix="mini_agent_python_step_") as tmp_dir:
            req_path = Path(tmp_dir) / "request.json"
            req_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

            _is_windows = sys.platform == "win32"
            # [P11 §5] api_key 通过环境变量传递，不落盘进 request.json。
            _child_env = dict(os.environ)
            if runner._cfg.api_key:
                _child_env["MINI_AGENT_STEP_API_KEY"] = runner._cfg.api_key
            # [编码健壮性] 子进程（py_step_runner.py）用 print(json.dumps(...))
            # 把结果传回来——这是父子进程之间唯一的通信通道。子进程自己的
            # stdout 编码默认由宿主机 locale 决定（Windows 上常是 GBK），
            # 脚本产出的数据（比如爬到的知乎问题标题）只要带 emoji 这类
            # GBK 编不了的字符，子进程会在自己的 print() 那一行直接崩溃
            # （returncode!=0），把整个 step 判定为失败——这不是父进程解码
            # 阶段的问题，是子进程写出阶段就先炸了，所以必须从子进程的
            # 环境变量入手，强制它用 UTF-8 写 stdout/stderr。
            # py_step_runner.py 里也加了 sys.stdout.reconfigure(utf-8) 兜底，
            # 这里的环境变量是双保险，覆盖"reconfigure 因为某些环境不可用"
            # 的边缘情况。
            _child_env["PYTHONIOENCODING"] = "utf-8"
            # [Ctrl+C 修复] 原来这里用 subprocess.run(..., timeout=timeout)，
            # 其底层在 Windows 上是一次性 WaitForSingleObject(timeout_ms)——
            # 一旦某个 python_step 的 timeout 设得比较长（比如
            # enrich_questions 现在的 1800s），这一整段时间里主进程都卡在
            # 这一次 C 级阻塞调用上：即使用户按 Ctrl+C、监听线程/内核也把
            # SIGINT 递给了主进程，CPython 也要等这次阻塞调用返回才会检查
            # 到 pending 信号、抛出 KeyboardInterrupt——实际表现就是"按了
            # Ctrl+C 没反应，得等这个 step 跑完/超时才退出"。
            #
            # 改法：用 Popen 起子进程后，改成短间隔（POLL_INTERVAL）轮询
            # proc.wait()，每次阻塞最多 POLL_INTERVAL 秒就返回一次 Python
            # 字节码执行层，KeyboardInterrupt 能在两次轮询之间被正常抛出；
            # stdout/stderr 改成重定向到临时文件而不是 PIPE，避免轮询期间
            # 不去 communicate() 导致管道缓冲区写满、子进程反过来卡死的
            # 经典死锁。捕获到 KeyboardInterrupt 时主动杀掉子进程（Windows
            # 下用 taskkill /T 连子进程树一起杀，因为 CREATE_NEW_PROCESS_GROUP
            # 只是让子进程不再被同一个 Ctrl+C 信号直接杀掉，不代表它会跟着
            # 父进程一起退出），再把 KeyboardInterrupt 重新抛出去，让上层
            # （runner 主循环 / CLI）走正常的中断处理路径。
            POLL_INTERVAL = 0.3
            stdout_path = Path(tmp_dir) / "stdout.log"
            stderr_path = Path(tmp_dir) / "stderr.log"

            _popen_kwargs: dict = {
                "cwd": str(runner._cfg.project_root),
                "stdout": open(stdout_path, "w", encoding="utf-8", errors="replace"),
                "stderr": open(stderr_path, "w", encoding="utf-8", errors="replace"),
                "env": _child_env,
            }
            if _is_windows:
                _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                _popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen(
                [sys.executable, "-m", "mini_agent.workflow.py_step_runner", str(req_path)],
                **_popen_kwargs,
            )

            def _kill_proc_tree(p: "subprocess.Popen") -> None:
                try:
                    if _is_windows:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                            capture_output=True, timeout=10,
                        )
                    else:
                        import signal as _signal
                        os.killpg(os.getpgid(p.pid), _signal.SIGKILL)
                except Exception:
                    pass  # 子进程可能已经自己退出了，杀失败不影响后续处理
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass

            start_ts = time.monotonic()
            returncode = None
            try:
                while True:
                    try:
                        returncode = proc.wait(timeout=POLL_INTERVAL)
                        break
                    except subprocess.TimeoutExpired:
                        if timeout and (time.monotonic() - start_ts) > timeout:
                            _kill_proc_tree(proc)
                            raise subprocess.TimeoutExpired(
                                cmd="mini_agent.workflow.py_step_runner", timeout=timeout,
                            )
            except KeyboardInterrupt:
                _kill_proc_tree(proc)
                raise
            finally:
                _popen_kwargs["stdout"].close()
                _popen_kwargs["stderr"].close()

            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")

            # [P11 §6] 无论成功/失败都把 stdout/stderr 挂到 runner 实例属性上，
            # 供 _execute_step 合并进 StepResult.debug_log（成功时之前会
            # 被直接丢弃，脚本里的 print() 调试信息只有失败才看得到）。
            runner._last_subprocess_debug = {
                "subprocess_stdout": stdout_text,
                "subprocess_stderr": stderr_text,
            }
            if _undeclared_py_deps:
                runner._last_subprocess_debug["undeclared_dependency_usage"] = _undeclared_py_deps

            last_line = ""
            for line in stdout_text.splitlines():
                line = line.strip()
                if line:
                    last_line = line
            try:
                result = json.loads(last_line) if last_line else {}
            except json.JSONDecodeError:
                result = {}

            if returncode != 0 or not result.get("ok"):
                raise RuntimeError(
                    f"python_step 执行失败（returncode={returncode}）：\n"
                    f"error: {result.get('error')}\n"
                    f"traceback: {result.get('traceback')}\n"
                    f"stdout: {stdout_text}\nstderr: {stderr_text}"
                )
            return result.get("output", "")


class SkillAgentStepExecutor(StepExecutor):
    """type=skill_agent（workflow_directory_mode_design.md 阶段3）：
    临时启动一个只强制挂载 step.skill_name 指定的 skill 的最小 Agent 执行
    prompt，不做关键词触发判断——用于"这一步明确要用某个 skill 的能力"，
    比依赖关键词命中更直接。查找顺序：先 workflow 本地资源包
    （文件夹模式 workflow 的 skills/ 目录），再全局 skills_dir。
    """

    # [skill_agent 结果文件契约] result_file 校验失败后的重试预算：先原地
    # resume 同一个 agent（省 token，且能带着上下文直接被点名"你没写文件"）
    # 最多 RESUME_RETRIES 次；如果 resume 也救不回来，再整个重开一个全新
    # agent（大概率是 resume 那次已经把上下文搅乱了）最多 RESTART_RETRIES
    # 次；两轮预算都耗尽仍未产出合法结果文件，判定该 step 失败，交给
    # runner 现有的 retry_on_error 机制处理（重跑整个 step）。
    RESUME_RETRIES = 3
    RESTART_RETRIES = 3

    @staticmethod
    def _strip_skill_tags(text: str) -> str:
        import re
        return re.sub(r'<skill_used>[^<]*</skill_used>', '', text).strip()

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        if not step.skill_name:
            raise ValueError(f"步骤 {step.id!r} 是 skill_agent 类型但未指定 skill_name")

        max_turns = runner._effective_step_field(step, "max_turns", 50)
        timeout = runner._effective_step_field(step, "timeout", None)

        # 没有声明 result_file 的 skill_agent step：保持旧行为（对话输出即
        # 结果），不引入任何新约束，向后兼容现有 workflow。
        if not step.result_file:
            agent = runner._spawn_minimal_agent(
                step, skill_name=step.skill_name, max_turns=max_turns, timeout=timeout,
            )
            output = self._strip_skill_tags(agent.run_turn(prompt))
            runner._record_step_agent_session(agent)
            return output

        # [next_doc/workflow_python_step_and_zhihu_publish_plan.md §B3]
        # "构造最小 Agent" 的逻辑已抽到 runner._spawn_minimal_agent()
        # （内部转发到 agent_spawn.build_minimal_agent），与 python_step 的
        # ctx.run_agent_turn() 共用同一份实现，这里不再重复写一遍。
        result_path = runner.resolve_result_file_path(step)
        file_instruction = (
            f"\n\n【重要】任务的最终结果不是靠这段对话回复来交付的，你必须使用"
            f"文件写入工具，把最终的结构化结果以合法 JSON 格式写入这个绝对路径："
            f"{result_path}\n"
            f"写完之后请自行确认该文件已经存在且内容是合法 JSON，再结束本轮任务。"
        )

        agent = runner._spawn_minimal_agent(
            step, skill_name=step.skill_name, max_turns=max_turns, timeout=timeout,
        )
        runner._record_step_agent_session(agent)
        output = self._strip_skill_tags(agent.run_turn(prompt + file_instruction))
        ok, reason = runner._validate_result_file(step)
        if ok:
            return output

        # 第一轮：resume 同一个 agent（沿用其上下文/浏览器状态），直接点名
        # 让它补写文件。
        for attempt in range(1, self.RESUME_RETRIES + 1):
            resume_prompt = (
                f"你刚才没有把结果正确写入 {result_path}（校验失败原因："
                f"{reason}）。请立即使用文件写入工具，将完整的结构化结果以"
                f"合法 JSON 格式写入这个绝对路径：{result_path}\n"
                f"不要只在对话里回复内容，必须实际创建/覆盖这个文件。"
            )
            output = self._strip_skill_tags(agent.run_turn(resume_prompt))
            ok, reason = runner._validate_result_file(step)
            if ok:
                return output

        # 第二轮：resume 没救回来，大概率上下文已经跑偏，整个重开一个全新
        # agent 从头再来。
        for attempt in range(1, self.RESTART_RETRIES + 1):
            agent = runner._spawn_minimal_agent(
                step, skill_name=step.skill_name, max_turns=max_turns, timeout=timeout,
            )
            runner._record_step_agent_session(agent)
            output = self._strip_skill_tags(agent.run_turn(prompt + file_instruction))
            ok, reason = runner._validate_result_file(step)
            if ok:
                return output

        raise RuntimeError(
            f"步骤 {step.id!r}（skill_agent）在 resume×{self.RESUME_RETRIES} + "
            f"重开×{self.RESTART_RETRIES} 次尝试后仍未产出合法的 result_file "
            f"（{result_path}）：{reason}"
        )


# ── 分发表 ────────────────────────────────────────────────────────────────────
# runner._dispatch_step() 按 step.effective_type 查表；新增类型只需在这里
# 加一行注册，不需要改动 runner 的核心循环。

_EXECUTORS: dict[str, StepExecutor] = {
    "agent": AgentStepExecutor(),
    "role_agent": RoleAgentStepExecutor(),
    "sub_workflow": SubWorkflowStepExecutor(),
    "tool_call": ToolCallStepExecutor(),
    "human_input": HumanInputStepExecutor(),
    "script": ScriptStepExecutor(),
    "skill_agent": SkillAgentStepExecutor(),
    "python_step": PythonStepExecutor(),
}


def get_executor(step_type: str) -> StepExecutor:
    executor = _EXECUTORS.get(step_type)
    if executor is None:
        raise ValueError(f"未知的 step 类型：{step_type!r}（可选：{list(_EXECUTORS)}）")
    return executor


def get_registered_types() -> tuple[str, ...]:
    """返回当前所有已注册的 step 类型（内置 7 种 + 插件注册的自定义类型）。"""
    return tuple(_EXECUTORS.keys())


def register_step_executor(type_name: str, executor: StepExecutor) -> None:
    """
    [P7-④1 workflow_mechanism_improvement_plan.md] 注册一个自定义 step
    Executor，供外部插件调用（地位类似 tools.py 的 @tool 装饰器）。

    典型用法（myplugins/my_http_step.py）：

        from mini_agent.workflow.executors import StepExecutor, register_step_executor

        class HttpStepExecutor(StepExecutor):
            def execute(self, runner, step, prompt):
                ...
                return response_text

            def validate_step(self, step):
                errs = []
                if not step.tool_args.get("url"):
                    errs.append(f"步骤 {step.id!r} 是 http 类型但未指定 url")
                return errs

        def register(cfg):
            register_step_executor("http", HttpStepExecutor())

    `register(cfg)` 由 mini_agent.plugins.discover_and_register_plugins()
    在启动阶段扫描 myplugins/*.py 时自动调用（见 plugins.py）。

    覆盖内置类型（"agent"/"role_agent"/... 这 7 个）会打印警告但仍然允许
    （便于测试环境替换实现），生产场景不建议覆盖内置类型。
    """
    if type_name in _EXECUTORS and type_name in (
        "agent", "role_agent", "sub_workflow", "tool_call", "human_input", "script", "skill_agent",
    ):
        import logging
        logging.getLogger(__name__).warning(
            "[workflow] register_step_executor 覆盖了内置 step 类型 %r，"
            "这通常只应在测试中发生", type_name,
        )
    _EXECUTORS[type_name] = executor
