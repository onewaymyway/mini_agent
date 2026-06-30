# 全局 Daemon · 代码/数据分离 · 进程级自我修改隔离 —— 架构设计文档

> 本文档是架构设计稿，用于指导后续分阶段实施，不是最终代码规范。
> 讨论背景：当前 agent 在运行过程中（包括 skill / subagent 代码的自我修改），无论是通过
> `write_file` / `patch_file` 等工具，还是通过 `bash` 任意脚本，都是**直接修改主项目目录**，
> 没有"创建副本 → 修改副本 → 验证 → 同步回主目录"的强制流程。同时，daemon 当前是
> per-project 绑定的，不符合"agent 自我应当是全局唯一、持续存在的实体，项目只是它正在
> 处理的一份工作"这一定位。本文档给出整体重构方案。

---

## 0. 现状摘要（已实现 vs 缺口）

### 已实现，且可直接复用的能力

| 模块 | 文件 | 能力 |
|---|---|---|
| `StateRepo` | `evolution/state_repo.py` | 自我修改的"唯一写入入口"：风险分级（T0~T3）、校验、原子提交、结构化 commit message、`revert()` / `checkout_file()` |
| `protected_paths` | `scripts/protected_paths.py` | 受保护路径清单，命中即强制升级为 T3，且本文件自身不在 agent 可写范围内 |
| `EvolutionWorkspace` | `evolution/workspace.py` | 基于 `git worktree` 的进程级隔离副本，近零成本创建/销毁，`smoke_boot()` 做最低验证 |
| `AgentPaths` | `storage/paths.py` | Global/Workdir/Session/Task 四层路径管理，已有 `~/.agent/` 全局目录概念 |
| `project_id_for` | `perception/global_knowledge.py` | 已有"路径 → 稳定项目 ID"的映射函数 |
| `projects_index` | `global_knowledge.py` | 已有"曾经工作过的 workdir"注册表雏形 |
| Daemon 多用户/角色设计 | `next_doc/daemon-multiuser-architecture.md` | 已提出"Self 常驻、SessionAgent 池"的世界观，但落地仍绑死单项目 |

### 核心缺口

1. **自我修改无强制副本流程**：`write_file` / `patch_file` / `patch_file_simple` 直接 `write_text()` 落盘；`bash` 工具的 `cwd` 默认是进程真实 OS cwd，任意脚本/命令同样直接作用于主目录。`protected_paths` 清单也不包含 `skills/`、`.agent/agents/`（subagent 定义）等路径，这些内容目前不受任何治理。
2. **数据与代码混在一起**：`.agent/` 挂在 `project_root` 下，且 `.gitignore` 未完整排除（`memory.jsonl`、`knowledge.md` 等仍可能被 git 跟踪）。一旦引入"代码走副本"，每个副本都会带着一份过时数据快照，写入会产生脏 diff，无法干净分离"代码改动"与"数据积累"。
3. **`project_id_for` 基准不稳**：基于传入路径的 `resolve()` 计算 hash，若以后同一项目在不同 worktree 副本下运行（路径不同），会被误判为不同项目，导致数据目录错位。
4. **Daemon 是 per-project 的**：PID 文件、HTTP 端口、Self 状态全部绑定单一 `project_root`，"自我"被项目边界切碎，且单机部署多个独立实例缺乏支持。
5. **多进程/跨进程通信缺失**：当前 SubAgent 走线程池（`TaskManager`），同进程内执行，无法做到"工作进程级"的天然隔离，也没有为多进程间通信设计统一通道。

---

## 1. 总体世界观

```
                          ┌──────────────────────────────────────────┐
                          │   主 Daemon（Supervisor，全局唯一/实例）   │
                          │   = Agent 的「自我」常驻载体               │
                          │                                          │
                          │  - 全局 Self（self_profile / 全局记忆 /  │
                          │    跨项目 goal backlog / 自主循环）       │
                          │  - 全局角色系统（owner/family/...）       │
                          │  - projects_index（已知项目注册表）       │
                          │  - 全局技能库 ~/.agent/skills/           │
                          │  - HTTP API（单一对外端口）               │
                          │  - 控制通道（对内，工作进程接入）         │
                          │  - 串行化 git 写入（唯一执行体）          │
                          └───────────────┬──────────────────────────┘
                                          │ 按需拉起 / 监控 / 回收
                  ┌───────────────────────┼───────────────────────┐
                  ▼                       ▼                       ▼
          ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
          │ Worker 进程 A  │       │ Worker 进程 B  │       │ Worker 进程 C  │
          │ 项目1 · 主目录 │       │ 项目1 · evolve │       │ 项目2 · 主目录 │
          │ session1       │       │ 副本 · session2│       │ session1       │
          └───────────────┘       └───────────────┘       └───────────────┘
```

**关键理念**：

- **Self 是全局的、唯一的**：自我模型、长期记忆、跨项目目标、人格连续性，不因为正在处理哪个项目而分裂。
- **项目是 Self 当前在做的"一份工作"**：任何一个 `code_root`（无论是 mini_agent 自身仓库，还是被当作工具操作的其他业务仓库）都只是工作对象，地位平等，统一纳入 `projects_index` 管理。
- **代码与数据是两个独立的域**：代码域（git 管理、需要副本验证）与数据域（运行时产物、全项目共享、不需要副本）物理上分离存放。
- **"工作"以进程为隔离边界**：每一份工作（通常对应一个 session，或一次自我修改验证）拉起一个独立 OS 子进程，天然获得 cwd 隔离，不需要在工具代码里做路径改造。
- **daemon 退化为纯调度者**：不再直接跑 agent 对话循环，只做身份认证、路由、进程生命周期管理、跨进程消息中转、git 写入串行化。

---

## 2. 全局目录与多实例

### 2.1 目录解析优先级

```
1. CLI 参数 --agent-home <path>
2. 环境变量 MINI_AGENT_HOME
3. 默认值 ~/.agent
```

一旦确定 `AGENT_HOME`，该 daemon 实例的一切状态（PID、HTTP 端口、Self 状态、角色表、项目注册表、全局技能库）全部挂在这个目录下，**实例间互不感知、互不干扰**。这是单机部署多个独立实例（例如不同人格、不同客户、测试/生产环境分离）的全部支撑机制——不需要额外发明隔离逻辑，纯粹是"指向不同 home 目录"的自然结果。

### 2.2 目录结构

```
${AGENT_HOME}/                          # 默认 ~/.agent，可配置
├── daemon.pid
├── daemon_info.json                    # 含：pid / http_port / control_port / started_at / token
├── self_profile.json                   # Self 自我模型（已存在）
├── goal_backlog.json                   # Self 全局目标池（跨项目）
├── cross_project_index.json            # 已存在
├── activity_log.jsonl                  # 已存在
├── users/
│   └── users.json                      # 全局角色系统（owner/family/colleague/agent/public）
├── skills/                             # 全局技能库（跨项目通用，改动按更高 tier 治理）
├── prompts/                            # 全局自定义 prompt（已存在）
├── projects_index.json                 # project_id ↔ code_root ↔ 元信息（调度核心数据结构）
│
└── projects/
    └── proj_<slug>_<hash6>/            # project_id（基准修正为 git 稳定身份，见 §3.3）
        ├── data/                       # 该项目 DATA_ROOT：sessions/memory/knowledge/cache/...
        ├── roles_override.json         # 项目级角色权限覆盖（可选）
        └── evolve_worktrees/           # 该项目自我修改用的 worktree 副本集合
```

### 2.3 多实例端口处理

各实例独立监听端口，避免写死：启动时优先尝试配置的首选端口，被占用则自动顺延/随机选取，**实际监听端口写入该实例的 `daemon_info.json`**。`DaemonClient` 始终先读 `${AGENT_HOME}/daemon_info.json` 获取真实端口再连接，不依赖约定端口号。

控制通道（§4.2）的端口同理，单独记录在 `daemon_info.json` 中。

### 2.4 实例发现（非必需，UX 糖）

在用户主目录放一个极轻量旁路登记文件 `~/.mini_agent_instances.json`，仅记录"曾经使用过的 `AGENT_HOME` 列表"，支撑 `mini_agent instances list` 这类便民命令。不是架构必需品，可在主体方案稳定后再做。

---

## 3. 代码域 / 数据域分离

### 3.1 两个域的定义

| | 代码域（CODE_ROOT） | 数据域（DATA_ROOT） |
|---|---|---|
| 内容 | agent 自身代码、skill 定义、subagent 文档（`.agent/agents/*.md`）、prompt 模板 | session 历史、用户 profile/memory、knowledge.md、timeline、cache、几乎全部现有 `.agent/` 数据 |
| 治理方式 | 需要"副本 → 验证 → 同步"，受 `StateRepo` + tier 体系约束 | 直接共享读写，不需要副本化 |
| 物理位置 | 项目原本所在路径，或某个 evolve worktree 副本路径 | `${AGENT_HOME}/projects/<project_id>/data/`，与 code_root 当前物理路径无关 |
| 是否随副本变化 | 是（每次副本物理路径都不同） | 否（同一项目无论在主目录还是副本里跑，永远指向同一份数据） |

### 3.2 `AgentPaths` 改造

```python
class AgentPaths:
    def __init__(self, code_root: Path, data_root: Path) -> None:
        self.code_root = Path(code_root).resolve()
        self.data_root = Path(data_root).resolve()

    @property
    def workdir_dir(self) -> Path:
        return self.data_root            # 原先是 code_root / ".agent"

    # session/cache/knowledge 等全部相对 data_root 解析，与 code_root 解耦
```

`data_root` 作为显式参数，在工作进程启动时由 daemon 传入（环境变量或启动参数），贯穿该工作进程整个生命周期不变；`code_root` 则是该工作进程的真实 cwd，运行主目录时是项目本身，运行 evolve 副本时是 worktree 路径——两者完全独立。

### 3.3 `project_id_for` 基准修正（关键 bug，必须先修）

当前实现对传入路径 `resolve()` 后算 hash。若以后同一项目在不同 worktree 副本下运行（路径不同），会算出不同的 `project_id`，导致同一逻辑项目的数据被错误地分裂到两个数据目录。

**修正方案**：判定基准改为 git 仓库的稳定身份，而非"当前工作目录长什么样"：

```python
def project_id_for(code_root: Path) -> str:
    git_common_dir = _resolve_git_common_dir(code_root)   # git rev-parse --git-common-dir
    identity_path = git_common_dir if git_common_dir else code_root.resolve()
    ...
```

`git worktree` 之间共享同一个 `.git`（common dir），用它作为 hash 输入，主目录和它的所有 worktree 副本会得到同一个 `project_id`。非 git 项目（罕见场景）回退到原有的"路径 resolve"逻辑。

### 3.4 `.gitignore` 与历史清理

`.agent/` 整体加入 `.gitignore`（当前只排除了 `sessions`/`logs`/`permissions.json` 三项，不完整）。不在自动化流程中重写 git 历史（风险高、影响所有协作者的本地仓库），仅在迁移报告中提示用户可自行执行历史清理。

---

## 4. 进程级隔离：工作 = 进程

### 4.1 为什么是进程级，而不是 contextvar 级

早期方案曾考虑用 `contextvars` 把"当前生效目录"注入 `bash`/`write_file`/`patch_file` 等工具，让它们不再依赖真实 OS cwd。这个方案能 work，但需要逐个改造所有文件类工具的路径解析逻辑，且无法覆盖"agent 用 bash 跑任意脚本/子进程"这种情况——脚本内部的相对路径解析，agent 进程层面根本无法拦截。

**改为进程级隔离后，这个问题不存在**：子进程的 `Path.cwd()` 本来就是 daemon 拉起它时传入的 `cwd`。`bash` 工具默认目录、`write_file`/`patch_file` 里裸 `Path(path)` 的相对路径解析，全部自动正确，**不需要改动任何工具代码**。本方案正式采纳进程级隔离，废弃 contextvar 方案。

### 4.2 进程角色

**主 Daemon（Supervisor）**：每个 `AGENT_HOME` 唯一。职责：
- 持有全局配置（home 目录、角色表、`projects_index`）
- 对外暴露唯一 HTTP API 端口：身份认证（全局角色） + 请求路由
- 工作进程生命周期管理：按请求中的 `project` / `workdir` / `session_id` 决定新建/复用哪个工作进程；监控存活、超时/崩溃处理、优雅关闭（SIGTERM 等待 → 超时 SIGKILL，复用现有 `daemon stop` 模式）
- 持有跨进程消息总线（控制通道，见 §4.3）
- **唯一的 git 写入执行体**（见 §4.4）
- 不直接运行 Agent 对话循环

**工作进程（Worker）**：每一份"工作"对应一个独立 OS 子进程，由 daemon 用：
```python
subprocess.Popen([...], cwd=<该工作对应的目录>, env={..., "MINI_AGENT_DATA_ROOT": data_root, ...})
```
拉起。`cwd` 决定了该 worker 的 `code_root`（项目主目录 或 evolve worktree 副本路径），`data_root` 始终通过环境变量/参数显式传入，与 `cwd` 无关。

子任务（subagent）默认仍以线程方式跑在父工作进程内（`TaskManager` 线程池模型不变，绝大多数场景不需要进程级隔离）。对"高风险/自主发起的探索性子任务"，支持按策略升格为独立子进程——可配置，不是非此即彼。

**升格判定规则（首版，已确认）**：

```python
# evolution/process_escalation.py（规则集中存放，便于后续扩展）

def should_escalate_to_process(initiator: str, tier: str, **ctx) -> bool:
    """
    判定一个子任务该用独立子进程而不是线程执行。

    首版规则：initiator == "autonomous" 且 tier >= T2。
    设计为"规则列表 + 任一命中即升格"的形式，而不是单条 if，
    便于后续新增规则（例如：涉及网络/外部凭据的任务、
    单次预估耗时超过阈值的任务、来自特定 colleague 角色发起的任务等），
    不需要改动调用方代码，只需要在 _ESCALATION_RULES 里新增一条。
    """
    return any(rule(initiator, tier, ctx) for rule in _ESCALATION_RULES)


def _rule_autonomous_high_tier(initiator: str, tier: str, ctx: dict) -> bool:
    return initiator == "autonomous" and _TIER_RANK[tier] >= _TIER_RANK["T2"]


_ESCALATION_RULES: list = [
    _rule_autonomous_high_tier,
    # 后续按需追加，例如：
    # _rule_long_estimated_duration,
    # _rule_untrusted_initiator_role,
]
```

调用方（`TaskManager`/`spawn_agent` 等）只依赖 `should_escalate_to_process()` 这一个入口，规则集合的增删改不影响调用方代码——这是"先按一条规则落地，但便于后续扩展"的具体实现方式。

### 4.3 进程间通信：hub-and-spoke

工作进程启动时主动连接回 daemon 暴露的本地控制通道（**回环 TCP + 行分隔 JSON 消息**，而非 Unix socket/命名管道——跨平台一致性最好，Windows/Linux/Termux 都不需要额外适配）。该连接承担三类职责：

1. **状态上报**：日志/事件/SSE 流回传给 daemon，daemon 转发给真正发起 HTTP 请求的客户端；daemon 本身不需要理解 agent 内部状态。
2. **指令下发**：daemon 向工作进程发取消、注入输入、优雅关闭等指令。
3. **跨进程消息（agent 间通信）**：不做 worker 间直连（避免 N×N mesh），统一经 daemon 转发——对应已规划的 `SelfMessageBus` 概念，承载体从"同进程内存总线"升级为"daemon 中转"。好处：① 不会随进程数增长出现连接数爆炸；② 跨项目/跨角色的转发权限检查可以集中在 daemon 一处判断，不需要每个 worker 各自实现一遍。

**安全性**：回环 TCP 在本机上理论上其他进程也能连接，因此控制通道必须基于 token 做认证。**token 与 `AGENT_HOME` 绑定、长期固定**（生成一次后持久化在 `${AGENT_HOME}/daemon_info.json` 或独立的 `${AGENT_HOME}/.control_token` 文件中，权限设为仅当前用户可读），daemon 重启不重新生成、不要求存量 worker 重连——daemon 重启本身会终止/重新拉起所有 worker（worker 生命周期依附于 daemon），因此"重连"场景实际不存在，重新生成 token 没有必要收益，反而增加管理复杂度。token 仅在用户主动要求轮换（如怀疑泄露）时才手动重置。

### 4.4 git 写入串行化（并发风险处理）

`StateRepo.apply()` 是对同一个 git 仓库的写操作。多个工作进程（同一项目下并发多个 session，各自在不同 worktree 副本完成验证、准备同步）若各自直接 `git commit`，存在状态竞争风险。

**规则**：真正落盘到主仓库的 git 写操作，**只能由 daemon 进程串行执行**，工作进程不直接接触主仓库。工作进程验证通过后，通过控制通道发一条"请求合并 `<worktree 分支>`"的消息给 daemon；daemon 维护一个串行队列，同一时刻只跑一个 `StateRepo.apply()`，处理结果（成功 commit hash / 校验失败原因 / 冲突）回传给发起的工作进程。

---

## 5. Self 与"项目工作"的两层关系

| 层级 | 范围 | 内容 | 由谁驱动 |
|---|---|---|---|
| Self 层 | 全局、唯一（每 `AGENT_HOME` 一个） | 自我模型、长期记忆、跨项目 goal backlog、自主循环 tick | daemon 自身 |
| 项目工作层 | 每项目一份，可并发多个 | SessionAgent 池、该项目的 worktree 副本治理、该项目 data_root | 由 Self 调度，或由外部 HTTP 请求直接触发 |

自主循环 tick 时，Self 遍历 `projects_index.json` 中已知的项目，挑选有 backlog 目标的项目，**在该项目对应的工作进程/worktree 副本里执行**——调度主体是全局唯一的 Self，但执行落地仍是项目级的。这解释了"自我要全局、工作要按项目隔离"二者并不矛盾，是上下级关系。

**特别说明**：mini_agent 自身的代码仓库，本身也是 `projects_index.json` 中的"一个项目"（`code_root` = mini_agent 仓库路径）。"mini_agent 自我演化自己的 skill/代码"与"mini_agent 被当作工具去改造用户的其他项目"，走的是**同一套治理机制**，只是 `project_id` 不同——这是统一的，不需要区分两套代码路径。

---

## 6. 角色系统：全局身份 + 项目级权限覆盖

- **全局角色**（`${AGENT_HOME}/users/users.json`）：owner / family / colleague / agent / public，基于 token 区分，与"这是谁"绑定，不跟项目绑定——某用户在任何项目里都是同一个全局身份，不需要逐项目重新认证。
- **项目级权限覆盖**（`${AGENT_HOME}/projects/<id>/roles_override.json`，可选）：同一全局身份，在不同项目可以有不同权限（例如某 colleague 在项目 A 可读写、在项目 B 只读）。判定逻辑：先查全局角色基线，再查该项目是否存在覆盖规则，有则覆盖、无则沿用基线。

与现有 `next_doc/daemon-multiuser-architecture.md` 中"角色权限矩阵"设计完全兼容，只是把作用域从"单 daemon 内"扩展为"全局身份 + 逐项目覆盖"两层。

---

## 7. 全局资源的差异化治理

`${AGENT_HOME}/skills/`（及其他"全局"性质的路径，如未来可能新增的全局 prompt/全局 subagent 定义）在风险分级判定中，统一比同等改动在单项目内部时**至少高一档**：

```python
def resolve_tier(paths, requested_tier, initiator, is_global_resource: bool = False):
    effective = requested_tier
    if is_global_resource:
        effective = _bump_one_level(effective)   # T0→T1, T1→T2，已是 T3 不变
    if any(is_protected_path(p) for p in paths):
        effective = "T3"
    ...
```

原因：全局资源的改动会影响该实例下**所有**项目，风险面天然更大，理应比"只影响单个项目"的同类改动门槛更高。具体判定逻辑作为受保护路径清单旁的一条独立规则实现，不散落在各处特判。

---

## 8. 数据迁移（针对已有项目）

1. **复用 `projects_index`**：现有索引已记录"曾经工作过的 workdir"，迁移脚本据此遍历，无需用户手动列清单。
2. **逐项目搬迁**：`<project_root>/.agent/*` → `${AGENT_HOME}/projects/<project_id>/data/`（`project_id` 用 §3.3 修正后的算法计算）。用"先搬到临时名、成功后 rename"的方式保证原子性，避免中间态。
3. **兼容期标记**：原 `<project_root>/.agent/` 位置留一个极小标记文件 `data_root_redirect.json`，内容是新数据目录的绝对路径，防止未升级完的代码静默地"以为这是个新项目"。
4. **`.gitignore` 补全**：迁移后把 `.agent/` 整体加入 `.gitignore`，不自动重写历史。
5. **备份与回滚**：正式搬迁前先做 dry-run（只打印映射清单），用户确认后执行；执行前对待搬迁目录打包快照到 `${AGENT_HOME}/_migration_backup/<timestamp>/`，失败可直接还原。
6. **Daemon 收编**：若用户机器上同时存在多个旧式 per-project daemon 进程，提示先逐个 `daemon stop`，再启动唯一的全局 daemon；旧项目数据搬迁完成后自动出现在新 daemon 的 `projects_index` 中，不需要重新走注册流程。

---

## 9. 启动语义（用户可见行为）

- **隐式工作目录**：在某项目目录下执行 `mini_agent` / `mini_agent work`，默认以当前 cwd 作为 `code_root`。客户端检查全局 daemon 是否在跑（按 §2.1 解析出的 `AGENT_HOME`），不在则拉起（daemon 本身只拉一次，与项目无关）；daemon 收到请求后用 `code_root` 算出 `project_id`，查 `projects_index`，没有则注册并在 `${AGENT_HOME}/projects/<id>/` 下建好 `data/`，然后拉起一个 worker 进程开始这份工作。
- **显式工作目录**：`mini_agent --workdir /path/to/project` 在任意目录都能执行，效果与隐式一致，只是 `code_root` 来自参数而非 `Path.cwd()`。
- **结果**：同一台机器（同一 `AGENT_HOME`）上，无论在哪个目录、同时处理几个项目，背后永远是同一个 daemon、同一个 Self，只是当前关注的 `project_id` 随请求切换。

---

## 10. 设计决策记录（已确认）

| 问题 | 决策 |
|---|---|
| 跨项目自主任务调度优先级 | 简化版：轮询 + 按 backlog 优先级排序，不在第一阶段做复杂资源仲裁 |
| 子任务升格为独立进程的判定 | 首版规则 `initiator == "autonomous" 且 tier >= T2`，实现为可扩展规则列表（见 §4.2） |
| 控制通道 token 策略 | 与 `AGENT_HOME` 绑定、长期固定，daemon 重启不轮换；仅用户主动要求时手动重置 |
| `roles_override.json` 粒度 | 首版仅项目级整体覆盖，子目录/操作类型级别的细粒度覆盖留待后续按需扩展 |

## 11. 实施阶段建议（顺序，非本次实施范围，方案已确认，待另行启动）

1. `AgentPaths` 双坐标拆分（`code_root` / `data_root`） + `project_id_for` 基准修正
2. 数据域迁移脚本（dry-run → 备份 → 搬迁 → 兼容标记）
3. 全局 daemon 骨架（`AGENT_HOME` 解析、`daemon_info.json` 扩展端口信息、`projects_index` 升级为调度核心结构）
4. Worker 进程拉起与控制通道（回环 TCP + token 认证 + 状态上报/指令下发）
5. git 写入串行化（daemon 端合并队列）
6. 全局技能库 tier 加权规则
7. 角色系统两层化（全局基线 + 项目覆盖）
8. AutonomousLoop 多项目调度改造
