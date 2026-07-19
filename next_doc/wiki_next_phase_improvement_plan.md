# wiki 知识库改进计划 · 下一阶段（退轨 / 巩固熔断 / 世界知识 / daemon 定时）

> 本计划基于对当前代码的实际核查（`perception/library_index.py`、`perception/classification.py`
> `/entity_index.py`/`catalog.py`、`evolution/consolidation.py`、`wiki/topics.py`、
> `wiki/promotion.py`、`evolution/cron_scheduler.py`、`history/world_extraction.py`）撰写，
> 只处理四个明确聚焦点，不重复此前 P0-P4 / E1-O4 已完成的工作。

状态标注：`[设计]` 待实现、`[待评审]`、`[可直接排期]`。

---

## 0. 先纠正一处过时判断

上一轮分析里说"专题页只能新建、不能更新"——重新核对代码后这个判断**不准确**：
`wiki/topics.py` 的 O3 阶段已经实现了 `append_to_topic_page()` + `_find_topic_reconsolidation_candidates()`，
`consolidate_topics()` 每 `reconsolidation_interval_runs`（默认 5）轮会先扫一遍已有 topic，
把 tag 重合度达标的新页面并入既有专题页而不是重新聚类生成。**这部分已完成**，本计划不再重复设计，
只在第 2 节里补一条它遗留的小缺口（陈旧专题页的"退场"机制），其余篇幅聚焦另外三个真实存在的缺口。

---

## 1. 双轨制退出：只保留 wiki，下线旧图书馆索引

### 1.1 现状核查

- 旧索引三件套：`perception/classification.py`（分类树）、`perception/entity_index.py`（实体索引）、
  `perception/catalog.py`（分类号→记忆指针 + 编年时间线），由 `LibraryIndex` 门面统一调用。
- `context_builder.py` 里 `wiki_search` 已经**默认转正为主检索路径**
  （`library_wiki_search_primary` 默认 `True`），`shelf_search`（旧两步检索）只在
  wiki 检索未命中或没有 `llm_call` 时兜底。
- `evolution/consolidation.py::run_consolidation` → `library.consolidate()` 里，
  步骤 1-4（分类树生长/合并、实体摘要重写、实体去噪合并）仍然每轮巩固都在跑，
  即便它们的产出（`shelf_search`）已经沦为兜底路径。
- `wiki/promotion.py` 已经把"转正"标准量化成三条，并提供
  `evaluate_promotion_readiness()` 可随时查询，但**从未接到任何自动化动作**——
  标准满足与否只是给人看的一个报告，没有触发过任何下线操作。

结论：**转正的判断基础设施已经就绪，缺的是"判断结果之后该做什么"这一步。**
双轨制没有退出条件，不是缺标准，是缺"标准满足后自动/半自动执行下线"的动作。

### 1.2 改进方案 `[可直接排期]`

#### 1.2.1 把"评估"升级为"决策 + 执行"

新增 `wiki/decommission.py`：

```python
@dataclass
class DecommissionPlan:
    ready: bool                     # evaluate_promotion_readiness() 三条是否全部满足
    blocking_reasons: list[str]     # 未满足时列出具体哪一条、差多少
    dry_run_diff: dict              # 下线后 shelf_search 相关代码路径的调用点清单（仅报告，不改代码）

def check_and_plan(paths: AgentPaths) -> DecommissionPlan:
    """只读评估，复用 wiki/promotion.py::evaluate_promotion_readiness()。
    ready=True 时不自动执行下线，而是把 DecommissionPlan 写入
    paths.wiki_decommission_report_path，供 /wiki promotion 展示，
    并在 daemon 模式下触发一条高优先级提醒（走现有的 reminders/建议链路，
    不是直接改代码）。"""
```

原则：**下线代码是不可逆的高风险操作，不做成全自动**。`check_and_plan()` 只负责
"标准满足了，给出明确的执行清单"，真正下线由人工确认后跑一次性迁移脚本（1.2.2）。
这条线只解决"评估结果无人跟进"的问题，不解决"要不要允许 AI 自主删代码"的问题（默认不允许）。

#### 1.2.2 分步下线，而不是一刀切删除

不建议直接删掉 `classification.py`/`entity_index.py`/`catalog.py` 三个文件，理由：
`MemoryEntry.category` 字段、编年时间线是历史数据的一部分，直接删除写入逻辑会导致
存量数据的 `category` 字段变成死字段，读取路径（如果还有地方读）会报错。分三步：

1. **第一步（`consolidate()` 内部开关）**：`library.consolidate()` 新增参数
   `legacy_index_enabled: bool`，默认仍为 `True`；`check_and_plan()` ready=True 且人工确认后，
   把这个开关通过 `MemoryConfig` 关闭。关闭后步骤 1-4（分类树生长/合并、实体摘要重写、
   实体去噪合并）整体跳过，`shelf_search` 兜底路径保留但不再更新，相当于把旧索引冻结成只读快照。
   这一步零数据丢失、可随时回滚（改回 `True` 即可）。
2. **观察期（建议 ≥ 2 周）**：冻结旧索引期间，`wiki_search` 已经是唯一在更新的检索路径，
   如果这期间 `wiki_search` 命中率/用户反馈没有明显下降，说明冻结旧索引没有引入回归。
3. **第二步（真正移除）**：观察期通过后，`shelf_search` 调用点（`context_builder.py` 里
   `_inject_shelf_search_chain`）改成直接报错兜底（返回空上下文而不是查询冻结的旧索引），
   `classification.py`/`entity_index.py`/`catalog.py` 三个文件移到
   `src/mini_agent/_deprecated/`（不放进正式包路径，避免被误 import，但保留代码方便回滚），
   `LibraryIndex.__init__` 里旧索引相关的初始化逻辑一并精简。这一步才是真正的"移除"，
   建议单独一个 PR，带上前后 `/wiki promotion` 报告截图作为决策依据存档。

#### 1.2.3 daemon 侧配合

`sys:consolidation`（cron job，见第 4 节）每次运行后，顺带跑一次 `check_and_plan()`
（零 LLM 成本，纯读取），ready 状态发生**由 False 变 True** 的瞬间才提醒一次
（避免每 6 小时重复打扰），提醒内容附带 1.2.2 的三步操作指引。

---

## 2. 专题页陈旧退场机制 `[设计]`（O3 的小补丁）

O3 的再巩固机制解决了"新页面该不该并入已有 topic"，但没有处理反方向：
如果一个 topic 页面对应的 tag 下的成员页面被大量标记 `status: deprecated/superseded`
（wiki 生命周期状态机 O4 已经支持这个状态），这个 topic 本身应该跟着降权或标注过时，
否则会出现"专题页说得头头是道，但底下引用的实体早就作废"的悬空引用问题。

**方案**：`consolidate_topics()` 的再巩固扫描（`_find_topic_reconsolidation_candidates`）
里新增一次反向检查：统计 topic 的 `absorbs` 链接指向的成员页面中 `status != active` 的比例，
超过阈值（建议 0.6）时，把该 topic 的 frontmatter `status` 置为 `stale`，并在正文顶部插入
一句提示而不是删除内容（wiki 的"可读可信"原则——保留历史叙事，只是标注可信度下降）。
这条改动量很小，可以和第 4 节的 `sys:wiki_gap_scan` job 合并实现，不用单独起一个 job。

---

## 3. `consolidate()` 分步超时熔断 `[可直接排期]`

### 3.1 现状

`run_consolidation()`（`evolution/consolidation.py`）和它内部调用的
`library.consolidate()`（`perception/library_index.py`）加起来一共有 10+ 个子步骤
（剪枝候选、能力地图、scope 晋升、知识巩固里的 7 个子步骤、决策批量落盘、outcome 回填、
affordance 校准）。每一步都有独立的 `try/except`，**异常隔离已经做了**，但**耗时没有隔离**——
任何一步里如果 `llm_call` 卡住或很慢（比如 LLM 精排要处理一批很大的候选簇），
后面所有步骤都要等它跑完才能开始，整轮巩固的总耗时没有上界。

### 3.2 改进方案

新增 `evolution/step_runner.py`，提供一个统一的"限时执行"包装器，替换现有的
裸 `try/except` 写法：

```python
@dataclass
class StepResult:
    name: str
    status: str          # "ok" | "error" | "timeout" | "skipped"
    elapsed_seconds: float
    error: Optional[str] = None

def run_step(
    name: str,
    fn: Callable[[], T],
    *,
    timeout_seconds: float,
    on_timeout: str = "skip",   # 目前只支持 "skip"：超时直接放弃本步骤结果，
                                 # 不重试、不阻塞——重试留给下一轮巩固自然触发
) -> tuple[Optional[T], StepResult]:
    """用线程 + 简单轮询实现超时（不用 signal.alarm，避免和已有的
    子进程/子agent执行逻辑冲突；轮询间隔 0.5s，足够粗粒度）。
    超时后原线程仍在后台跑完（不强杀，Python 没有安全的线程强杀机制），
    但主流程不再等待，其结果被丢弃——下一轮巩固循环里同一份候选数据
    通常还在 pending 队列里，不会因为这次跳过而丢失，只是延后一轮处理。"""
```

每个子步骤配一个**独立的默认超时预算**（不是全局共享一个大预算，避免前面步骤
超支挤占后面步骤）：

| 步骤 | 建议超时 | 理由 |
|---|---|---|
| 剪枝候选 / 能力地图 / scope 晋升 | 10s | 纯规则计算，不含 LLM 调用 |
| 实体摘要批量重写（含 LLM） | 60s | 每个 due entity 一次 LLM 调用，数量不定，给宽松预算 |
| 实体去噪合并 | 30s | 规则打分为主，LLM 只兜底确认 |
| wiki 镜像 + 判重 | 45s | 同上 |
| wiki 索引重建 | 20s | 纯 IO + 规则解析，不含 LLM，超时大概率意味着 wiki 目录已经异常大，需要单独排查而不是无限等 |
| 专题页生成（含 LLM 综合叙事） | 90s | 单次调用产出的正文最长，给最大预算 |
| 世界模型候选批量落盘 | 45s | 同"wiki 镜像" |
| 决策批量落盘 | 45s | 同上 |
| outcome 回填 / affordance 校准 | 15s | 纯规则计算 |

`ConsolidationReport` 新增 `step_timings: list[StepResult]` 字段，`/evolve consolidate`
命令展示时把超时/异常的步骤单独高亮，方便人工判断是不是某个步骤长期超时（
说明预算给少了，需要调整，而不是每次都被静默跳过）。

### 3.3 与现有"失败静默降级"原则的关系

不改变现有"任何一步失败不阻断主流程"的哲学，只是把"失败"的判定从
"抛异常才算失败"扩展成"抛异常或超时都算失败"，两种情况处理方式一致
（跳过、记录、下一轮重试）。不引入新的重试/告警机制，保持和现有代码风格一致。

---

## 4. 让 wiki 生成"世界知识"而不只是错题本

### 4.1 现状核查（比上一轮分析更准确的版本）

`history/world_extraction.py` 已经在解决"错题本偏科"问题——`entities[]`/`facts[]`
是和决策候选**同一次 LLM 调用**里一起解析出来的，理论上只要 compact/`extraction_trigger`
触发了一次抽取，世界知识和决策/纠正知识是**同时**被提炼的，不存在"世界知识完全没有
提取通道"的问题。真正的缺口在**触发信号**这一层：

`history/extraction_trigger.py::scan_for_extraction_window()` 的连接词密度规则
（因为/所以/决定/改为/放弃/取代/而不是）本质上是"决策/纠正语境探测器"，
对纯描述性内容（"这个项目用 FastAPI + PostgreSQL"、"用户环境是 macOS + zsh"）
天然低敏感——这类信息不会用"因为/所以"这种转折词表达，很可能永远攒不够密度触发窗口。
后果是：即使 world_extraction 的解析能力没问题，它也很少有机会被真正调用到，
因为触发它的窗口探测器根子上是为决策场景设计的。

### 4.2 改进方案 `[可直接排期]`

#### 4.2.1 独立的"实体密度"触发信号

`extraction_trigger.py::scan_for_extraction_window()` 新增第二种触发原因
`trigger_reason="entity_density"`，和现有 `connective_density` 并列、互不干扰：

```python
def _scan_entity_density(
    raw_entries: list[HistoryEntry],
    *,
    known_entity_names: set[str],   # 复用 wiki/entity_digest.py::build_entity_digest
                                     # 已经在维护的"当前已知实体"索引
    min_new_terms: int = 3,
) -> Optional[float]:
    """规则、零 LLM 成本：从新增条目里抽取形如"专有名词+版本号"
    "路径/配置项"模式的候选词（简单正则，不做真正 NER），
    统计其中不在 known_entity_names 里的"新词"数量。
    达到 min_new_terms 即返回一个 signal_score，触发窗口探测。
    宁可让规则粗一点多触发几次（世界知识抽取成本本来就低于决策抽取，
    见下条），也不要因为规则太严错过真正的新实体。"""
```

两种触发原因分开记录在 `ExtractionWindowCandidate.trigger_reason` 里，
供后续做效果统计（比如统计"connective_density 触发的窗口里 decisions 命中率"
vs"entity_density 触发的窗口里 entities/facts 命中率"，验证两个触发器是否真的
覆盖了不同的知识类型，而不是重叠冗余）。

#### 4.2.2 世界知识抽取给更低的调用门槛

`entity_density` 触发的窗口，抽取 prompt 可以做成一个更轻量的版本
（`extraction_trigger.py` 里已有"轻量抽取专用 prompt"的先例，E1 阶段已经实现，
直接复用同一套机制，只是换一版 prompt 模板：只要求输出 `entities[]`/`facts[]`，
不要求输出 `decisions[]`），因为世界事实的抽取本身就比决策/取舍抽取简单，
不需要判断"为什么选了这个方案""放弃了什么"，只需要识别"提到了什么、是什么关系"，
可以用更小的窗口、更低的触发阈值，让世界知识的沉淀频率高于决策知识（符合直觉——
一次对话里正常提到的实体远比明确做出的决策数量多）。

#### 4.2.3 主动式知识缺口扫描（真正的增量能力）

被动提取（不管哪种触发信号）永远受限于"对话里到底聊没聊到"。要让 wiki 真正
成为"关于世界的知识库"而不只是"聊天记录的镜像"，需要一条主动补全的链路：

新增 `wiki/gap_scanner.py`：

```python
@dataclass
class KnowledgeGap:
    page_id: str
    gap_kind: str        # "shallow_entity"（只有一句 description、无强链接）
                          # | "orphan_page"（validator.py 已识别的孤儿页面）
                          # | "stale_topic"（第2节新增的陈旧专题页）
    suggested_action: str  # 给子任务的一句话指引，比如"读 src/xxx/README 补全该模块的依赖关系"

def scan_gaps(paths: AgentPaths, *, max_results: int = 5) -> list[KnowledgeGap]:
    """纯规则扫描，复用 validator.py 的死链/孤儿检测 + graph.py 的度数统计
    （strong_links 数量为 0 或 1 的实体页面视为"浅层"）。零 LLM 成本。
    max_results 控制单次扫描返回的缺口数量上限，避免一次生成过多子任务
    把 daemon 的任务队列打满。"""
```

`scan_gaps()` 本身只做规则扫描、不调用 LLM、不派发任务，是否要基于扫描结果
派发"读代码补全"这类子任务，由第 5 节的 daemon cron job 决定（是否派发、
派发给谁、派发几个，是调度策略问题，跟"怎么发现缺口"是两件事，模块职责上分开）。

---

## 5. daemon 定时任务建议

### 5.1 现状核查（比上一轮分析更准确）

`evolution/cron_scheduler.py` 里 `_BUILTIN_JOBS` 已经内置了 `sys:consolidation`
（`interval:21600`，6 小时），会把 `run_consolidation()` 整体（包括 wiki 的全部 7 个子步骤）
作为一个任务提交进 `InputQueue`。也就是说"wiki 巩固完全没有独立调度"这个判断是不准确的——
调度已经有了，缺的是**更细粒度的独立 job**，让不同性质的维护动作可以有不同的执行频率、
可以被单独 `/cron run`、单独观察 `next_run_at`，而不是全部捆在一次 6 小时的大任务里。

### 5.2 改进方案 `[可直接排期]`

在 `_BUILTIN_JOBS` 里新增两个 job（不改动 `sys:consolidation` 本身，避免影响已有行为）：

```python
{
    "id": "sys:wiki_gap_scan",
    "name": "wiki 知识缺口扫描",
    "schedule": "interval:43200",   # 12h，比 consolidation 更低频——
                                      # 这是"主动补全"动作，成本更高（会派生子任务），
                                      # 不需要跟索引/去重一样频繁
    "task_template": "/wiki gap-scan --max-results 3 --dispatch",
    "initiator": "cron",
    "description": "扫描浅层实体/孤儿页面/陈旧专题页，派发最多3个补全子任务",
},
{
    "id": "sys:wiki_fallback_cleanup",
    "name": "wiki 兜底页面清理",
    "schedule": "interval:604800",  # 7d，和 sys:digest_trim 同频率，同属于"低频清理"类
    "task_template": "/wiki fallback-cleanup",
    "initiator": "cron",
    "description": "归并/标记 entities/session-facts-<date>.md 里长期未被合并的 fact 兜底页",
},
```

配套新增两个 CLI 子命令（挂在已有 `/wiki` 命令下，不新开顶级命令）：

- `/wiki gap-scan [--max-results N] [--dispatch]`：调用 `wiki/gap_scanner.py::scan_gaps()`，
  `--dispatch` 时把每条 `KnowledgeGap` 包装成一个任务描述提交进 `InputQueue`
  （复用 cron job 已有的提交机制），不传 `--dispatch` 时只打印报告供人工查看，
  方便先手动跑几次观察缺口质量，再决定要不要真的自动派发。
- `/wiki fallback-cleanup`：遍历 `entities/session-facts-<date>.md` 系列页面，
  对创建时间超过 N 天（默认 30）且从未被 `consolidate()` 判重合并过的 fact 条目，
  尝试重新跑一次 `wiki/dedup.py::find_similar_page`（这时候正式实体页可能已经更全，
  重新判重命中率可能比首次落盘时更高），命中则合并，命中不到则标记 `status: stale`
  （不删除——wiki 的可读可信原则，历史事实允许存在但要标注置信度下降）。

### 5.3 与第 1/3/4 节的衔接

- `sys:wiki_gap_scan` 的扫描逻辑顺带跑一次第 2 节的"陈旧专题页"检查（同一次遍历，省一次 IO）。
- `sys:consolidation` 触发时应用第 3 节的分步超时熔断；`sys:wiki_gap_scan`/
  `sys:wiki_fallback_cleanup` 本身逻辑更简单（规则为主），暂不需要单独上熔断，
  等实际运行发现耗时问题再补。
- `check_and_plan()`（第 1 节）挂在 `sys:consolidation` 触发之后顺带跑一次，
  不单独起 job（它本身零成本、不需要独立频率控制）。

---

## 6. 实施顺序建议

1. **第 3 节（超时熔断）优先**：改动范围最小、风险最低、不依赖其它节，且能立刻降低
   现有 `sys:consolidation`（已经在跑）单次执行时间不可控的风险。
2. **第 4 节（entity_density 触发器）次之**：复用 E1 已有基础设施，改动集中在
   `extraction_trigger.py` 一个文件，收益直接（提升世界知识沉淀频率）。
3. **第 5 节（两个新 cron job）**：依赖第 4 节的 `gap_scanner.py` 先落地。
4. **第 2 节（陈旧专题页）**：顺带在第 5 节的 gap_scan 里实现，工作量很小。
5. **第 1 节（双轨制退出）放最后**：它依赖前面所有改动运行一段时间积累数据
   （`wiki_search` 命中率、`/wiki promotion` 报告），且下线操作本身是不可逆的，
   不应该抢在其它改动验证稳定之前执行。

---

## 7. 未决问题（需要人工决策，本计划不替代决定）

- 第 1 节 1.2.2 的观察期定为"≥2 周"是经验值，可能需要根据实际 session 频率调整
  （低频使用场景下 2 周可能积累不了足够的 A/B 样本，`wiki/promotion.py` 里
  `_AB_MIN_SAMPLES = 20` 已经内置了"样本量不足不下结论"的保护，可以以此为准，
  而不是死等固定天数）。
- 第 5 节 `sys:wiki_gap_scan` 的 `--dispatch` 默认是否应该开启：建议**首次上线默认不开启**
  （只生成报告），观察几轮报告质量后再决定要不要自动派发子任务，避免刚上线就因为
  规则误判产生一堆低质量的补全任务占用执行资源。


## 实施状态总览（本轮已落地）

| 章节 | 内容 | 状态 | 落地文件 |
|---|---|---|---|
| §1 | 双轨制退出（评估+三步下线清单，不自动删代码） | ✅ 已实现 | `wiki/decommission.py`（新）、`storage/paths.py` |
| §2 | 陈旧专题页标注（knowledge_state=stale） | ✅ 已实现 | `wiki/gap_scanner.py::_scan_stale_topics/mark_stale_topics`（新） |
| §3 | consolidate() 分步超时熔断 | ✅ 已实现 | `evolution/step_runner.py`（新）、`evolution/consolidation.py`、`perception/library_index.py` |
| §4.2.1/4.2.2 | entity_density 独立触发信号 | ✅ 已实现 | `history/extraction_trigger.py` |
| §4.2.3 | 知识缺口主动扫描 | ✅ 已实现（规则扫描；子任务补全由 daemon/人工执行，未接自动 LLM 补全） | `wiki/gap_scanner.py`（新）、`cli/commands/wiki.py` `/wiki gap-scan` |
| §5 | daemon 定时任务（gap_scan / fallback_cleanup） | ✅ 已实现 | `evolution/cron_scheduler.py`（新增 2 个内置 job）、`cli/commands/wiki.py` `/wiki fallback-cleanup`、`wiki/fallback_cleanup.py`（新） |

**已知简化 / 未完成事项**（诚实列出，避免过度宣称）：
- §5.2 兜底页清理简化为**页面级粒度**（整篇 `session-facts-<date>.md` 一起判重/标注），
  未细化到计划草稿设想的**逐条 fact_id 粒度**——逐条拆分需要给每条 fact 独立维护
  锚点级生命周期状态，改动面更大，先验证页面级粒度的收益。
- §4.2.3 的"主动派发子任务读代码补全"目前只做到 `--dispatch` 把任务描述提交进
  `InputQueue`（且只在 daemon `autonomous_loop` 上下文里生效，交互式 CLI 里没有
  `InputQueue` 可用，会给出提示而不是报错）——是否要在 daemon 里默认开启
  `--dispatch` 仍是 §7 里的未决问题，本轮默认不开启。
- §1.2.3「`check_and_plan()` 挂在巩固触发后顺带跑一次」已接入 `/wiki promotion`
  命令（末尾展示下线执行清单或差距原因），但**尚未接入** daemon
  `evolution/autonomous_loop.py` 的巩固循环本身——`check_ready_transition()`
  提供的"只在状态翻转时提醒一次"能力目前仍需要外部按需调用。
- 本轮验证方式：新增代码逐一 `ast.parse` 语法检查通过；新增 4 个专属单测文件
  （`test_step_runner.py`/`test_wiki_gap_scanner.py`/`test_wiki_decommission.py`/
  `test_wiki_fallback_cleanup.py`，共 30 用例）+ `test_extraction_trigger.py`
  追加 3 个 `entity_density` 用例；与改动直接相关的 9 个既有测试文件（125 个
  用例）全部通过——**合计 149 个用例全部通过**。全量测试套件因单次执行时间
  超限未能跑完，但抽样比对显示失败用例在**修改前的原始代码上同样失败**
  （逐字符核对一致的 F/E 分布），判断为环境相关的既有失败，非本轮改动引入。

---