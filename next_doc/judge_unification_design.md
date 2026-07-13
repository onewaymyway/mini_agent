# 判官机制（Judge/RoleAgent）统一化改造计划

> 目标：把 evaluator / coach / goal_judge / turn_judge 这四类"内部判官 Agent"
> 从四套并行手写实现，收敛成一套统一协议 + 一个工厂函数 + 一条注册/触发路径，
> 使得未来新增判官类型（安全审查、成本核查、ensemble judge 等）只需要写
> prompt + profile 声明，不需要再碰 `agent/core.py`、`goal_mode/runner.py`
> 或 dispatcher 的核心代码。
>
> 本文档遵循项目一贯的"设计先行、分阶段实施、每阶段独立可回归"的原则。
> 六个阶段设计为严格的依赖顺序：前一阶段落地并跑通全量测试后，
> 再进入下一阶段；每阶段都保持行为不变（behavior-preserving refactor），
> 只有阶段五、六才涉及行为/接口变化，且都单独提交、单独评审。

---

## 0. 现状盘点（改造前基线）

| 判官类型 | 实现位置 | 接线位置 | 状态输出方式 | 反馈注入方式 | 失败处理 |
|---|---|---|---|---|---|
| Evaluator | `role_agents/evaluator.py` | `RoleAgentDispatcher` / `role_judge.py::_run_role_agents_output` | 正则抠 `SCORE:` | `RoleFeedback` + `build_inject_message` | `[EvaluatorAgent 运行失败: ...]` 字符串 |
| Coach | `role_agents/coach.py` | `RoleAgentDispatcher.trigger_tool_use` | 无状态，纯建议文本 | `RoleFeedback` + `build_inject_message` | 同上字符串约定 |
| GoalJudge | `role_agents/goal_judge.py` | `goal_mode/runner.py` 主循环硬编码调用 | 正则抠 `GOAL_STATUS:` | **未走** `RoleFeedback`，GoalRunner 自己拼 | 同上字符串约定，**未接** auto_quarantine |
| TurnJudge | `role_agents/turn_judge.py` | `agent/role_judge.py::_maybe_run_turn_judge` 硬编码调用 | 正则抠 `TURN_STATUS:` | `RoleFeedback` + `build_inject_message` | 独立 try/except，**未走** dispatcher 的失败识别 |

共性重复代码（在 4 个文件里各出现一次）：
```python
judge_cfg = load_config(..., auto_approve=True, model=..., provider=...)
judge_cfg.api_key = base_cfg.api_key
judge_cfg.max_turns = N
judge_cfg.stream = False
judge_cfg.system_extra = ...
judge_cfg.turn_judge = TurnJudgeConfig(enabled=False)  # 防递归
guard = PermissionGuard(auto_approve=True, sandbox=..., project_root=...)
registry = get_default_registry().filtered(names=[], groups=[])
judge_agent = Agent(cfg=judge_cfg, guard=guard, registry=registry, is_subagent=True)
try:
    return judge_agent.run_turn(prompt)
except Exception as e:
    return f"[XxxAgent 运行失败: {e}]"
```

另有两处独立实现的"卡住检测"（相似度阈值 + 恢复额度计数），逻辑一致但代码不共享：
- `goal_mode/runner.py`（GoalState 里的 `consecutive_same_feedback` / `stuck_recoveries_used`）
- `agent/role_judge.py::_maybe_run_turn_judge`（`_turn_judge_consecutive_same` / `_turn_judge_stuck_recoveries_used`）

---

## 阶段一：抽取 `StuckDetector`（纯内部重构，零行为变化）

**目的**：消除 goal_mode 和 turn_judge 里重复的"连续 N 轮输出高度相似 → 判定卡住"逻辑。

**新增文件**：`src/mini_agent/role_agents/stuck_detector.py`

```python
from __future__ import annotations
import difflib
from dataclasses import dataclass
from enum import Enum


class StuckSignal(str, Enum):
    NONE = "none"           # 无问题，正常继续
    RECOVER = "recover"     # 判定卡住，且还有恢复额度 → 调用方应 compact + 换角度提示
    GIVE_UP = "give_up"     # 判定卡住，且恢复额度已耗尽 → 调用方应终止/交还用户


@dataclass
class StuckDetector:
    """连续输出相似度卡死检测器。

    用法（每次拿到主 Agent 一轮输出后调用一次）：
        signal = detector.observe(assistant_output)
        if signal is StuckSignal.RECOVER:
            ...  # compact + 换角度提示，detector 内部已经计数
        elif signal is StuckSignal.GIVE_UP:
            ...  # 终止 / 交还用户，调用方负责 reset()
    """
    similarity_threshold: float = 0.92
    consecutive_limit: int = 3          # 连续多少轮相似判定为"卡住"
    max_recoveries: int = 2             # 恢复额度上限

    _prior_output: str | None = None
    _consecutive_same: int = 0
    _recoveries_used: int = 0

    def observe(self, output: str) -> StuckSignal:
        if self.consecutive_limit <= 0:
            self._prior_output = output
            return StuckSignal.NONE

        if self._prior_output is not None:
            ratio = difflib.SequenceMatcher(None, self._prior_output, output).ratio()
            if ratio >= self.similarity_threshold:
                self._consecutive_same += 1
            else:
                # 出现真实进展，重置卡住计数和恢复额度
                self._consecutive_same = 0
                self._recoveries_used = 0
        self._prior_output = output

        if self._consecutive_same >= (self.consecutive_limit - 1):
            if self._recoveries_used >= self.max_recoveries:
                return StuckSignal.GIVE_UP
            self._recoveries_used += 1
            self._consecutive_same = 0
            return StuckSignal.RECOVER

        return StuckSignal.NONE

    def reset(self) -> None:
        self._prior_output = None
        self._consecutive_same = 0
        self._recoveries_used = 0

    @property
    def recoveries_used(self) -> int:
        return self._recoveries_used
```

**改造点**：
1. `agent/role_judge.py::_maybe_run_turn_judge` 中，删除手写的
   `_turn_judge_consecutive_same` / `_turn_judge_stuck_recoveries_used` /
   `_turn_judge_prior_output` 三个实例字段和对应的 difflib 比对代码块，
   改为持有一个 `self._turn_judge_stuck_detector = StuckDetector(...)`，
   在方法开头调用 `signal = self._turn_judge_stuck_detector.observe(assistant_output)`。
2. `goal_mode/runner.py` 中同理，`GoalRunner.__init__` 里持有一个
   `StuckDetector` 实例，`_check_stuck`/`_try_stuck_recovery` 相关方法
   改为调用 `self._stuck_detector.observe(feedback_text)`。
   注意：GoalRunner 目前是拿"judge 反馈文本"做相似度比较，
   TurnJudge 是拿"主 Agent 输出"做比较——`StuckDetector` 对输入内容不做假设，
   两边可以各自传入合适的字符串，不需要统一语义。
3. `GoalState`（`goal_mode/state.py`）落盘时，把
   `consecutive_same_feedback` / `stuck_recoveries_used` 两个字段
   替换为直接落盘 `StuckDetector` 的三个内部状态（或给 `StuckDetector`
   加 `to_dict()`/`from_dict()` 方便序列化），保持断点续跑能力不变。

**验证**：
- 跑 `tests/test_resource_arbiter_behavior_gating.py` 等相关既有测试全绿
- 新增 `tests/test_stuck_detector.py`，覆盖：连续相似触发 RECOVER、
  恢复额度耗尽触发 GIVE_UP、出现不同输出后计数重置
- 跑一次 goal_mode 的手动回归用例（`test_cases/goal_test.txt`），
  确认"卡住恢复"提示文案和之前完全一致（本阶段不改变对外文案）

**风险**：低。纯提取，不改变任何外部可观察行为。

---

## 阶段二：统一判官反馈注入（GoalRunner 接入 `RoleFeedback`）

**目的**：消除 GoalRunner 自己拼反馈文本、不走 `RoleFeedback`/`build_inject_message`
的特例，让四类判官的反馈注入格式统一。

**改造点**：
1. `role_agents/goal_judge.py::run_goal_judge` 的调用方（`goal_mode/runner.py`）
   改为：拿到 `raw` 判定文本后，构造
   ```python
   from mini_agent.role_agents.feedback import RoleFeedback, build_inject_message, extract_goal_status
   status = extract_goal_status(raw) or "CONTINUE"  # 解析失败保守 CONTINUE，语义不变
   feedback = RoleFeedback(
       role_name="goal_judge", role_type="goal_judge",
       raw_output=raw, inject_as="user", goal_status=status,
   )
   ```
   `GoalRunner._build_prompt` 里原本手工拼接判定文本的地方，
   改为调用 `build_inject_message(feedback)["content"]` 生成同样带
   "🎯 目标核查" 标签的文本块。
2. 确认 `feedback.py::format_feedback` 里 `goal_status` 的展示文案
   与 GoalRunner 之前手写的提示文案语义一致（如有出入，以现有 GoalRunner
   文案为准调整 `format_feedback`，不要反过来改 GoalRunner 迁就格式化函数）。

**验证**：
- 跑 goal_mode 相关全部既有测试
- 人工跑一次 `test_cases/goal_test.txt`，对比注入到历史里的反馈文本
  格式变化是否符合预期（只是加了统一 header，语义不变）

**风险**：低中。涉及对外可见的注入文本格式变化，需要重点看现有依赖
"反馈文本具体格式"做字符串匹配的测试用例（如有，需要同步更新断言）。

---

## 阶段三：`spawn_judge_agent` 工厂函数（消除四处样板重复）

**目的**：把"构造一个受限内部 Agent"这件事收敛成一个函数，
后续所有判官类型的实现都变成"调工厂 + 传自己的 prompt/工具白名单"。

**新增文件**：`src/mini_agent/role_agents/judge_factory.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.agent import Agent
    from mini_agent.orchestrator.agent_profiles import AgentProfile


@dataclass
class JudgeResult:
    """判官内部 Agent 一次运行的结果，统一给调用方消费。"""
    ok: bool
    raw_output: str = ""
    error: Optional[str] = None


def spawn_judge_agent(
    *,
    profile: Optional["AgentProfile"],
    base_cfg: "AppConfig",
    role_cfg_block=None,          # 如 cfg.goal_mode / cfg.turn_judge，提供 judge_model/judge_provider
    display_name: str,            # 如 "🎯 GoalJudge"，仅用于打印前缀
    system_prompt: str,
    max_turns: int = 2,
    tools_enabled: bool = False,
    allowed_tools: Optional[list[str]] = None,
    allowed_tool_groups: Optional[list[str]] = None,
    force_sandbox_when_tools: bool = True,
) -> "Agent":
    """按统一规则构造一个受限的内部判官 Agent 实例。

    收敛了此前 evaluator.py / coach.py / turn_judge.py / goal_judge.py /
    dispatcher._run_custom_role 里重复的：
      - load_config 三层 model/provider 优先级解析（复用 model_resolution.py）
      - 显式禁用 judge_cfg.turn_judge，防止内部 Agent 对自己递归触发 TurnJudge
      - 按 tools_enabled 开关决定空注册表 or 过滤注册表
      - is_subagent=True 标记（第二道防递归保险）
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry
    from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
    from mini_agent.role_agents.model_resolution import resolve_role_model

    judge_model, judge_provider = resolve_role_model(profile, role_cfg_block, base_cfg)

    yes_mode = bool(getattr(role_cfg_block, "judge_yes_mode", False)) if tools_enabled else False
    judge_sandbox = (
        (not yes_mode) if (tools_enabled and force_sandbox_when_tools) else base_cfg.sandbox
    )

    judge_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=base_cfg.verbose,
        sandbox=judge_sandbox,
        auto_approve=True,
        model=judge_model,
        llm_provider=judge_provider,
        llm_base_url=base_cfg.llm_base_url,
        debug_llm=getattr(base_cfg, "debug_llm", False),
        debug_llm_console=getattr(base_cfg, "debug_llm_console", False),
    )
    judge_cfg.api_key = base_cfg.api_key
    judge_cfg.max_turns = max_turns
    judge_cfg.stream = False
    judge_cfg.system_extra = (
        profile.system_prompt if (profile and profile.system_prompt.strip()) else system_prompt
    )
    judge_cfg.agent_name = display_name
    judge_cfg.turn_judge = _TurnJudgeConfig(enabled=False)  # 防递归，唯一权威开关点

    guard = PermissionGuard(
        auto_approve=True, sandbox=judge_sandbox, project_root=base_cfg.project_root,
    )

    if tools_enabled:
        allowed_tools = list(allowed_tools or [])
        allowed_groups = list(allowed_tool_groups or [])
        if profile and profile.tools:
            allowed_tools = [t for t in profile.tools if t in allowed_tools] or profile.tools
        if profile and profile.tool_groups:
            allowed_groups = [g for g in profile.tool_groups if g in allowed_groups] or profile.tool_groups
        registry = get_default_registry().filtered(names=allowed_tools, groups=allowed_groups)
    else:
        registry = get_default_registry().filtered(names=[], groups=[])

    return Agent(cfg=judge_cfg, guard=guard, registry=registry, is_subagent=True)


def run_judge_turn(agent: "Agent", prompt: str, *, failure_role_label: str) -> JudgeResult:
    """统一的"跑一轮判官 Agent + 异常兜底"逻辑，替代四处重复的 try/except。"""
    try:
        raw = agent.run_turn(prompt)
        return JudgeResult(ok=True, raw_output=raw)
    except Exception as e:
        return JudgeResult(ok=False, error=str(e), raw_output=f"[{failure_role_label} 运行失败: {e}]")
```

**改造点**（逐文件替换，行为保持完全一致）：
- `evaluator.py::run_evaluator` → 内部改为调用 `spawn_judge_agent` + `run_judge_turn`
- `coach.py::run_coach` → 同上
- `turn_judge.py::run_turn_judge` → 同上（`tools_enabled=False`）
- `goal_judge.py::run_goal_judge` → 同上（`tools_enabled=` 由 `judge_tools_enabled` 决定，
  白名单参数原样传入）
- `dispatcher.py::_run_custom_role` → 同上

每个改造点都是"函数体替换为调用工厂"，**函数签名和返回值完全不变**，
上层调用方（`role_judge.py`、`goal_mode/runner.py`、`dispatcher.py`）不需要任何修改。

**验证**：
- 全量测试跑一遍（这一阶段理论上零行为变化，任何一个既有测试失败
  都说明工厂函数和原实现有细微差异，需要逐项核对）
- 特别关注：`judge_cfg.turn_judge = TurnJudgeConfig(enabled=False)` 这一行
  在工厂函数里只写了一次，需要确认原来四处“防递归”写法完全一致
  （已核对，四处实现确实是同一行代码，可以安全合并）

**风险**：中。改动面覆盖 5 个文件，但每处都是行为等价替换，
配合全量测试可以较快发现偏差。**建议单独提交，不与阶段一/二混在一起**。

---

## 阶段四：失败识别与 auto_quarantine 上报统一

**目的**：现在只有走 `RoleAgentDispatcher` 路径的 evaluator/coach/custom
会触发 `_report_role_agent_failure`（正则识别 `[XxxAgent 运行失败: ...]`
上报 auto_quarantine），GoalJudge 和 TurnJudge 完全没接这条能力。

**改造点**：
1. 把 `dispatcher.py` 里的 `_ROLE_FAILURE_RE` + `_report_role_agent_failure`
   移到 `judge_factory.py`（阶段三新建的文件），改名为
   `report_judge_outcome(result: JudgeResult, profile_name: str) -> None`，
   直接基于 `JudgeResult.ok` 布尔值判断，**不再需要正则匹配字符串**
   （这是阶段三引入 `JudgeResult` 之后的直接收益：失败识别不再依赖
   约定俗成的字符串前缀，而是类型化的 `ok` 字段）。
2. `run_judge_turn` 内部（或其调用方统一位置）在拿到 `JudgeResult` 后
   调用一次 `report_judge_outcome`，这样 evaluator/coach/goal_judge/
   turn_judge/custom 五类判官全部自动获得 auto_quarantine 保护，
   不需要各自记得接入。
3. `dispatcher.py` 里原本手写的 `_ROLE_FAILURE_RE.match(raw or "")` 判断
   （用于决定要不要 `record_success`）同步替换为 `JudgeResult.ok` 判断。

**验证**：
- 新增测试：模拟 GoalJudge 连续失败 N 次，断言 auto_quarantine 会
  屏蔽该 profile（之前这个场景对 GoalJudge 是没有测试覆盖的空白点）
- 跑 `tests/test_*.py` 中涉及 auto_quarantine 的既有用例

**风险**：低中。属于"补齐能力"而非"改变已有行为"，主要风险是
GoalJudge/TurnJudge 之前从未被 quarantine 过，现在如果 LLM 不稳定
可能第一次触发屏蔽——建议这一阶段上线后观察一段时间的日志，
确认阈值（`auto_quarantine` 的连续失败次数配置）对判官类 Agent
是否合适（判官调用频率通常远高于普通 subagent，可能需要单独配置
更宽松的阈值，而不是复用现有默认值）。

---

## 阶段五（较大变更，需单独评审）：结构化判定输出（强制 JSON + json_repair 解析）

**目的**：替代当前"自由文本 + 正则抠 `XXX_STATUS:` 行"的脆弱方案。
**不采用 tool_use 方案**（会引入额外的工具调用往返、且部分 provider/model
对"只允许调用一个特定工具"的约束支持不稳定），改为：
prompt 里强制模型只输出一段 JSON，用 `json_repair` 兜底解析
（能自动修复模型输出里常见的尾随逗号、多余 Markdown 代码块围栏、
单引号、截断等问题，比 `json.loads` 严格解析更适合"模型偶尔不听话"
的场景，也比正则抠单个字段更结构化、更容易扩展字段）。

**依赖新增**：`json_repair`（纯 Python 库，无 C 扩展，`pip install json_repair`）。
加入 `pyproject.toml` / `requirements.txt` 主依赖列表。

**统一 Verdict 数据结构**（新增文件 `role_agents/verdict.py`）：
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class JudgeVerdict:
    """所有判官类型统一的结构化判定结果。

    status 的合法取值由每种判官类型自己的枚举集合决定（如 GoalJudge 是
    DONE/CONTINUE/NEED_COMPACT，TurnJudge 是 NEED_USER/AUTO_CONTINUE/
    NEED_COMPACT），本类不做枚举层面的强约束，交给调用方按自己的
    valid_statuses 集合校验，避免不同判官类型之间产生耦合。
    """
    status: str
    feedback: str = ""
    raw_json: Optional[dict[str, Any]] = None   # 保留解析出的完整 dict，便于以后扩展字段
    parse_ok: bool = True                        # False 表示走了兜底路径（解析失败/字段缺失）


def parse_judge_verdict(
    raw_text: str,
    *,
    valid_statuses: list[str],
    fallback_status: str,
) -> JudgeVerdict:
    """从模型输出文本中解析结构化判定结果。

    解析策略（层层兜底，任何一层失败都不抛异常，只降级到下一层）：
      1. 用 json_repair 尝试修复并解析出一个 dict
      2. 校验 dict 里的 status 字段是否在 valid_statuses 内
      3. 两步都失败则返回 fallback_status（由调用方传入，语义与当前
         "解析失败保守回退"完全一致——GoalJudge 传 CONTINUE，
         TurnJudge 传 NEED_USER）
    """
    import json_repair

    try:
        parsed = json_repair.loads(raw_text)
    except Exception:
        parsed = None

    if not isinstance(parsed, dict):
        return JudgeVerdict(status=fallback_status, feedback=raw_text, parse_ok=False)

    status = str(parsed.get("status", "")).strip().upper()
    feedback = str(parsed.get("feedback", "")).strip()

    if status not in valid_statuses:
        # JSON 解析成功但 status 字段不合法/缺失，同样保守回退，
        # 但保留原始 feedback 字段（如果有的话）供人工排查
        return JudgeVerdict(
            status=fallback_status, feedback=feedback or raw_text,
            raw_json=parsed, parse_ok=False,
        )

    return JudgeVerdict(status=status, feedback=feedback, raw_json=parsed, parse_ok=True)
```

**Prompt 改造**：
在各判官的 system prompt（`prompts/system/goal_judge.md`、`turn_judge.md` 等）
末尾统一追加一段固定的"输出格式约束"片段（放进 `prompts/fragments/`
方便复用）：

```
你必须只输出一个 JSON 对象，不要输出任何 JSON 之外的文字、不要用 Markdown
代码块包裹，字段如下：
{
  "status": "<必须是以下取值之一：DONE | CONTINUE | NEED_COMPACT>",
  "feedback": "<给主助手看的具体、可执行的反馈文本>"
}
```
（每种判官类型的合法 status 取值列表不同，片段里用占位符渲染进去。）

**改造点**：
- 新增 `role_agents/verdict.py`（如上）
- `prompts/fragments/` 新增 `judge_json_output.md` 片段，
  `goal_judge.md` / `turn_judge.md` 等 system prompt 模板末尾引用它
- `judge_factory.py::run_judge_turn` 返回值里增加可选的
  `verdict: Optional[JudgeVerdict]` 字段（`JudgeResult` 增加该字段），
  由调用方决定是否要解析（`evaluator`/`coach` 这类不需要状态机的
  判官类型可以不解析，继续走纯文本路径，只有 GoalJudge/TurnJudge
  这类需要状态机驱动的判官才调用 `parse_judge_verdict`）
- `feedback.py` 的 `extract_goal_status` / `extract_turn_status` 两个
  正则函数标记为 deprecated，内部实现改为委托给 `parse_judge_verdict`
  （保留函数签名不变，方便还没来得及切换调用方的地方过渡期兼容），
  `extract_score`（evaluator 用）保持不变，暂不纳入本次改造范围
  （evaluator 的 0-10 分制场景切换成本收益不高，可以留到以后单独做）

**验证**：
- 新增 `tests/test_judge_verdict_parsing.py`，覆盖以下用例：
  - 标准合法 JSON
  - JSON 外包了一层 ```json 代码块围栏（json_repair 应能修复）
  - JSON 里有尾随逗号 / 单引号（json_repair 应能修复）
  - status 字段值不在合法取值内 → 走 fallback_status
  - 完全不是 JSON 的自由文本（模型完全没听指令）→ 走 fallback_status
  - JSON 合法但缺 feedback 字段 → feedback 为空字符串，status 仍正常解析
- 跑 goal_mode / turn_judge 相关全部既有测试，重点看：
  由于 prompt 变了（要求纯 JSON 输出），原本依赖"判定文本里包含某些
  可读中文描述"做展示的地方（比如 `format_feedback` 里直接把
  `raw_output` 原样展示给用户）需要调整——JSON 输出对用户来说不够
  友好，展示层应该展示 `verdict.feedback` 而不是原始 JSON 文本，
  这是本阶段一个容易漏掉的细节，需要同步检查 `role_judge.py` 里
  `R.console.print(format_feedback(feedback_obj))` 这类打印点

**风险**：中。相比 tool_use 方案，风险主要集中在两点：
1. 模型不听话完全不输出 JSON（比 tool_use 更容易发生，因为没有
   API 层面的强约束），但 `parse_judge_verdict` 的兜底路径已经
   覆盖了这个场景，且语义与当前"正则没匹配到就保守回退"完全一致，
   不算引入新风险，只是发生概率可能略高，建议上线后观察
   fallback 触发率（可以在 `parse_judge_verdict` 里加一个
   `parse_ok=False` 的计数上报，接入现有 `traces.jsonl` 观测体系）
2. 展示层需要从"展示原始判定文本"切换成"展示 verdict.feedback"，
   如果漏改会导致用户看到裸 JSON，体验倒退——这是需要重点检查的
   回归点，不是设计层面的风险

---

## 阶段六（较大变更，需单独评审）：判官接线统一为 profile 驱动

**目的**：现在 evaluator/coach 走 `RoleAgentDispatcher` 的 `trigger_on`
注册机制，goal_judge/turn_judge 却是硬编码在 `goal_mode/runner.py` 和
`agent/core.py` 里，两条互不相通的接线路径。统一之后，新增判官类型
只需要写 profile，不需要碰主循环代码。

**设计**：
- `trigger_on` 增加两个内建值：`goal_review`（GoalRunner 每轮循环边界触发）、
  `turn_end_review`（主循环每轮结束、交还用户前触发）
- `RoleAgentDispatcher` 增加对应的查询接口
  `get_goal_review_roles()` / `get_turn_end_review_roles()`
- `goal_mode/runner.py` 和 `agent/role_judge.py::_maybe_run_turn_judge`
  改为向 dispatcher 查询该 trigger 下注册了哪些判官，而不是直接
  `import run_goal_judge` / `run_turn_judge` 硬调用
- 内建的 goal_judge/turn_judge 本身也改造成"预注册的 profile"
  （而不是特殊路径），这样"关闭 goal_judge"和"关闭一个自定义 evaluator"
  是同一套开关机制（`role_agent.block` 白/黑名单）

**风险**：高。这一阶段改变了判官的触发路径和配置语义
（`cfg.goal_mode.enabled` / `cfg.turn_judge.enabled` 这两个专属开关
未来是否要继续保留、还是完全并入 `role_agent.allow/block`，
涉及配置文件向后兼容问题）。**必须单独出一份更细的迁移方案
（含配置迁移脚本、旧配置兼容期），不建议和前面几个阶段一起做。**

---

## 总体验证策略

- 阶段一～四：behavior-preserving，每阶段完成后跑全量测试
  （目标零回归，与你之前"~1,700-test suite 零回归"的验收标准一致），
  外加针对本阶段新增能力的专项测试
- 阶段五～六：接口级变化，需要先在 `test_cases/goal_test.txt` /
  `spawn_subagent_test.txt` 之外补充针对"模型不输出合法 JSON"、
  "新 trigger_on 值与旧硬编码路径共存期间"的专项回归用例，
  再考虑灰度（比如先只对 GoalJudge 生效，TurnJudge 保持旧路径一段时间
  观察稳定性，确认无误后再切换）

## 实施顺序总结

```
阶段一 StuckDetector 抽取        [低风险，可立即做]
   ↓
阶段二 GoalRunner 接入 RoleFeedback [低中风险]
   ↓
阶段三 spawn_judge_agent 工厂      [中风险，改动面大但机械化]
   ↓
阶段四 失败识别与 auto_quarantine 统一 [低中风险，纯能力补齐]
   ↓
—— 建议在此处停一停，观察阶段一～四上线后的实际运行情况 ——
   ↓
阶段五 结构化判定输出（需单独评审） [中高风险]
   ↓
阶段六 profile 驱动统一接线（需单独评审，含配置迁移方案）[高风险]
```

阶段一～四做完，就已经消灭了当前四处判官实现里 90% 的重复代码和
"GoalJudge 没接 auto_quarantine"这类不一致问题，且完全不改变任何
配置文件语义和外部行为，是性价比最高的一批改动。阶段五、六收益更大
（真正做到"新增判官类型零改动主循环代码"），但涉及接口和配置语义变化，
建议等前四阶段稳定运行一段时间、且有明确的新判官类型需求时再启动。
