"""
hybrid_exec/runner.py — ScriptRunner：复用 workflow/py_step_runner.py 的
子进程隔离协议来执行 hybrid_exec 管理的脚本。

对应 next_doc/hybrid_exec_design_plan.md §3.4。

不重新发明脚本沙箱：直接拼出与
`workflow/executors.py::PythonStepExecutor` 一致结构的 request.json，拉起
`python -m mini_agent.workflow.py_step_runner <request_json_path>` 子进程，
解析同样的"stdout 最后一行单行 JSON 结果包"协议。

与 PythonStepExecutor 的差异（刻意简化，MVP 阶段够用）：
  - 不做 workflow 那边的 Ctrl+C 友好轮询 kill 逻辑，直接用
    `subprocess.run(..., timeout=...)`——hybrid_exec 的调用方（daemon /
    CLI / 未来的 hybrid_step）目前没有"用户按 Ctrl+C 需要立即打断"的强
    需求，简单实现更容易测试和维护；如果后续需要，可以对齐
    PythonStepExecutor 的实现补上。
  - inputs 直接透传 TaskSpec.input_data，不做 workflow 里"按 depends_on
    过滤"那一层（hybrid_exec 本身不感知 workflow 的依赖图）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .spec import ScriptOutcome, TaskSpec


@dataclass
class RunnerAppConfig:
    """构造 py_step_runner 子进程请求所需的应用配置，字段对齐
    workflow/executors.py::PythonStepExecutor 里 app_cfg 的构造。"""

    project_root: str
    sandbox: bool = True
    model: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_base_url: Optional[str] = None
    debug_llm: bool = False
    debug_llm_console: bool = False
    skills_dir: Optional[str] = None
    api_key: Optional[str] = None

    @classmethod
    def from_mini_agent_config(cls, cfg) -> "RunnerAppConfig":
        """从 mini_agent.config.load_config() 返回的 Config 对象构造，
        方便调用方直接传项目已有的 cfg，不必手工拼字段。"""
        return cls(
            project_root=str(cfg.project_root),
            sandbox=bool(getattr(cfg, "sandbox", True)),
            model=getattr(cfg, "model", None),
            llm_provider=getattr(cfg, "llm_provider", None),
            llm_base_url=getattr(cfg, "llm_base_url", None),
            debug_llm=bool(getattr(cfg, "debug_llm", False)),
            debug_llm_console=bool(getattr(cfg, "debug_llm_console", False)),
            skills_dir=str(getattr(cfg, "skills_dir", "") or "") or None,
            api_key=getattr(cfg, "api_key", None),
        )

    def to_dict(self) -> dict:
        return {
            "project_root": self.project_root,
            "sandbox": self.sandbox,
            "model": self.model,
            "llm_provider": self.llm_provider,
            "llm_base_url": self.llm_base_url,
            "debug_llm": self.debug_llm,
            "debug_llm_console": self.debug_llm_console,
            "skills_dir": self.skills_dir,
        }


class ScriptRunner:
    """把 hybrid_exec 的脚本拉起到子进程里执行，复用 py_step_runner 协议。"""

    def __init__(self, app_cfg: RunnerAppConfig) -> None:
        self.app_cfg = app_cfg

    def run(
        self,
        script_path: Path,
        task: TaskSpec,
        *,
        session_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        timeout: Optional[float] = None,
    ) -> ScriptOutcome:
        timeout = timeout if timeout is not None else task.script_timeout_seconds
        project_root = Path(self.app_cfg.project_root)
        session_dir = session_dir or (
            project_root / ".agent" / "hybrid_exec" / "runs" / task.task_id
        )
        output_dir = output_dir or session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        # hybrid_exec 脚本没有"上游 step"概念，把 TaskSpec.input_data 整体
        # 塞进一条虚拟 input（step_id 固定为 "task_input"），脚本内通过
        # ctx.input_json("task_input") 取回；同时也放进 params 里，方便
        # 脚本作者直接用 ctx.params 读取，两条路径都能拿到同一份数据，
        # 减少 Explorer/Repairer 生成脚本时的心智负担。
        inputs_payload = {
            "task_input": {
                "status": "done",
                "output": json.dumps(task.input_data, ensure_ascii=False),
                "score": None,
                "result_file": None,
            }
        }

        request = {
            "step_id": task.task_id,
            "session_dir": str(session_dir),
            "output_dir": str(output_dir),
            "inputs": inputs_payload,
            "params": dict(task.input_data),
            "script_path": str(script_path),
            "workflow_dir": None,
            "app_cfg": self.app_cfg.to_dict(),
        }

        with tempfile.TemporaryDirectory(prefix="mini_agent_hybrid_exec_") as tmp_dir:
            req_path = Path(tmp_dir) / "request.json"
            req_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

            child_env = dict(os.environ)
            if self.app_cfg.api_key:
                child_env["MINI_AGENT_STEP_API_KEY"] = self.app_cfg.api_key
            child_env["PYTHONIOENCODING"] = "utf-8"

            start_ts = time.monotonic()
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "mini_agent.workflow.py_step_runner", str(req_path)],
                    cwd=str(project_root),
                    env=child_env,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as e:
                duration = time.monotonic() - start_ts
                return ScriptOutcome(
                    ok=False,
                    error=f"脚本执行超时（>{timeout}s）",
                    error_type="TimeoutExpired",
                    duration=duration,
                    stdout_tail=(e.stdout or "")[-2000:] if e.stdout else "",
                    stderr_tail=(e.stderr or "")[-2000:] if e.stderr else "",
                )
            duration = time.monotonic() - start_ts

            return self._parse_result(proc.stdout or "", proc.stderr or "", proc.returncode, duration)

    @staticmethod
    def _parse_result(stdout: str, stderr: str, returncode: int, duration: float) -> ScriptOutcome:
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        if not lines:
            return ScriptOutcome(
                ok=False,
                error="子进程无输出（可能崩溃或被信号杀死）",
                error_type="EmptyOutput",
                duration=duration,
                stderr_tail=stderr[-2000:],
            )
        last_line = lines[-1]
        try:
            packet = json.loads(last_line)
        except json.JSONDecodeError:
            return ScriptOutcome(
                ok=False,
                error=f"子进程结果包不是合法 JSON：{last_line[:500]!r}",
                error_type="ResultPacketParseError",
                duration=duration,
                stdout_tail=stdout[-2000:],
                stderr_tail=stderr[-2000:],
            )

        if packet.get("ok"):
            output_text = packet.get("output", "")
            output: object = output_text
            if packet.get("output_is_json"):
                try:
                    output = json.loads(output_text)
                except json.JSONDecodeError:
                    pass  # 脚本自己声明是 JSON 但解析失败，就原样保留文本，交给上层校验器判定
            return ScriptOutcome(ok=True, output=output, duration=duration)

        return ScriptOutcome(
            ok=False,
            error=packet.get("error", "未知错误"),
            error_type=packet.get("error_type"),
            traceback=packet.get("traceback"),
            duration=duration,
            stdout_tail=stdout[-2000:],
            stderr_tail=stderr[-2000:],
        )
