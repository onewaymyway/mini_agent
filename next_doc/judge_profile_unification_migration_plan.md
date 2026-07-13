# 判官接线统一为 profile 驱动：配置迁移子方案

> 状态：**设计稿，未开始任何代码修改**，对应
> `judge_unification_design.md` 阶段六的要求——"必须单独出一份更细的
> 迁移方案（含配置迁移脚本、旧配置兼容期），不建议和前面几个阶段一起做"。
> 本文档就是这份细化方案，需要你确认第 8 节列出的开放问题后再动手实现。
>
> 阶段一～五（`StuckDetector` 抽取 / `RoleFeedback` 接入 / `spawn_judge_agent`
> 工厂 / `auto_quarantine` 上报统一 / 结构化 JSON 判定输出）已经完成并全量
> 测试通过，本文档只涉及阶段六本身。

---

## 1. 背景：现状盘点（精确到文件/函数级别）

### 1.1 两条互不相通的接线路径

| | evaluator / coach（含自定义角色） | goal_judge / turn_judge |
|---|---|---|
| Profile 来源 | `.agent/agents/*.md` 文件，`AgentProfileLoader` 加载 | 无 profile，硬编码在调用方 |
| 触发注册表 | `RoleAgentDispatcher._output_roles` / `_tool_roles`（按 `trigger_on` 分类） | 无注册表 |
| 谁决定要不要跑 | `dispatcher.has_output_roles` / `has_tool_roles` | `goal_mode/runner.py::_run_judge`（每轮硬调用）／`agent/role_judge.py::_maybe_run_turn_judge`（每轮硬调用） |
| 调用方式 | `dispatcher.trigger_output(...)` / `dispatcher.trigger_tool_use(...)` | 直接 `from mini_agent.role_agents.goal_judge import run_goal_judge` /
`from mini_agent.role_agents.turn_judge import run_turn_judge` |
| 开关 | `cfg.role_agent.enabled` + `allow`/`block` 白黑名单 | `cfg.goal_mode.enabled` / `cfg.turn_judge.enabled`（各自独立总开关，且各自还有一大堆专属子配置：`judge_model`/`max_rounds`/`judge_tools_enabled`/`consecutive_same_feedback_limit`/`persist_state`/… 详见 `config/models.py` 的 `GoalModeConfig`/`TurnJudgeConfig`） |

**关键点**：`goal_mode`/`turn_judge` 的配置块不是"总开关 + 通用过滤"这种
简单形状，而是各自一整套专属子系统配置（安全阀阈值、状态持久化、工具
权限……）。这些字段**不是本次迁移的对象**——阶段六要统一的只是"触发
路径"这一层（谁来决定要不要调用、什么时候调用），不是要把这两个子系统
的全部配置都塞进 `RoleAgentConfig` 或推翻重做。

### 1.2 `RoleAgentDispatcher` 的构造条件——一个必须先解决的兼容性风险

```python
# src/mini_agent/cli/app.py:326-328
from mini_agent.role_agents import init_role_agent_system
if cfg.role_agent.enabled:
    role_sys = init_role_agent_system(cfg, profile_loader)
```

`RoleAgentDispatcher` **只有在 `cfg.role_agent.enabled == True` 时才会被
构造**；否则 `get_dispatcher()` 全程返回 `None`（`role_agent.enabled` 默认
`False`）。

这意味着：如果阶段六天真地把 `goal_mode/runner.py` 和 `role_judge.py`
改成"向 dispatcher 查询该 trigger 下注册了哪些判官"，那么**任何只开了
`goal_mode.enabled=true`/`turn_judge.enabled=true`、但没有额外打开
`role_agent.enabled=true` 的现有用户**（这是完全合理、大概率存在的组合——
两个子系统当前互相独立，没有理由认为用户会因为想用 Goal 模式就顺便打开
泛化的 evaluator/coach 系统），会在升级后遭遇 **`get_dispatcher()` 返回
`None` → 判官完全不再触发** 的静默功能回归，且没有任何报错提示。

这是本次迁移**最高优先级、必须在动代码前定案**的风险点，详见第 3.2 节的
解决方案。前面阶段一～五之所以能做到"零回归"，正是因为都没有触碰任何
既有的启用条件；阶段六如果不先解决这一点，会直接违反"阶段五～六：
接口级变化"这条verification 策略里"新旧路径共存期间需要专项回归用例"
的要求。

### 1.3 `trigger_on` 现有取值与命名冲突排查

`AgentProfile.trigger_on` 目前的合法取值（`dispatcher.py` 文档字符串 +
`_discover()` 实现）：

```
"output"          → 主 Agent 完成整个 turn 的输出后（串行修订循环，evaluator 典型用法）
"turn_end"        → turn 结束时，当前实现是 "output" 的别名（两者归到同一个 _output_roles 桶）
"tool_use:<name>" → 特定工具调用完成后（coach 典型用法）
```

阶段六设计草案提出新增 `goal_review` / `turn_end_review` 两个值。这里有
一个**必须先排掉的命名冲突**：现有的 `"turn_end"` 和新提议的
`"turn_end_review"` 长得非常像，但语义完全不同——

- `"turn_end"`：现在是 `"output"` 的别名，触发的是 evaluator/coach 那种
  **主 Agent 输出后、还没决定要不要交还用户之前**的质量修订循环，运行
  时机比 TurnJudge 更早（在 `turn_loop.py` 里，`_run_role_agents_output`
  先跑，然后才是 TurnEnd hook，再然后才是 `_maybe_run_turn_judge`）。
- `"turn_end_review"`（新提议）：对应 TurnJudge，触发时机是**主 Agent
  输出 + evaluator/coach 修订都跑完、TurnEnd hook 也没接管之后**，判断
  "这轮到底要不要交还真人"。

另外，`hooks.py` 里还有一个同名但完全独立的概念：hook 系统的 `"TurnEnd"`
事件（`_hook_mgr._all_specs("TurnEnd")`，见 `agent/turn_loop.py`），是给
外部脚本/进程用的钩子机制，和 `trigger_on` 字段是两套不相关的系统，只是
恰好都用了"turn end"这个词。

现状是**三个同名却不同义的"turn end"概念**同时存在：
`trigger_on: turn_end`（现有别名，语义=output）、hooks 的 `TurnEnd` 事件
（外部钩子）、以及提议中的 `turn_end_review`（=TurnJudge）。这已经是
一个容易踩坑的命名债，建议阶段六实现时一并处理，具体方案见 §3.4。

---

## 2. 本次迁移的边界（明确不做什么）

- **不**改变 `goal_mode`/`turn_judge` 现有的任何子配置字段（`judge_model`/
  `max_rounds`/`consecutive_same_feedback_limit`/`judge_tools_enabled`/
  `persist_state` 等全部原样保留，含义不变）。
- **不**要求现有用户修改任何配置文件才能保持当前行为——升级后
  `goal_mode.enabled=true` + 其它配置不变的用户，行为必须与升级前完全
  一致（这是"零迁移成本"的硬性要求，而不是"提供迁移脚本降低成本"）。
  因此本文档标题虽然是"配置迁移方案"，但设计目标是**尽量不需要用户
  做任何迁移动作**；只有当用户主动想利用新能力（比如用
  `role_agent.block: ["goal_judge"]` 屏蔽内建判官）时才涉及新配置项。
- **不**把 goal_judge/turn_judge 的 prompt/model 解析逻辑改道
  `render_profile_prompt`/`spawn_named_agent` 那一套（自定义子 agent 的
  参数化模板机制）——它们继续用 `judge_factory.spawn_judge_agent` +
  `prompts/system/goal_judge.md`/`turn_judge.md`，profile 只是"注册与
  触发"这一层的统一，不是"实现"这一层的统一。

---

## 3. 核心设计决策

### 3.1 三层开关如何共存（不合并，明确分工）

维持三层开关**各自独立、语义不重叠**，不做合并：

```
cfg.goal_mode.enabled     — 子系统总开关：/goal 命令是否存在、GoalRunner
                             这一整套机制是否可用（和触发方式无关）
cfg.turn_judge.enabled    — 子系统总开关：TurnJudge 这一整套机制是否可用
cfg.role_agent.allow/block — 精细化开关：在子系统已启用的前提下，具体某个
                             判官 profile（内建的 goal_judge/turn_judge，
                             或任何自定义 profile）要不要真正生效
```

**最终触发判定**（以 goal_judge 为例，turn_judge 同理）：

```
goal_judge 真正触发
  = cfg.goal_mode.enabled
    AND "goal_judge" not in cfg.role_agent.block
    AND (cfg.role_agent.allow is empty OR "goal_judge" in cfg.role_agent.allow)
```

这样两层开关各管各的边界：关掉 `goal_mode.enabled` 时，`/goal` 命令和
`GoalRunner` 整套机制都不存在，讨论"要不要触发 goal_judge"这件事本身
就无意义；`goal_mode.enabled=true` 时，`role_agent.allow/block` 才开始
起精细化过滤作用（默认空列表，等价于"不过滤"，向后兼容）。

**不采用**的替代方案：把 `goal_mode.enabled`/`turn_judge.enabled` 完全
废弃、只用 `role_agent.allow/block` 控制。理由：
1. 这两个专属开关承载的语义比"是否触发某个判官"更广（比如
   `goal_mode.enabled=false` 时 `/goal` 命令本身应该报错提示未启用，
   而不是"命令存在但触发不了判官"这种更confusing 的中间态）；
2. 会构成一次真正的破坏性配置变更（所有现有 `agent_config.json` 里的
   `goal_mode.enabled`/`turn_judge.enabled` 字段语义作废），且没有
   对应收益——精细化过滤本来就该是 allow/block 的职责，没必要为了
   "统一成一层开关"牺牲现有配置的稳定性。

### 3.2 `RoleAgentDispatcher` 构造条件改造（解决 §1.2 的风险）

`RoleAgentDispatcher` 的构造条件从：

```python
if cfg.role_agent.enabled:
    role_sys = init_role_agent_system(cfg, profile_loader)
```

改为：

```python
if cfg.role_agent.enabled or cfg.goal_mode.enabled or cfg.turn_judge.enabled:
    role_sys = init_role_agent_system(cfg, profile_loader)
```

即：**只要有任何一个需要 dispatcher 的子系统被启用，dispatcher 就必须
存在**——dispatcher 从"role_agent 专属对象"升级为"判官触发的公共基础
设施"，`role_agent.enabled` 只是众多"是否需要 dispatcher"的条件之一，
不再是唯一条件。

配套地，`RoleAgentDispatcher._discover()` 里发现自定义 profile
（`.agent/agents/*.md` 里 `role_type` 非空的普通 evaluator/coach/custom）
这部分逻辑，需要继续单独受 `cfg.role_agent.enabled` 门控——`goal_mode.
enabled=true` 但 `role_agent.enabled=false` 的用户，只应该获得内建
goal_judge 的触发能力，**不应该**意外激活其磁盘上定义的自定义
evaluator/coach（那是两件不相关的事，用户没有主动打开
`role_agent.enabled` 就不该生效）。也就是说 `_discover()` 内部要按来源
分别判断：

```python
def _discover(self) -> None:
    ra_cfg = self._cfg.role_agent

    # 磁盘上的自定义 profile：仍然完全受 role_agent.enabled 门控
    if ra_cfg.enabled:
        for name in self._loader.available:
            ...（现有逻辑不变：allow/block 过滤 + 按 trigger_on 分类）

    # 内建判官 profile：分别受各自子系统的开关门控，
    # 且始终额外经过 allow/block 过滤（哪怕 role_agent.enabled=False）
    for profile in get_builtin_profiles(self._cfg):
        name = profile.name
        if allow_set and name not in allow_set:
            continue
        if name in block_set:
            continue
        if profile.trigger_on == "goal_review":
            self._goal_review_roles.append(profile)
        elif profile.trigger_on == "turn_end_review":
            self._turn_end_review_roles.append(profile)
```

（`allow_set`/`block_set` 无论 `role_agent.enabled` 是否为 True 都会计算，
因为 allow/block 是"精细化开关"，语义上不依赖 `role_agent.enabled`
这个"是否加载自定义 profile"的总闸。）

### 3.3 内建 profile 合成机制（不写 `.md` 文件，磁盘同名 profile 可覆盖）

新增 `role_agents/builtin_profiles.py`：

```python
def get_builtin_profiles(cfg: "AppConfig") -> list["AgentProfile"]:
    """按当前配置合成内建判官 profile（goal_judge / turn_judge）。
    只有对应子系统 enabled 时才会被合成到列表里；system_prompt 留空，
    这样 judge_factory.spawn_judge_agent 会继续 fallback 到
    prompts/system/goal_judge.md / turn_judge.md（除非磁盘上存在同名
    的自定义 profile 文件，见下文覆盖规则）。
    """
    profiles = []
    if cfg.goal_mode.enabled:
        profiles.append(AgentProfile(
            name="goal_judge", role_type="goal_judge", trigger_on="goal_review",
            model=cfg.goal_mode.judge_model, provider=cfg.goal_mode.judge_provider,
            tools=list(cfg.goal_mode.judge_allowed_tools) if cfg.goal_mode.judge_tools_enabled else [],
            tool_groups=list(cfg.goal_mode.judge_allowed_tool_groups) if cfg.goal_mode.judge_tools_enabled else [],
        ))
    if cfg.turn_judge.enabled:
        profiles.append(AgentProfile(
            name="turn_judge", role_type="turn_judge", trigger_on="turn_end_review",
            model=cfg.turn_judge.judge_model, provider=cfg.turn_judge.judge_provider,
        ))
    return profiles
```

**磁盘同名 profile 覆盖规则**：如果 `.agent/agents/goal_judge.md`（或
`turn_judge.md`）真实存在，`_discover()` 里"内建 profile"这一步应该
**跳过**已经被磁盘 profile 占用的名字（磁盘优先，和 `AgentProfileLoader`
现有的"后加载覆盖先加载"精神一致），把选择权交给愿意深度定制的用户
（比如想完全自定义 GoalJudge 的 system prompt/model，不想被
`prompts/system/goal_judge.md` 限制）。这属于新增的高级能力，不影响
默认行为。

**热重载（hot reload）注意事项**：`AgentProfileLoader.rediscover()`
只重新扫描磁盘、重建自己的 `_all`；`get_builtin_profiles(cfg)` 每次
`RoleAgentDispatcher._discover()` 调用时都重新按当前 `cfg` 合成（开销
可忽略——只是几个 dataclass 构造），所以 `dispatcher._discover()` 本身
需要在热重载时被重新调用一次（而不是只调用 `loader.rediscover()`），
才能保证"运行时通过 `/turnjudge`/`/goal` 相关命令切换开关"之类的场景
正确反映到 `_goal_review_roles`/`_turn_end_review_roles`。这属于实现
细节，标记在 §6 的实施清单里。

### 3.4 命名冲突处理（对应 §1.3）

**决定**：保留现有 `trigger_on: turn_end`（output 别名）不变——它已经是
公开文档化的取值，改名或废弃属于不必要的破坏性变更，且目前没有任何
实际 profile 在使用它（`grep` 结果显示只有 `dispatcher.py` 自己的分类
逻辑和文档字符串提到它，`.agent/agents/*.md` 里没有一个用到），风险
可控。

为了避免和新的 `turn_end_review` 混淆，新增值采用更明确的命名：
**`turn_end_review`**（保留原设计草案的名字，但要求实现和文档里必须
显式对照说明与 `turn_end`/hooks `TurnEnd` 的区别——即本文档 §1.3 那张
对照表，原样搬进最终的 `docs/role-agents-guide.md` 更新里），不额外
改名，理由：
1. 改名（比如 `pre_return_judge`）收益有限，因为命名冲突的根源是
   "turn end"这个短语本身描述的时间点确实相似（都在轮次结束附近），
   换个词不解决"容易搞混"的根本问题，不如靠清晰的文档说明来区分；
2. 设计草案里 `goal_review`/`turn_end_review` 的命名和
   `get_goal_review_roles()`/`get_turn_end_review_roles()` 已经形成了
   对称的命名习惯，贸然改一个会破坏这种对称性。

如果你更倾向于强制改名以从根本上避免混淆，这是本文档 §8 的开放问题之一。

### 3.5 `RoleAgentDispatcher` 新增接口

```python
@property
def has_goal_review_roles(self) -> bool:
    return bool(self._goal_review_roles)

@property
def has_turn_end_review_roles(self) -> bool:
    return bool(self._turn_end_review_roles)

def get_goal_review_roles(self) -> list["AgentProfile"]:
    """返回当前注册的 goal_review 判官 profile 列表（通常只有一个
    内建 goal_judge，但保留多个的可能性，方便未来支持自定义 goal 判官）。"""
    return list(self._goal_review_roles)

def get_turn_end_review_roles(self) -> list["AgentProfile"]:
    return list(self._turn_end_review_roles)
```

**是否支持"多个"goal_review/turn_end_review 判官同时注册？** 当前
`goal_mode/runner.py`/`role_judge.py` 的状态机（DONE/CONTINUE/NEED_COMPACT
这种单一状态驱动外层循环）设计上只期待**一个**判定结果。如果允许多个
判官同时注册在同一个 trigger 上，"多个 JudgeVerdict 冲突时以谁为准"
是一个新问题，本次不展开设计——**实现上先只取列表第一个 profile**
（`get_goal_review_roles()[0]` if non-empty），多判官协同的场景（比如
多个维度分别核查再汇总）留作未来的独立设计课题，接口保留 `list` 返回
类型是为了不因为"当前只用第一个"而在未来又要改一次函数签名。

### 3.6 `goal_mode/runner.py` / `agent/role_judge.py` 调用方式改造

现有：
```python
from mini_agent.role_agents.goal_judge import run_goal_judge, build_goal_judge_prompt
...
raw = run_goal_judge(profile=profile, base_cfg=self._cfg, ...)
```

改造后（`profile` 不再是 runner.py 自己现场用 `AgentProfile(name="goal_judge", ...)`
拼一个临时对象，而是从 dispatcher 查询）：
```python
from mini_agent.role_agents import get_dispatcher
from mini_agent.role_agents.goal_judge import run_goal_judge, build_goal_judge_prompt

dispatcher = get_dispatcher()
goal_review_roles = dispatcher.get_goal_review_roles() if dispatcher else []
if not goal_review_roles:
    # goal_mode.enabled=true 但 goal_judge 被 role_agent.block 屏蔽了，
    # 或 dispatcher 因为某种原因未初始化——保守起见不能跳过核查直接判 DONE，
    # 这里的处理策略是本文档 §8 的开放问题之一（见该节第 3 条）
    ...
profile = goal_review_roles[0]
raw = run_goal_judge(profile=profile, base_cfg=self._cfg, ...)
```

`run_goal_judge`/`run_turn_judge` 函数本身**签名和内部实现完全不变**——
它们不关心 `profile` 是 runner.py 现场拼的还是从 dispatcher 查来的，
唯一的区别是 `profile` 现在有一个真实存在的"注册来源"，可以被
`role_agent.block` 屏蔽。

---

## 4. 配置兼容矩阵（升级前后行为对照）

| 现有配置组合 | 升级前行为 | 升级后行为（本方案） | 是否需要用户改配置 |
|---|---|---|---|
| `goal_mode.enabled=true`，其余默认（`role_agent.enabled=false`） | GoalJudge 正常触发 | **不变**：dispatcher 因 §3.2 的改造而被构造，`get_builtin_profiles` 合成 goal_judge，`allow`/`block` 默认空不过滤 | 否 |
| `turn_judge.enabled=true`，其余默认 | TurnJudge 正常触发 | **不变**，同上 | 否 |
| `goal_mode.enabled=true` + `role_agent.enabled=true` + 用户自定义了 `evaluator`/`coach` | 两套机制并行触发，互不干扰 | **不变**：`_discover()` 分别处理磁盘 profile（受 `role_agent.enabled` 门控）和内建 profile（受各自子系统开关门控），互不影响 | 否 |
| `goal_mode.enabled=true` + `role_agent.block: ["goal_judge"]`（新用法） | （`role_agent.block` 目前不影响 goal_judge，因为根本没走 dispatcher） | GoalJudge 被屏蔽，`/goal` 命令仍然存在，但每轮不再核查（见 §8 开放问题 3 关于"屏蔽后如何兜底"的处理策略） | 是（这是主动使用新能力，不是被迫迁移） |
| 用户在 `.agent/agents/goal_judge.md` 自建同名 profile（新用法） | 该文件此前被 `AgentProfileLoader` 加载但从未被使用（`goal_judge` role_type 不会被任何现有触发路径消费，等于一个死文件） | 该文件会**覆盖**内建 goal_judge profile（磁盘优先，见 §3.3），实现完全自定义的 GoalJudge | 是（主动定制） |

**结论**：对"什么都不改、只是安装了新版本"的用户，矩阵前三行覆盖的
主流场景行为完全不变，不存在"配置迁移脚本"意义上的强制迁移动作。
"迁移方案"这个说法主要体现在**新旧触发路径共存期间的灰度验证**
（见 §6），而不是"用户需要手动改 JSON 配置文件"。

---

## 5. 灰度上线策略

按设计草案要求"先只对 GoalJudge 生效，TurnJudge 保持旧路径一段时间
观察稳定性，确认无误后再切换"，具体拆成两个子阶段：

**阶段 6a（GoalJudge 切换 + 基础设施搭建）**
- 完成 §3.2（dispatcher 构造条件改造）、§3.3（`builtin_profiles.py`）、
  §3.5（dispatcher 新增接口，两个都实现，为 6b 做准备）
- `goal_mode/runner.py` 切换到走 dispatcher 查询
- `agent/role_judge.py::_maybe_run_turn_judge` **暂不改动**，继续用现有
  的硬编码 `run_turn_judge` 调用路径（`turn_end_review` 的注册表虽然已经
  实现，但 `role_judge.py` 先不消费它）
- 全量测试 + 针对 §7 列出的新场景补专项测试
- 观察期：建议至少跑完一轮真实使用（比如你自己用 Goal 模式跑几个真实
  任务），确认 `role_agent.block`/`allow` 能正确影响 goal_judge、且
  默认配置下行为和升级前一致

**阶段 6b（TurnJudge 切换）**
- 确认 6a 观察期无异常后，再把 `agent/role_judge.py` 切换到走
  `dispatcher.get_turn_end_review_roles()`
- 复用 6a 已经搭好的基础设施，改动量应该显著小于 6a

这个拆分本身也是"降低单次改动风险面"的手段，不需要额外的 feature flag
开关（GoalJudge/TurnJudge 各自的 `enabled` 开关已经是天然的隔离边界）。

---

## 6. 实施清单（供 6a/6b 各自的编码阶段对照）

- [ ] `role_agents/builtin_profiles.py`：新增 `get_builtin_profiles(cfg)`
- [ ] `role_agents/dispatcher.py`：
  - `_discover()` 拆分为"磁盘 profile（受 role_agent.enabled 门控）"+
    "内建 profile（受各自子系统开关门控，但始终经过 allow/block 过滤）"
    两段逻辑
  - 新增 `_goal_review_roles`/`_turn_end_review_roles` 两个内部列表
  - 新增 `has_goal_review_roles`/`has_turn_end_review_roles` property
  - 新增 `get_goal_review_roles()`/`get_turn_end_review_roles()` 方法
  - 处理热重载：确认现有的 hot-reload 钩子会重新调用
    `dispatcher._discover()` 而不只是 `loader.rediscover()`（需要先确认
    `hot_reload_guide.md` 描述的现有机制里 dispatcher 是否已经在
    重新发现的调用链上，如果没有需要补上）
- [ ] `cli/app.py`：dispatcher 构造条件改为
  `cfg.role_agent.enabled or cfg.goal_mode.enabled or cfg.turn_judge.enabled`
- [ ] `goal_mode/runner.py::_run_judge`：改为查询
  `dispatcher.get_goal_review_roles()`，处理"列表为空"的兜底策略
  （取决于 §8 开放问题 3 的确认结果）
- [ ]（6b）`agent/role_judge.py::_maybe_run_turn_judge`：同上，改为查询
  `dispatcher.get_turn_end_review_roles()`
- [ ] 文档：`docs/role-agents-guide.md` 新增"内建判官如何接入 dispatcher"
  一节（含 §1.3 的命名区分表）；`docs/goal-mode-guide.md`/
  `docs/turn-judge-guide.md` 补充"如何用 `role_agent.block` 屏蔽内建
  判官"、"如何用 `.agent/agents/goal_judge.md` 自定义 GoalJudge"的用法说明

---

## 7. 需要补充的专项测试（对应总体验证策略里"接口级变化需要专项回归"的要求）

- `role_agent.enabled=false` + `goal_mode.enabled=true`：GoalJudge 必须
  正常触发（回归 §1.2 发现的兼容性风险，这是最重要的一条）
- `role_agent.enabled=false` + `turn_judge.enabled=true`：TurnJudge
  必须正常触发（6b 阶段补充）
- `role_agent.block: ["goal_judge"]`：GoalJudge 必须被屏蔽，且
  `GoalRunner.run()` 在这种情况下的行为符合 §8 开放问题 3 确认的策略
- `role_agent.allow: ["some_other_profile"]`（不含 `"goal_judge"`）：
  同上应被屏蔽（allow 非空且不包含时的语义）
- 磁盘存在 `.agent/agents/goal_judge.md` 自定义同名 profile：验证
  该文件的 `model`/`system_prompt` 生效，内建的默认配置不再使用
- `goal_mode.enabled=true` 且 `role_agent.enabled=true` 且用户额外
  自定义了 `evaluator`：两者互不干扰，各自独立触发
- 热重载场景：运行时通过命令切换 `goal_mode`/`turn_judge`/
  `role_agent.block` 后，下一次触发能反映最新状态（不需要重启进程）

---

## 8. 需要你确认的开放问题

1. **§3.4 命名方案**：新 trigger_on 值保留原设计草案的 `goal_review`/
   `turn_end_review`（配合文档做区分说明），还是要求改一个更不容易和
   现有 `turn_end`（output 别名）/ hooks `TurnEnd` 事件混淆的名字？
   本文档默认建议"不改名，靠文档区分"，但这是个纯命名决策，想听你的
   偏好。

2. **§3.1 三层开关的分工**：本文档建议 `goal_mode.enabled`/
   `turn_judge.enabled` 继续独立存在、`role_agent.allow/block` 只做
   精细化过滤，不做任何合并/废弃。是否同意这个方向？（如果你其实
   希望借这次机会把配置进一步简化/合并，需要另外讨论，因为那属于
   比本文档范围更大的破坏性变更。）

3. **`role_agent.block` 屏蔽了 goal_judge/turn_judge 后的兜底策略**：
   `goal_mode.enabled=true` 但 `"goal_judge"` 被 `block` 掉时，
   `GoalRunner.run()` 每轮拿不到任何 goal_review profile，这时候应该：
   - (a) 直接判定为 `CONTINUE`（相当于"核查不了，保守认为还没做完"，
     但会导致 `max_rounds` 跑满才停，因为永远拿不到 DONE）；
   - (b) 直接判定为 `DONE`（相当于"没人来核查，就当主 Agent 自己说了算"，
     风险是绕过了整个验收机制）；
   - (c) 视为配置错误，`GoalRunner.run()` 启动时就报错拒绝执行
     （类似现有 `goal_spec.confirmed=False` 时直接 `raise ValueError`
     的处理方式），提示用户"`goal_mode.enabled=true` 但 goal_judge
     已被 block，请检查配置"。

   本文档倾向于 **(c)**：这种组合本身就是自相矛盾的配置（开了 Goal
   模式却把唯一的验收判官拉黑），与其运行时静默降级成某个可能出乎
   意料的行为，不如启动时就明确报错，让用户自己决定到底想要哪种。
   但这是一个行为选择，需要你确认。TurnJudge 同理（`turn_end_review`
   列表为空时，本文档倾向于保守回退到"当作 TurnJudge 未启用"，即
   `NEED_USER`/直接不触发，因为 TurnJudge 本来就有"任何异常都保守回退
   到等待真人输入"的既定原则——这一条风险明显低于 GoalJudge 的情形，
   分歧较小，但一并列出供确认）。

4. **是否要在 6a 就把"多个 goal_review 判官协同"的场景一起设计**，
   还是按 §3.5 的建议先支持单个、把多判官协同标记为独立的未来课题？
   本文档倾向于后者（降低这一阶段的设计面）。

---

## 9. 风险与回滚

- 本方案不涉及任何持久化数据格式变更（`goal_state.json`/
  `agent_config.json` 结构不变），回滚只需要恢复代码到阶段五末尾的
  版本即可，不存在"数据已经按新格式写入、无法回退"的问题。
- 唯一的运行时行为差异集中在 §3.2 的 dispatcher 构造条件改动——如果
  上线后发现有未预料到的副作用（比如某个依赖"`role_agent.enabled=false`
  时 dispatcher 一定是 `None`"这个前提的现有代码路径被打破），可以
  单独回退这一处改动，不影响阶段一～五已经上线的部分。
