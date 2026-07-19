# 主对话循环之外的 LLM 调用统一为 LLMHelper：改造计划

> 状态：**开放问题已确认，进入实现阶段**。第 6 节的 4 个问题确认结果：
> 1. `override_model`/`override_provider` 逃生舱**保留**。
> 2. `sub_agent.py` **不动 client 构造逻辑**，只补一层重试。
> 3. `override_temperature` **加入** `LLMHelper`，`make_llm_call` 迁移进 helper。
> 4. 重试次数**分场景区别对待**，默认 `max_retries=3`（原草案的 2 已废弃）。

---

## 1. 背景：现状盘点（精确到文件/函数级别）

项目里"发起一次 LLM 请求"目前有三条互不相通的路径：

| | 模式 A：主循环路径 | 模式 B：裸 `client.chat()` | 模式 C：自建独立 client |
|---|---|---|---|
| 典型文件 | `agent/turn_loop.py`、`compaction.py`、`lifecycle.py`、`reflection.py`、`profile.py`、`role_judge.py`、`snapshot.py`、`history_manager.py`、`history/*.py`、`tool_executor.py` | `perception/memory_factory.py::build_llm_call`、`agent/llm_control.py`（`/model` 探测性调用） | `ensemble/judge.py`、`ensemble/decision.py`、`ensemble/strategies.py`、`orchestrator/sub_agent.py`、`evolution/objective_executor.py`、`perception/goal_backlog.py` |
| provider/model 来源 | `self._client_pool.current_client`，跟随 `/model` 实时切换 | 调用方传入的 `client`（通常也是 `current_client`） | 各自 `LLMConfig.from_app_config(cfg)` 重新读一份**静态**配置，与当前实际在用的 provider/model 可能已经不一致 |
| 调用方式 | `self._llm.chat_with_retry(...)` | 裸 `client.chat(...)` | 裸 `client.chat(...)`（`objective_executor` / `goal_backlog` 甚至传了 `chat()` 不支持的 `max_tokens=` 参数） |
| 重试 / fallback | `RetryPolicy` + `LLMClientPool` 多 key 轮转 + 多配置 fallback | 无 | 无 |
| 每次调用开销 | 复用已有 client | 复用已有 client | 每次新建一个 `LLMClient` 实例 |

### 1.1 已确认的两处 Bug（与"要不要统一"无关，本身就是坏的）

- `src/mini_agent/evolution/objective_executor.py:551`
  ```python
  result = llm_client.chat(messages=msgs, max_tokens=500)
  ```
- `src/mini_agent/perception/goal_backlog.py:453`
  ```python
  result = llm_client.chat(messages=msgs, max_tokens=200)
  ```
  两处的 `llm_client` 实际是 `LLMClient` 实例（调用链：`api/server.py::_llm_decompose` 传入 `agent._llm`），而 `LLMClient.chat()` 的签名是 `chat(self, messages, system, tools)`，**不接受 `max_tokens` 关键字参数**。实际运行会抛 `TypeError`，被外层 `except Exception` 吞掉，`_llm_decompose` 静默返回 `[]`／`None`，"目标自动拆解"功能形同虚设，且没有任何日志之外的可见报错。

### 1.2 重复造轮子的三处（`ensemble/judge.py` / `ensemble/decision.py` / `ensemble/strategies.py`）

三处代码结构几乎逐字重复：

```python
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client

base_llm_cfg = LLMConfig.from_app_config(cfg)
llm_cfg = LLMConfig(
    provider=judge_provider or base_llm_cfg.provider,
    model=judge_model or base_llm_cfg.model,
    api_key=base_llm_cfg.api_key,
    base_url=base_llm_cfg.base_url,
    max_tokens=...,
    temperature=...,
    requires_api_key=base_llm_cfg.requires_api_key,
    use_system_tool_call=base_llm_cfg.use_system_tool_call,
    system_message_format=base_llm_cfg.system_message_format,
)
client = create_client(llm_cfg)
resp = client.chat(messages=..., system=..., tools=[])
```

问题：
- `cfg` 是启动时的静态 `AppConfig`，如果用户在会话中用 `/model` 切换过 provider/model，这三处**不会跟着变**，会用一个"过期"的模型去做 judge/decision/候选生成，这在语义上是不对的（judge 应该反映"现在在用的模型怎么看"，而不是"启动参数里配的模型怎么看"）。
- 无重试：`ensemble` 场景本身就是"多打几次拿更稳的结果"，但单次内部调用反而没有重试兜底，一次网络抖动就直接影响一个候选。
- 三份几乎相同的代码，后续任何 provider 层的修复（比如 NVIDIA 403 → `LLMPermanentError` 那类）都要三处分别改。

### 1.3 `orchestrator/sub_agent.py`（性质不同，需要单独判断）

这里的"自建 client"是**有意为之**的设计：sub agent 要在独立线程里跑一个完全隔离的对话（独立历史、独立统计），文件头注释也写明"继承主 Agent 的 LLMConfig 但可覆盖"。它不属于"应该直接复用主 agent 当前 client"的场景，但目前也没有重试/fallback 能力，可以视为独立的改造项（见第 6 节开放问题）。

### 1.4 `perception/memory_factory.py::build_llm_call`

设计上就是"失败就静默降级，不影响主流程"（分类规则兜底/朴素摘要），这个降级语义要保留，但目前完全没有重试，一次瞬时错误就直接降级，有点浪费——加上统一重试后能减少不必要的降级。

---

## 2. 目标

新增一层**"主对话循环之外场景专用"的轻量封装**，作为所有旁路 LLM 调用（judge / decompose / ensemble 评审 / 摘要重写 / 路由判定）的唯一入口，要求：

1. **默认复用当前 agent 正在用的 provider/model**（即 `agent._client_pool`），不再各自重新读一份启动时的静态配置；`/model` 切换后旁路调用自动跟随，无需额外同步逻辑。
2. **自带统一重试**（复用现有 `RetryPolicy` / `chat_with_retry` / `LLMClientPool.call_with_pool` 语义），不用每处再手写 try/except。
3. **调用签名统一且正确**（`ask(prompt, ...) -> str` / `chat(messages, system, tools) -> LLMResponse`），从源头避免 `max_tokens=` 这类参数不匹配的问题。
4. 对确有理由使用不同 model 的场景（judge_model/judge_provider 覆盖），保留一个显式、集中的"逃生舱"参数，而不是让每个调用点各自重新实现一遍 client 构造逻辑。

不做的事：不改变 `LLMClient` / `LLMClientPool` / `RetryPolicy` 的既有行为和接口，纯粹是在其上加一层薄封装 + 迁移调用点。

---

## 3. 新增模块：`src/mini_agent/llm/service.py`

```python
class LLMHelper:
    """
    供 Agent 主对话循环之外的场景复用的轻量 LLM 调用入口。

    - 只持有 client_pool 的引用（不 copy 配置），因此天然跟随
      Agent 当前的 provider/model（包括 /model 切换）。
    - 默认路径统一走 chat_with_retry 语义（空响应重试 + 异常重试），
      调用方不用各自处理重试。
    - 需要临时切换到不同 model/provider（如 judge 想固定用某个更强的模型）
      时，通过 override 参数一次性构造独立 client，但仍复用同一套
      重试逻辑，不再各自重新实现 LLMConfig 拼装。
    """

    def __init__(self, client_pool: "LLMClientPool", *, default_retry: Optional["RetryPolicy"] = None): ...

    def ask(
        self,
        prompt: str,
        *,
        system: str = "",
        max_retries: int = 3,
        retry_policy: Optional["RetryPolicy"] = None,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
        override_temperature: Optional[float] = None,
    ) -> str:
        """最常见场景：单轮、无工具、只要文本。内部调用失败时抛出 LLMError，
        调用方按自己的语义决定是否要捕获降级（如 memory_factory 场景）。"""

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        tools: Optional[list["ToolSchema"]] = None,
        *,
        max_retries: int = 3,
        retry_policy: Optional["RetryPolicy"] = None,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
        override_temperature: Optional[float] = None,
    ) -> "LLMResponse":
        """完整能力入口，走 client_pool.call_with_pool。
        override_temperature 与 override_model/override_provider 同属"临时构造
        独立 client"分支——只要三者任一被传入，就不再走 call_with_pool，而是
        一次性 create_client() 后套用同一个 RetryPolicy 调用（无 fallback，
        因为这是"临时用一个特定配置"的场景，不该切到 fallback chain 的其他 entry）。"""
```

- 无 `override_*` 时：`chat()` 内部走 `self._pool.call_with_pool(call_fn=lambda client: client.chat(...), retry_policy=...)`，即完全复用现有的多 key 轮转 + 多配置 fallback。
- 有 `override_*` 时：一次性用 `LLMConfig.from_app_config(cfg)` 为基底、覆盖 provider/model 字段，`create_client()` 构造一个临时 client，但仍然套上同一个 `RetryPolicy` 再调用（而不是裸 `client.chat()`）。这一分支就是给 judge/decision 这类"想用不同模型评审"的场景用的，逻辑集中写一次，替掉三份重复代码。

挂载方式：在 `Agent` 上加一个懒加载属性 `agent.llm_helper`，构造方式与现有 `self._llm = self._client_pool.current_client` 的懒取模式一致（每次访问都基于当前 `_client_pool`，不缓存过期引用）。

---

## 4. 调用点迁移清单

| 文件 / 函数 | 现状 | 改造动作 | 优先级 |
|---|---|---|---|
| `evolution/objective_executor.py::_default_llm_decompose` | `llm_client.chat(messages=msgs, max_tokens=500)`（bug，实际抛异常后静默失效） | 改为接收 `LLMHelper`，调用 `helper.ask(prompt, max_retries=...)` | **P0（修 bug）** |
| `perception/goal_backlog.py::_llm_decompose` | 同上 bug | 同上 | **P0（修 bug）** |
| `ensemble/judge.py::judge_llm` | 自建 client，裸 `chat()` | 改为 `agent.llm_helper.chat(..., override_model=judge_model, override_provider=judge_provider)` | P1 |
| `ensemble/decision.py::_model_based_signal` | 同上 | 同上 | P1 |
| `ensemble/strategies.py::make_llm_call` | 同上，`override_model`/`temperature` 是主要诉求，provider 一般不覆盖 | 迁移进 helper，用 `override_model` + `override_temperature`；候选生成场景重试次数调低（`max_retries=1`，见 4.1 重试次数分场景表） | P1 |
| `perception/memory_factory.py::build_llm_call` | 裸 `client.chat()`，失败返回空串 | 改为 `helper.ask(...)`，外层 try/except 保持"失败返回空串"的降级语义不变，只是内部多了重试 | P2 |
| `orchestrator/sub_agent.py` | 独立 client，设计上就应隔离，且目前裸 `create_client()` 后没有重试 | **不改 client 构造逻辑**（保留独立 provider/model），但内部调用改为套用 `RetryPolicy`（不经过 `LLMHelper`，因为它本来就不用 `client_pool`，直接对自己持有的 client 调 `chat_with_retry` 即可） | P2 |
| `agent/llm_control.py`（`/model` 探测调用） | 裸 `client.chat()` | 不迁移——这是"测试新配置是否可用"的探测性调用，本身就不该有重试/fallback（重试会掩盖配置错误） | 不改 |

---

## 5. 落地步骤

1. 新建 `src/mini_agent/llm/service.py`，实现 `LLMHelper`（含单测：`tests/test_llm.py` 或新建 `tests/test_llm_helper.py`，覆盖默认路径、override 路径、重试触发、异常向上抛出几种情形）。
2. 在 `Agent`（`agent/core.py` 或 `agent/__init__.py`）上加 `llm_helper` 懒加载属性。
3. 按上表 P0 → P1 → P2 顺序迁移调用点，每步跑一遍相关既有测试（`tests/test_orchestrator.py`、ensemble 相关测试、`tests/test_llm.py` 等）。
4. P0 两处修完后单独验证一次"目标自动拆解"链路（`_default_llm_decompose` / `_llm_decompose`）确实能正常产出多步骤，而不是静默返回空列表。
5. 全部迁移完成后，检索确认代码库里不再有裸的 `LLMConfig.from_app_config(cfg)` + `create_client(...)` 重复片段（`ensemble/` 三处应清零）。

---

## 6. 开放问题确认结果

| # | 问题 | 结论 |
|---|---|---|
| 1 | `override_model`/`override_provider` 是否保留 | **保留**。judge 系列可以继续用和主对话不同的模型，按第 3 节设计实现 |
| 2 | `sub_agent.py` 是否纳入本次范围 | **不动 client 构造逻辑**，只在其内部调用外面套一层 `RetryPolicy`（不经过 `LLMHelper`） |
| 3 | `make_llm_call` 的 `temperature` 抖动怎么处理 | 加 `override_temperature` 参数，`make_llm_call` 迁移进 `LLMHelper` |
| 4 | 重试次数默认值 | **分场景区别对待**，默认 `max_retries=3`；ensemble 候选生成场景单独调低（见下表） |

### 6.1 各调用点的 `max_retries` 取值

| 调用点 | max_retries | 理由 |
|---|---|---|
| `LLMHelper` 默认值 | 3 | 作为兜底默认值，未显式传参的调用点都用这个 |
| `objective_executor._default_llm_decompose` | 3（用默认值） | 后台任务，不追求低延迟，失败了直接降级为单步执行，可以多试几次换成功 |
| `goal_backlog._llm_decompose` | 3（用默认值） | 同上 |
| `ensemble/judge.py::judge_llm` | 3（用默认值） | 评审只跑一次，值得多试几次保证给出结果，而不是评审失败退化成"没得选" |
| `ensemble/decision.py::_model_based_signal` | 2 | 只是"要不要触发 ensemble"的路由判定，异常本身已经在 caller 里当作"不触发"处理，不必和主流程一样重试 3 次 |
| `ensemble/strategies.py::make_llm_call` | 1（即不重试，仅原始调用失败才由上层重试策略处理） | 候选生成场景下单个候选失败应该快速 fail、把资源让给其他候选，而不是每个候选都卡在重试上拖慢整体产出候选的总时长；候选整体成功率由"多候选"这个机制本身保证，不需要单候选内部重试 |
| `perception/memory_factory.py::build_llm_call` | 3（用默认值） | 分类/摘要兜底调用，失败反正会静默降级，多试几次换成功的性价比高，不影响响应体感（本来就是异步/后台路径） |
| `sub_agent.py` | 沿用 `default_retry_policy()`（与主循环一致，通常对应 `max_retries=3` 左右的默认策略） | 子任务本身是一次完整对话，应该和主 agent 对话享受同等的重试保障 |
