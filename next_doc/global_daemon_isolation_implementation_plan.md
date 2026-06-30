# 全局 Daemon · 代码/数据分离改造 —— 实施计划

> 本文档基于 `next_doc/global_daemon_isolation_design.md`（架构设计稿，已确认）与当前代码库
> 实际状态核对后产出，定位是**可执行的实施计划**，不是架构设计。所有文件路径、函数名均来自
> 对源码的逐项核对。
>
> 核查时间：2026-06（对应代码快照见仓库当前 HEAD）。

---

## 一、现状核对（落到具体文件）

| 设计文档涉及项 | 当前实际状态 |
|---|---|
| `AgentPaths` | `storage/paths.py`，单一 `project_root` 构造，`workdir_dir = project_root / ".agent"`，代码域/数据域未拆分 |
| `project_id_for` | `perception/global_knowledge.py:110`，基于传入路径 `resolve()` 计算 hash，未绑定 git 稳定身份 |
| `.gitignore` | 仅排除 `.agent/logs`、`.agent/sessions`、`.agent/permissions.json` 三项，`.agent/memory.jsonl`、`knowledge.md` 等未排除 |
| Daemon 启停 | `cli/daemon.py`，PID 文件 `<project_root>/.agent/daemon.pid`，启动参数 `--project <project_root>`，单 daemon = 单项目 |
| HTTP Server | `api/server.py` `create_app()` / `HttpServer`，单一 `project_root` 贯穿，`app.state.project_root` |
| Agent 运行体 | `api/server.py` `AgentRunner(threading.Thread)`，**线程而非进程**，与 daemon 同进程内运行 |
| SubAgent 编排 | `tools/orchestration.py`，`TaskManager` 线程池（`max_workers`），无进程级隔离 |
| `StateRepo` | `evolution/state_repo.py`，已实现 tier 判定、`apply()`、`revert()`，**调用方目前只有 skill_propose 一条链路** |
| `protected_paths` | `scripts/protected_paths.py`，覆盖 `agent.py`/`permissions.py`/`hooks/`/`evolution/`，**不含 skills/subagent 路径，也无"全局资源加权"概念** |
| `EvolutionWorkspace` | `evolution/workspace.py`，`git worktree` 副本 + `smoke_boot()`，**已是进程级隔离的现成基础设施**，只是触发时机仅限 skill_propose |
| `projects_index.json` | `global_knowledge.py` 已有"曾经工作过的 workdir"登记，字段不足以支撑调度 |
| `AutonomousLoop` | `cli/daemon.py` 内，单项目上下文 tick，无跨项目调度概念 |

**核心判断**：地基设施（`StateRepo`/`EvolutionWorkspace`/`AgentPaths`/`project_id_for`/`projects_index`）已经存在，但全部是"单项目假设"下的产物。本次改造**不是从零搭建**，而是：① 把数据域从 `project_root` 中剥离；② 把 daemon 从"项目的附属物"升级为"全局调度者"；③ 把 `AgentRunner` 从线程改为可选的独立进程；④ 把 `EvolutionWorkspace` 的使用范围从"仅 skill_propose"扩展为"任意工作"的默认隔离手段。

---

## 二、改造原则

1. **不破坏现有 CLI/HTTP API 兼容性**：现有单项目用法（`mini_agent daemon start` 在项目目录下执行）改造后必须继续可用，内部实现切换为"自动注册为全局 daemon 管理的一个项目"，用户侧无感。
2. **严格按依赖顺序推进，不允许跳跃**：`AgentPaths` 双坐标拆分是后续一切的前提（连 `projects_index` 怎么登记项目都依赖它）；`project_id_for` 不修正，全局 daemon 阶段会直接埋雷（worktree 副本被误判成新项目）。这两项必须最先做。
3. **进程级隔离分两步走，不一步到位**：先把 `EvolutionWorkspace` 的"创建副本→验证→串行合并"流程接入到普通工作流（不引入独立 worker 进程，`AgentRunner` 仍是线程，但运行目录可以指向 worktree 副本），验证"代码/数据分离 + worktree 默认化"本身先跑通；再做"`AgentRunner` 进程化 + 控制通道"，把隔离粒度从"目录"提升到"进程"。这样每一步都可独立验证，不会出现"进程化没调通，副本机制对不对都不知道"的情况。
4. **每个阶段都有可独立验证的产物**：照搬项目既有惯例（Stage 0/1/2 完成记录格式），每阶段写测试、写验证场景，不允许"代码写完但不知道有没有用"。
5. **迁移脚本先 dry-run，默认不破坏性**：任何对存量 `.agent/` 数据的搬迁操作，默认只打印计划、加 `--apply` 才真正执行，且执行前自动备份。

---

## 三、阶段划分总览

```
Stage 0  AgentPaths 双坐标拆分 + project_id_for 基准修正        [地基，必须最先]
Stage 1  数据域迁移脚本（.agent/ → ~/.agent/projects/<id>/data/）  [依赖 Stage 0]
Stage 2  受保护路径 / tier 体系扩展（skills、subagent、全局资源加权）[可与 Stage 1 并行]
Stage 3  worktree 副本默认化接入普通工作流（仍是线程模型）         [依赖 Stage 0/1，建议先于 Stage 4]
Stage 4  全局 daemon 骨架（AGENT_HOME、projects_index 升级）       [依赖 Stage 0]
Stage 5  AgentRunner 进程化 + 控制通道（IPC）                      [依赖 Stage 3、Stage 4]
Stage 6  git 写入串行化（daemon 端合并队列）                       [依赖 Stage 5]
Stage 7  子任务升格规则（process_escalation.py）                  [依赖 Stage 5，可与 Stage 6 并行]
Stage 8  角色系统两层化（全局 + 项目覆盖）                         [依赖 Stage 4]
Stage 9  AutonomousLoop 多项目调度改造                             [依赖 Stage 4、Stage 6]
```

依赖图（箭头表示"必须先完成"）：

```
Stage 0 ──┬──> Stage 1 ──┐
          │              ├──> Stage 3 ──┐
          ├──> Stage 2 ──┘              ├──> Stage 5 ──┬──> Stage 6 ──┐
          │                             │              ├──> Stage 7  ├──> Stage 9
          └──> Stage 4 ─────────────────┘              └─────────────┤
                       └──────────────────────────────> Stage 8 ─────┘
```

---

## Stage 0 —— `AgentPaths` 双坐标拆分 + `project_id_for` 基准修正

**目标**：把"代码在哪跑"和"数据存在哪"在数据结构层面彻底解耦，且项目身份判定对 worktree 副本稳定。

**改动文件**：
- `src/mini_agent/storage/paths.py`
- `src/mini_agent/perception/global_knowledge.py`

**具体步骤**：

1. `AgentPaths.__init__` 改签名：
   ```python
   def __init__(self, code_root: Path, data_root: Optional[Path] = None) -> None:
       self.code_root = Path(code_root).resolve()
       self.data_root = (Path(data_root).resolve() if data_root is not None
                          else self.code_root / _WORKDIR_DIR)   # 兼容默认值，行为不变
       self.project_root = self.code_root   # 保留旧属性名一段时间，标记 deprecated，降低改造面
   ```
   `workdir_dir` 等原本基于 `project_root / ".agent"` 的属性，全部改为基于 `self.data_root`。**不传 `data_root` 时行为与现状完全一致**——这是保证本阶段不破坏现有调用方的关键，所有现存 `AgentPaths(project_root)` 调用点不需要立刻全部修改。
2. `project_id_for()` 改基准：
   ```python
   def project_id_for(code_root: Path) -> str:
       common_dir = _resolve_git_common_dir(code_root)   # 新增：subprocess 跑 `git rev-parse --git-common-dir`，失败返回 None
       identity_source = common_dir.resolve() if common_dir else code_root.resolve()
       resolved = str(identity_source)
       slug = ...  # slug 仍用 code_root 的目录名（人类可读性不变），不用 identity_source 的目录名
       digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:6]
       return f"proj_{slug}_{digest}"
   ```
   新增 `_resolve_git_common_dir(path)`：`subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=path, ...)`，非 git 目录或命令失败时返回 `None`，回退到原逻辑（保证非 git 场景不受影响）。
3. 全仓搜索 `AgentPaths(` 的调用点（预估 15~20 处，分布在 `agent.py`、`api/server.py`、`tools/*.py`），**本阶段不改调用点**，只保证签名兼容；调用点的 `data_root` 显式传入放到 Stage 4（daemon 骨架）落地时再统一改，避免本阶段改动面失控。

**验证标准**：
- 新增 `tests/test_agent_paths_dual_root.py`：断言不传 `data_root` 时行为与改造前一致（回归）；传入不同 `data_root` 时各 workdir 级路径正确跟随 `data_root` 而非 `code_root`。
- 新增 `tests/test_project_id_git_worktree.py`：在临时目录建一个 git 仓库 + 一个 `git worktree add` 副本，断言两者 `project_id_for()` 结果相同；非 git 临时目录断言回退逻辑不报错。
- 跑一遍现有全量测试套件，确认零回归（本阶段设计为纯加法，理论上不应有任何现有测试失败）。

**风险/回滚**：低风险——`data_root` 默认值兜底保证向后兼容；`project_id_for` 改动只在"能拿到 git common dir"时改变行为，拿不到则与原实现完全一致。回滚只需 revert 这两个文件。

**工作量估计**：0.5~1 人天（含测试）。

---

## Stage 1 —— 数据域迁移脚本

**目标**：把存量项目的 `.agent/*` 数据搬到全局位置，且可安全重复执行、可回滚。

**新增文件**：
- `scripts/migrate_data_root.py`（CLI 脚本，不放进 `src/mini_agent/` 包内，理由同 `protected_paths.py`——迁移这类一次性高风险操作独立于主包之外，便于审计）

**具体步骤**：

1. 读取（当前实现下唯一可用的）`~/.agent/projects_index.json`（若不存在则退化为"用户手动传入待迁移目录列表"模式），遍历每个历史 `project_root`。
2. 对每个 `project_root`：
   - 用 Stage 0 修正后的 `project_id_for()` 计算目标 `project_id`
   - 计算源目录 `<project_root>/.agent`、目标目录 `~/.agent/projects/<project_id>/data`（默认 `AGENT_HOME`，支持 `--agent-home` 覆盖）
   - 跳过本来就该留在原地的极少数文件（当前核对结果：没有这类文件，`.agent/` 下全部内容均属数据域）
3. **dry-run 模式（默认）**：只打印 "源目录 → 目标目录" 映射表 + 每个目录大小，不做任何写操作。
4. **`--apply` 模式**：
   - 备份：`shutil.make_archive` 打包源目录到 `${AGENT_HOME}/_migration_backup/<timestamp>/<project_id>.tar.gz`
   - 原子搬迁：先 `shutil.move(src, dst + ".tmp")`，成功后 `os.rename(dst + ".tmp", dst)`；单个项目失败不影响其余项目继续迁移，失败列表最终汇总打印
   - 在原 `<project_root>/.agent/` 位置写入 `data_root_redirect.json`：`{"data_root": "<dst 绝对路径>", "migrated_at": "<timestamp>"}`
   - 在 `<project_root>/.gitignore` 追加 `.agent/`（若尚未包含），不触碰 git 历史
5. 输出迁移报告（成功/失败/跳过清单），失败项给出具体原因（权限、磁盘空间等）。

**验证标准**：
- 新增 `tests/test_migrate_data_root.py`：构造临时"伪项目"（含若干 `.agent/` 下文件），跑 dry-run 断言不产生任何文件系统变化；跑 `--apply` 断言数据出现在目标位置、备份包存在、`data_root_redirect.json` 内容正确、原目录数据已不在原位置。
- 手工验证：在迁移后的项目里跑一次现有 CLI 交互，确认 session/memory 等功能读写正常（依赖 Stage 4 把 `data_root_redirect.json` 接入实际读取逻辑，因此这一项手工验证延后到 Stage 4 完成后再做）。

**风险/回滚**：迁移操作默认 dry-run，`--apply` 前自动备份，是本计划里**对存量数据风险最高**的一步，因此要求：① 必须先在测试项目验证；② 备份包路径在迁移报告里显著展示；③ 提供 `scripts/migrate_data_root.py --rollback <project_id>` 子命令，从备份包还原。

**工作量估计**：1~1.5 人天（含备份/回滚逻辑和测试）。

---

## Stage 2 —— 受保护路径 / tier 体系扩展

**目标**：把 skill / subagent 路径纳入治理范围，并实现"全局资源加权"规则。

**改动文件**：
- `scripts/protected_paths.py`
- `src/mini_agent/evolution/state_repo.py`（`resolve_tier` 增加 `is_global_resource` 参数）

**具体步骤**：

1. `protected_paths.py` 新增一类"受治理但非红线"的路径集合（区别于现有 `PROTECTED_PATHS` 这种"命中即强制 T3"的红线）：
   ```python
   GOVERNED_PATHS: tuple[str, ...] = (
       "skills/",
       "myplugins/",
       ".agent/agents/",     # subagent 定义
   )
   GLOBAL_RESOURCE_PATTERNS: tuple[str, ...] = (
       r".*/\.agent/skills/.*",   # 全局技能库（按 AGENT_HOME 解析后的绝对路径再匹配，见下）
   )
   ```
   新增 `is_governed_path(path) -> bool` 和 `is_global_resource_path(path, agent_home) -> bool` 两个判定函数，与现有 `is_protected_path()` 并列，不修改后者的行为（保持 T3 红线判定逻辑不变）。
2. `StateRepo.resolve_tier()` 增加 `is_global_resource: bool = False` 参数：命中则在原有 tier 基础上"只升一级"（`T0→T1`、`T1→T2`，`T2`/`T3` 不变），实现 `_bump_one_level()` 工具函数。调用顺序：先算 `initiator` 上浮（已有逻辑）→ 再算全局资源加权 → 最后判断是否命中 `protected_paths` 强制 T3（红线优先级最高，放最后判断且只升不降，与现有"强制升级只升不降"原则一致）。
3. `tools/evolution.py`（`skill_propose` 等）调用 `resolve_tier`/`apply` 时，补充判断"目标路径是否落在 `${AGENT_HOME}/skills/` 下"，传入 `is_global_resource=True`。

**验证标准**：
- 扩展 `tests/test_protected_paths.py`：新增对 `is_governed_path`/`is_global_resource_path` 的用例。
- 扩展 `evolution/state_repo.py` 现有测试：构造"全局技能路径 + T1 请求"用例，断言生效 tier 为 T2；"全局技能路径 + T3 请求"断言仍为 T3（不降级）。

**风险/回滚**：低风险，纯加法，不改变任何现有路径的判定结果（新增路径集合与现有 `PROTECTED_PATHS` 互不重叠）。

**工作量估计**：0.5 人天。

---

## Stage 3 —— worktree 副本默认化接入普通工作流

**目标**：在不引入进程级隔离之前，先验证"代码改动默认走副本，数据默认共享"这套流程本身是通的。本阶段 `AgentRunner` 仍是线程模型，只是把它的"运行目录"从 `project_root` 改成可选的 `EvolutionWorkspace` 副本目录。

**改动文件**：
- `src/mini_agent/api/server.py`（`AgentRunner` 初始化逻辑）
- `src/mini_agent/evolution/workspace.py`（补充"同步回主目录"相关方法）

**具体步骤**：

1. `EvolutionWorkspace` 新增方法：
   ```python
   def propose_merge(self, message: str, meta: dict, tier: str) -> ApplyResult:
       """对比 worktree 分支与 base 的 diff，转换为 changes，调用 repo.apply()"""
   ```
   （Stage 6 引入串行合并队列前，本阶段先做"同进程内直接调用"的版本，验证流程逻辑正确性，不处理并发问题——本阶段默认单 worker，天然不存在并发竞争。）
2. `AgentRunner` 启动时新增可选参数 `use_worktree: bool`：为 `True` 时先 `EvolutionWorkspace.create()`，把 Agent 的 `code_root` 指向副本路径，`data_root` 仍指向 Stage 0/1 之后的全局数据目录（**两者第一次在真实运行中验证解耦是否生效**）；session 结束时调用 `smoke_boot()`（已有）+ 视 tier 决定是否 `propose_merge()` 或丢弃副本。
3. CLI/HTTP 暴露一个显式开关（例如 `mini_agent --evolve-mode` 或 HTTP 请求体里的 `mode: "evolve"`），默认关闭（不影响现状），开启后才走 worktree 副本流程——这是为了让本阶段可以在不影响日常使用的前提下独立灰度验证。

**验证标准**：
- 新增集成测试：开启 `use_worktree=True` 跑一次"修改 skill 文件"的场景，断言主目录文件未被直接改动、worktree 副本里改动存在、`smoke_boot()` 通过后主目录才出现该改动（走 `propose_merge`）。
- 验证"数据共享"：在 worktree 模式下写一条 session 记忆，断言它出现在全局 `data_root` 下，而不是 worktree 副本内部。

**风险/回滚**：中等——这是本计划第一个改变运行时行为的阶段。通过"显式开关默认关闭"控制风险面，不开启时现状完全不变。

**工作量估计**：1.5~2 人天。

---

## Stage 4 —— 全局 daemon 骨架

**目标**：daemon 不再绑定单一项目，PID/端口/Self 状态全局化，`projects_index` 升级为调度核心结构。

**改动文件**：
- `src/mini_agent/cli/daemon.py`（PID 文件位置、启动参数、`DaemonClient`）
- `src/mini_agent/api/server.py`（`create_app`/`HttpServer` 去掉单一 `project_root` 假设，改为按请求携带的 `project_id`/`workdir` 路由）
- `src/mini_agent/perception/global_knowledge.py`（`projects_index` 数据结构扩展）

**具体步骤**：

1. 新增 `AGENT_HOME` 解析函数（建议放 `storage/paths.py` 或新建 `storage/agent_home.py`）：
   ```python
   def resolve_agent_home(cli_arg: Optional[Path] = None) -> Path:
       if cli_arg: return Path(cli_arg).resolve()
       if os.environ.get("MINI_AGENT_HOME"): return Path(os.environ["MINI_AGENT_HOME"]).resolve()
       return Path.home() / ".agent"
   ```
2. `daemon.py` 的 PID/info 文件路径从 `<project_root>/.agent/daemon.pid` 改为 `<AGENT_HOME>/daemon.pid`；`daemon_info.json` 新增字段：`http_port`、`control_port`（Stage 5 用）、`control_token`（Stage 5 用，按"长期固定"策略生成一次即不变）。
3. `cmd_daemon_start` 不再要求 `--project`，改为可选 `--agent-home`；启动时**不再绑定任何具体项目**，仅初始化全局状态（`self_profile.json`、`users/`、`skills/` 若不存在则创建空骨架）。
4. `projects_index.json` 结构扩展（在现有"曾经工作过的 workdir 列表"基础上加字段）：
   ```json
   {
     "proj_xxx_abc123": {
       "code_root": "/path/to/project",
       "data_root": "<AGENT_HOME>/projects/proj_xxx_abc123/data",
       "registered_at": "...",
       "last_active_at": "...",
       "active_session_count": 0
     }
   }
   ```
   新增 `register_or_get_project(agent_home, code_root) -> ProjectRecord` 函数：查表命中则更新 `last_active_at`，未命中则创建 `data` 目录并写入新记录（沿用 Stage 0 的 `project_id_for`）。
5. HTTP 路由调整：现有路由隐式假设"daemon 服务的就是它启动时绑定的项目"，改为请求体/路径参数显式携带 `project_id` 或 `workdir`（新会话创建接口必传其一），`HttpServer` 内部用 `register_or_get_project()` 解析出 `code_root`/`data_root` 再构造 `AgentPaths(code_root, data_root)`。
6. **兼容旧用法**：`mini_agent daemon start` 在项目目录下执行且不传 `--agent-home` 时，行为对用户表现不变（自动用默认 `~/.agent`，自动把当前目录注册为一个项目并直接进入该项目的工作上下文），只是内部实现已经是"全局 daemon + 自动注册项目"，不是真的起一个绑定该项目的独立 daemon 进程。检测到本机已有全局 daemon 在跑时，新的 `daemon start` 调用应识别为"附加到已有 daemon、注册新项目"，而不是报错或起第二个 daemon 进程。

**验证标准**：
- 新增 `tests/test_agent_home_resolution.py`：覆盖三层解析优先级。
- 新增 `tests/test_projects_index_registration.py`：同一 `code_root` 两次调用 `register_or_get_project` 返回同一 `project_id`，`last_active_at` 更新。
- 集成测试：在两个不同临时项目目录下分别发起工作请求，断言落到同一个 daemon 进程（同一 PID），但各自的 `data_root` 互不干扰。
- 回归测试：现有"单项目场景"的 CLI/HTTP 测试用例全部通过（验证兼容旧用法这一条）。

**风险/回滚**：本阶段是改造的核心枢纽，改动面较大（涉及 HTTP 路由签名变化）。建议：① 先在新增测试充分覆盖的前提下合并；② 提供 `--legacy-single-project` 兼容开关，允许临时退回旧行为，作为过渡期保险；③ 该开关计划在 Stage 9 验收通过后移除。

**工作量估计**：3~4 人天（本计划里改动量最大的阶段）。

---

## Stage 5 —— `AgentRunner` 进程化 + 控制通道

**目标**：把"一份工作"从线程升级为独立 OS 子进程，daemon 退化为纯调度者。

**新增文件**：
- `src/mini_agent/daemon/worker_launcher.py`（daemon 端：拉起/监控/回收 worker 子进程）
- `src/mini_agent/daemon/control_channel.py`（daemon 端：回环 TCP 服务端，行分隔 JSON 协议）
- `src/mini_agent/cli/worker_entry.py`（worker 端：子进程入口，`python -m mini_agent.cli.worker_entry --code-root ... --data-root ... --control-port ... --control-token ...`，内部连接回 daemon 控制通道，复用现有 `AgentRunner` 逻辑但运行在独立进程而非线程）

**具体步骤**：

1. **控制通道协议**（行分隔 JSON，最简单可靠，避免引入额外依赖）：
   ```
   worker → daemon: {"type": "auth", "token": "..."}
   worker → daemon: {"type": "log", "session_id": "...", "data": "..."}
   worker → daemon: {"type": "request_merge", "branch": "...", "tier": "...", "meta": {...}}
   daemon → worker: {"type": "merge_result", "ok": true, "commit": "..."}
   daemon → worker: {"type": "cancel"} / {"type": "inject_input", "text": "..."} / {"type": "shutdown"}
   ```
2. `worker_launcher.py`：
   - `spawn_worker(code_root, data_root, session_id) -> WorkerHandle`：`subprocess.Popen([sys.executable, "-m", "mini_agent.cli.worker_entry", ...], cwd=code_root, env={**os.environ, "MINI_AGENT_DATA_ROOT": str(data_root), "MINI_AGENT_CONTROL_PORT": str(port), "MINI_AGENT_CONTROL_TOKEN": token})`
   - 维护 `dict[session_id, WorkerHandle]`，定期心跳检测（控制通道断开超过阈值视为崩溃）
   - 崩溃处理：记录日志、标记 session 为异常状态、不自动重启（避免崩溃循环），由上层（HTTP 客户端/Self）决定是否重新发起
   - 优雅关闭：先发 `{"type": "shutdown"}`，等待 N 秒，超时 `proc.terminate()` 再超时 `proc.kill()`（复用现有 `daemon stop` 的"SIGTERM 等待 → SIGKILL"模式）
3. `worker_entry.py`：进程启动时从环境变量读取 `data_root`/`control_port`/`control_token`，`os.getcwd()` 即 `code_root`（天然正确，无需额外传参）；构造 `AgentPaths(code_root=Path.cwd(), data_root=...)`；建立到控制通道的 TCP 连接并发送 `auth`；之后复用现有 `AgentRunner` 的核心逻辑（对话循环、工具执行），把原先"直接写 SSE/HTTP 响应"的输出路径改为"通过控制通道发 `log`/事件消息给 daemon"。
4. `HttpServer` 改造：原先直接持有 `AgentRunner` 线程实例，改为通过 `worker_launcher.spawn_worker()` 拉起子进程，HTTP 请求/SSE 流通过控制通道与 worker 进程交互（daemon 充当中转）。

**验证标准**：
- 新增 `tests/test_worker_launcher.py`：拉起一个最简 worker（不依赖真实 LLM key，只验证进程能起、能连上控制通道、能优雅关闭），断言 `WorkerHandle` 状态正确流转。
- 新增 `tests/test_control_channel_protocol.py`：mock 一个 worker 端连接，断言 `auth`/`log`/`request_merge` 等消息能被 daemon 正确解析和路由。
- 集成测试（依赖真实子进程，标记为较慢测试）：完整跑一次"HTTP 请求 → daemon 拉起 worker → worker 执行一个简单任务 → 结果通过控制通道回传 → HTTP 响应返回"的端到端链路。
- 性能基线：记录"拉起一个 worker 进程到可以开始处理请求"的耗时，确保进程化没有引入不可接受的延迟（worktree 创建近零成本，主要开销是 Python 解释器启动+模块 import，需要实测给出具体数字，作为后续优化（如 worker 进程池预热）的参考基线）。

**风险/回滚**：高风险、改动面最大的阶段，直接影响主交互路径的可用性。建议：① 提供配置开关在"线程模式"（Stage 3 之前的行为）与"进程模式"间切换，默认仍用线程模式，进程模式作为可选项先在内部验证；② 充分的端到端测试覆盖后再考虑切换默认值；③ 该阶段建议安排独立的灰度周期，不与其他阶段合并发布。

**工作量估计**：4~5 人天（含协议设计、子进程生命周期管理、SSE 转发改造）。

---

## Stage 6 —— git 写入串行化

**目标**：多 worker 并发请求合并时，主仓库 git 写操作不出现竞争。

**改动文件**：
- `src/mini_agent/daemon/control_channel.py`（处理 `request_merge` 消息）
- 新增 `src/mini_agent/daemon/merge_queue.py`

**具体步骤**：

1. `merge_queue.py`：单线程消费者模型——`queue.Queue` 接收 `MergeRequest(worker_id, branch, tier, meta)`，daemon 内一个专职线程串行 `pop` 并调用 `StateRepo.apply()`（复用 Stage 3 已验证的 `propose_merge` 逻辑，但执行体从"worker 自己调用"改为"daemon 唯一执行"）。
2. `control_channel.py` 收到 `request_merge` 消息后，不直接处理，而是 `merge_queue.put(...)`，并记录"待回复"映射（`request_id → worker connection`），合并队列处理完成后通过该映射把 `merge_result` 消息发回对应 worker。
3. 处理结果三态：成功（返回 commit hash）、校验失败（返回具体 `validation_errors`，worker 可据此提示 agent 重试）、冲突（与主分支当前 HEAD 不兼容，返回冲突说明，本阶段不做自动冲突解决，直接判失败转人工）。

**验证标准**：
- 新增 `tests/test_merge_queue_serialization.py`：并发发起多个合并请求（用线程模拟多 worker），断言 git 仓库提交历史是线性的、无交叉损坏，且每个请求都收到对应的结果。
- 压力测试：连续提交 N 个合并请求，验证队列不丢消息、不死锁。

**风险/回滚**：中等风险，主要风险点是队列处理线程本身的健壮性（异常处理不当可能导致队列卡死）。要求每个 `StateRepo.apply()` 调用都包在 `try/except` 内，任何异常都要保证给对应 worker 返回明确的失败结果，不能让队列消费线程因单次异常而退出。

**工作量估计**：1.5~2 人天。

---

## Stage 7 —— 子任务升格规则（`process_escalation.py`）

**目标**：实现设计文档 §4.2 已确认的可扩展规则机制。

**新增文件**：`src/mini_agent/evolution/process_escalation.py`

**具体步骤**：按设计文档给出的代码骨架直接实现 `should_escalate_to_process()` + `_ESCALATION_RULES` 列表，首版仅含 `_rule_autonomous_high_tier`。`tools/orchestration.py` 的 `spawn_agent`/`spawn_agents` 在分发子任务前调用该函数，为 `True` 时改走 Stage 5 的 `worker_launcher.spawn_worker()`（子任务也是一种"工作"，复用同一套进程拉起机制），为 `False` 时维持现状的线程池分发。

**验证标准**：单测覆盖规则函数本身（给定 `initiator`/`tier` 组合，断言判定结果）；集成测试验证 `orchestration.py` 在判定为 `True` 时确实调用了进程拉起路径而非线程池。

**风险/回滚**：低风险，纯加法，且依赖 Stage 5 已经过验证的进程拉起能力。

**工作量估计**：0.5~1 人天。

---

## Stage 8 —— 角色系统两层化

**目标**：全局角色基线 + 项目级覆盖（首版仅项目整体覆盖粒度）。

**改动文件**：`api/server.py`（权限判定相关逻辑）、新增 `src/mini_agent/permissions_global.py` 或在现有 `permissions.py` 旁新增角色解析模块（具体文件需在实施时结合 `permissions.py` 现有结构核对，本计划先占位）。

**具体步骤**：
1. 全局角色表迁移：现有按项目存放的 `users.json`（如果存在单项目场景下的用户表）迁移到 `${AGENT_HOME}/users/users.json`，复用 Stage 1 迁移脚本的备份/原子搬迁模式。
2. 权限判定函数改为两段式：`resolve_effective_role(token, project_id) -> Role`，先查全局基线，再查 `${AGENT_HOME}/projects/<id>/roles_override.json` 是否有该 token 的覆盖项，有则覆盖。
3. `roles_override.json` 不存在时全部退化为纯全局角色判定（保证未配置覆盖的项目行为不变）。

**验证标准**：新增测试覆盖"无覆盖文件""有覆盖文件但未命中该 token""有覆盖文件且命中"三种场景。

**风险/回滚**：中等风险（涉及权限判定，出错代价较高），要求测试覆盖所有判定分支，且本阶段上线前需要人工复核一遍权限矩阵的真值表。

**工作量估计**：1.5~2 人天。

---

## Stage 9 —— `AutonomousLoop` 多项目调度改造

**目标**：Self 遍历 `projects_index`，按"轮询 + backlog 优先级"调度跨项目自主任务（已确认的简化版策略）。

**改动文件**：`cli/daemon.py`（`AutonomousLoop`/`tick()` 相关逻辑）、`evolution/`（如有独立的 goal backlog 模块）。

**具体步骤**：
1. `tick()` 改造：原先在单项目上下文里直接判断 backlog，改为：① 遍历 `projects_index` 取出所有项目；② 对每个项目读取其 `data_root` 下的 backlog 文件，按优先级字段排序；③ 用简单轮询指针（记录"上次处理到哪个项目"）选出本次 tick 要处理的项目和目标，避免单个高优先级项目长期饿死其他项目。
2. 选中目标后，调用 Stage 5/7 的进程拉起机制（`initiator="autonomous"`），目标对应的具体执行仍然是"项目级"的（落在该项目的 worker/worktree 副本里）。

**验证标准**：构造多个伪项目各自有不同优先级的 backlog 条目，跑若干次 `tick()`，断言调度顺序符合"轮询 + 优先级"预期，且不存在单项目饿死其他项目的情况。

**风险/回滚**：中等风险，建议先以"干跑"模式（只打印本次 tick 会选择哪个项目/目标，不真正执行）验证调度逻辑正确，再接入真实执行路径。

**工作量估计**：1.5~2 人天。

---

## 四、总体工作量与里程碑建议

| Stage | 工作量估计 | 建议里程碑 |
|---|---|---|
| 0 | 0.5~1 人天 | M1：地基就绪，全量回归通过 |
| 1 | 1~1.5 人天 | M1 |
| 2 | 0.5 人天 | M1 |
| 3 | 1.5~2 人天 | M2：worktree 默认化在线程模型下验证通过 |
| 4 | 3~4 人天 | M3：全局 daemon 骨架可用，旧单项目用法零回归 |
| 5 | 4~5 人天 | M4：进程化模型跑通端到端链路（建议独立灰度） |
| 6 | 1.5~2 人天 | M4 |
| 7 | 0.5~1 人天 | M4 |
| 8 | 1.5~2 人天 | M5 |
| 9 | 1.5~2 人天 | M5 |
| **合计** | **约 16.5~22 人天** | |

**建议合并发布节奏**：M1（Stage 0~2）可作为一次提交合并，纯加法、低风险；M2（Stage 3）建议灰度开关验证一周以上再固化；M3（Stage 4）是用户可见行为变化的开始，需要充分的兼容性测试；M4（Stage 5~7）是风险最高的一批，建议单独拉一个 `evolve/process-isolation` 分支长期验证后再合并主线；M5（Stage 8~9）依赖 M3/M4 完全稳定后再做。

---

## 五、不在本次计划范围内（明确排除，避免范围蔓延）

- 自动解决 git 合并冲突（本计划所有阶段遇到冲突一律判失败转人工）
- worker 进程池预热/复用优化（Stage 5 先用"每次新建子进程"的简单模型，性能基线测出来后再决定是否需要池化）
- `roles_override.json` 细粒度到子目录/操作类型（已确认留待后续按需扩展）
- 跨项目资源仲裁（已确认用简化版轮询调度，不做复杂仲裁）
- 实例发现 UI/CLI 便民命令（设计文档 §2.4 标注为非必需 UX 糖）
