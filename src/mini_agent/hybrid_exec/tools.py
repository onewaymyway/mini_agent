"""
hybrid_exec/tools.py — 向主 Agent 暴露 hybrid_exec 的内部工具函数

对应 next_doc/hybrid_exec_design_plan.md 的后续需求："agent 中应该有内部的
工具函数可以执行这个 task"。写法与 workflow/tools.py::register_workflow_tools
完全一致（同一套 @tool 装饰器 + 工具注册表）：

  run_hybrid_exec_task   执行一次 hybrid_exec 任务（脚本优先，坏了先修脚本，
                          修不好再降级 LLM/Agent 兜底），供主 Agent 在对话中
                          直接调用，不需要用户先手写 workflow yaml 或切到
                          命令行。
  list_hybrid_exec_tasks 列举 .agent/hybrid_exec/scripts/ 下已归档的任务
                          及当前状态，帮助 Agent/用户判断该复用哪个 task_id、
                          或该不该新建一个。
  show_hybrid_exec_task  查看某个 task_id 的仓库元信息与当前 active 脚本
                          源码，便于 Agent 判断脚本内容是否符合预期、要不要
                          force_reexplore。

在 cli/app.py 里调用 register_hybrid_exec_tools(cfg) 即可完成注册（与
register_workflow_tools(cfg) 相邻放置）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


def register_hybrid_exec_tools(cfg: "AppConfig") -> None:
    """向全局工具注册表注册 hybrid_exec 相关工具。"""
    from mini_agent.tools import tool

    project_root = Path(cfg.project_root)

    @tool(name="run_hybrid_exec_task", group="hybrid_exec",
          description="执行一个 hybrid_exec 混合任务：优先复用/生成一个可复用脚本执行，"
                      "脚本报错先尝试自动修复，修不好再降级到 LLM/Agent 直接兜底出结果。"
                      "适合'反复要做同一类事、值得沉淀成脚本'的任务（如格式转换/统计/抽取），"
                      "而不是一次性、探索性的任务（那种直接自己做或用普通工具即可，不必套这层）。"
                      "task_id 相同时会自动复用/迭代同一个脚本仓库，跨对话/跨 workflow 都能命中。"
                      )
    def run_hybrid_exec_task(
        task_id: str,
        description: str,
        input_json: str = "{}",
        allow_tiers: Optional[str] = None,
        force_reexplore: bool = False,
        agent_fs_write_enabled: bool = False,
        max_script_repair_attempts: int = 2,
    ) -> str:
        """
        task_id: 任务的稳定标识，同一个 task_id 会复用/迭代同一份脚本仓库
                （.agent/hybrid_exec/scripts/<task_id>/），跨调用/跨 workflow 都可复用。
                起名建议：动词+对象+版本语义，如 extract_entities_v1。
        description: 自然语言目标描述，脚本不存在或需要修复/降级时会作为
                探索/修复的 prompt 依据，请写清楚输入结构、期望输出结构。
        input_json: 本次调用的具体输入，JSON 字符串（object），如 '{"text": "..."}'，
                脚本内通过 ctx.params 读取。
        allow_tiers: 逗号分隔，限制允许使用的层级，可选 script/llm/agent 的子集，
                如 "script,llm" 表示禁止升级到 Agent（控制成本/权限）。不传则三层都允许。
        force_reexplore: True 时忽略仓库里已有脚本，强制重新探索一版新脚本
                （人工触发用，比如怀疑现有脚本逻辑过时）。
        agent_fs_write_enabled: True 时，探索/修复过程中拉起的 Agent 允许写文件系统；
                默认 False（只读沙箱），避免探索阶段误改动项目文件。
        max_script_repair_attempts: 脚本报错后，先尝试修复几次再降级到 LLM/Agent，默认 2。
        """
        from mini_agent.hybrid_exec import ExecutionTier, TaskSpec, default_executor

        try:
            input_data = json.loads(input_json)
        except json.JSONDecodeError as e:
            return f"[run_hybrid_exec_task] input_json 不是合法 JSON：{e}"
        if not isinstance(input_data, dict):
            return "[run_hybrid_exec_task] input_json 解析后必须是一个 JSON object"

        task_kwargs = dict(
            task_id=task_id,
            description=description,
            input_data=input_data,
            force_reexplore=bool(force_reexplore),
            agent_fs_write_enabled=bool(agent_fs_write_enabled),
            max_script_repair_attempts=int(max_script_repair_attempts),
        )
        if allow_tiers:
            try:
                task_kwargs["allow_tiers"] = tuple(
                    ExecutionTier(t.strip()) for t in allow_tiers.split(",") if t.strip()
                )
            except ValueError as e:
                return f"[run_hybrid_exec_task] allow_tiers 取值非法（可选 script/llm/agent）：{e}"

        executor = default_executor(project_root, mini_agent_config=cfg)
        result = executor.run(TaskSpec(**task_kwargs))

        output_text = (
            json.dumps(result.output, ensure_ascii=False)
            if isinstance(result.output, (dict, list))
            else str(result.output)
        )
        header = (
            f"ok={result.ok} tier_used={result.tier_used.value} "
            f"script_version={result.script_version} duration={result.duration:.2f}s"
        )
        if not result.ok:
            attempts_text = "; ".join(
                f"{a.stage}({a.tier.value}):{'OK' if a.ok else 'FAIL ' + a.detail}"
                for a in result.attempts
            )
            return f"{header}\n执行失败，决策轨迹：{attempts_text}"
        return f"{header}\noutput={output_text}"

    @tool(name="list_hybrid_exec_tasks", group="hybrid_exec", requires_approval=False,
          description="列举 .agent/hybrid_exec/scripts/ 下已归档的所有 hybrid_exec 任务及其"
                      "当前 active 脚本版本/成功率/连续失败次数，用于判断该复用哪个 task_id。")
    def list_hybrid_exec_tasks() -> str:
        scripts_dir = project_root / ".agent" / "hybrid_exec" / "scripts"
        if not scripts_dir.exists():
            return "尚无任何已归档的 hybrid_exec 任务"
        lines = []
        for task_dir in sorted(p for p in scripts_dir.iterdir() if p.is_dir()):
            meta_path = task_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            active_version = meta.get("active_version")
            info = meta.get("versions", {}).get(str(active_version), {})
            lines.append(
                f"{task_dir.name}: active=v{active_version} "
                f"成功={info.get('success_count', 0)} 失败={info.get('fail_count', 0)} "
                f"连续失败={info.get('consecutive_fail', 0)} 状态={info.get('status', '?')}"
            )
        return "\n".join(lines) if lines else "尚无任何已归档的 hybrid_exec 任务"

    @tool(name="show_hybrid_exec_task", group="hybrid_exec", requires_approval=False,
          description="查看某个 hybrid_exec task_id 的仓库元信息（各版本统计）与当前 active "
                      "脚本源码，判断脚本内容是否仍符合预期、是否该 force_reexplore。")
    def show_hybrid_exec_task(task_id: str) -> str:
        task_dir = project_root / ".agent" / "hybrid_exec" / "scripts" / task_id
        meta_path = task_dir / "meta.json"
        if not meta_path.exists():
            return f"未找到 task_id={task_id!r}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        parts = [json.dumps(meta, ensure_ascii=False, indent=2)]
        active_version = meta.get("active_version")
        if active_version is not None:
            script_path = task_dir / f"v{active_version}.py"
            if script_path.exists():
                parts.append(f"--- v{active_version}.py ---\n{script_path.read_text(encoding='utf-8')}")
        return "\n".join(parts)
