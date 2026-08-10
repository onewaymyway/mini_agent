# 成长顾问诊断数据源修复 + 用户语言检测改进计划

- **版本**: v1
- **前置文档**:
  - `next_doc/growth_advisor_design.md`（原始方案）
  - `next_doc/growth_advisor_improvement_plan_v4.md`（N1 诊断面板健康度
    趋势化，本文档修的是 N1 展示的数据源本身的 bug）
  - `next_doc/growth_advisor_implementation_record.md`（逐阶段实施记录，
    本次改动完成后会在该文档追加一节）
- **触发背景**: 用户反馈"诊断面板记忆总条数显示 0，但健康度趋势里是
  99，两边对不上；LLM 增强调用状态也一直显示从未触发过"，以及"Agent
  对你的了解"画像用英文生成，不跟随用户实际使用的语言。排查后确认是
  两个独立问题：一个是代码 bug（构造 `MemoryStore` 时传参类型错误，
  静默降级为空），一个是设计缺失（画像语言完全依赖"跟记忆条目同语言"
  这条弱约束，没有显式检测/落盘）。
- **本文档定位**: 给出问题的根因定位、修复方案、验证方式和分阶段实施
  记录，改完每个阶段就地更新本文档的"实施状态"，不再另开一份实施记录
  文档（改动量级不需要）。

---

## 方向一：诊断面板 / 手动扫描 / CLI 的 `MemoryStore` 构造 bug

### 1.1 根因

`MemoryStore.__init__(self, path=None, ...)` 的 `path` 参数期望的是
记忆 JSONL 文件路径（如 `paths.workdir_memory`），但以下三处调用把
`AgentPaths` 实例整个传了进去：

- `src/mini_agent/api/routes.py:5911`（`GET /growth/summary`，诊断面板
  数据源）
- `src/mini_agent/api/routes.py:6001`（"🔍 立即为我看看"手动扫描按钮
  对应的端点，内部调用 `ga.run_daily_cycle(...)`）
- `src/mini_agent/cli/commands/growth_cmd.py:65`（CLI `mini-agent growth`
  子命令共用的 store 构造函数）

`self._path = path or Path(".agent")/"memory.jsonl"` 里 `AgentPaths`
实例是 truthy 的，所以 `self._path` 被赋值成一个不是路径的对象；后续
`all_entries()` 触发的磁盘加载会失败，而 `diagnostics_snapshot()` 里这
段是 `try/except Exception: entries = []` 静默吞掉的，导致：

- 诊断面板"记忆总条数"永远是 0（跟健康度趋势里 cron 任务用正确路径
  记录的真实条数对不上）；
- 手动扫描按钮触发的 `run_daily_cycle` 在 0 条记忆上跑，扫描窗口内 0
  命中，`last_scan_at` 即使更新也没有任何主题命中；
- LLM 信号增强的触发条件是"未匹配记忆数达到阈值"，0 条永远达不到，
  于是 `llm_call_status` 一直是空，看起来像"从没被触发过"。

三处 bug 是同一根因的三个症状，只需要统一修一处工具函数即可。

### 1.2 修复方案

新增一个小工具函数，统一"如何从 `AgentPaths` 构造一个可用的
`MemoryStore`"，避免以后再有第四处写错：

```python
# src/mini_agent/perception/memory_factory.py 新增
def build_default_memory_store(paths: "AgentPaths") -> "MemoryStore":
    """按项目 scope 的默认路径构造一个只读用途的 MemoryStore。

    用于诊断面板 / 手动触发扫描 / CLI 等"临时需要读一次记忆"的场景，
    跟 `load_memory_backend()` 走完整配置驱动的正式后端不同——这里刻意
    保持轻量（不接 library_index/LLM 分类回调），因为调用方只是要读
    `all_entries()`，不需要写入路径的分类能力。
    """
    from mini_agent.perception.memory_store import MemoryStore
    return MemoryStore(paths.workdir_memory)
```

三处调用点分别替换：

```python
store = MemoryStore(paths)
```
→
```python
from mini_agent.perception.memory_factory import build_default_memory_store
store = build_default_memory_store(paths)
```

选择 `paths.workdir_memory`（project scope）而不是 `paths.global_memory`，
是为了跟 `_load_local()` 里 `scope != "global"` 分支的默认值保持一致
（`cfg.memory.store_path or paths.workdir_memory`）——诊断面板要跟"用户
实际在用的记忆库"对齐，而不是引入 global scope 的歧义。如果用户显式配置
了 `cfg.memory.store_path`（自定义路径），这个轻量工具函数目前不会读取
该配置；这是已知的简化，跟当前 `growth_cmd.py:65` 的既有行为一致，不在
本次改动范围内扩大（留在后续 issue 里跟"多 store_path 支持"一起做）。

### 1.3 验证方式

- 单元测试：构造一个临时 `AgentPaths`，写入几条 `MemoryEntry` 到
  `workdir_memory`，分别调用三处改动后的代码路径，断言
  `diagnostics_snapshot()["memory"]["total_entries"]` 等于写入条数。
- 回归测试：`tests/test_growth_advisor.py` 里已有的 diagnostics 相关
  用例保持通过。
- 手动验证：本地起服务后依次看
  1) `/growth/summary` 的 `memory.total_entries` 是否等于
     `/growth/health_trend` 最新一条的 `total_entries`；
  2) 点"🔍 立即为我看看"后 `signal_scan.last_scan_at` 是否更新且
     `topic_hit_counts` 不再全是 0（前提是记忆库里确实有能命中内置
     关键词的内容）。

### 1.4 实施状态

- [x] 新增 `build_default_memory_store()` 工具函数
- [x] `routes.py:5911`（`/growth/summary`）改用工具函数
- [x] `routes.py:6001`（手动扫描端点）改用工具函数
- [x] `growth_cmd.py:65`（CLI）改用工具函数
- [x] 补充/更新单元测试
- [x] `next_doc/growth_advisor_implementation_record.md` 追加实施记录

---

## 方向二：用户常用语言检测 + 画像/生成内容语言跟随

### 2.1 现状问题

`prompts/system/profile_summarizer.md` 里已经有一句"用记忆条目相同的
语言输出"，但这是个弱约束：如果上游（session 摘要生成那一层）本身习惯
输出英文，画像层拿到的 `memory_text` 已经是英文，"跟记忆条目同语言"这
条指令等于没有基准可跟。这类"依赖上游文本语言做隐式传递"的设计，只要
链路上任意一环偏了，下游就会跟着偏，且没有任何显式信号可以排查。

### 2.2 设计方案

**检测点**：不依赖任何一层的摘要文本，而是直接对用户最原始的输入（用户
在对话里实际打的字）做语言检测，检测结果作为 profile 的一个独立字段
落盘，跟 `summary/tech_stack/habits` 平级、但不参与"跟记忆条目同语言"
这种间接推断。

**检测方式**：不引入新依赖、不额外调用 LLM，用轻量的 Unicode 区间统计
做启发式判断——统计一段文本里 CJK 统一表意文字 / 平假名片假名 / 谚文
等区间字符的占比，超过阈值判定为对应语言，否则退回默认英文。这个函数
足够便宜，可以在每次画像刷新时对"本次新增的 delta 记忆摘要"重新跑一遍，
自然具备"新鲜度"（用户换语言使用一段时间后，画像语言会跟着漂移过去）。

```python
# src/mini_agent/profile.py 新增（或独立成
# src/mini_agent/utils/lang_detect.py，供其它模块复用）
def detect_primary_language(texts: list[str]) -> str:
    """粗粒度语言检测：返回 ISO 639-1 code（zh/ja/ko/en/...）。

    不追求精确（不区分简繁体、不做地区变体），只满足"生成内容该用哪种
    语言"这一个用途。基于 Unicode 区间字符占比的启发式判断，无外部依赖、
    无网络/LLM 调用，可在任意热路径调用。
    """
```

**落盘位置**：`profile.derived["preferred_language"]`，`UserProfileManager
.generate()` 每次刷新画像时，用参与本次 prompt 的 `delta_entries`（用户
侧原始文本，而不是可能已经跑偏的历史 summary）重新计算并覆盖。

**消费方**：新增一个 prompt 变量 `{{preferred_language}}`，让语言指令从
"猜"变成"读"：

- `prompts/system/profile_summarizer.md`：改成"用 {{preferred_language}}
  输出 summary/tech_stack/habits，不要根据记忆条目的语言自行判断"；
- 后续如果成长顾问的调研报告生成、月度复盘文案等其它面向用户的生成类
  prompt 也有类似问题，同样接入这个字段（本次改动先只落地画像这一处，
  作为其它 prompt 复用该字段的参考实现；全量排查所有生成类 prompt 属于
  更大的改动，列入下一版计划，不在本次范围内）。

**看板展示**：诊断面板"配置"区块可以顺带展示一下当前检测到的
`preferred_language`，方便用户确认检测结果是否符合预期（不在本次改动
强制要求，如果实现顺利可以顺手加一行）。

### 2.3 验证方式

- 单元测试：给 `detect_primary_language` 喂中文/英文/日文/混合文本，
  断言返回预期的 language code；边界情况（空列表、纯数字/符号）返回
  默认值不报错。
- 集成测试：`UserProfileManager.generate()` 用中文 session 摘要跑一遍，
  断言 `profile.derived["preferred_language"] == "zh"` 且 prompt 里确实
  带上了这个变量。
- 手动验证：找一个之前画像是英文的账号，用中文继续对话攒够新记忆后触发
  一次画像刷新，看"Agent 对你的了解"是否变成中文。

### 2.4 实施状态

- [x] 新增 `detect_primary_language()` 工具函数 + 单元测试
- [x] `UserProfileManager.generate()` 接入检测，写入
      `derived["preferred_language"]`
- [x] `profile_summarizer.md` / `profile_update_request.md` 新增
      `{{preferred_language}}` 变量并接入语言指令
- [x] 诊断面板展示当前检测到的语言（顺手加，非强制）
- [x] `next_doc/growth_advisor_implementation_record.md` 追加实施记录
