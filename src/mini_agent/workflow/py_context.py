"""
workflow/py_context.py — python_step 脚本的运行时上下文
（next_doc/workflow_python_step_and_zhihu_publish_plan.md §B1/§B2）

背景：python_step 让 workflow 的一个步骤由用户编写的 .py 脚本实现（而不是
一次 Agent 对话），脚本里如果需要用到大模型能力（做摘要/分类/结构化抽取
这类"需要理解力"的子任务），不应该让脚本作者自己去 import provider SDK、
自己写重试/fallback——那样每个脚本各写一套，稳定性和现有 Agent 主循环
完全脱节。PyStepLLM 是对 llm/service.py::LLMHelper 的窄接口封装，脚本只
通过 ctx.llm.ask()/ask_json() 调用，底层复用同一套 LLMClientPool（多
provider、多 key 轮转、fallback）和 RetryPolicy，不重新造轮子。

PyStepContext 在子进程侧（py_step_runner.py）构造并传给脚本的 run(ctx)
入口函数；run_agent_turn 用于"需要 agent 判断力而非单次问答"的场景（见
runner.py::WorkflowRunner._spawn_minimal_agent，与 SkillAgentStepExecutor
共用同一段"临时起一个最小 Agent"逻辑，避免两处重复实现）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import StepResult
    from .llm.service import LLMHelper  # noqa: F401 (仅类型标注用)


class PyStepLLM:
    """python_step 脚本调用 LLM 的入口，转发到 LLMHelper，不新建 provider/
    重试/fallback 逻辑。

    惰性构造：子进程侧 py_step_runner.py 用 helper_factory 传一个无参
    callable——很多 python_step 脚本根本不调用 ctx.llm（比如纯粹的数据
    搬运/落盘），这种情况下不应该因为 provider/api_key 配置无效就让整个
    step 直接失败，只有脚本真的调用了 ask()/ask_json() 才需要一条能用的
    LLMHelper。

    注意：不用"传入对象是否 callable"来判断走哪条路径——LLMHelper 实例
    本身、测试里常用的 MagicMock，都是 callable 的，用 callable() 做隐式
    判断会把"已构造好的 helper"误判成"工厂函数"。改用显式的两个参数。
    """

    def __init__(self, helper=None, *, helper_factory=None) -> None:
        if helper is None and helper_factory is None:
            raise ValueError("PyStepLLM 需要传 helper 或 helper_factory 其中之一")
        self._helper_instance = helper
        self._helper_factory = helper_factory

    @property
    def _helper(self):
        if self._helper_instance is None:
            self._helper_instance = self._helper_factory()
        return self._helper_instance

    def ask(
        self,
        prompt: str,
        *,
        system: str = "",
        max_retries: int = 3,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
        override_temperature: Optional[float] = None,
    ) -> str:
        """单轮问答，返回纯文本。等价于 Agent 主循环之外场景统一使用的
        LLMHelper.ask()。"""
        return self._helper.ask(
            prompt,
            system=system,
            max_retries=max_retries,
            override_model=override_model,
            override_provider=override_provider,
            override_temperature=override_temperature,
        )

    def ask_json(
        self,
        prompt: str,
        *,
        system: str = "",
        schema_hint: str = "",
        max_retries: int = 3,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
    ) -> dict:
        """
        约定模型返回 JSON 的场景（[计划 §C] 批量过滤等结构化输出）。

        - schema_hint 会拼进 system 提示里，明确告知模型期望的 JSON 形状。
        - 用 json_repair 做宽松解析（模型偶尔会输出 ```json 包裹/多余文本），
          解析失败时把上次的解析错误追加进 prompt 里重试，最多 max_retries
          次；仍失败则抛出 ValueError，交由 python_step 脚本自己决定是否
          降级（比如拆小批重试，见 §C 的漏判保护）。
        """
        import json_repair

        sys_prompt = system or "你是一个只输出 JSON、不输出任何其它文字的助手。"
        if schema_hint:
            sys_prompt += f"\n严格按以下 JSON 形状输出（不要加 markdown 代码块标记）：\n{schema_hint}"

        last_err: Optional[Exception] = None
        cur_prompt = prompt
        for attempt in range(max(1, max_retries)):
            text = self._helper.ask(
                cur_prompt,
                system=sys_prompt,
                max_retries=1,  # 网络层重试交给 LLMHelper 内部；这里是"解析失败重试"
                override_model=override_model,
                override_provider=override_provider,
            )
            try:
                cleaned = text.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.strip("`")
                    if cleaned.lower().startswith("json"):
                        cleaned = cleaned[4:]
                parsed = json_repair.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
                last_err = ValueError(f"期望返回 JSON object，实际是 {type(parsed).__name__}")
            except Exception as e:  # noqa: BLE001 — 解析失败按重试处理，不当场炸
                last_err = e
            cur_prompt = (
                f"{prompt}\n\n[上一次输出解析失败：{last_err}，请只输出合法 JSON，不要有多余文字]"
            )
        raise ValueError(f"ask_json 在 {max_retries} 次重试后仍无法解析出合法 JSON：{last_err}")


@dataclass
class PyStepContext:
    """python_step 脚本 run(ctx) 的入参。"""

    step_id: str
    session_dir: Path
    output_dir: Path
    inputs: dict  # dict[str, StepResult]，上游 step 结果，key 为 step_id
    params: dict  # workflow.yaml 里 step 级 params，脚本自定义参数
    llm: PyStepLLM
    run_agent_turn: Callable[..., str]
    workflow_dir: Optional[Path] = None  # 目录化 workflow 的所在目录，用于定位 prompts/ 等资源
    extra: dict = field(default_factory=dict)

    def input_output(self, step_id: str, default: str = "") -> str:
        """便捷方法：取某个上游 step 的纯文本输出。
        若该 step 声明了 result_file 且已通过校验落盘（见
        runner.py::WorkflowRunner._validate_result_file），优先读文件内容；
        没有 result_file 时退回旧行为——读 step 的对话原文输出。"""
        r = self.inputs.get(step_id)
        if r is None:
            return default
        result_file = getattr(r, "result_file", None)
        if result_file:
            try:
                return Path(result_file).read_text(encoding="utf-8")
            except OSError:
                pass  # 文件读取失败时退回 output 文本，不让上游更可靠反而拖累下游
        return getattr(r, "output", default)

    def input_json(self, step_id: str, default: Any = None) -> Any:
        """便捷方法：取某个上游 step 的输出并按 JSON 解析。优先读
        result_file（skill_agent 主动写的结果文件，已经过校验，可信度更
        高）；没有的话退回解析 output 文本——这条旧路径本身不保证是纯净
        JSON（skill_agent 的对话输出常见"前面一段解释文字 + JSON"这种
        格式），所以这里用 json_repair 兜底，能容忍 markdown 代码块围栏、
        前后多余文字等常见瑕疵，而不是直接 json.loads 遇到非 JSON 字符就炸。"""
        text = self.input_output(step_id, "")
        if not text:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        import json_repair
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        return json_repair.loads(cleaned)

    def load_prompt_file(self, relative_path: str) -> str:
        """读取 workflow 目录下的 prompt 模板文件（相对 workflow_dir），
        脚本内用于加载 prompts/xxx.md 并 .format() 填充占位符。"""
        if self.workflow_dir is None:
            raise ValueError("workflow_dir 未设置，无法解析相对路径的 prompt 文件")
        fpath = (self.workflow_dir / relative_path).resolve()
        fpath.relative_to(self.workflow_dir.resolve())  # 路径穿越保护
        return fpath.read_text(encoding="utf-8")

    def write_output(self, filename: str, data: Any) -> Path:
        """便捷方法：把结构化数据写到 output_dir/filename（JSON 缩进格式化），
        返回写入的绝对路径。脚本可以在 return 之外，用这个方法主动落盘
        中间产物（比如分批过滤时每批的原始判定结果，便于排查）。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        fpath = self.output_dir / filename
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
        fpath.write_text(text, encoding="utf-8")
        return fpath
