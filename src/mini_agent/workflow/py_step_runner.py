"""
workflow/py_step_runner.py — python_step 子进程侧入口
（next_doc/workflow_python_step_and_zhihu_publish_plan.md §B4）

被 executors.py::PythonStepExecutor 以
`python -m mini_agent.workflow.py_step_runner <request_json_path>` 方式拉起
的子进程入口。职责：
  1. 读取父进程写好的请求文件（session/output 路径、step 输入、脚本路径、
     基础 LLM 配置等，全部是可 JSON 序列化的原始值，不传对象）；
  2. 在本子进程内独立构造一条 LLMHelper（[计划 §B4] 简化版：不做跨进程共享
     主进程 LLMClientPool 状态的本地代理服务，先用独立 pool 跑通，
     代价是不跟随主 Agent 运行期 /model 切换——这个取舍在计划文档里
     已经写明，是刻意的分阶段实现，不是遗漏）；
  3. 用 runpy 加载 step.script_path 指定的脚本，调用其 run(ctx) 入口；
  4. 把结果（str 或可 JSON 序列化的 dict）包装成约定的结果包，最后一行
     打印到 stdout，供父进程解析。

约定的结果包格式（stdout 最后一行，单行 JSON）：
  {"ok": true, "output": "...", "output_is_json": false}
  {"ok": false, "error": "...", "error_type": "ValueError", "traceback": "..."}

脚本内部如果想输出调试信息，用 print() 到 stdout 也没关系——父进程按
"最后一行是结果包，之前的行是普通日志"来解析，不是逐行 JSON。
"""

from __future__ import annotations

import json
import os
import runpy
import sys
import traceback
from pathlib import Path


def _build_llm_helper(app_cfg_dict: dict):
    """[计划 §B4 简化版] 子进程内独立构造一条 LLMClientPool，不跨进程共享
    主进程状态。走的是与 executors.py::SkillAgentStepExecutor /
    agent_spawn.build_minimal_agent 一致的 load_config() 路径，保证行为
    一致（同样的 provider/model/api_key 解析规则）。"""
    from mini_agent.config import load_config
    from mini_agent.llm.service import LLMHelper

    cfg = load_config(
        project_root=Path(app_cfg_dict["project_root"]),
        verbose=False,
        sandbox=app_cfg_dict.get("sandbox", True),
        auto_approve=True,
        model=app_cfg_dict.get("model"),
        llm_provider=app_cfg_dict.get("llm_provider"),
        llm_base_url=app_cfg_dict.get("llm_base_url"),
        debug_llm=app_cfg_dict.get("debug_llm", False),
        debug_llm_console=app_cfg_dict.get("debug_llm_console", False),
    )
    cfg.api_key = os.environ.get("MINI_AGENT_STEP_API_KEY") or app_cfg_dict.get("api_key")
    return LLMHelper.from_config(cfg)


def _make_run_agent_turn(app_cfg_dict: dict, session_dir=None):
    """构造 ctx.run_agent_turn 可调用对象：转发到 agent_spawn.build_minimal_agent
    （与 runner.py::WorkflowRunner._spawn_minimal_agent 共用同一份构造逻辑）。

    [数据聚合修复] session_dir 由调用方（main() 里）传入，指向
    workflow_sessions/<wf_session_id>/step_<step_id>_agent_turn/——python_step
    脚本内每调用一次 ctx.run_agent_turn() 都会新起一个最小 Agent，
    SessionManager 会在这个目录下再各自建一层随机 session_id 子目录，
    多次调用互不覆盖，同时都归档在这次 workflow 执行的数据目录里，
    不再散落进全局 .agent/sessions/。"""
    from .agent_spawn import build_minimal_agent

    def _run_agent_turn(prompt: str, *, skill_name=None, max_turns: int = 6) -> str:
        agent = build_minimal_agent(
            project_root=Path(app_cfg_dict["project_root"]),
            verbose=False,
            sandbox=app_cfg_dict.get("sandbox", True),
            model=app_cfg_dict.get("model"),
            llm_provider=app_cfg_dict.get("llm_provider"),
            llm_base_url=app_cfg_dict.get("llm_base_url"),
            api_key=os.environ.get("MINI_AGENT_STEP_API_KEY") or app_cfg_dict.get("api_key"),
            debug_llm=app_cfg_dict.get("debug_llm", False),
            debug_llm_console=app_cfg_dict.get("debug_llm_console", False),
            max_turns=max_turns,
            skill_name=skill_name,
            global_skills_dir=Path(app_cfg_dict["skills_dir"]) if app_cfg_dict.get("skills_dir") else None,
            session_dir=session_dir,
        )
        return agent.run_turn(prompt)

    return _run_agent_turn


def main() -> int:
    # [编码健壮性] 子进程默认继承宿主机 locale 作为 stdout/stderr 编码
    # （Windows 上常是 GBK）。父进程虽然已经通过 PYTHONIOENCODING=utf-8
    # 环境变量要求子进程用 UTF-8（见 executors.py::PythonStepExecutor），
    # 这里再显式 reconfigure 一遍作为双保险——万一某些环境变量没有正确
    # 传递到解释器启动阶段（比如被更外层的 launcher/venv 包装吞掉），
    # 这行代码能兜底同样的效果。脚本里的 print() 调试信息、以及下面的
    # 结果包 print()，都会因此不再受宿主机代码页限制，不会因为文本里
    # 出现 emoji/生僻字就把整个 step 判定为失败。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # 极老 Python 版本 / 非标准 stdout 对象没有 reconfigure，忽略

    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "缺少 request_json_path 参数"}))
        return 1

    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))

    from .py_context import PyStepContext, PyStepLLM
    from .schema import StepResult, StepStatus

    inputs = {}
    for step_id, raw in (request.get("inputs") or {}).items():
        inputs[step_id] = StepResult(
            step_id=step_id,
            status=StepStatus(raw.get("status", "done")),
            output=raw.get("output", ""),
            score=raw.get("score"),
            result_file=raw.get("result_file"),
        )

    # [数据聚合修复] session_dir 就是 workflow_sessions/<wf_session_id>/
    # （见 executors.py::PythonStepExecutor.execute 里的 session_dir 构造），
    # 每个 step 一个子目录，避免同一 workflow 里多个 python_step 调用
    # run_agent_turn() 时互相覆盖彼此的 agent session 数据。
    run_agent_turn_session_dir = Path(request["session_dir"]) / f"step_{request['step_id']}_agent_turn"

    ctx = PyStepContext(
        step_id=request["step_id"],
        session_dir=Path(request["session_dir"]),
        output_dir=Path(request["output_dir"]),
        inputs=inputs,
        params=request.get("params") or {},
        llm=PyStepLLM(helper_factory=lambda: _build_llm_helper(request["app_cfg"])),
        run_agent_turn=_make_run_agent_turn(request["app_cfg"], session_dir=run_agent_turn_session_dir),
        workflow_dir=Path(request["workflow_dir"]) if request.get("workflow_dir") else None,
    )

    try:
        mod_globals = runpy.run_path(request["script_path"], run_name="__mini_agent_python_step__")
        run_fn = mod_globals.get("run")
        if run_fn is None:
            raise ValueError(f"脚本 {request['script_path']!r} 未定义 run(ctx) 入口函数")
        result = run_fn(ctx)
        output_is_json = not isinstance(result, str)
        output_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "output": output_text, "output_is_json": output_is_json}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — 顶层捕获，转成结果包让父进程走统一错误处理
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
