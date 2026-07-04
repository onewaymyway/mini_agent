# 微信接入指南（weixin_bot.py）

> 说明 `weixin_bot.py` 的架构、每用户 Agent 隔离方式、权限审批改造，
> 以及一个关键的 asyncio 事件循环死锁陷阱（`_get_or_create` 的修复记录）。

---

> **另有一套独立方案**：`apps/weixin_plugin/` 基于多用户 Daemon 的 HTTP API
> （`/v1/*`），支持微信侧独立进程部署、跨机部署、按角色隔离多个微信用户，
> 参见 [微信接入指南 v2](weixin-plugin-guide.md)。两者互不共享状态，不要
> 同时对同一个 mini_agent 实例混用；轻量单机场景可继续使用本文档描述的方案。

## 1. 概述

`weixin_bot.py` 与 `main.py` 同级放在项目根目录，直接内嵌 `mini_agent`
包，复用 `agent_config.json` / `providers.json` / `skills/` 等全部配置，
把微信作为额外的对话入口。

设计目标：

- **每用户上下文隔离**：每个微信 `openid` 对应一个独立的 `Agent` 实例
  （独立会话历史、独立权限白名单），互不影响
- **权限审批走消息而非终端**：`WeixinPermissionGuard` 覆盖
  `PermissionGuard._prompt()`，把原本阻塞在终端的审批交互改成"推一条
  微信消息 + 阻塞在 `threading.Event`"，用户回复 `/yes` `/no` `/always`
  `/denyalways` 即可远程审批危险工具调用
- **同步 Agent + 异步 IM SDK 的桥接**：`Agent.run_turn()` 是同步阻塞调用，
  而微信网关 SDK（`weixin.bot`）基于 asyncio；两者通过
  `loop.run_in_executor()` + `run_coroutine_threadsafe()` 互相桥接

## 2. 整体架构

```
WeixinBot（asyncio 事件循环，网关消息分发）
  └── WeixinHandler.on_text()（协程，运行在事件循环线程）
        ├── await self._get_or_create(openid)
        │     └── loop.run_in_executor(executor, self._make_ctx)  # 线程池
        │           └── Agent(cfg, skill_loader, guard)
        │                 └── MCPManager.register_all(registry)   # 见第 4 节
        │
        ├── /命令  → _dispatch_command()（/help /status /sessions /session /ls /cat /find /yes /no ...）
        └── 普通文本 → _do_chat()
              └── await loop.run_in_executor(executor, ctx.agent.run_turn, text)
                    └── 危险工具调用 → WeixinPermissionGuard._prompt()
                          ├── _push_fn(msg)   # 推审批消息给用户
                          └── threading.Event.wait(timeout=300s)
```

每个 `openid` 的 `_UserCtx`（`agent` + `guard` + `session_index` + `busy`
标志）缓存在 `WeixinHandler._contexts: dict[str, _UserCtx]` 中，懒加载：
第一次收到该用户消息时才创建。

## 3. 关键 Bug 记录：`_get_or_create` 事件循环死锁

### 3.1 现象

首次与某个微信用户对话时（即该 `openid` 第一次触发 `_make_ctx()` 创建
`Agent`），只要 `agent_config.json` 里配置了 MCP server，就必现如下报错：

```
File "weixin_bot.py", line 213, in _get_or_create
    self._contexts[openid] = self._make_ctx()
File "weixin_bot.py", line 246, in _make_ctx
    agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)
File "mini_agent/agent.py", line 401, in __init__
    self._mcp_manager.register_all(self.registry)
File "mini_agent/mcp/manager.py", line 91, in register_all
    future.result(timeout=30)
concurrent.futures._base.TimeoutError
```

固定卡满 30 秒后超时，而不是快速失败——这是死锁的典型特征。

### 3.2 根因

`MCPManager.register_all()`（`src/mini_agent/mcp/manager.py`）为了兼容
"同步调用方 + 内部用 asyncio 驱动"的场景，做了这样的判断：

```python
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = None

if loop is not None and loop.is_running():
    # 假设：调用方在另一个线程，此处把协程提交回 loop 所在线程执行
    future = asyncio.run_coroutine_threadsafe(self._register_all_async(registry), loop)
    future.result(timeout=30)   # 阻塞等待
else:
    asyncio.run(self._register_all_async(registry))
```

这段逻辑隐含一个前提：**调用 `register_all()` 的线程，与它检测到的
"正在运行的 event loop"不是同一个线程**。只有这样，
`run_coroutine_threadsafe` 提交的协程才能被 loop 正常调度，
`future.result()` 的阻塞等待才会在有限时间内解除。

而修复前的 `weixin_bot.py`：

```python
async def on_text(self, bot, msg, text):
    ...
    ctx = self._get_or_create(openid)   # ← 同步调用，直接在 on_text 协程里跑
```

`on_text` 本身就是运行在 `WeixinBot` 的 asyncio 事件循环线程上的协程。
`_get_or_create` → `_make_ctx()` → `Agent(...)` → `register_all()` 全部
在这**同一个线程**里同步执行。于是：

1. `register_all()` 检测到"有正在运行的 loop"（就是它自己所在的 loop）
2. 把注册协程 `run_coroutine_threadsafe` 提交回这个 loop
3. 用 `future.result(timeout=30)` **阻塞当前线程**等结果
4. 但当前线程正是这个 loop 的执行线程——它被卡住了，永远没有机会去
   调度第 2 步提交的协程
5. 30 秒后超时，抛 `TimeoutError`

这是"自己阻塞自己"的经典死锁，与网络、MCP server 是否可达无关——即使
MCP server 完全正常，这里也必然超时。

反过来看 `_do_chat()` 里真正调用 `agent.run_turn()`（进而可能触发
`MCPManager.call_tool_sync()`）的路径：

```python
result = await loop.run_in_executor(self._executor, _run)
```

`_run` 是在**线程池的独立线程**里执行的，这时线程池线程调用
`run_coroutine_threadsafe(coro, loop)` 提交回主 loop，主 loop 所在线程
是空闲的、可以正常调度协程，`future.result()` 很快就能拿到结果——
这条路径本身没有问题。

**结论**：唯一的问题点是 `Agent()` 的首次构造（`_make_ctx()`）没有像
`run_turn()` 一样丢进线程池，而是直接摆在事件循环线程里同步跑。

### 3.3 修复

把 `_get_or_create` 改成 `async` 方法，用
`loop.run_in_executor(self._executor, self._make_ctx)` 让 `Agent()`
构造（含 MCP 注册）也在线程池线程里执行，与 `run_turn()` 走同样安全的
"跨线程 `run_coroutine_threadsafe`"路径：

```python
async def on_text(self, bot, msg, text):
    ...
    ctx = await self._get_or_create(openid)   # 改为 await

async def _get_or_create(self, openid: str) -> _UserCtx:
    if openid not in self._contexts:
        loop = asyncio.get_event_loop()
        self._contexts[openid] = await loop.run_in_executor(
            self._executor, self._make_ctx
        )
    return self._contexts[openid]
```

`_make_ctx()` 本身（同步方法）不需要改动。

### 3.4 排查这类问题的通用经验

- `asyncio.run_coroutine_threadsafe(coro, loop).result()` **只能**在
  "调用线程 ≠ `loop` 所在线程"的前提下使用；在 `loop` 自己的线程里调用
  必然死锁，且大多数情况下不会立刻报错，而是卡到 `timeout` 才抛
  `TimeoutError`，容易被误判为"网络慢"或"MCP server 没响应"
- 排查线索：`TimeoutError` 精确等于代码里写死的 `timeout` 参数值（本例
  是 30 秒整），说明是等待超时而非目标任务本身耗时——这种"整数秒"的
  超时特征是判断死锁 vs 真实慢操作的重要信号
- 任何在异步 handler（如 `on_text`/`on_message`）里**同步**构造/调用
  会内部驱动 asyncio 的对象（如本例的 `MCPManager`、或其他内部用
  `run_coroutine_threadsafe` 桥接同步/异步的组件），都要检查是否需要
  丢进 `run_in_executor`，不能想当然认为"同步方法直接调用没问题"

## 4. 相关模块

- `weixin_bot.py` — 微信 Handler、每用户 Agent 生命周期、权限审批桥接
- `src/mini_agent/agent.py` — `Agent.__init__` 中同步调用
  `MCPManager.register_all()`
- `src/mini_agent/mcp/manager.py` — `register_all()` / `call_tool_sync()`
  的同步-异步桥接实现，详见 [MCP 集成指南](mcp-guide.md)
- `src/mini_agent/permissions.py` — `PermissionGuard` 基类，
  `WeixinPermissionGuard` 在此基础上覆盖 `_prompt()`

## 5. 使用方式

```bash
# 微信网关配置（openclaw）走环境变量，默认读 ~/.openclaw/openclaw.json
export WEIXIN_BASE_URL=...
export WEIXIN_TOKEN=...

# 启动
python weixin_bot.py [--project <路径>] [--yes] [--no-stream]
```

可用指令（发给微信 bot）：

```
直接发文字         — 与 Agent 对话
/sessions          — 列出我的所有会话
/session new        — 新建会话
/session use <序号>  — 切换会话
/session del <序号>  — 删除会话
/status             — 当前 Agent 状态
/ls [路径]           — 查看目录
/cat <路径>          — 查看文件（只读）
/find <关键词>        — 搜索文件名
/yes /no /always /denyalways — 响应审批请求
/help                — 查看本帮助
```
