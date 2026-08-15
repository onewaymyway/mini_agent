# 参数系统指南：统一的参数注册与解析机制

> **新增任何 `agent_config.json` 配置项或 CLI 参数之前，先看这份文档。**
> 对应代码：`src/mini_agent/config/param_registry.py`。
> 对应重构记录：本文档所述机制于 2026-08 引入，一并修复了 `autonomy`/
> `observability` 两个配置块"改配置没生效"的历史 bug（详见
> [Goal 执行公平性调度配置](goal-execution-fairness-config.md) 里的说明）。

---

## 1. 这份文档要解决什么问题

重构前，`agent_config.json` 里的配置项有两种历史遗留的读取方式，且第二
种内部风格也不统一：

1. **"nested block"参数**（如 `autonomy.fairness_time_slicing_enabled`、
   `tech_radar.daily_seed_limit`）——概念上应该是"JSON key 与 dataclass
   字段名一一对应，写一次通用遍历代码就够了"。但重构前，`config/
   loader.py` 里只有 `autonomy`/`observability` 两个 block 真的用了这种
   通用遍历，其余十几个 block（`tech_radar`/`goal_mode`/`turn_judge`/
   `cron`/`digest_advisor`/`proprioception`/`affordance`/
   `workdir_knowledge`/`global_knowledge`/`ecosystem_positioning`）都是
   手写的一大段：

   ```python
   # 重构前的写法（现在已不需要这样写了）
   _cron = file_cfg.get("cron") if isinstance(file_cfg.get("cron"), dict) else {}
   cron_cfg = CronConfig(
       max_concurrent_jobs=int(_cron.get("max_concurrent_jobs", 2)),
       default_timeout_seconds=int(_cron.get("default_timeout_seconds", 20 * 60)),
       default_max_steps=int(_cron.get("default_max_steps", 60)),
       inner_max_turns=int(_cron.get("inner_max_turns", 15)),
   )
   ```

   问题：加一个新字段要同时改 `models.py`（加字段）和 `loader.py`（加一
   行手写构造），两处默认值经常不同步；`config_catalog.py`（看板配置
   UI 用的字段目录）又维护了第三份 block 清单。三处要保持一致，全靠人
   工细心，`autonomy`/`observability` 就是活生生的反例——它们的 dataclass
   定义、字段说明文档都写好了，唯独 `loader.py` 里漏了接入 `file_cfg`，
   导致这两个 block 的所有字段（包括 goal 执行公平性调度 P1-P4 的全部
   `fairness_*` 参数）无论配置文件里写什么，永远只生效硬编码默认值——
   这个 bug 存在了相当长时间才被发现。

2. **"flat CLI 参数"**（如 `--memory`/`--workers`/`--rpm`）——CLI flag
   定义（`cli/parser.py`）、优先级合并（`loader.py` 里的
   `_f`/`_fb`/`_fn` 闭包调用）、字段说明（`config_catalog.py` 的
   `_CORE_FIELDS` 等列表）三处独立维护，同一个参数要在三个文件里各改
   一遍。

**本次重构做的事**：把"nested block"这一类（数量最多、后续新增参数最
频繁的场景）收敛成一份唯一的注册表，`loader.py` 和 `config_catalog.py`
都从同一处 import，不再各自维护重复列表；同时为"flat CLI 参数"提供一
套声明式写法，作为**新增**参数的强制规范。

> **更新（`next_doc/flat_nested_config_unification_migration_plan.md`）**：
> 本节最初的结论是"存量的 `_f`/`_fb`/`_fn` 调用点数量大、逐个改造收益
> 低，本次不做存量迁移"，但这个决定的副作用是 `memory`/`compress`/
> `tool_trim`/`skill`/`perception`/`session`/`profile`/`debug`/`http`/
> `retry`/`ensemble` 这 11 个手写 flat block（合计 151 个字段）被冻结在
> "新参数不许走这里、旧参数也没人去改"的状态，连续暴露了同类"字段读不到
> /改不生效"的 bug（详见该迁移计划 §0）。这 11 个 block 现已全部迁移完成
> （见第 8 节），`loader.py` 里不再有针对它们的手写字段级构造代码。

---

## 2. 我该用哪种机制？先看决策树

```
新参数只需要能写进 agent_config.json，不需要 --xxx 命令行覆盖？
├─ 是 → 用「Nested Block 机制」（第 3 节）——这是绝大多数参数的归宿
└─ 否，确实需要 --xxx 命令行覆盖
   ├─ 这个参数所属的功能域已经有 CLI 覆盖逻辑
   │  （reminder / format_correction / privacy / role_agent 等，
   │   特征：loader.py 里对应 block 的构造代码引用了函数参数
   │   如 reminder_enabled / privacy_secrets）
   │  → 在该功能域已有的手写代码块里加一行，风格保持一致（第 5 节）
   └─ 这是全新的、独立的 CLI 参数，不属于任何已有 block
      → 用「Flat ParamSpec 机制」（第 4 节）
```

判断"是否需要 CLI 覆盖"的经验法则：**默认不需要**。CLI flag 应该只
留给"每次运行都可能想临时改一次"的参数（如 `--verbose`、
`--max-turns`）；纯粹的功能开关/阈值调优（`fairness_yield_after_steps`
这种）写进 `agent_config.json` 长期生效即可，不必加 CLI flag——加了反而
是要多维护一处代码、多一种"CLI 和配置文件都能改、优先级容易搞混"的
心智负担。

---

## 3. Nested Block 机制（推荐，绝大多数参数走这里）

### 3.1 给已有 block 加一个新字段

**只需要一步**：在 `config/models.py` 对应的 `@dataclass` 里加字段 +
默认值。

```python
# config/models.py
@dataclass
class AutonomyConfig:
    ...
    my_new_threshold: float = 0.5   # ← 就加这一行
```

完事。原因：`param_registry.load_nested_block()` 用
`dataclasses.fields(cls)` 遍历字段，`agent_config.json` 里
`autonomy.my_new_threshold` 会被自动发现、按字段默认值的类型
（bool/int/float/str）做转换、缺失时自动回退到 dataclass 默认值。
`config_catalog.py`（看板配置只读展示 + PATCH 更新接口）同样通过
`dataclasses.fields()` 自动展示新字段，不需要改。

不需要改 `loader.py`，不需要改 `config_catalog.py`，不需要改
`param_registry.py`。

> **显式 null 的语义**：如果字段默认值不是 `None`（比如
> `WorkflowConfig.approval_wait_timeout_seconds` 默认 `600.0`），但你希望
> 配置文件里显式写 `"approval_wait_timeout_seconds": null` 来表示"取消
> 这个限制"，直接写就行——`load_nested_block()` 对"key 存在且值为
> `null`"统一按 `None` 处理，不受字段默认值类型限制，不需要专门为这个
> 字段写特殊逻辑。

### 3.2 新增一个全新的 block

比"加字段"多两步：

**步骤一**：在 `models.py` 定义新的 `@dataclass`，并在 `AppConfig` 里
加一个对应字段：

```python
# config/models.py
@dataclass
class MyFeatureConfig:
    enabled: bool = False
    param_a: int = 10
    param_b: str = "default"

@dataclass
class AppConfig:
    ...
    my_feature: MyFeatureConfig = field(default_factory=MyFeatureConfig)
```

**步骤二**：在 `config/param_registry.py::_build_nested_blocks()` 里
注册一行：

```python
NestedBlockSpec("my_feature", _m.MyFeatureConfig),
```

`config_catalog.py`（看板配置 UI 的字段目录）里额外维护了一份带
label/icon/敏感字段标记的展示用列表，职责不同不能直接合并成一份，但
模块加载时会自动校验两边同一个 `attr_name` 对应的 dataclass 类型必须
一致——如果你只在其中一处注册了新 block（或改错了类型），import 时会
立刻抛 `AssertionError`，不会等到运行时才发现看板显示的是旧字段。所以
把新 block 加进看板 UI 时，记得同时在 `config_catalog.py::_NESTED_BLOCKS`
里也加一行（label/icon 自己起，dataclass 类必须和 `param_registry.py`
里的一致）。

**步骤三**：在 `config/loader.py` 里，`AppConfig(...)` 构造调用处加一行
`my_feature=_nested_blocks["my_feature"],`（`_nested_blocks =
load_all_nested_blocks(file_cfg, NESTED_CONFIG_BLOCKS)` 已经在
`load_config()` 前面统一调用过一次，直接取值即可，不需要重新加载）。

如果这个新配置类需要被外部代码 `from mini_agent.config import
MyFeatureConfig` 直接引用，记得同时把类名加进
`config/__init__.py` 的 import 列表和 `__all__`（这一步和参数加载机制
无关，是 Python 包重导出的常规要求）。

### 3.3 什么样的 block 不适合走这个机制

只剩 `mcp` 一个真正的例外——`mcp.servers` 是一个由独立 `MCPServerConfig`
组成的列表（每个 server 一份 dataclass，字段结构在列表内还可以互不相同
的可选项），不是"顶层 dict 的 key 直接映射到某个 dataclass 字段"这种
扁平结构，天然不适合本机制，继续在 `loader.py` 里保留专属的列表解析
代码（找 `mcp_server_list` 那一段）。

`reminder`/`format_correction`/`privacy`/`role_agent` 这 4 个 block 的
多数字段已经走通用加载，只有 `enabled`/`verbose` 等少数几个支持 CLI 覆
盖的字段、以及 `privacy.secrets`（配置文件值 + 代码传入值合并去重）、
`role_agent.allow/block`（逗号分隔字符串解析）、`*.custom_dir`/
`agents_dir`（`str → Path`）这几个"通用类型转换机制处理不了"的字段，
仍在 `loader.py` 里用 `apply_overrides()` 做小范围手工覆盖——这是"通用
加载 + 少量手工覆盖"的两段式组合，不是"完全没接入机制"。`env_info` 的
`provider_kwargs` 字段由 `include_hostname`/`include_username` 派生，
通用加载后在 `loader.py` 里单独做一次派生填充。给这些 block **新增字段**
时：如果新字段不需要 CLI 覆盖/特殊转换，直接改 `models.py` 就完事（和
3.1 节完全一样）；只有确实需要 CLI 覆盖或特殊转换时，才需要在
`loader.py` 对应位置的 `apply_overrides(...)` 调用里加一个新的覆盖项。

---

## 4. Flat ParamSpec 机制（仅用于全新的、需要 CLI 覆盖的独立参数）

在 `config/param_registry.py` 的 `FLAT_PARAM_SPECS` 里加一项：

```python
FLAT_PARAM_SPECS = [
    ParamSpec(
        name="my_flag",                 # argparse dest / 内部标识
        json_key="my_flag",             # agent_config.json 顶层 key
        cli_flags=("--my-flag",),       # 不需要 CLI 覆盖就留空 tuple
        env_var="MY_FLAG",              # 可选，没有就传 None
        default=False,
        value_type=bool,
        cli_action="store_true",        # bool 开关常用；数值/字符串参数留空
        help="这个参数是干什么的",
    ),
]
```

然后两处各一行调用：

```python
# cli/parser.py（构建 parser 的地方）
from mini_agent.config.param_registry import FLAT_PARAM_SPECS, register_cli_argument
for _spec in FLAT_PARAM_SPECS:
    register_cli_argument(p, _spec)

# config/loader.py（load_config() 内，组装 AppConfig 之前）
from .param_registry import FLAT_PARAM_SPECS, resolve_flat_param
my_flag_val = resolve_flat_param(file_cfg, args, next(s for s in FLAT_PARAM_SPECS if s.name == "my_flag"))
```

解析优先级固定为 **CLI > agent_config.json > 环境变量 > 默认值**，和
`loader.py` 里其它参数的优先级约定一致，`resolve_flat_param()` 内部已经
实现好，不需要重新写判断逻辑。

> 存量的 `_f`/`_fb`/`_fn` 调用点（`--workers`/`--rpm` 等约 80 个）暂不
> 强制迁移到这套机制——它们已经稳定工作，逐个迁移的重构收益低于风险。
> 但**新增**的 flat CLI 参数一律要求用 `ParamSpec`，不要再手写新的
> `_f("xxx", cli_val, default)` 调用点。

---

## 5. `apply_overrides()` / `env_fallback`：通用加载之外的"小范围手工覆盖"

`reminder`/`format_correction`/`privacy`/`role_agent` 这 4 个 block 已经
走通用加载（第 3 节），但各自有少数几个字段额外支持 CLI 覆盖，或者需要
通用类型转换机制处理不了的转换（`str → Path`、逗号分隔字符串解析、
"配置文件值 + 代码传入值合并去重"）。这类字段不再需要手写 `bool()`/
`int()` 转换，而是用两个辅助函数在通用加载结果之上做小范围覆盖：

**`apply_overrides(instance, **overrides)`**：拿通用加载好的 dataclass
实例，覆盖其中几个字段，`overrides` 里值为 `None` 的项会被跳过（表示
"这次没有更高优先级的值，保持通用加载结果不变"）。典型用法（以
`format_correction` 为例，完整代码见 `loader.py`）：

```python
format_correction_cfg = apply_overrides(
    _nested_blocks["format_correction"],
    enabled=format_correction_enabled,           # CLI 参数，可能是 None
    max_retries_per_turn=format_correction_max_retries,
    verbose=format_correction_verbose,
)
```

`enabled`/`max_retries_per_turn`/`verbose` 这 3 个字段：CLI 传了就用 CLI
值，没传（`None`）就保留通用加载已经算好的"配置文件值或默认值"，效果上
等价于"CLI 参数 > 配置文件 > 默认值"三级优先级，但不需要为每个字段重复
写一遍 if-else。字段需要"配置文件值 + 代码传入值合并"（如
`privacy.secrets`）或"字符串转 Path/解析成列表"（如
`role_agent.agents_dir`/`allow`）时，先手动算出最终值，再传给
`apply_overrides()`，其余没有特殊逻辑的字段完全不用碰。

**`load_nested_block(block, cls, env_fallback={...})`**：给 `web_search`
这类"配置文件 > 环境变量 > 默认值"三级回退的 block 用，`env_fallback`
是 `{字段名: 环境变量名}` 映射，只对列出的字段生效，其它字段行为不变。

给这 5 个 block（含 `env_info`/`workflow`）新增字段时：不需要 CLI 覆盖/
环境变量回退/特殊转换的字段，直接改 `models.py` 就完事，和第 3.1 节
完全一样；只有确实需要这些特殊处理时，才去 `loader.py` 里对应的
`apply_overrides(...)`/`env_fallback={...}` 调用里加一项。

---

## 6. 验证新参数确实生效

新加一个字段后，建议本地跑一次最小验证（不需要起完整 agent）：

```python
import json, tempfile, os
from mini_agent.config.loader import load_config

d = tempfile.mkdtemp()
with open(os.path.join(d, "agent_config.json"), "w") as f:
    json.dump({"autonomy": {"my_new_threshold": 0.9}}, f)
os.chdir(d)
cfg = load_config()
assert cfg.autonomy.my_new_threshold == 0.9
```

`tests/test_goal_execution_fairness_p4.py` 和
`tests/test_goal_execution_fairness.py` 也可以作为"某个 nested block 字
段确实从配置文件生效"的参考测试写法。

---

## 7. 存量 flat block 迁移（已完成，`flat_nested_config_unification_migration_plan.md`）

`memory`/`compress`/`tool_trim`/`skill`/`perception`/`session`/`profile`/
`debug`/`http`/`retry`/`ensemble` 这 11 个历史遗留的手写 flat block，已经
全部迁移成"nested 优先、旧 flat key 兼容兜底"的写法，`loader.py` 里不再
有针对它们的手写字段级构造代码。

### 7.1 新写法（推荐）

```json
{
  "memory": {"enabled": true, "top_k": 5},
  "skill": {"semantic_enabled": true, "compact_budget": 30000}
}
```

跟第 3 节的 nested block 写法完全一样，字段名与 `models.py` 里对应
dataclass 的字段名一一对应，新增字段只需要改 `models.py`。

### 7.2 旧写法（仍然兼容，但不建议新配置继续使用）

```json
{"memory_enabled": true, "memory_top_k": 5, "skill_semantic_enabled": true}
```

这批旧 flat key 通过 `param_registry.load_nested_block_with_flat_compat()`
继续读取，避免存量 `agent_config.json` 升级后配置失效。

### 7.3 优先级

**nested 写法 > 旧 flat key > CLI/命令行参数（如果该字段有 CLI 覆盖）
> dataclass 默认值** —— 注意 CLI 参数优先级实际上比 nested/flat 都高
（跟第 5 节 `apply_overrides()` 的两段式组合顺序一致：先算出通用加载的
结果，再用非 `None` 的 CLI 值覆盖）；同一个字段如果 nested 和旧 flat key
同时写了不同的值，nested 写法生效。

### 7.4 给这 11 个 block 新增字段

跟第 3.1 节完全一样：只改 `models.py` 即可，不需要碰 `loader.py`。只有
这个新字段还需要一个"历史遗留的旧 flat key"时（这种情况应该越来越少，
新字段本来就不该有旧 flat key），才需要去 `loader.py` 里对应
`load_nested_block_with_flat_compat(...)` 调用的 `flat_key_map={...}`
参数里补一项 `"字段名": "旧flat key"`。

### 7.5 已知的历史遗留不一致（迁移时特意保留，不在本次范围内修正）

- `compress.strategy`：迁移前的代码里，只要 `agent_config.json` 没写
  `auto_compress_strategy` 这个 flat key，就无条件用硬编码的
  `"turn_aligned"` 作为默认值（而不是 `CompressConfig.strategy` 的
  dataclass 默认值 `"compact_with_skills"`），且这个硬编码值会覆盖掉
  nested 写法 `{"compress": {"strategy": ...}}` 里配置的值。为了保证
  "迁移前后行为一致"，`loader.py` 里原样保留了这个怪癖（见对应代码注释）。
  如果后续要修正为遵循 `CompressConfig.strategy` 的 dataclass 默认值，
  需要单独排期、单独评估对存量用户的影响，不属于本次迁移范围。

### 7.6 看板 UI（`config_catalog.py`）

这 11 个 block 在看板配置管理界面里的 `json_key` 已经从 `"memory_top_k"`
这种旧 flat key 形式，改成了 `"memory.top_k"` 这种与 nested 写法一致的
形式（PATCH 写回时统一写 nested 形式）。副作用：如果存量
`agent_config.json` 里只写了旧 flat key、没有对应的 nested block，看板上
会显示这些字段"未显式配置过"（`customized: false`）——这只影响这一个
展示态，不影响 `load_config()` 实际读到的值（旧 flat key 依然通过
兼容层生效）。

---

## 8. 相关文档

- [配置系统指南](config-guide.md) — 配置架构总览、子配置块详解、加载优先级
- [Goal 执行公平性调度配置](goal-execution-fairness-config.md) — `AutonomyConfig.fairness_*` 字段的完整业务语义说明（本机制的典型使用案例）
- [看板配置管理](kanban-config-management.md) — `config_catalog.py`（看板 UI 只读展示 + PATCH 更新）如何复用 `NESTED_CONFIG_BLOCKS`
- [flat / nested 配置写法统一迁移计划](../next_doc/flat_nested_config_unification_migration_plan.md) — 第 7 节所述 11 个 block 的迁移方案与验收记录

---

*创建：2026-08，引入 `config/param_registry.py` 统一参数注册与解析机制；
同批完成 `reminder`/`format_correction`/`privacy`/`role_agent`/
`env_info`/`workflow`/`web_search` 全部迁移，`loader.py` 里手写的嵌套
block 构造代码只剩 `mcp.servers` 一处。迁移 `workflow` 时顺带发现并修复
了 `max_total_tokens`/`session_to_workflow_enabled`/
`condition_static_check_enabled`/`dry_run_preview_on_generate`/
`git_hint_enabled` 5 个字段此前从未被 `loader.py` 读取的 bug（与
`autonomy`/`observability` 曾经的问题同源）。*
