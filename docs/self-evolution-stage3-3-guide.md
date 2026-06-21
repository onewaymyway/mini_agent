# 自我演化 SubAgent 信息继承（Stage 3.3 / Phase E）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 3.3，
> 设计依据 `next_doc/self_evolution_design.md` 第 5 节"SubAgent 信息继承"。

---

## 1. 这是什么

主 agent 在长任务中可能积累了三类"状态"，而在此之前 SubAgent（并发子任务）
对这些状态一无所知，每次都从零开始：

1. **当前激活的 skill** —— 主 agent 已经因为某个关键词激活了 `docx` skill，
   spawn 出去的 SubAgent 却毫不知情，重新摸索一遍。
2. **工具调用结果缓存** —— SubAgent A 刚读过 `app.py`，SubAgent B 又要重新
   读一次，白白消耗 token 和时间。
3. **运行期间产生的经验（lesson）** —— SubAgent 触发的"连续失败"规则型
   lesson，只留在自己的内存里，session 结束、线程销毁后就跟着消失，主
   agent 后续的检索完全看不到。

Stage 3.3 针对这三类状态分别打通了继承/共享/回流路径，**不引入任何新的
持久化机制**——全部复用已有的 `SkillLoader`、`ToolResultCache`、
`MemoryStore` 自身的能力，只是改变了"谁构造、谁持有、谁能看到"。

涉及的文件：

| 文件 | 改动 |
|---|---|
| `orchestrator/task.py` | `Task` 新增 `active_skills: list[str]` 字段 |
| `tools/orchestration.py` | thread-local 的"当前激活 skill 列表"提供者机制 |
| `orchestrator/sub_agent.py` | SubAgent 启动时按名称激活继承的 skill；独立 `ToolRegistry` 副本 |
| `orchestrator/task_manager.py` | 跨 SubAgent 共享的 `ToolResultCache`；SubAgent 结束时触发主 agent memory `reload()` |
| `perception/tool_cache.py` | `ToolResultCache` 加 `threading.Lock`，支持并发共享 |
| `agent.py` | `Agent.__init__` 登记自己的 memory backend 给 `TaskManager` |

---

## 2. Skill 继承：thread-local provider + 按名称激活

### 2.1 为什么是 thread-local，不是普通模块级变量

每个 SubAgent 在独立的 `threading.Thread` 中运行自己的 `Agent` 实例（见
[SubAgent 机制说明](subagent-mechanism.md)）。如果用普通模块级变量记录
"当前激活的 skill 列表"，递归 spawn 场景下（SubAgent 自己也调用
`set_active_skills_provider()`）会被并发线程互相覆盖——主 agent 线程的
`spawn_agent` 调用可能读到某个并发 SubAgent 线程刚刚注册的 provider，串台。

`tools/orchestration.py` 用 `threading.local()` 保证每个线程只看到"该线程
所属 Agent 实例"注册的 provider：

```python
_active_skills_local = _threading.local()

def set_active_skills_provider(provider):
    """由 Agent.__init__ 调用，为当前线程注册回调"""
    _active_skills_local.provider = provider

def _get_active_skills() -> list[str]:
    """spawn_agent / spawn_named_agent 内部调用"""
    provider = getattr(_active_skills_local, "provider", None)
    if provider is None:
        return []
    try:
        return list(provider())
    except Exception:
        return []   # provider 调用失败不影响 spawn 本身
```

`Agent.__init__` 尾部调用：

```python
set_active_skills_provider(lambda: self.skill_loader.active)
```

### 2.2 传递路径：spawn 工具 → Task.active_skills → SubAgent 激活

`spawn_agent` / `spawn_agents`（批量）/ `spawn_named_agent` 三个工具在创建
`Task` 时统一调用 `_get_active_skills()`，写入新建 Task 的 `active_skills`
字段：

```python
Task(
    ...,
    active_skills=_get_active_skills(),
)
```

`SubAgent` 启动构造自己的 `Agent` 时（`orchestrator/sub_agent.py`），按
名称逐个激活：

```python
skill_loader = None
if task.active_skills:
    skill_loader = SkillLoader(skill_dirs, ...)   # 目录解析逻辑与主 Agent 完全一致
    for name in task.active_skills:
        skill_loader.activate(name)
```

只有 `task.active_skills` 非空时才构造 `SkillLoader`，避免没有继承需求的
普通任务也付出一次技能目录扫描的开销。`skill_dirs` 必须与主 Agent 构造
`SkillLoader` 时用的目录解析逻辑保持一致——否则"按名称激活"会因为根本
没扫描到该 skill 而静默失败（`SkillLoader.activate()` 对未知名称直接
返回 `False`，不抛异常）。

### 2.3 生产 bug 修复：独立 `ToolRegistry` 副本

这一步踩中了一个真实的生产 bug。`Agent.__init__` 在 `self.skill_loader`
非空时会调用 `register_skill_tools()`/`register_compact_tool()`/
`register_skill_stats_tool()`，把 `skill_list`/`skill_activate`/
`compact_skill_context` 等工具注册到 `self.registry`。

如果 SubAgent 没有自定义工具限制（`task.allowed_tools`/`allowed_tool_groups`
都为空），`registry` 参数默认是 `None`，`Agent.__init__` 会回退到全局单例
`get_default_registry()`。而主 agent 启动时已经在那个全局单例上注册过
同名工具——**SubAgent 继承了 `active_skills` 就会重复注册，直接抛
`ValueError: Tool 'skill_list' already registered` 崩溃任务**。

第一反应的修复方式是给 `register_fn()` 加 `override=True`，但这是错误的：
这些工具函数通过闭包捕获 `skill_loader`/`agent` 参数，`override` 会把
**全局** registry 里 `skill_list` 等工具的实现直接替换成指向这一个
SubAgent 的 `skill_loader`——之后主 agent 或其他并发 SubAgent 调用
`skill_list` 时，实际执行的会是这个早已结束的 SubAgent 的闭包，造成
**跨 agent 串台**，比直接崩溃更隐蔽、更危险。

正确的修复是给"持有自己 `skill_loader`"的 SubAgent 一份独立的 registry
副本：

```python
if registry is None:
    registry = get_default_registry().filtered()   # filtered() 返回新对象，不是引用
```

`ToolRegistry.filtered()` 不传 `names`/`groups` 参数时返回全部工具的一份
**独立拷贝**，工具注册各自隔离，互不影响——既避免了重复注册崩溃，也避免了
闭包跨实例污染。

### 2.4 验证要点

- `task.active_skills` 为空 → 不构造 `SkillLoader`，SubAgent 与之前行为
  完全一致（零开销）。
- 主 agent 激活了 `docx`、`pdf` 两个 skill → spawn 的 SubAgent 启动后
  `skill_loader.active == ["docx", "pdf"]`。
- `active_skills` 包含一个不存在的 skill 名 → `activate()` 静默返回
  `False`，不影响其他正常激活的 skill，不崩溃。
- spawn 一个继承了 `active_skills` 的 SubAgent 后，主 agent 自己的
  `skill_list` 输出不受污染（验证独立 registry 副本生效）。
- 主 agent 与 SubAgent 各自的 `skill_loader` 可以共存，互不干扰对方状态。

---

## 3. 工具结果跨 SubAgent 共享：`ToolResultCache` + 锁

### 3.1 谁持有共享实例

默认情况下每个 `Agent` 实例持有自己私有的 `ToolResultCache`（session 内
有效，无并发访问）。`TaskManager`（`orchestrator/task_manager.py`）在
`tool_cache_enabled` 开关打开时，额外持有**一个跨 SubAgent 共享的全局
实例**：

```python
self._shared_tool_cache = None
if getattr(base_cfg, "tool_cache_enabled", False):
    from mini_agent.perception.tool_cache import ToolResultCache
    self._shared_tool_cache = ToolResultCache(
        max_entries=getattr(base_cfg.perception, "tool_cache_max_entries", 256)
    )
```

仅在功能开关打开时创建，避免未启用 `tool_cache` 的场景下白白占用一份
空缓存对象。每次 `TaskManager` 构造 `SubAgent` 时把这个实例注入：

```python
SubAgent(..., shared_tool_cache=self._shared_tool_cache)
```

`SubAgent` 构造自己的 `Agent` 时把它当作 `tool_cache` 参数透传：

```python
Agent(..., tool_cache=self._shared_tool_cache, ...)
```

`Agent.__init__` 收到非 `None` 的 `tool_cache` 时直接复用该实例，不再
各自新建一份私有缓存：

```python
if tool_cache is not None:
    self._tool_cache = tool_cache
else:
    self._tool_cache = ToolResultCache(...) if cfg.tool_cache_enabled else None
```

### 3.2 并发安全：`threading.Lock`

多个 SubAgent 线程会并发调用同一个 `ToolResultCache` 实例的 `get`/`put`/
`invalidate_file`。`perception/tool_cache.py` 内部加了一把
`threading.Lock` 保护所有读写 `self._store`（含 `OrderedDict` 的
`move_to_end`/`popitem` 等非原子操作）和 `self._stats` 的代码段，保证
共享场景下不会出现 dict 结构损坏或计数错乱。

私有场景（默认，单 Agent 独享）下加锁的开销可忽略（无竞争锁），因此
**不区分"共享/私有"两套实现**，保持代码简单——同一个类，区别只在于
"是否被多个调用方持有同一引用"。

### 3.3 验证要点

- `tool_cache_enabled=True` 时，`TaskManager` 创建共享缓存实例；
  `tool_cache_enabled=False` 时不创建（`_shared_tool_cache is None`）。
- 两个并发 SubAgent 共享同一个缓存对象（`is` 而非 `==`）。
- 高并发下 `get`/`put` 不产生数据损坏（并发压测用例覆盖）。
- 并发场景下 `cache.stats_summary()` 不抛异常。

---

## 4. lesson 回流主 agent：磁盘共享 + 事后 `reload()`

### 4.1 为什么不是"内存里搬运一份列表"

实施计划最初设想是"SubAgent 结束时把规则型 lesson 汇总写回主 agent 的
`memory.jsonl`"，听起来像是要在 SubAgent 与主 agent 之间显式传递一份
lesson 列表。但实际实现更简单、也更可靠：**SubAgent 构造自己的 `Agent`
实例时，走的是与主 agent 完全相同的 `create_both_memory_backends(cfg)`
工厂函数，读写的是同一个项目级 `memory.jsonl` 磁盘文件路径**。

也就是说，SubAgent 触发的规则型 lesson（连续失败/拒绝重试成功，见
[记忆管理指南](memory-management-guide.md#lesson-memory)）在 SubAgent
运行期间就已经**实时写入磁盘**了——不需要等"汇总"这一步。真正的问题是：
主 agent 自己内存里的 `MemoryStore` 实例是在 session 开始时加载的一份
**快照**，不会自动感知到磁盘上其他进程/线程新写入的内容。

### 4.2 解决方式：SubAgent 终态触发 `reload()`

`TaskManager` 持有主 agent 注册的 memory backend 引用，在任意 SubAgent
进入终态（`DONE`/`FAILED`/`CANCELLED`，无论成功失败都可能已经写过 lesson）
时，让主 agent 的 memory backend 重新从磁盘加载：

```python
def _handle_terminal(self, task_id, old_status, new_status):
    if new_status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
        ...
        self._reload_main_memory_sinks()

def _reload_main_memory_sinks(self) -> None:
    for sink in (self._main_memory, self._main_global_memory):
        if sink is None:
            continue
        try:
            sink.reload()
        except Exception:
            pass   # reload 失败不影响 SubAgent 终态通知本身
```

reload 之后，本 session 后续的 `memory.search()` 就能检索到 SubAgent
期间产生的新 lesson，而不是停留在 session 开始时的旧快照。

### 4.3 登记机制：`set_memory_sinks()` + `is_subagent` 显式区分

`TaskManager` 通常在主 Agent 构造**之前**就已经 `init_task_manager()`
创建好（见 `cli/app.py` 的初始化顺序），所以主 agent 用"事后注册"而不是
构造函数参数传入：

```python
# Agent.__init__ 尾部
if cfg.memory_enabled:
    self._memory, self._global_memory = create_both_memory_backends(cfg)
    if not self._is_subagent:
        _tm = get_task_manager()
        if _tm is not None:
            _tm.set_memory_sinks(memory=self._memory, global_memory=self._global_memory)
```

**关键点：必须用 `is_subagent` 显式区分**，不能简单地"谁先构造谁登记"。
SubAgent 是在 `TaskManager` 的后台调度线程里**异步**构造的，时间上完全
可能晚于主 agent（例如主 agent 在某个 turn 里调用 `spawn_agent` 之后）。
如果不加区分，SubAgent 自己的 `Agent.__init__` 会把主 agent 的登记
**覆盖掉**，导致本应回灌给主 agent 的 `reload()` 调用错误地作用在某个
已经跑完、即将被回收的 SubAgent 的 memory 实例上——表现为"主 agent 再也
收不到任何 SubAgent 产生的新 lesson"，且没有任何报错，非常隐蔽。

### 4.4 验证要点

- `set_memory_sinks()` 正确存储传入的 memory/global_memory 引用。
- SubAgent 进入终态时触发主 agent memory 的 `reload()`。
- 非终态变更（如 `PENDING → RUNNING`）不触发 reload。
- `DONE`/`FAILED`/`CANCELLED` 三种终态都会触发 reload（不只是成功路径）。
- reload 抛异常不会向上传播，不影响终态通知本身的正常流程。
- 未注册任何 sink 时调用 `_reload_main_memory_sinks()` 是安全的空操作。
- 主 agent 构造时若已存在 `TaskManager`，会立即完成登记；SubAgent 自己
  的 `Agent.__init__` 不会覆盖主 agent 已完成的登记。

---

## 5. 三类继承机制的依赖关系

```
Task.active_skills 字段（数据载体）
  ├─→ Skill 继承：spawn 工具读取 thread-local provider 写入字段
  │     └─→ SubAgent 启动时按名称 activate()，独立 registry 副本兜底
  ├─→ ToolResultCache 共享：与 active_skills 机制无直接依赖，
  │     由 TaskManager 独立持有并通过构造参数注入
  └─→ lesson 回流：与前两者均无直接依赖，依靠"同一 memory.jsonl 路径
        + SubAgent 终态钩子触发 reload()" 实现
```

三者相互独立，可分别开关（`active_skills` 为空、`tool_cache_enabled`
为 `False`、`memory_enabled` 为 `False` 时各自互不影响地禁用）。这也是
为什么实施计划称这一项"对 Stage 2 依赖最弱（不涉及 `StateRepo`），可
提前于 3.1/3.2 启动"——三类继承机制本身只依赖 Stage 1 的 lesson 数据
结构（用于判断"写入了什么"），不依赖 Stage 2 的安全网。

---

## 6. 测试

```bash
pytest tests/test_subagent_inheritance.py -v
```

共 31 个测试用例，按机制分为四组：

| 测试类 | 覆盖范围 |
|---|---|
| `TestTaskActiveSkillsField` | `Task.active_skills` 字段默认值与独立性 |
| `TestSpawnToolsPropagateActiveSkills` | spawn 工具如何从 thread-local provider 读取并写入字段（含线程隔离、异常兜底） |
| `TestSubAgentSkillActivation` | SubAgent 按名称激活继承的 skill；独立 registry 副本验证（含未知 skill 名兜底） |
| `TestSharedToolResultCache` | 共享缓存的创建条件、注入、并发读写正确性 |
| `TestMemorySinkReloadOnTaskCompletion` | `set_memory_sinks`/终态触发 reload/异常兜底/非终态不触发 |
| `TestMainAgentRegistersMemorySink` | 主 agent 登记时机，SubAgent 不会覆盖主 agent 的登记 |

全部通过，项目现有测试套件无回归。

---

## 7. 相关文档

- [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) — Stage 3.3 的完整需求背景
- [自我演化设计文档](../next_doc/self_evolution_design.md) — 第 5 节 Phase E：SubAgent 信息继承
- [SubAgent 机制说明](subagent-mechanism.md) — SubAgent/TaskManager 的整体架构、生命周期与重试机制
- [记忆管理指南](memory-management-guide.md) — lesson memory 的数据结构与四条写入路径（Stage 1）
- [Skill 系统指南](skill-system-guide.md) — `SkillLoader` 的激活/排除机制

---

*创建时间：2026-06（self_evolution_implementation_plan.md Stage 3.3）*
