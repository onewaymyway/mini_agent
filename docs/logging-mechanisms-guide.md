# 日志保存机制指南

本文档系统梳理 `mini_agent` 项目里所有"日志/审计流"的保存机制：写在哪里、
谁在写、什么时候写、保留多久。项目里没有一个统一的"日志中枢"，而是按用途
分散在若干个独立子系统里，本文档的目的是把它们放在一起对比，便于排查问题
时知道该去哪个文件找。

## 一、总览：这个项目里到底有几套"日志"

按性质分两大类：

**A. 诊断/调试类日志**——记录"系统运行状况"，供人工排查问题，内容是
异常堆栈、请求响应、进程输出，通常不参与 agent 的任何决策：

| 机制 | 落盘位置 | 作用域 | 默认开关 |
|---|---|---|---|
| 全局错误日志 | `~/.agent/logs/error.jsonl` | 全局，跨项目 | 常开 |
| LLM 调试日志 | `<project>/.agent/sessions/<sid>/llm_debug.jsonl` | session 级 | 关闭（需 `--debug-llm` 或配置开启） |
| Daemon 控制台日志 | `<project>/.agent/daemon.log` | 项目级，daemon 进程 | daemon 后台模式常开 |
| 代理池日志 | `<project>/.agent/proxy/proxy.log` | 项目级 | 常开（2026-07 已修复：原来只在独立脚本模式下生效，现在 agent 内部调用路径也会正确写入，详见第八节） |

**B. 观测/审计类数据流**——记录"agent 做了什么"，本质是结构化数据而非
传统意义的日志，部分会被 Phase G / 自我演化 / 用户画像等模块**读回来当输入
用**，不是纯粹的"事后查错"：

| 机制 | 落盘位置 | 作用域 | 谁会读回去用 |
|---|---|---|---|
| 会话追踪 | `<project>/.agent/sessions/<sid>/traces.jsonl` | session 级 | `/diagnostics`、异常检测、`self_maintenance.py` |
| 记忆审计流 | `<project>/.agent/sessions/<sid>/memory_delta.jsonl` | session 级 | 人工审计，暂无自动消费方 |
| 全局活动流水 | `~/.agent/activity_log.jsonl` | 全局 | `global_knowledge.py` 跨项目模式聚合 |
| 知识生命周期编年目录 | `<project>/.agent/knowledge_timeline.jsonl` | 项目级 | `LibraryIndex.timeline_for()`、`/evolve timeline` |
| 自主活动摘要 | `<project>/.agent/activity_digest.jsonl` | 项目级 | AutonomousLoop / SelfMaintenanceModule / 晨报 |
| 行为感知事件 | `~/.agent/behavior/events/<date>.jsonl` | 全局，按天分文件 | `behavior/analyzer.py` 日报聚合 |
| Task 输出 | `<project>/.agent/sessions/<sid>/tasks/<tid>/output.log`+`events.jsonl` | task 级 | `/tasks log`、kanban 展示 |
| Persona 使用审计 | `~/.agent/persona_usage.jsonl` | 全局 | `/role stats` |
| Artifacts 索引 | `<project>/.agent/artifacts_index.jsonl` | 项目级 | 产出预览 tab |

下面逐个展开。

---

## 二、全局错误日志（`errors.py`）

**这是项目里唯一"专门为异常设计"的日志机制**，其余都是各子系统顺带记录
自己的运行数据。

- 路径：`~/.agent/logs/error.jsonl`（`AgentPaths.global_error_log`），
  `MINI_AGENT_HOME` 环境变量可整体改写 `~/.agent` 的位置。
- 格式：JSON Lines，每行一条完整记录：
  ```json
  {"ts": "2026-07-10T09:00:00+00:00", "pid": 12345, "thread": "MainThread",
   "where": "tools.builtin.read_file", "exc_type": "FileNotFoundError",
   "message": "...", "traceback": "...", "extra": {"path": "..."}}
  ```
- 轮转：`RotatingFileHandler`，单文件 10MB，保留最近 5 个备份
  （`error.jsonl` / `error.jsonl.1` … `.5`）。

### 三层覆盖机制

1. **显式捕获点**：业务代码 `except Exception as e: log_exception(e, where=..., extra=...)`，
   全项目约 **56 个文件、218 处调用**，是覆盖面最广的一层。
2. **logging 转发兜底**：`_RootErrorRouteHandler` 挂在 root logger 上，把所有
   `level >= ERROR` 的标准 `logging` 调用（`logger.error(...)`/`logger.exception(...)`）
   额外转发一份进来，不影响原有输出行为、不重复消费。
3. **进程级最终兜底**：接管 `sys.excepthook`（主线程未捕获异常）和
   `threading.excepthook`（子线程未捕获异常），保证真正"漏网"的异常也留痕
   （`KeyboardInterrupt` 除外，正常退出不记录）。

### 安装时机

`install_global_error_logging()` 只在 `cli/app.py::main()` 开头调用一次
（幂等）。`daemon start`/多用户 `user` 子命令都是在 `main()` 内部短路分发
（本质是 `sys.executable -m mini_agent ... --daemon-mode` 重新走一遍
`main()`），所以普通 CLI 和 daemon 后台进程都能覆盖到；但如果 HTTP API
（`api/server.py`）被其它脚本直接 import 启动、绕开 `cli/app.py::main()`，
第二、三层钩子就不会安装，只有各处显式的 `log_exception()` 调用点仍然生效。

### 已知缺口

- `error_log_path()` 函数注释里写着"暴露给外部（如 `/debug` CLI 命令）查询"，
  但**目前没有任何地方调用它**，也没有 `/debug errors` 之类的命令——想看错误
  日志现在只能自己 `tail -f ~/.agent/logs/error.jsonl`。
- 没有对应的 `tests/test_errors.py`，三层覆盖机制、轮转、JSON 安全序列化都
  没有测试断言。

---

## 三、LLM 调试日志（`llm/debug_logger.py`）

记录每次 LLM 调用的完整请求/响应，用于调试 prompt 拼装是否正确、模型输出
是否异常。**默认关闭**（`DebugConfig.llm_enabled = False`），是主动开启的
调试工具，不是常驻错误日志。

- 每次调用写两条 JSONL 记录：`event=request` 和 `event=response`（或
  `event=error`），带 `seq` 自增序号、耗时 `duration_ms`。
- 路径优先级（`_resolve_log_file`）：
  1. 显式传入 `log_dir` 时：`<log_dir>/llm_debug_<日期>.jsonl`
  2. 默认（`init_debug_logger_for_session` 设置后）：
     `<project_root>/.agent/sessions/<session_id>/llm_debug.jsonl`
  3. session 尚未建立时的兜底：`<project_root>/.agent/logs/llm_debug_<日期>.jsonl`

**注意**：第 3 种兜底路径里的 `.agent/logs/` 是**项目级**目录，跟全局错误
日志的 `~/.agent/logs/`（用户 home 目录）只是同名、不是同一个地方，排查时
容易搞混，需要看清是 `<project_root>` 还是 `~`。

**已知问题（2026-07 已修复）**：`--debug-llm` 只会传给主 Agent 的配置，但
框架内部有不少"一次性内部 Agent 调用"——`EvaluatorAgent`、`CoachAgent`、
`TurnJudgeAgent`、`GoalJudgeAgent`、自定义角色 Dispatcher、Workflow 的
`step`/生成器、以及 `GoalSpecBuilder`（`/goal from-history` 用到的正是这个）
——它们会重新构造一份独立的内部配置，之前这份配置里**硬编码写死了
`debug_llm=False`**，导致外层加了 `--debug-llm` 也完全不影响这些内部调用：
一旦失败点恰好落在这些内部 Agent 身上（比如 GoalSpecBuilder 判定目标失败、
TurnJudge/GoalJudge 判定异常），`llm_debug.jsonl` 里什么都不会有，排查时
容易误以为"日志系统坏了"。现在已改为这些内部配置统一继承外层
`--debug-llm` / `--debug-llm-console`，不再单独硬编码关闭。

**另一个更容易踩到的坑（daemon 模式下）**：如果项目已经有一个存活的
daemon，`mini-agent`/`python main.py` 会直接短路进入"连接客户端"模式，
根本不会在当前进程构建 Agent，此时命令行带的 `--debug-llm` **完全不生效**——
真正处理 LLM 调用的是那个更早启动、且启动时未必带了这个 flag 的 daemon 进程。
详见 `daemon-multi-client-guide.md` 第 4 节；简单说：要给 daemon 开调试日志，
必须在 `daemon start` 那一刻就带上 `--debug-llm`，而不是在之后连接它的客户端
命令上加。

**第三个坑（2026-07 已修复，非 daemon 模式也会踩到）**：`_traced_chat` /
`_traced_stream`（`llm/providers/_base_mixin.py`）里，`_prepare_tools()`
（注入工具协议到 system）和 `_apply_system_format()`（按
`system_message_format` 把 system 合并进 messages）这两步是在
`logger.log_request()` **之前**执行的，完全没有 try/except 保护——如果异常
恰好出在这两步（比如工具 schema 序列化失败、system 格式合并出错），会直接
向上抛出，连 request 记录都不会写，`llm_debug.jsonl` 里自然什么都看不到。
这是"LLM 调用失败但日志完全是空的"里最容易被忽略、也最难排查的一种情况——
和是否开了 `--debug-llm`、是不是 daemon 模式**都没有关系**，纯粹是这两步
本身没被日志覆盖到。现在这两步也纳入了保护范围，失败时会写一条独立的
`event=prepare_error` 记录（`seq` 固定为 0，因为走到这一步时还没有真正
"发出"过请求，没有关联的 seq），排查时能一眼看出"根本没发出请求"和
"发出去但失败了"的区别。

---

## 四、Daemon 控制台日志（`cli/daemon.py`）

daemon 以后台模式启动时，子进程的 `stdout`/`stderr` 会被重定向到文件而非
`DEVNULL`，方便进程崩溃时还能看到最后的输出：

- 路径：`<project_root>/.agent/daemon.log`
- 写入方式：`open(log_path, "w", ...)`——**以覆盖模式打开**，每次
  `daemon start` 都会清空重写，不做轮转、不追加历史。也就是说只能看到
  "最近一次启动到现在"的输出，之前的记录不会保留。
- 前台模式（`--daemon-attach-console` 之类）不走这个文件，直接继承当前
  终端的 stdout/stderr。

---

## 五、会话级观测追踪（`perception/observability.py::SessionTracer`）

对应设计文档第 9 章（Stage 6），记录每个 session 内工具调用的时序、耗时、
异常分类，是 Phase G 剪枝判断和异常检测的数据基础，**不是纯诊断日志，会被
读回去参与决策**：

- 路径：`<project_root>/.agent/sessions/<session_id>/traces.jsonl`
- 用 `span()` context manager 自动计时打点
- 消费方：`GET /v1/diagnostics` 健康检查端点、`classify_error()`（14 种
  `error_category` 分类）、`detect_anomalies()`（k-σ 异常检测）、
  `evolution/self_maintenance.py`（推断 stale_tools/conflicting_lessons）
- 生命周期：随 session 保留，官方设计是"session 结束后可归档，长期只保留
  `/diagnostics` 聚合的统计摘要"，但**目前没有实现自动归档/清理任务**，
  文件会一直留在 session 目录下。

---

## 六、用户行为感知事件（`perception/behavior/events.py::BehaviorEventStore`）

**总开关默认关闭**（`behavior_config.json` 独立配置文件，跟 `agent_config.json`
同级目录），采集范围详见
[用户行为感知系统指南](./behavior-perception-guide.md)。

- 路径：`~/.agent/behavior/events/<YYYY-MM-DD>.jsonl`，按天分文件
- 内容：`ActivityEvent`（前台窗口切换、空闲检测、媒体播放、应用启停、
  git/终端 hook 上报、手机端 Tasker/快捷指令上报等）
- 消费方：`behavior/analyzer.py` 每日聚合成"工作画像+生活画像"结构化日报，
  落盘 `.json`+`.md`

---

## 七、其它审计类数据流

这几个都是各子系统"顺手记一笔"的结构化数据，不是为了排错设计的，但排查
相关功能行为时经常要看：

| 文件 | 写入方 | 用途 |
|---|---|---|
| `<project>/.agent/sessions/<sid>/memory_delta.jsonl` | `agent.py::_append_memory_delta()` | 本 session 产生的记忆条目审计流水，人工审计用，暂无自动消费方 |
| `~/.agent/activity_log.jsonl` | `perception/global_knowledge.py` | W3 全局知识层的跨 session 活动时序，供 `scan_cross_project_patterns()` 聚合 |
| `<project>/.agent/knowledge_timeline.jsonl` + `knowledge_timeline_index.json` | `perception/catalog.py`（本轮图书馆式索引改造新增） | 知识生命周期事件（created/superseded/new_category/category_merged），`LibraryIndex.timeline_for()`/`/evolve timeline` 读取，侧车索引支持按实体/分类过滤 |
| `<project>/.agent/activity_digest.jsonl` | `evolution/autonomous_loop.py`/`resource_arbiter.py`/`self_maintenance.py` | 自主运行时活动摘要 + 健康报告，`/digest` 命令展示 |
| `<project>/.agent/sessions/<sid>/tasks/<tid>/output.log` + `events.jsonl` | Task 执行器 | 单个 task 的完整输出与事件流，`/tasks log` 查看 |
| `~/.agent/persona_usage.jsonl` | `orchestrator/persona_profiles.py` | Persona 激活事件审计，`/role stats` 查看 |
| `<project>/.agent/artifacts_index.jsonl` | `storage/artifacts.py` | 产出物索引，kanban"产出预览"tab 用 |

---

## 八、`proxy.log` 缺口修复记录（2026-07）

### 根因回顾

`AgentPaths.workdir_proxy_log`（`<project>/.agent/proxy/proxy.log`）**写入点其实
一直存在，只是挂在一条几乎不会被触发的路径上**：`scripts/proxy_ctl.py::_setup_logging()`
原本调 `logging.basicConfig(handlers=[FileHandler(...), StreamHandler(stdout)])`
配置 **root logger**，这个函数只在该脚本以 `python scripts/proxy_ctl.py refresh`
**独立进程**方式运行、由 `if __name__ == "__main__": main()` 触发才会执行。
而 agent 内部实际触发 proxy 刷新的两条路径——`cli/commands/proxy.py::handle_proxy_cmd()`
（`/proxy refresh` 命令）和 `tools/proxy_manager.py`（agent 自己可调用的 proxy
工具）——都是用 `from scripts.proxy_ctl import _do_refresh` **直接函数级
import 调用**，完全绕开了 `main()`/`_setup_logging()`。

更深一层的坑：即使强行在这两条路径里也调用一次 `_setup_logging()`，由于
`logging.basicConfig()` 的规则是"root logger 已经有 handler 时整个调用默认
是 no-op（除非传 `force=True`）"，而 agent 进程启动时 `errors.py::install_
global_error_logging()` 早就给 root logger 加过 handler 了，所以就算加一次
调用也不会真正生效——这是本次修复过程里额外发现的第二层问题。

### 修复方式

`scripts/proxy_ctl.py` 改动：
1. `_setup_logging()` 不再碰 root logger，改成给专属的具名 logger
   `logging.getLogger("mini_agent.proxy_ctl")`（`propagate=False`）挂
   `FileHandler`（总是加）+ `StreamHandler`（仅 `include_console=True` 时加），
   不受 agent 主进程是否已经配置过 logging 影响。
2. 用 `_log.handlers` 判断是否已装过 handler，幂等跳过，避免同一进程内多次
   调用（比如反复 `/proxy refresh`）重复叠加 handler。
3. 文件内所有 `logging.info/warning/error(...)` 调用改成 `_log.info/warning/error(...)`。
4. `_do_refresh()`（agent 内部两条调用路径唯一会经过的函数）开头新增
   `_setup_logging(paths)` 懒加载调用（`include_console` 默认 `False`，
   agent 内部调用时不会往 stdout 刷屏）；`main()` 里的调用改成显式传
   `include_console=True`（独立脚本模式下用户仍然能在终端看到实时进度）。

修复后已用单测验证：模拟 root logger 已有 handler 的场景下，`_do_refresh()`
调用后 `proxy.log` 被正确创建，且多次调用不会重复加 handler。

`errors.py::error_log_path()` 没有被任何 CLI 命令调用这一点**尚未修复**，
仍是已知缺口，见第二节。

---

## 九、路径速查表

按"全局 vs 项目级 vs session/task 级"重新汇总一遍，方便按作用域查找：

**全局（`~/.agent/` 或 `$MINI_AGENT_HOME`）**
```
~/.agent/logs/error.jsonl              # 全局错误日志（本文档第二节）
~/.agent/activity_log.jsonl            # W3 全局活动流水
~/.agent/persona_usage.jsonl           # Persona 使用审计
~/.agent/behavior/events/<date>.jsonl  # 行为感知事件（按天）
~/.agent/memory.jsonl                  # 全局记忆（非日志，附带列出便于对照）
```

**项目级（`<project_root>/.agent/`）**
```
<project>/.agent/daemon.log            # daemon 后台进程控制台输出（覆盖写）
<project>/.agent/knowledge_timeline.jsonl        # 知识生命周期编年目录
<project>/.agent/knowledge_timeline_index.json   # 编年目录侧车索引
<project>/.agent/activity_digest.jsonl # 自主活动摘要/健康报告
<project>/.agent/artifacts_index.jsonl # 产出物索引
<project>/.agent/proxy/proxy.log       # 代理池日志（2026-07 已修复：agent 内部调用路径现在也会正确写入）
<project>/.agent/logs/llm_debug_<日期>.jsonl  # LLM 调试日志兜底路径
```

**session 级（`<project_root>/.agent/sessions/<session_id>/`）**
```
traces.jsonl        # 观测追踪（工具调用时序）
llm_debug.jsonl      # LLM 调试日志（session 建立后的默认路径）
memory_delta.jsonl   # 本 session 记忆条目审计
```

**task 级（`.../sessions/<sid>/tasks/<task_id>/`）**
```
output.log    # task 原始输出
events.jsonl  # task 事件流
```

---

## 相关文档

- [观察性系统指南（Stage 6）](./observability-guide.md) — `traces.jsonl` 追踪、`/diagnostics` 端点、异常检测细节
- [用户行为感知系统指南](./behavior-perception-guide.md) — 行为事件采集范围与隐私边界
- [图书馆式知识索引指南](./library-index-guide.md) — `knowledge_timeline.jsonl` 的读写细节
- [Stage 9 自主运行时指南](./self-evolution-stage9-guide.md) — `activity_digest.jsonl` 的生成时机

---

*首次编写：2026-07（系统梳理全项目日志/审计流保存机制，含已知缺口记录）*
