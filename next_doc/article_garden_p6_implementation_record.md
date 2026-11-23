# article_garden P6 实施记录（成文与审阅）

- **关联方案**：`next_doc/article_garden_external_project_plan.md`（§8 阶段 F、§14 P6、§16 风险、§19.3「✍ 审阅」）
- **完成日期**：2026-11-23
- **状态**：已完成，等待确认后进入 P7
- **重要前置说明**：本期开始时，上传的代码包中**缺少 P5 的 `article_garden/research/` 包**（`entrypoints/research.py`、`ui/research_page.py`、`tests/test_research.py` 都引用它，导致看板无法启动、5 项冒烟测试失败）。本期先按 `tests/test_research.py` 与 P5 记录恢复了该包（见第 6 节），再实施 P6。

## 1. 验收对照（方案 §14 P6）

| 验收项 | 结果 |
|---|---|
| 校验全通过才入库 | 小节与全文校验、独立评审全部通过后才写入 `library/generated/`；失败时 `status=failed`，不入库，中间产物保留在 `drafts/<tid>/`（`test_forbidden_claim_exhausts_repairs_and_keeps_artifacts`、`test_persistent_critic_issue_fails_without_registering`） |
| 生成文章不成为唯一证据 | ① 只出自生成文章的概念不生成定义（`status: generated_only`）；② 定义与冲突只依据导入文章的断言；③ 生成文章不参与关系抽取（`test_generated_only_concept_gets_no_definition`、`test_mixed_claims_use_only_imported_for_definition`、`test_generated_articles_produce_no_relation_edges`）。已用变异测试确认第 ③ 条确实被测试覆盖 |

## 2. 新增 / 修改的文件（相对项目根目录）

| 路径 | 说明 |
|---|---|
| `article_garden/writing/__init__.py` | 包说明 |
| `article_garden/writing/checks.py` | 代码校验：小节级（证据标记、授权来源、连续照抄、禁用说法、范围外、用语统一、库内相似度）与全文级（字数、必备小节、免责声明、文体模板） |
| `article_garden/writing/brief.py` | 选题简报 `drafts/<tid>/brief.yaml`（读者、要点、互链、库内已有内容、来源清单、章程文风与禁区、编辑意见） |
| `article_garden/writing/outline.py` | 大纲：模型给出小节 / 要点 / 依据来源，代码校验小节数、标题唯一、必备小节、要点全覆盖、来源有效 |
| `article_garden/writing/draft.py` | 逐节初稿（每节只拿本节来源与前文标题）、组装（标题、免责声明、AI 声明、正文、延伸阅读 = 库内互链 + 参考来源脚注） |
| `article_garden/writing/critic.py` | 独立评审：另起一次调用，只看成稿与来源摘录，问题按小节归属 |
| `article_garden/writing/runner.py` | 成文主流程、入库（front matter + 目录索引）、回灌摘要、审阅动作（通过 / 发布 / 放弃）、退回重写 |
| `article_garden/planning/states.py` | 状态机扩展：`drafting / drafted / failed / reviewed / published`；`begin_draft / end_draft / move / revert_to / link_article` |
| `article_garden/wiki/build.py` | 生成文章守卫：断言账本记录 `origin`；只出自生成文章的概念 → `generated_only`；定义与冲突排除生成文章；关系抽取跳过生成文章 |
| `article_garden/research/*`（8 个文件） | **恢复**的 P5 代码包（见第 6 节） |
| `ui/review_page.py` | ✍ 审阅页（替换占位页）：成稿并排、证据与来源、校验与修复轮数、通过 / 确认发布 / 退回重写（附意见）/ 放弃（必须给原因） |
| `ui/placeholder_pages.py` | 移除审阅页占位 |
| `app.py` | 审阅页改为 `ui.review_page` |
| `entrypoints/write_article.py` | 成文与审阅入口：`<domain> <topic>`；`--feedback`、`--approve`、`--publish`、`--abandon <原因>` |
| `project.yaml` | 登记 `write_article`（managed，超时 3600 秒） |
| `tests/test_writing.py` | 20 + 2 项测试（全部假模型，见第 4 节） |
| `tests/test_ui_smoke.py` | 审阅页冒烟测试指向新页面 |
| `PROJECT.md` `docs/USAGE.md` `docs/README.md` | 文档更新 |

## 3. 关键实现与决定

| 事项 | 做法 | 理由 |
|---|---|---|
| 流程顺序 | brief → outline → 逐节初稿 → 组装 → 全文校验 → 独立评审 → 入库 → 摘要 | 方案 §8.1 |
| 修复预算 | 每篇共享 `max_repair_rounds`（默认 6）：小节内的代码校验失败、以及每次整篇重写都消耗一次 | 方案 §13「每篇最多修复轮数（硬上限）」 |
| 评审循环 | 评审或全文问题最多重写 3 轮（`MAX_DRAFT_ATTEMPTS`），之后 `failed` | 防止评审与重写无休止地来回 |
| 延伸阅读 | 由代码生成（库内互链 + 参考脚注），模型只需在大纲中列出标题 | 来源脚注必须与真实来源一致，不能让模型编造 |
| 引用 | 段落必须带 `[^src-NNNN]`；只能引用分配给本节的来源；组装时只为正文实际引用的来源生成脚注 | 方案 §8.1 第 4 步、§8.3 |
| 照抄 | 与摘录连续重合 ≥ `copy_chars_max`（默认 60）即拒绝 | 方案 §8.3 |
| 同质化 | 段落字符二元组 Jaccard ≥ `overlap_max`（默认 0.35）即拒绝，对比库内全部文章（排除本篇旧版） | 方案 §8.1 重复度校验 |
| 入库 | `library/generated/<id>.md`，`origin: generated`、`status: draft`，front matter 记录 `derived_from`、`evidence`、`concepts`、`content_hash`；目录索引同步 | 方案 §4.3、Q5 |
| 回灌 | 入库后立即执行摘要（A 步，`force`）；B/C 步（概念、树）仍需运行 `build_wiki` / `build_tree`（P7 才自动化） | 与方案 §8.1 第 7 步一致，闭环的自动部分留给 P7 |
| 重写 | 复用同一文章 id；旧版本保存在 `drafts/<tid>/revisions/` | 避免同一选题产生多篇文章 |
| 审阅 | 通过（drafted→reviewed）、发布（reviewed→published，需明确执行）、放弃（需原因，文章归档） | 方案 §8.1 第 8 步、§19.4 流程 C |
| 停止 / 异常 | 选题退回成文前状态并记录原因；`failed.json` 与各节草稿保留 | 方案 §16 |
| 模型未就绪 | `write()` 直接报错，不生成半成品 | 成文必须经过模型与校验，不能降级 |

## 4. 验证

- `pytest`：**173 项全部通过**（P5 恢复后的 152 项 + P6 新增 21 项：20 项成文测试 + 1 项边守卫测试）。
- `pyflakes` 无告警；`project.yaml` 经 daemon 的 `mini_agent.external_projects.manifest.load_manifest` 解析通过（10 个入口，含 `write_article`，web_app 端口 8508）。
- 入口脚本 `write_article.py` 与 `health.py` 在真实进程中运行正常。
- **变异测试**：移除“生成文章不产生关系边”守卫后，对应测试失败；恢复后通过。说明该测试确实覆盖了守卫。
- 测试使用**确定性假模型**（`WriterLLM`，按提示词阶段标记分派），覆盖：正常成文、照抄被拒并修复、未授权引用被拒并修复、禁用说法耗尽修复轮数后失败并保留产物、评审问题触发重写后通过、评审持续不通过失败、大纲缺必备小节后修复、停止后退回、摘要缺失校验、重写复用文章 id 并保留旧版、放弃需原因、审阅顺序、看板页渲染、入口脚本的审阅动作。

## 5. 与方案的偏差 / 未做

- **未在真实模型上验证**：大纲、正文、评审的提示词在真实模型上是否稳定、修复轮数是否够用，需要用 20 余篇 AI 文章的真实库跑一遍再调整。
- **文章类型模板不是“纯配置”**：方案 §8.2 要求模板为纯配置、新增类型不改代码。本期的文体要求写在 `checks.KIND_HINTS` 与 `checks.check_document` 中（对比表、教程小节、综述互链、FAQ），新增类型仍需改代码。
- **评审只有一个独立调用**：方案 §8.1 第 6 步未要求多个评审员，本期只做一个。
- **候选事实抽取未做**：沿用 P5 的决定（P5 记录第 5 节）。
- **“直接编辑后通过”未做**：审阅页只能通过、退回（附意见）、放弃，不能在页面里直接改正文。需要改动时请退回重写，或用编辑器改 `library/generated/<id>.md` 后再通过（改动会被下次重写覆盖，需谨慎）。
- **看板不能自动回灌 B/C**：入库后要手动运行 `build_wiki` 与 `build_tree`（或在看板对应页面）。
- **文章未回填 `tree_node`**：front matter 中的 `tree_node` 目前来自选题的 `node` 字段（选题生成时的节点），并未随挂载提案更新。
- **未导出**：生成文章的导出与站点导出在 P7。
- **阈值未校准**：`overlap_max`、`copy_chars_max` 使用默认值，未用你的语料校准（方案 §21-7）。
- **看板只做了 AppTest 冒烟测试**，没有在真实浏览器中点过。

## 6. P5 代码包恢复说明（重要）

本期开始时上传的压缩包缺少 `article_garden/research/` 目录。恢复依据如下，**不是**从原始源码逐字复制：

1. `tests/test_research.py`（16 项，作为行为规格）；
2. `entrypoints/research.py`、`ui/research_page.py`、`app.py` 中对模块的调用方式（函数名、参数、返回字段）；
3. `next_doc/article_garden_p5_implementation_record.md` 第 2–5 节的设计决定（可信度规则、充分性阈值、robots 与节流规则、中断恢复）。

恢复后的模块：`search.py`（搜索与缓存）、`fetch.py`（robots、节流、缓存）、`extract.py`（正文提取）、`sources.py`（来源卡与可信度）、`queries.py`（查询生成）、`pack.py`（证据包）、`runner.py`（调研流程、手动资料、删除来源）。

恢复后 152 项（P5 记录所述数量）全部通过。**如果你本地另有 P5 的原始代码，请以原始代码为准**：两者应当行为一致，但文件内部实现可能不同；本期的 P6 代码只依赖 `tests/test_research.py` 所定义的接口与 `pack.yaml`、`sources/src-NNNN.yaml` 的字段，因此可以直接替换。

## 7. 已知限制

- 文章中的 `[^src-NNNN]` 脚注只在正文实际引用时生成；评审只看成稿与摘录，无法发现“与未引用的来源相悖”这类问题。
- 修复预算用尽时保留的是最后一次整篇组装结果（`drafts/<tid>/last_output.md`），不是可直接发布的版本。
- 成文的中间产物不跨进程续跑：每次成文从大纲开始（重写时会复用旧文章 id，但内容重新生成）。
- 停止信号只在同一进程内有效（与 P1 摘要一致，跨进程停止留待 P7）。
