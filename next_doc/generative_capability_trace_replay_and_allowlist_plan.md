# generative-capability：trace-replay 定位调整 + 原语来源自动化方案

对应背景讨论：`next_doc/generative-capability-skill-plan.md`（原始方案）、
`next_doc/generative_capability_explorer_rearch_plan.md`（探索器改为
SubAgent 驱动 + 三条蒸馏路径的前身方案）。本文档是继上述两份文档之后的
独立后续方案，按项目惯例（见 rearch_plan.md 阶段五收尾说明）不再往那两份
文档里继续追加，避免细节在多份文档间漂移。

## 0. 背景与现状核对

用户基于一次真实知乎抓取探索失败案例，提出四点问题。核对代码后发现：
三条蒸馏路径优先级（`script_source` > `llm_synthesized` > `trace_replay`）
与"探索子agent默认拥有完整通用工具集、领域原语是追加而非收窄"这两点，
在代码里其实已经落地（`distiller.py`/`explorer_runtime.py` 阶段二十/
二十五的改动），但**没有对应的文档记录**——`generative_capability_explorer_
rearch_plan.md` 停在"7.1 节"，`generative-capability-skill-plan.md` 也没有
阶段二十五的记录。这是一处已知的文档缺口，本文档不打算回填阶段二十五的
完整记录（改动已上线且有测试覆盖，重新梳理历史收益不高），只在下面各阶段
涉及到相关代码时如实引用现状。

用户本轮提出、代码里**尚未实现**、需要本方案落地的四点：

1. trace-replay 序列里没有"通用处理原语"（比如插一段脚本处理中间结果），
   只能死板顺序调工具。
2. trace-replay 在真实场景下命中率存疑（这次知乎案例即活例），需要明确
   "不再对它加码，只作为兜底"的产品定位，并在生命周期/健康巡检层面体现
   这种"弱信任"。
3. `tool_allowlist.json` 靠人工手写维护一份工具名清单，与
   `real_tools.py::load_skill_local_tool_implementations()` 实际加载的
   `TOOL_IMPLEMENTATIONS` 字典是两份平行维护、容易漂移的数据源，应该改成
   从"依赖的 skill"自动读取。
4. 蒸馏产物不应该被误导为"必须都调用领域原语"，应当被更明确地告知：纯
   处理逻辑可以直接用标准库/通用 Python，领域原语只是可选的便利手段。

## 1. 改动方案（按实施阶段划分）

### 阶段 A —— LLM 事后总结路径的自由度澄清（低风险，先做）

- 改动文件：`src/mini_agent/skills/generative_capability/distiller.py`
  （`_LLM_SYNTHESIZE_SYSTEM_PROMPT`）。
- 内容：新增一条硬性要求，明确"纯逻辑处理（过滤、判空、正则清洗、重试）
  直接写标准 Python，不需要也不应该强行套进 `tool_runtime.get_tool_
  executor()`；只有真正需要驱动浏览器/外部系统的步骤才通过 `executor()`
  调用领域原语"。同时在 `explorer_runtime.py` 探索阶段的 system_extra
  提示里做同样的澄清（探索子agent决定要不要写 `script_source` 时，同样
  需要知道"纯逻辑不必套壳"这件事）。
- 不改变任何函数签名/数据结构，纯 prompt 文案改动，无需新增测试（不改变
  可测试的行为分支）。

### 阶段 B —— `depends_skills` 自动派生领域原语列表，`tool_allowlist.json` 降级为可选收窄声明

- 改动文件：
  - `src/mini_agent/skills/generative_capability/explorer_runtime.py`
    （`_load_tool_allowlist` 及桥接领域工具那一段逻辑）。
  - 各 skill 的 `capability.yaml`（`explorer.base_tools` 增加等价别名
    `explorer.depends_skills`，两者兼容，`depends_skills` 优先）。
- 内容：
  1. 新增函数：从 `capability.yaml -> explorer.depends_skills`（或兼容的
     `base_tools`）声明的每个静态 skill 名，复用
     `real_tools.py::load_skill_local_tool_implementations()` 已有的
     "按约定路径 `<skills_root>/<name>/impl/tools_impl.py` 动态加载
     `TOOL_IMPLEMENTATIONS`" 逻辑，取其 `keys()` 作为**默认**领域原语名单。
  2. `tool_allowlist.json`/`capability.yaml -> explorer.tool_allowlist`
     两种历史写法继续兼容读取，但语义从"必须手写才有原语"改为"可选的
     收窄声明"——写了就在自动派生的默认集合基础上做交集收窄，不写就是
     默认集合全量可用。
  3. 不改变探索子agent"黑名单机制、领域原语是追加不是收窄"这个既有安全
     模型，只改变"追加的这批工具名从哪来"。
- 兼容性：三个存量 skill（`browser-site-scraper`/`doc-template-generation`/
  `text-transform-capability`）的 `tool_allowlist.json` 保留不动即可继续
  工作（走"收窄"分支）；不依赖 `tool_allowlist.json` 存在与否。
- 测试：新增/扩展 `tests/test_explorer_runtime_subagent.py` 内的用例，
  覆盖"未声明 tool_allowlist 时自动派生全量原语"与"声明了 tool_allowlist
  时按交集收窄"两种路径。

### 阶段 C —— trace-replay 弱信任标记（生命周期/健康巡检层面降级，不做重投入）

- 结论：不追加"trace-replay 内联处理原语"这类会把 trace-replay 拖成
  "简化版 llm_synthesized" 的复杂机制（详见文档末尾"已考虑但暂不采纳"），
  改为在生命周期层面明确它的兜底定位。
- 改动文件：
  - `src/mini_agent/skills/generative_capability/distiller.py`
    （`_atomic_persist` 写 `registry.json` 时，`distill_source_kind ==
    "trace_replay"` 的 member 使用更短的 probation 阈值）。
  - 对应 skill 的 `capability.yaml`（`lifecycle` 增加可选
    `trace_replay_probation_success_threshold`，未声明时退回引擎默认更
    保守的值，比如比 `probation_success_threshold` 更高的门槛，即要求
    trace-replay 产物验证更多次成功才能转正）。
- 记录：`meta.json` 已有 `distill_source_kind` 字段，无需新增字段，只是
  让 `registry.json` 落盘时按这个字段读取不同的生命周期参数。

### 阶段 D —— 打包与文档同步

- 每完成一个阶段（A/B/C），更新本文档"2. 实施记录"对应小节为"已完成"，
  并附改动文件清单、测试结果。
- 全部完成后打包本次修改/新增的所有文件（保留原始目录结构，便于直接
  覆盖到用户本地项目）供下载。

## 2. 已考虑但暂不采纳：trace-replay 内联处理原语（`__inline_exec__`）

评估过"给 trace-replay 序列加一种 `{"tool": "__inline_exec__", "code":
...}` 步骤类型，用来插入过滤/判空逻辑"的方案，结论是不采纳，原因：

- 判断"探索 trace 里哪一段 bash/python 调用属于可安全复用的纯处理逻辑"
  本身就需要语义理解，机械的 `_templatize_steps` 做不到——真做出来，
  实际上是把 `llm_synthesized` 路径的能力搬回了 trace-replay，等于让
  trace-replay 长成 `llm_synthesized` 的简化版，而不是保持"最后兜底"的
  定位。
- `llm_synthesized` 已经能做这件事，且做得更彻底（可重组整个控制流，不
  局限于固定位置插一段 exec）。继续投入 trace-replay 的结构能力，收益
  会被 `llm_synthesized` 覆盖，边际价值低。
- 因此本方案选择"阶段 C：弱信任 + 更慢转正"而不是"结构增强"作为
  trace-replay 的应对方式，把工程投入让给阶段 A/B。

## 3. 实施记录

### 阶段 A —— 已完成

**改动文件**:
- `src/mini_agent/skills/generative_capability/distiller.py`
  （`_LLM_SYNTHESIZE_SYSTEM_PROMPT` 新增第 5 条硬性要求）。
- `src/mini_agent/skills/generative_capability/explorer_runtime.py`
  （探索阶段 system_extra 提示新增一段同等澄清，供探索子agent写
  `script_source` 时参考）。

**验证**: `tests/test_generative_capability_engine.py` +
`tests/test_explorer_runtime_subagent.py` +
`tests/test_distiller_script_source.py` +
`tests/test_generative_capability_real_tools.py`，改动前后对比失败用例
集合完全一致（8 个因测试桩未带 `script_source`/沙盒环境限制导致的既有
失败 + 1 个因沙盒缺 `websocket-client` 库导致的既有失败，均与本次改动
无关，改动前 baseline 同样失败），无新增回归。本阶段是纯 prompt 文案
改动，不改变任何可测试的行为分支，因此未新增测试用例。

### 阶段 B —— 已完成

**改动文件**:
- `src/mini_agent/skills/generative_capability/capability_engine.py`
  （`explore()` 新增 `explorer_cfg["_resolved_skill_dir"]` 注入）。
- `src/mini_agent/skills/generative_capability/explorer_runtime.py`
  （新增 `_auto_derive_domain_tool_names()`；`_resolve_domain_tool_names()`
  优先级改为：`allowed_tools` 内联 > 自动派生 > `tool_allowlist.json`
  收窄/兜底 > `base_tools` 名字兜底）。
- `.claude/skills/browser-site-scraper/capability.yaml`（新增
  `explorer.depends_skills: [browser-core]`，`base_tools` 保留作兼容
  别名）。
- `tests/test_explorer_runtime_subagent.py`（`TestResolveDomainToolNames`
  新增 3 个用例：自动派生全量可用、allowlist 收窄自动派生集合、依赖
  skill 无 impl 时退回旧的 allowlist 直接使用行为）。

**实现要点**:
- 唯一真实数据源是 `real_tools.py::load_skill_local_tool_implementations()`
  已有的"按约定路径动态加载 `TOOL_IMPLEMENTATIONS`"机制，本次只是把
  `explorer_runtime.py` 桥接领域工具名时也接到这份数据源上，没有重新发明
  一套加载逻辑。
- `tool_allowlist.json` 从"必须手写才有原语"降级为"可选的交集收窄声明"，
  且在自动派生结果为空时（依赖 skill 尚无 `impl/tools_impl.py`，如
  `doc-core`/`text-core`）安静退回旧行为直接使用该文件列表，不影响
  `doc-template-generation`/`text-transform-capability` 两个存量 skill。
- `depends_skills` 是 `base_tools` 的新别名（语义更贴近"依赖哪个 skill"），
  两者兼容，未强制迁移其余 skill 的 `capability.yaml`（`doc-template-
  generation`/`text-transform-capability` 暂不改，等它们各自的
  `impl/tools_impl.py` 真正落地后再迁移意义更大）。

**验证**: 同一批测试文件，失败用例集合与阶段 A 后完全一致（9 个既有失败，
均与本次改动无关，逐一核对过是"测试桩未带 script_source"/"沙盒缺
websocket-client 库"两类环境性失败），新增 3 个用例全部通过，
`49 passed`（阶段 A 后为 `44 passed` + 阶段 A 未新增用例，本阶段净增 5：
3 个新增 + 之前统计口径含 e2e 用例）。无回归。

### 阶段 C —— 已完成

**改动文件**:
- `src/mini_agent/skills/generative_capability/distiller.py`
  （`_atomic_persist()` 写 `registry.json` 时，`distill_source_kind ==
  "trace_replay"` 的 member 额外写入
  `probation_success_threshold_override`：优先取
  `capability.yaml -> lifecycle.trace_replay_probation_success_threshold`
  显式声明值，未声明时默认取"领域默认 `probation_success_threshold` 的
  两倍"，不需要额外配置即可生效）。
- `src/mini_agent/skills/generative_capability/capability_engine.py`
  （`_apply_lifecycle()` 优先读取 member 级别的
  `probation_success_threshold_override`，没有才退回
  `capability.yaml` 的领域默认门槛；`degrade_failure_threshold`（降级
  速度）不受影响，只调整"转正"速度）。
- `tests/test_distiller_script_source.py`（新增
  `test_trace_replay_member_gets_conservative_probation_override`：验证
  trace-replay 产物带 `override=6`（默认 3 的两倍），script_source 产物
  不带这个字段）。
- `tests/test_generative_capability_engine.py`（新增
  `test_apply_lifecycle_honors_member_level_probation_override`：验证
  带 override 的 member 3 次成功不转正、6 次才转正；不带 override 的
  member 仍按领域默认 3 次转正，行为不变）。

**未采纳的方案**（见"2. 已考虑但暂不采纳"一节）：给 trace-replay 序列加
`__inline_exec__` 内联处理原语。结论是这类结构增强会让 trace-replay 长成
`llm_synthesized` 的简化版，边际价值被后者覆盖，故本方案改为只做生命周期
层面的"弱信任"，不做结构增强。

**验证**: 同一批测试文件，失败用例集合与阶段 A/B 后完全一致（9 个既有
失败，逐一核对与本次改动无关），新增 2 个用例全部通过，`51 passed`
（阶段 B 后 `49 passed` + 本阶段净增 2）。无回归。

### 阶段 D —— 已完成（本节）

**内容**: 打包本次新增/修改的全部文件（保留原始目录结构），见下方文件
清单，供直接覆盖到本地项目使用。

**改动/新增文件总清单**（阶段 A + B + C 合并）：
- `next_doc/generative_capability_trace_replay_and_allowlist_plan.md`（新增，本文档）
- `src/mini_agent/skills/generative_capability/distiller.py`
- `src/mini_agent/skills/generative_capability/explorer_runtime.py`
- `src/mini_agent/skills/generative_capability/capability_engine.py`
- `.claude/skills/browser-site-scraper/capability.yaml`
- `tests/test_explorer_runtime_subagent.py`
- `tests/test_distiller_script_source.py`
- `tests/test_generative_capability_engine.py`

**本方案（v1.0）实施状态：阶段 A~D 全部完成。**

## 4. 文档同步记录

- `docs/skill-system-guide.md` 第 3.8 节：新增"领域原语从哪来"、"蒸馏
  产物不要求全程只调用领域原语"、"三条蒸馏路径与各自定位"三个小节，
  并在文末变更记录追加本次条目。
- `README.md`：变更日志顶部追加本次改动摘要条目。
- `CLAUDE.md`：文档索引新增指向 `skill-system-guide.md` 第 3.8 节与本
  方案文档的条目。
