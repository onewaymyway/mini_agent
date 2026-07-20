# LLMHelper：主对话循环之外的统一 LLM 调用入口

## 概述

mini-agent 里"发起一次 LLM 请求"分两大类场景：

1. **主对话循环**：`agent/turn_loop.py` 及其直接协作模块（`compaction.py`、`lifecycle.py`、`reflection.py`、`profile.py`、`role_judge.py`、`snapshot.py`、`history_manager.py` 等），统一走 `self._llm.chat_with_retry(...)`，天然复用 `LLMClientPool` 的多 key 轮转 + 多配置 fallback，并跟随 `/model` 实时切换。
2. **主对话循环之外**：judge 评审、ensemble 候选生成、目标自动拆解、路由判定、记忆摘要重写……这类"旁路"调用**统一通过 `LLMHelper`**（`src/mini_agent/llm/service.py`），不再各自裸调 `client.chat()` 或各自 `LLMConfig.from_app_config(cfg)` 重新拼一份配置。

> 背景与迁移过程详见 `next_doc/llm_helper_unification_plan.md`（改造计划，状态：已收尾）。本文档面向"如何使用"和"以后新增旁路调用该怎么接入"。

---

## 为什么需要 LLMHelper

在引入 `LLMHelper` 之前，旁路调用有三个问题：

- **配置过期**：`ensemble/judge.py`、`ensemble/decision.py`、`ensemble/strategies.py` 三处各自 `LLMConfig.from_app_config(cfg)` 重新读一份**启动时的静态配置**，用户在会话中用 `/model` 切换过 provider/model 后，这三处不会跟着变。
- **无重试**：裸 `client.chat()` 没有重试兜底，一次网络抖动直接影响一次判定/摘要/候选生成。
- **签名不对齐导致的隐藏 bug**：`evolution/objective_executor.py` 与 `perception/goal_backlog.py` 曾经传入 `chat(messages=msgs, max_tokens=500)`，而 `LLMClient.chat()` 根本不接受 `max_tokens` 关键字参数，实际运行抛 `TypeError`，被外层 `except Exception` 静默吞掉——"目标自动拆解"功能形同虚设且没有可见报错。

`LLMHelper` 用一层薄封装同时解决以上三个问题，且**不改变** `LLMClient`/`LLMClientPool`/`RetryPolicy` 的既有行为。

---

## 基本用法

### 获取 LLMHelper 实例

在有 `Agent` 引用的场景，永远优先用懒加载属性：

```python
helper = agent.llm_helper  # 每次访问都基于当前 self._client_pool，跟随 /model 切换
```

在没有 `Agent` 引用可取的场景（独立工具函数、无 agent 的后台任务），用兜底构造：

```python
from mini_agent.llm.service import LLMHelper
helper = LLMHelper.from_config(app_cfg)  # 从 AppConfig 现建一条单链 LLMClientPool
```

### `ask()` —— 最常见场景：单轮文本

```python
result_text = helper.ask(
    "请把下面的目标拆解为 3-5 个可执行步骤：...",
    max_retries=3,
)
```

- 内部转发到 `chat()`，取 `resp.text` 并 `strip()`。
- 调用失败（重试预算耗尽）时抛出 `LLMError`，是否捕获降级由调用方自己决定（不同调用点的降级语义不同：有的返回空串，有的返回 `None`/`[]`）。

### `chat()` —— 完整入口（需要 messages / system / tools）

```python
resp = helper.chat(
    messages=[{"role": "user", "content": prompt}],
    system=system_prompt,
    tools=None,
    max_retries=1,
    override_model=judge_model,
    override_provider=judge_provider,
    override_temperature=0.0,
)
```

### override 参数：显式"逃生舱"

`override_model` / `override_provider` / `override_temperature` 三者任一被传入时，`chat()` 不再走 `client_pool.call_with_pool`（即不占用/切换主 pool 状态、不触发 fallback chain 的其它 entry），而是**一次性**基于 `LLMConfig.from_app_config(cfg)` 覆盖对应字段、`create_client()` 构造一个临时 client，仍然套用同一个 `RetryPolicy` 调用。

这是给"确实需要用不同模型/温度问一次"的场景用的（例如 judge 想固定用某个更强的模型评审、`make_llm_call` 想要更高的采样温度），逻辑只写一次，替掉了历史上三处几乎逐字重复的 client 构造代码。

---

## `max_retries` 取值：按场景区别对待，没有统一"一刀切"

| 调用点 | max_retries | 理由 |
|---|---|---|
| `LLMHelper` 默认值 | 3 | 未显式传参时的兜底默认值 |
| `evolution/objective_executor.py::_default_llm_decompose` | 3（默认） | 后台任务，不追求低延迟，失败直接降级为单步执行 |
| `perception/goal_backlog.py::_llm_decompose` | 3（默认） | 同上 |
| `ensemble/judge.py::judge_llm` | 3（默认） | 评审只跑一次，值得多试几次保证给出结果 |
| `ensemble/decision.py::_model_based_signal` | 2 | 只是路由判定，异常已在 caller 处当作"不触发"处理 |
| `ensemble/strategies.py::make_llm_call` | 1（不重试） | 候选生成场景应快速 fail，把资源让给其他候选；整体成功率由"多候选"机制保证 |
| `perception/memory_factory.py::build_llm_call` | 见下方"例外" | 分类/摘要兜底，失败会静默降级，多试几次换成功性价比高 |
| `orchestrator/sub_agent.py` | 沿用 `default_retry_policy()` | 子任务是一次完整对话，应享受和主 agent 同等的重试保障 |

新增旁路调用点时，先问自己：**这次调用失败了，上层会怎么处理？** 如果是"直接降级/退化成默认行为"，可以用默认的 3 次多换成功率；如果是"快速失败、由外层机制（如多候选并发）兜底整体成功率"，调低甚至设为 1。

---

## 例外：`perception/memory_factory.py::build_llm_call`

这里**没有**改用 `LLMHelper`，原因是它只持有 `client`（单个 `LLMClient` 实例），没有 `client_pool` 可传给 `LLMHelper`。改造方式是直接调用 `LLMClient` 已有的 `chat_with_retry(max_retries=3)`，风险更小，且保留了原有的"失败静默降级（分类规则兜底/朴素摘要）"语义。

**结论**：不是所有旁路调用都必须字面意义上"用 LLMHelper 类"，核心诉求是"统一重试 + 跟随当前 provider/model + 签名正确"，如果调用点手上只有 `client` 而非 `client_pool`，用 `LLMClient.chat_with_retry()` 同样满足这三点。

---

## 例外：`orchestrator/sub_agent.py` 不接入 LLMHelper

Sub agent 的"自建 client"是**有意为之**的设计：要在独立线程里跑一个完全隔离的对话（独立历史、独立统计）。它把独立 client 传给内层完整的 `Agent(...)`，内层 Agent 自带单链 `LLMClientPool` + `chat_with_retry`；`SubAgent._run_with_capture()` 外面还套了一层针对 5xx/超时的重试（`_RETRY_MAX_ATTEMPTS=8`）。已经满足"补一层重试"的诉求，**不迁移、不改动 client 构造逻辑**。

## 例外：`agent/llm_control.py` 的 `/model` 探测调用

`/model` 命令切换 provider/model 前会先探测性调用一次新配置是否可用，这个调用**故意不接入** `LLMHelper` 的重试语义——重试会掩盖"这个配置本身就是错的"这一信号，探测调用就应该一次成功或一次失败。

---

## 工具函数入口：`tools/orchestration.py` 如何拿到当前 agent 的 llm_helper

`run_ensemble_llm` / `run_ensemble_subagents` 这两个工具函数在**工具执行的线程**里运行，手上没有 `agent` 的直接引用，因此不能直接写 `agent.llm_helper`。这里复用了和 `active_skills` 完全相同的 thread-local provider 模式：

```python
# agent/core.py: Agent.__init__ 尾部（无条件注册，不依赖 skill_loader 是否启用）
set_current_llm_helper_provider(lambda: self.llm_helper)

# tools/orchestration.py 内部
helper = _get_current_llm_helper()  # 读不到时返回 None
run_llm_ensemble(..., llm_helper=helper)  # None 时 runner 内部退化为 LLMHelper.from_config(cfg)
```

- **绑定 agent 的线程**（正常工具调用场景）：能读到 `lambda: self.llm_helper`，跟随主 agent 的 `/model` 切换。
- **未绑定 agent 的线程**（如 `TaskManager` 独立运行场景）：`_get_current_llm_helper()` 返回 `None`，`ensemble/runner.py` 内部自动降级为 `LLMHelper.from_config(cfg)`，不影响既有行为，也不会抛异常。

新增任何"运行在独立线程、但想跟随主 agent 当前 provider/model"的工具函数，都可以复用这一套 provider 注册机制，不需要重新设计。

---

## 给"新增旁路 LLM 调用"的检查清单

以后新增一处不属于主对话循环的 LLM 调用时，按以下顺序检查：

1. **能拿到 `client_pool` 吗？**
   - 能（有 `Agent` 引用，或运行在能读到 thread-local provider 的线程里）→ 用 `agent.llm_helper` / `_get_current_llm_helper()`。
   - 不能（只有独立 `AppConfig`，没有活跃 agent）→ `LLMHelper.from_config(cfg)`。
   - 只有单个 `client`、没有 `client_pool`（罕见）→ 直接用 `LLMClient.chat_with_retry()`，不必强行套 `LLMHelper`。
2. **要不要覆盖 model/provider/temperature？** 需要 → 传 `override_model` / `override_provider` / `override_temperature`；不需要 → 什么都不传，默认走 `call_with_pool`（跟随主 agent 当前配置 + 完整 fallback chain）。
3. **`max_retries` 该设多少？** 参考上面的取值表选一个最贴近的场景类比，而不是照抄默认值 3。
4. **调用失败后怎么办？** 想清楚是"向上抛出让调用方决定"还是"内部 try/except 静默降级"，`ask()`/`chat()` 本身不吞异常，降级逻辑留给调用方写。
5. **禁止**再手写 `LLMConfig.from_app_config(cfg)` + `create_client()` 的组合去发起旁路调用——这正是本次改造要消灭的重复模式，新写的旁路调用一律走 `LLMHelper`。

---

## 代码自检：如何确认没有残留的旧模式

```bash
grep -rn "LLMConfig.from_app_config" src/
```

预期只剩两处，均为设计上不该迁移的：

- `orchestrator/sub_agent.py` — 独立 client 构造，设计上保留
- `agent/core.py` — Agent 自身主 client 的构造，不属于"旁路调用"范畴

其余任何新出现的 `LLMConfig.from_app_config(cfg)` + `create_client()` 组合都应视为需要改用 `LLMHelper` 的信号。

---

## 相关文件

- `src/mini_agent/llm/service.py` — `LLMHelper` 实现
- `src/mini_agent/agent/llm_control.py` — `Agent.llm_helper` 懒加载属性
- `src/mini_agent/tools/orchestration.py` — thread-local provider 机制，`run_ensemble_llm`/`run_ensemble_subagents` 接入
- `src/mini_agent/evolution/objective_executor.py` — `_default_llm_decompose`
- `src/mini_agent/perception/goal_backlog.py` — `_llm_decompose`
- `src/mini_agent/ensemble/judge.py` / `decision.py` / `strategies.py` / `runner.py`
- `tests/test_llm_helper.py` — `LLMHelper` 单测（default/override 路径、重试、目标拆解回归）
- `tests/test_orchestration_llm_helper_provider.py` — thread-local provider 注册/降级/透传单测
- `next_doc/llm_helper_unification_plan.md` — 完整改造计划与排查记录

另见 [LLM 故障转移指南](llm-failover-guide.md)（`LLMClientPool` 本身的 fallback 机制）与 [重试退避指南](retry-backoff-guide.md)（`RetryPolicy`/`BackoffStrategy`）。
