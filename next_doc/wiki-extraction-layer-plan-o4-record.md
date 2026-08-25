# wiki 提取层与组织层改进计划 · O4 实施记录

> 对应 `wiki-knowledge-base-extraction-and-organization-plan.md` §7（问题 O4：decision/experience/
> entity/fact 四类页面缺统一知识生命周期状态机），实施 §7.2.1（最小状态字段
> 集合）、§7.2.2（统一状态更新接口）、§7.2.3（fact 独立状态化）。依赖
> O1-O3、E1-E3（均已完成），按 §8 排期属于第四批，也是全部改进计划条目的
> 最后一项。

## 1. 改动内容

### 1.1 §7.2.1 最小状态字段集合

`src/mini_agent/wiki/writer.py` 新增：

- `update_lifecycle_fields(paths, page, *, knowledge_state=None,
  validated_by_append="", note="")`：统一写入三个 frontmatter 字段
  `knowledge_state`（fresh | stale | superseded）、`last_validated_at`
  （ISO8601 时间戳）、`validated_by`（来源标记列表，追加去重）。
  `note` 非空时额外在正文追加一段"历史沿革"并刷新 `updated`；否则
  `updated` 保持不变（与既有 `increment_grounded_hit_count` 一致：状态
  标记本身不算一次内容编辑）。
- `replace_body(paths, page, *, body)`：整体替换正文并刷新 `updated`，
  frontmatter 其余字段不变，供 fact 锚点粒度标记（§1.3）复用。

### 1.2 §7.2.2 统一状态更新接口

新增 `src/mini_agent/wiki/lifecycle.py`：

- `mark_page_state(paths, page_id, *, confidence, reason="",
  validated_by="", anchor=None) -> bool`：跨页面类型的统一入口。
  `page_id` 对 entity/decision/experience/topic/process 任意 `page_type`
  一视同仁（内部只是按 id 找到对应 `WikiPage` 后调用
  `update_lifecycle_fields`），不要求调用方区分目标页面类型。传入
  `anchor` 时改走 fact 锚点粒度标记（§1.3）。所有异常内部吞掉，返回
  `False`，不向上抛出。
- `touch_validated(paths, page_id, *, validated_by) -> bool`：记一次隐式
  验证（比如被检索命中）。若当前状态是 `stale` 则回升为 `fresh`；
  `superseded` 不因隐式验证回升——已被明确证据推翻的知识需要一次明确的
  反向 `mark_page_state(..., confidence="fresh"/"stale")` 调用才能恢复，
  避免被简单地重新检索命中就"洗白"。与 O1 已有的
  `increment_grounded_hit_count`（只维护命中计数）相互独立，可在同一个
  调用点先后调用。
- `stale_candidate_scan(paths, *, threshold_days=90) -> dict`：巡检函数，
  对 `knowledge_state=fresh` 且 `last_validated_at`（缺失时退回
  `created`）超过 `threshold_days` 的页面标记为 `stale`。只做标记，是否
  影响检索排序由 §1.4 的独立开关控制。

`src/mini_agent/agent/reminders_correction.py` 依赖的
`perception/library_index.py::mark_stale_from_correction()` 扩展：在原有
"标注遗留 `EntityStore` + 镜像 wiki 页面"逻辑之后，新增一步——若该实体已经
通过 `wiki/migration.py::load_entity_map` 能解析出对应的 wiki `page_id`，
额外调用 `mark_page_state(..., confidence="superseded",
validated_by="correction_check")`，把纠正检测的覆盖面从"只更新遗留
`EntityStore` 的 status 字段"扩展到"同时写入 wiki 页面的
`knowledge_state`"。两条标注路径并行存在、互不冲突；`wiki_paths` 未配置或
页面尚未镜像时静默跳过，不影响原有路径已完成的标注。

### 1.3 §7.2.3 fact 独立状态化

`src/mini_agent/wiki/world_writer.py`：

- 新增 `_next_fact_anchor(body, page_id)`：扫描正文里已有的
  `<!-- fact_id: <page_id>#fact-N; knowledge_state: ... -->` 注释，取最大
  N + 1 生成下一个锚点 id。
- 新增 `_fact_content_with_anchor(page_id, page_body, candidate)`：生成
  带锚点注释前缀的 fact 内容行，替换 `_merge_fact()` 里原来的纯文本
  `（confidence=...）statement`，两处调用点（合并进已有实体页面 / 归入
  当天兜底页面）均已切换到这个带锚点的版本。
- 效果：每条 fact 在正文里现在是
  `<!-- fact_id: client-pool#fact-3; knowledge_state: fresh -->\n（confidence=0.7）ClientPool 默认并发数是 4`
  这样一行注释 + 一行内容，不需要为每条 fact 单独开物理页面（避免页面
  数量爆炸，符合原计划 §7.2.3 的约束），`mark_page_state(paths, page_id,
  confidence="stale", anchor="client-pool#fact-3")` 可以原地更新这一行
  注释里的状态，不影响页面级 frontmatter 和同页面其它 fact。

### 1.4 检索排序集成（可选、默认关闭）

`src/mini_agent/wiki/search.py::_rule_score()` 新增
`lifecycle_discount_enabled: bool = False` 参数：开启后 `stale` 页面打
五折、`superseded` 页面归零（相当于从粗筛候选池排除，但页面本身不删除，
仍可通过 `/wiki <page-id>` 直接浏览）。默认关闭时与改动前完全一致
（回归保护）。

`src/mini_agent/config/models.py::MemoryConfig` 新增：

- `lifecycle_stale_threshold_days: int = 90`
- `lifecycle_discount_enabled: bool = False`

### 1.5 观测与 CLI

- `src/mini_agent/wiki/stats.py::WikiStats` 新增 `by_knowledge_state`
  字段，`compute_stats()` 统计每个页面的 `knowledge_state`（缺省视为
  `fresh`，与 `lifecycle.py` 的默认语义一致）。
- `src/mini_agent/cli/commands/wiki.py`：
  - `/wiki stats` 输出新增一张"按 knowledge_state"分布表。
  - 新增子命令 `/wiki lifecycle-scan [--days N]`，手动触发一次
    `stale_candidate_scan()`，`--days` 未传时取
    `MemoryConfig.lifecycle_stale_threshold_days`（无 agent 上下文时退回
    90）。

## 2. 与原计划的差异说明

- **字段名调整（最主要的偏离）**：原计划 §7.2.1 设想直接复用页面已有的
  `confidence` frontmatter 字段承载 `fresh | stale | superseded` 状态。
  但 `wiki/parser.py::WikiPage.confidence` 已经是一个 0-1 的数值型置信度
  分数（`render_page(confidence: Optional[float])`），与状态机语义完全
  不同，字面复用会导致同一个字段名承载两种不兼容的类型（数值 vs
  三态枚举），无法只依赖被动 `raw_frontmatter` 读取安全共存。因此改用
  独立字段名 `knowledge_state`，两个字段在 frontmatter 里并存、互不影响
  ——这是对原计划的必要修正，不是范围缩减。
- **`stale_candidate_scan()` 未接入 `consolidate()` 自动巡检**：原计划
  §7.2.2 提到"新增一个巡检任务（可以挂在 `consolidate()` 里作为新步骤，
  或独立定时任务）"，本身给出了两种选项。本次实现选择"独立触发"
  （`/wiki lifecycle-scan` 手动命令），未接入 `library_index.py::
  consolidate()` 的自动步骤链——原因：`consolidate()` 当前的调用链
  （`evolution/consolidation.py::run_consolidation()` →
  `evolution/autonomous_loop.py` / `cli/commands/evolve.py`）没有现成的
  途径把 `MemoryConfig` 传到 `LibraryIndex.consolidate()` 内部（现有各步骤
  都是显式关键字参数，不持有 `AppConfig` 引用），要打通需要改造多层调用
  签名，改动面明显超出 O4 本身的范围。手动 CLI 命令已经能满足"先跑一段
  观测期、用真实数据校准阈值"的执行纪律（§7.4 风险条款的核心诉求），
  自动挂载留待后续若确认需要再做，属于范围收窄但不影响验收标准（见 §3）。
- **检索排序折扣未做"排除 superseded section 内容"的 LLM 精排层面集成**：
  原计划 §7.3 验收标准提到"后续检索排序中该 fact 所在 section 不再被
  LLM 精排优先引用（prompt 里可以直接排除标记为 superseded 的 section
  内容，而不只是排除整个页面）"。本次只在规则粗筛层（`_rule_score`）
  做了页面级折扣（§1.4），未实现"从页面正文里摘除 superseded 的具体
  section 再喂给 LLM 精排"这一更细粒度的集成——这需要改造
  `_llm_rerank()` 组装 prompt 正文的逻辑，且 `lifecycle_discount_enabled`
  本身默认关闭、尚未有真实数据验证折扣阈值是否合理，在此之前投入更细
  粒度的 prompt 过滤性价比不高，留待观测期后按需补充。
- `mark_stale_from_correction()` 的覆盖扩展目前只延伸到"纠正事件命中的
  实体本身对应的 wiki 页面"，未按原计划 §7.2.2 字面意思扩展到"任意能
  通过 `source_entries` 血缘追溯到的页面"——`MemoryEntry` 的 `entry_id`
  与 wiki 页面 `source_entries` 存的是历史条目 id，两个 id 空间不直接
  对应，要做血缘追溯需要额外的映射层，本次未实现，范围收窄为"该实体
  自身的镜像页面"这一条已经打通的路径。

## 3. 验收方式（对应原计划 §7.3）

新增 `tests/test_wiki_lifecycle.py`（13 项用例，全部通过）：

- `mark_page_state` 对 entity/decision/experience 三类页面的分发正确性
  （状态、`validated_by`、`last_validated_at`、历史沿革追加均写入正确）；
  非法 `confidence` 取值、页面不存在两种失败场景返回 `False`。
- fact 锚点粒度状态标记：`world_writer.py` 生成的锚点注释可以被
  `mark_page_state(..., anchor=...)` 精确定位并原地更新；未知锚点返回
  `False`；同一页面连续两条 fact 分别获得 `#fact-1`/`#fact-2` 递增锚点。
- `touch_validated`：`stale → fresh` 正确回升；`superseded` 不因隐式验证
  回升。
- `stale_candidate_scan`：超期未验证的 `fresh` 页面被正确标记、刚验证过
  的页面不受影响、非 `fresh` 状态（如 `superseded`）不会被巡检"降级"。
- `search.py::_rule_score` 的 `lifecycle_discount_enabled`：默认关闭时
  分数与改动前完全一致（回归保护）；开启后 `stale` 页面得分精确减半、
  `superseded` 页面得分归零。

回归：`tests/` 目录下全部 `wiki`/`world_writer`/`extraction_trigger`/
`search_primary` 相关用例（含本次新增的 13 项）共 78 项全部保持通过；
在缺少 `rich`/`pydantic`/`json_repair` 等无关依赖的沙箱环境下，排除两个
因缺依赖而无法收集的既有测试模块后，跑了一遍全量 `tests/`（1750 通过 /
187 失败），确认失败用例均与本次改动的文件（`wiki/lifecycle.py`、
`wiki/writer.py`、`wiki/world_writer.py`、`wiki/search.py`、
`wiki/stats.py`、`config/models.py`、`cli/commands/wiki.py`、
`perception/library_index.py`）无关（失败集中在 `test_workdir_knowledge_tools.py`/
`test_skill_cli.py`/`test_skill_manager.py`，与本次未触碰的模块环境问题
有关），本次改动未引入新的回归。

## 4. 风险与兜底（延续原计划 §7.4）

- 所有新增写入路径（`update_lifecycle_fields`/`replace_body`/
  `mark_page_state`/`touch_validated`/`stale_candidate_scan`）遵循项目
  一贯的"失败不阻断主流程"风格：内部异常一律吞掉，返回
  `False`/空结果，不向上抛出。
- `lifecycle_discount_enabled` 默认关闭，`stale_candidate_scan()` 需要
  手动通过 `/wiki lifecycle-scan` 触发，不存在"上线即改变现有检索排序
  结果"的风险，符合 §7.4 "初期只记录不影响排序"的要求，也延续了 E2
  方案 C / P4 §6.5 一贯的"先观测、后切换"执行纪律。
- `mark_stale_from_correction()` 的新增分支包在独立的 `try/except` 里，
  即使 `load_entity_map`/`mark_page_state` 异常，也不影响该函数原有的
  `EntityStore` 标注和 wiki 镜像逻辑已经完成的工作。

## 5. 未在本次实施范围内的项

- `stale_candidate_scan()` 自动挂载进 `consolidate()` 巡检链路（见 §2
  差异说明）；`lifecycle_stale_threshold_days`/`lifecycle_discount_enabled`
  两个配置项也尚未接入 `evolution/consolidation.py::run_consolidation()`
  的显式参数链路，目前只在 `/wiki lifecycle-scan` CLI 命令与
  `wiki/search.py` 的直接调用点里生效。
- LLM 精排 prompt 层面按 section 粒度排除 `superseded` 内容（见 §2）。
- `mark_stale_from_correction()` 基于 `source_entries` 血缘的跨页面广播
  （见 §2），当前只覆盖纠正事件命中实体自身对应的页面。
- 至此，`wiki-knowledge-base-extraction-and-organization-plan.md` 列出的全部条目（O1-O4、
  E1-E3，以及已就位待观测切换的 E2 方案 C）均已完成实现；后续工作转为
  按各项遗留的"未在本次实施范围内"清单在真实观测数据支撑下逐步补齐，
  不再有新的设计阶段条目。
