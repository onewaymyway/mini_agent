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
套声明式写法，作为**新增**参数的强制规范（存量的 `_f`/`_fb`/`_fn` 调用
点数量大、逐个改造收益低，本次不做存量迁移）。

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

`param_registry.py` 顶部注释里列了当前的例外清单（`reminder` /
`format_correction` / `privacy` / `role_agent` / `env_info` /
`workflow` / `web_search` / `mcp`）——这些 block 的加载逻辑混入了 CLI
参数覆盖、多来源合并（如 `privacy.secrets` 要合并配置文件值和代码传入
值）、字段派生（如 `env_info` 的 `provider_kwargs` 是从
`include_hostname`/`include_username` 派生出来的）等无法用"JSON key
直接映射到字段"表达的逻辑，继续在 `loader.py` 里保留专属手写代码，这是
合理的、预期内的例外，不是重构遗留的技术债。给这些 block **加字段**
时，直接在对应的手写构造代码块里加一行即可；只有当新字段是"纯粹的
配置文件字段，不涉及 CLI/合并/派生"时，才考虑把整个 block 迁移进
`NESTED_CONFIG_BLOCKS`（迁移前请确认该 block 目前混入的所有 CLI
覆盖逻辑都已经不再需要，避免迁移后悄悄丢失 CLI 覆盖能力）。

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

## 5. CLI 覆盖已并入某个 block 手写逻辑时怎么加字段

如果新字段属于 `reminder`/`privacy`/`role_agent` 这类"有 CLI 覆盖"的
block（第 3.3 节例外清单），在 `loader.py` 里找到对应的手写构造代码块
（如 `reminder_cfg = ReminderConfig(...)` 那一段），照着旁边已有字段的
写法加一行，保持"CLI 参数 > 配置文件 > 默认值"的合并顺序一致即可，不
需要额外抽象。这类 block 数量少（6 个），手写维护成本可控，强行套用
通用机制反而会让"到底谁的优先级更高"变得不直观。

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

## 7. 相关文档

- [配置系统指南](config-guide.md) — 配置架构总览、子配置块详解、加载优先级
- [Goal 执行公平性调度配置](goal-execution-fairness-config.md) — `AutonomyConfig.fairness_*` 字段的完整业务语义说明（本机制的典型使用案例）
- [看板配置管理](kanban-config-management.md) — `config_catalog.py`（看板 UI 只读展示 + PATCH 更新）如何复用 `NESTED_CONFIG_BLOCKS`

---

*创建：2026-08，引入 `config/param_registry.py` 统一参数注册与解析机制。*
