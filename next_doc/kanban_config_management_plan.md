# 看板配置管理改进计划

- **版本**: v1.0
- **状态**: 已实现
- **变更记录**:
  - v1.0：初版设计 + 完整实现（含 loader.py 遗留 bug 修复）。

## 0. 背景与问题

`agent_config.json` 是 mini-agent 的主配置文件，覆盖记忆/压缩/技能/自主性/
隐私等 30 余个功能域、300+ 个字段。目前想知道"某个功能当前是否开启、阈值
是多少、我改没改过默认值"，唯一办法是：

1. 翻 `src/mini_agent/config/models.py` 找对应 dataclass 的字段和默认值；
2. 翻 `src/mini_agent/config/loader.py` 找这个字段在 `agent_config.json`
   里到底叫什么 key（历史遗留原因，很多字段的 JSON key 和 dataclass 字段名
   并不一致，见下）；
3. 手工 diff `agent_config.json` 和上面翻到的默认值，判断"是否被自定义过"。

看板本身已经有"🧠 自我状态"tab 展示大量运行时状态，但唯独"配置本身长什么
样、哪些被改过"这件事完全没有可视化，用户要么盲改 JSON，要么每次都要
重新翻源码确认。本计划要解决的就是这个问题：让看板能读 + 分类展示 +
（在安全范围内）编辑 `agent_config.json`。

## 1. 现状调研：agent_config.json 的两种落盘方式

调研 `loader.py`（`load_config()`，994 行）发现字段落盘方式并不统一，这是
设计"通用机制"时必须先接受的约束，而不是可以推倒重来的东西（改掉会是一次
大范围的破坏性变更，不在本计划范围内）：

1. **"flat"（历史遗留）**：顶层扁平键，键名往往不等于 AppConfig 内部字段名，
   例如 `"memory_enabled"` 对应 `MemoryConfig.enabled`、
   `"compact_max_turns"` 对应 `CompressConfig.max_turns_before_compact`。
   集中在 memory / compress / tool_trim / skill / perception / session /
   profile / debug / http / retry / ensemble 这些较早引入的功能域，以及
   少数核心运行参数（如 `"yes"` 对应 `auto_approve`、`"provider"` 对应
   `llm_provider`）。
2. **"nested"（较新约定）**：顶层是一个 dict block（如
   `"tech_radar": {...}`），block 内部字段名与对应 dataclass 字段名完全
   一致，可以用 `dataclasses.fields()` 自动展开，不需要为每个字段单独写
   映射。覆盖 tech_radar / web_search / ecosystem_positioning / reminder /
   format_correction / privacy / role_agent / goal_mode / turn_judge /
   env_info / workdir_knowledge / global_knowledge / proprioception /
   affordance / workflow / digest_advisor / cron。

调研过程中还发现一个**真实 bug**：`AutonomyConfig`（`autonomy` 子配置块，
包含本仓库这些年好几轮改进计划新增的字段，例如
`goal_execution_fairness_improvement_plan.md` P1-P4 的所有
`fairness_*`/`max_concurrent_objectives_per_goal` 字段）和
`ObservabilityConfig` 从未被 `load_config()` 从文件读取过——
`AppConfig(...)` 最终构造调用里根本没有传 `autonomy=`/`observability=`，
这两块无论 `agent_config.json` 里写了什么都只会用 dataclass 默认值。也就是
说，此前所有关于"在 `agent_config.json` 里配置 `autonomy.xxx`"的文档说明
（包括 `docs/goal-execution-fairness-config.md`）都是**写了也不会生效**的
死配置。见 §3 修复。

## 2. 设计的机制

### 2.1 字段目录（Catalog）：把两种落盘方式统一抽象

新增 `src/mini_agent/config/config_catalog.py`，核心抽象是 `FieldSpec`
（`json_key` + `attr_path` + `label` + `sensitive`）按 `CategorySpec`
分组：

- **flat 类别**（core / memory / compress / tool_trim / skill /
  perception / session / profile / debug / http / retry / ensemble）：
  逐字段手工登记 `json_key`（对照 loader.py 源码核实，保证正确，不是猜的）
  和 `attr_path`（AppConfig 上取值的路径，如 `"memory.enabled"`）。
- **nested 类别**（tech_radar / web_search / ... / autonomy /
  observability，共 19 个）：用 `dataclasses.fields()` 自动展开对应
  dataclass 的字段列表，`json_key` 和 `attr_path` 都是
  `f"{block_name}.{field_name}"`，不需要手写——新增一个 nested block 或给
  已有 block 加字段时，目录**自动**跟上，不需要改 `config_catalog.py`。
- **不收录的字段**：list/dict 类型的复杂字段（`mcp_servers`、
  `privacy.secrets`、`llm_fallback_chain`、`goal_mode.judge_allowed_tools`
  等）——通用的单值编辑控件不适合表达"列表/子对象"，这类字段目录里不生成
  `FieldSpec`，也就不会出现在看板的可编辑表单里（详见 §4 设计边界）。

当前实际收录：31 个分类、303 个可编辑字段（跑
`tests/test_kanban_config_routes.py` 时可以看到具体数字，随代码演进可能
变化）。

### 2.2 只读状态视图：GET /v1/self/config

对每个字段返回：
```json
{
  "json_key": "autonomy.fairness_time_slicing_enabled",
  "label": "fairness_time_slicing_enabled",
  "type": "bool",
  "value": false,           // 当前生效值（来自内存里的 AppConfig）
  "default": false,         // dataclass 定义的默认值
  "customized": false,      // agent_config.json 里是否显式配置过（不等于
                             // value != default，见下）
  "sensitive": false
}
```
`customized` 单独判断（检查 `agent_config.json` 原始 dict 里是否存在这个
key/嵌套 key），而不是简单地拿 `value != default` 推断——两者语义不同：
用户可能显式把某个字段设成了和默认值一样的值（说明"我确认过这个值，不是
没配置"），`value == default` 不代表"用户没碰过它"。

敏感字段（`http.api_token`、`web_search.api_key`、`privacy.secrets`）
`value`/`default` 一律不回显明文，只给"已配置/未配置"的布尔化展示。

### 2.3 写回：PATCH /v1/self/config

Body：`{"updates": [{"json_key": str, "value": Any}, ...]}`

- 白名单校验：`json_key` 必须在目录（`KNOWN_FIELDS`）里存在，且非
  `sensitive`，否则整批全部拒绝（400），不写入文件——要么全部生效要么都
  不生效，不产生"改了三项、第四项失败导致文件只改了一半"的中间状态。
- 写入用临时文件 + `os.replace` 原子替换，避免进程被杀等异常情况下把
  `agent_config.json` 写坏。
- 响应里固定带 `"restart_required": true`——`AppConfig` 目前是进程启动时
  一次性加载，没有热重载机制，本计划也不引入热重载（范围外，见 §4）。
  响应里的 `value` 会用本次提交的值覆盖显示（不等于"已生效"，只是让用户
  看到"文件已经按这个值写好了，重启后会是这个值"，避免因为内存态 cfg 还
  没变而显示旧值造成"是不是没保存成功"的困惑）。

### 2.4 看板 UI：⚙️ 配置 tab

- 每个分类一个可折叠区块（`st.expander`），默认只展开"含有已自定义字段"
  或命中筛选关键词的分类，避免 31 个分类全展开时页面过长。
- 每个分类内是一个独立的 `st.form`，按类型渲染控件（bool→checkbox，
  int/float→number_input，其余→text_input），保存按钮只提交*本分类内
  实际被改动过*的字段（前端比较控件当前值与拉取到的原始值），避免"手滑
  改了一个开关，结果把同一分类里其它没碰过的字段也重新提交了一遍"（虽然
  重新提交同样的值本身无害，但会让"这次到底改了什么"变得不清楚，尤其是
  配合 `customized` 判断逻辑时容易让人误以为改了更多字段）。
- 顶部提供关键词筛选框，按 `json_key`/`label` 过滤——303 个字段一次性
  浏览体验较差，筛选是必要的导航手段。
- 敏感字段展示为禁用状态的文本框（"已配置 ✓"/"未配置"），不提供编辑，
  修改需要直接编辑 JSON 文件。

## 3. 配套修复：AutonomyConfig / ObservabilityConfig 接入 loader.py

在 `loader.py` 里新增一个通用的 `_load_block_from_dict(block, cls)` 辅助
函数（用 `dataclasses.fields()` 通用遍历，按字段默认值的类型做
bool/int/float/str 转换，转换失败的字段忽略、退回默认值，不让一个写错的
字段拖垮整个 `AppConfig` 加载），用它构造 `autonomy_cfg`/
`observability_cfg`，并接入最终 `AppConfig(...)` 构造调用。

这是本计划顺带修复的一个真实 bug，不是"配置管理"这个新功能本身要求的，
但没有这个修复，`autonomy`/`observability` 两个分类在配置管理界面里会
显示"改了但改不动"的假象（写入文件成功、但重启后依然是默认值），会让
整个功能显得不可信，所以放在同一批一起修，并在
`tests/test_kanban_config_routes.py`／新增的 loader 手工验证脚本里覆盖了
写入 → 重新 `load_config()` → 断言生效的完整链路。

## 4. 设计边界（明确不做的事）

- **不做热重载**：修改后必须重启进程才能生效，本计划不引入"运行时替换
  `self_agent.cfg`"的机制——那涉及大量运行中状态（已建立的 memory
  backend、已加载的 skill、正在跑的 workflow 等）是否需要跟着重建的
  问题，风险和复杂度都远超"配置查看与编辑"这个本计划的范围。
- **不做 list/dict 字段的通用编辑**：`mcp_servers`（MCP 服务器列表）、
  `privacy.secrets`（明文密钥列表）、`llm_fallback_chain`（多 Provider
  故障转移链）这类字段结构复杂、且部分涉及敏感信息，通用的"一个字段一个
  输入框"模式不适用，仍然只能直接编辑 JSON，或者未来单独设计专项 UI（比如
  `mcp_servers` 未来可以做成看板里的"MCP 服务器管理"专门页面，但那是一个
  独立的功能，不在本计划里）。
- **不做 CLI 参数/环境变量的可视化**：只覆盖 `agent_config.json` 这一层，
  `load_config()` 的优先级链条里 CLI 参数和环境变量仍然会覆盖看板里显示
  的"生效值"（`GET /v1/self/config` 里的 `value` 已经是三者合并后的最终
  值，只是"如果被 CLI/环境变量覆盖了，看板改了 JSON 也不会生效"这件事看板
  本身不会提示，用户需要自己知道）。
- **不做 100% 字段覆盖**：303 个字段是当前收录的规模，覆盖了绝大多数
  bool/int/float/str 类型的功能开关和阈值，但不保证枚举了 loader.py 里
  every 一个可配置项——目录是可扩展的数据结构（新增一条 `FieldSpec` 或
  在 `_NESTED_BLOCKS` 里加一行即可），后续发现遗漏可以随时补，不需要
  推倒重来。

## 5. 涉及文件

- `src/mini_agent/config/config_catalog.py`（新增）—— 字段目录 + 状态
  视图 + 写回逻辑。
- `src/mini_agent/config/loader.py`（修改）—— 补齐 autonomy/observability
  block 读取。
- `src/mini_agent/api/routes.py`（修改）—— 新增
  `GET`/`PATCH /v1/self/config`。
- `apps/mini_agent_kanban/client.py`（修改）—— 新增
  `config_status()`/`config_update()`。
- `apps/mini_agent_kanban/app.py`（修改）—— 新增"⚙️ 配置"tab。
- `tests/test_kanban_config_routes.py`（新增）—— 6 个用例，覆盖只读状态
  展示/自定义值展示/敏感字段脱敏/写入并可重新加载/未知字段整体拒绝/敏感
  字段拒绝写入。
