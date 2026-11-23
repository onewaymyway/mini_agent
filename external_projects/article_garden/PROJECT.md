# article_garden 领域文章库 → 知识 wiki → 自动扩展成文

独立外部项目：给定一个领域和一批已有文章，先整理成有结构、有关系、可追溯的知识树（wiki），
再持续发现缺口、规划选题、自动收集资料、写成新文章并回灌知识树，不断完善该领域的内容。
完整设计见 `next_doc/article_garden_external_project_plan.md`（含界面交互 §19、多文章库 §20、AI 领域冷启动 §21）。

> 遵循外部项目标准结构：完全自包含、可独立于 mini_agent daemon 运行，可整体移动到任意路径，
> 在 daemon 侧 `mini-agent projects register external_projects/article_garden`（或看板「外部项目 → 注册新项目」扫描选择）即可被看见。

## 当前进度：P3 知识树与覆盖度已完成

| 期 | 内容 | 状态 |
|---|---|---|
| **P0** | 项目骨架、多文章库（新建/切换/归档/删除）、章程、两层设置、看板框架、health | ✅ |
| **P1** | 文章导入（预览→确认）、单篇摘要（增量、校验、修复循环、长文分块）、章程草稿反推 | ✅ |
| **P2** | 概念归一、概念页、断言账本、关系边、冲突 | ✅ |
| **P3** | 知识树（骨架 + 增量挂载提案）、覆盖度、树页面 | ✅ |
| P4 | 缺口检测与选题 | 待做 |
| P5 | 资料收集 | 待做 |
| P6 | 成文 | 待做 |
| P7 | 闭环、挡位、预算、定时、导出 | 待做 |

**P3 新增的能力**
- **知识树** `wiki/tree.yaml`：方向 → 主题 → 概念。顶层方向来自章程的范围（scope.in）；没有范围时使用「未分类」。
- **增量挂载提案**：只把**尚未挂载**的概念交给模型，每批 ≤ 20 个：挂入已有主题，或在某个方向下新建主题。已挂载的概念不会被重新安排。代码校验：每个概念恰好一次，主题存在且未锁定，方向存在，新主题不与同方向已有主题重名。
- **确认后才生效**：提案列出全部操作（新建方向/主题、挂载、解除挂载），在看板或 `build_tree.py --apply` 确认后才写入树。提案记录生成时的树版本，树若已变化则拒绝应用（提示重新生成）。
- **无变化零调用**：没有未挂载概念、骨架也没有变化时，不调用模型，也不生成提案。
- **id 稳定**：节点 id 一经分配永不改变；改名、移动、合并都不换 id。
- **人工操作**（立即生效，每次保存前把旧树备份为 `wiki/tree.prev.yaml`）：改名、移动主题到其它方向、合并同层节点、锁定（锁定的节点不接收自动挂载）、在方向下新建主题。
- **覆盖度**（纯代码，`wiki/coverage.json`）：每个节点的文章数、平均字数、断言数、层级分布、有依据概念占比、最新导入时间、悬空概念数，以及红/黄/绿等级。阈值按库规模分档（< 50 篇时：充足需 ≥ 2 篇且平均 ≥ 1500 字）。

**P2 新增的能力**
- **概念归一**：规则打分（去空白后相同、章程术语表别名、字符串相似度、包含关系），中间分交给模型逐对确认“是否同指”，拿不准一律不合并。规范名优先取章程术语表。
- **概念页** `wiki/concepts/c-0001.md`：定义（点名概念、长度 60–400 字）与依据断言。定义中引用的断言 id 必须是提供给模型的，否则修复。没有依据断言的概念标记为「无依据」，不编造定义。
- **断言账本** `wiki/claims.jsonl`：每条断言 → 文章、原文摘录、所属概念。依据摘录必须在原文中。
- **关系边** `wiki/edges.jsonl`：类型为 is_a / part_of / prerequisite / related / contrasts / causes / example_of。每条边必须附带能在原文中找到的摘录和理由，否则**整条丢弃**（丢弃数量计入运行日志）。
- **冲突** `wiki/conflicts.jsonl`：同一概念来自不同文章的互相矛盾断言，保留双方出处；只允许跨文章的冲突。
- **增量与保护**：定义按依据哈希复用；关系与冲突按文章内容与概念对缓存；`human_edited: true` 的概念页不会被自动覆盖；概念 id 按写法重叠保持稳定。

**P1 新增的能力**
- **导入**：支持 `.md / .txt / .zip` 与目录（递归）。导入前先**预览**（新增 / 重复 / 跳过 及原因），确认后才写入。源文件只读，入库的是复制品 `library/imported/a-0001.md`。
- **去重**：按正文哈希判断，忽略空白差异（换行、缩进、空格不影响判定）；既对照已入库文章，也对照本次导入内部。正文不足 200 字的跳过。
- **文章编号**：`a-0001`、`a-0002`…… 稳定、不随改名变化。索引 `library/index.json` 丢失或手放的 Markdown 会自动从文件重建。
- **单篇摘要**：每篇一次结构化输出（论点、类型、层级、自评、概念、断言、悬空概念）。**引文必须逐字出现在原文中、概念名必须出现在原文中**，由代码校验；不通过时把具体问题回喂模型修复。每篇有修复轮数上限（`budget.max_repair_rounds`，默认 6），用尽则标记为失败并保留最后一次输出，不影响其它篇。
- **长文分块**：超过约 6000 字的文章按章节分块，逐块提取概念与断言，代码合并去重，再做一次元信息调用。
- **增量**：内容哈希与摘要版本都未变且上次成功 → 零模型调用。
- **章程草稿**：由已摘要文章反推章程草稿，写入 `drafts/charter_draft.yaml`。**草稿不会自动生效**，需在看板点击「采用为正式章程」（旧章程先备份为 `charter.yaml.bak`）。

**P0 已有的能力**
- 多文章库：每个文章库是一个完全独立的工作空间（`data/domains/<id>/`），页面代码只能经 `DomainContext` 访问当前库，路径越界会被拒绝；归档只隐藏、不删数据；彻底删除须输入库名确认并先导出 zip 备份。
- 新建向导与章程模板：`空白` / `通用技术` / `人工智能（预置 10 个方向骨架与中英术语别名）` / `复制另一个库的章程`。
- 章程编辑器（范围、读者、文风、术语表、来源偏好、禁区），保存时校验并备份旧版本；非法内容回退保守默认并给出警告。
- 两层设置（全局默认 + 本库覆盖，覆盖项高亮、可一键恢复继承）。**自动化开关只属于文章库，新库一律保守默认（全关、G0），不继承其它库**；P0 阶段这些开关只被记录，对应能力随后续各期生效。
- 看板：首页（流水线状态条、待办收件箱、下一步建议）、文章库列表、运行日志（实时轮询）、设置、所有文章库总览；知识树/概念图/选题/调研/审阅页是带说明的占位页（空状态即引导）。

## 目录约定
```
external_projects/article_garden/
├── project.yaml  PROJECT.md  app.py  requirements.txt  pytest.ini
├── entrypoints/     health  list_domains  create_domain  import_library  digest  draft_charter
│                    # 每个看板动作都有对应入口（后续各期补齐）
├── article_garden/  paths  store  charter  charter_form  templates  domains  settings  overview
│                    cancel  joblog  netproxy  providers/text.py
│   ├── library/     front_matter  hashing  catalog（索引）  importer（收集/预览/入库）
│   └── digest/      llm_io  validate（校验）  summarize（提示词/修复/分块）  runner（增量）  charter_draft
│   └── wiki/        model（存储）  normalize（归一）  content（定义/关系/冲突校验）  build（编排）  validate_util
│   └── wiki/        ... + tree（树存储与人工操作）  placement（挂载提案）  coverage（覆盖度）
├── ui/              state  topbar  jobs  home/library/graph/tree/runs/settings/wizard 页面  placeholder_pages
├── tests/           119 项（单元 + 入口 + Streamlit AppTest 冒烟；摘要测试使用确定性的假模型）
└── data/            运行产物，已加入 .gitignore（可用环境变量 ARTICLE_GARDEN_HOME 改位置）
    ├── domains.json         文章库注册表
    ├── settings.yaml        全局默认设置（稀疏覆盖）
    ├── backups/             删除/导出文章库时的 zip 备份
```

## 命令
```
python entrypoints/health.py                              环境自检（JSON，不访问网络、不调用模型）
python entrypoints/create_domain.py ai 人工智能 ai         新建文章库（模板 blank/general_tech/ai；--copy-charter-from <id>）
python entrypoints/list_domains.py [--all]                列出文章库（--all 含已归档）
python entrypoints/import_library.py ai <路径…>           导入预览（不写入）；加 --apply 确认导入
python entrypoints/digest.py ai                           增量摘要（需模型）；--article-id a-0003 / --force
python entrypoints/draft_charter.py ai                    生成章程草稿（需模型）；--adopt 采用为正式章程
python entrypoints/build_wiki.py ai                       构建概念与关系（需模型）；--force 重写全部定义
python entrypoints/build_tree.py ai                       生成挂载提案（需模型）并计算覆盖度；--apply 确认应用；--discard 放弃
streamlit run app.py --server.port 8508                   看板（端口约定：world_simulator 8502 … article_to_video 8507，本项目 8508）
python -m pytest                                          测试
```
看板由 `project.yaml` 的 `web_app` 块声明，可在 mini_agent 看板「外部项目」里启动；默认只监听本机。
注册：`mini-agent projects register external_projects/article_garden`。**默认不登记 cron**：摘要、调研、成文会调用模型与网络，属于高成本操作，由你手动触发；定时 cycle 在 P7 提供且需在文章库设置里显式打开。

## 依赖与 mini_agent 的关系
- 依赖见 `requirements.txt`：streamlit、PyYAML、pytest。
- 模型：复用 mini_agent 的 `LLMHelper`（与 article_to_video / info_hub 同一套 provider 配置）。P1 起摘要与章程草稿使用；测试中全部用假模型，不访问真实模型。搜索：P5 起复用 `mini_agent.web_search`。
- 不 import `src/mini_agent/wiki`（那套服务于 agent 自身记忆，语义与领域文章库不同），只借鉴其判重与生命周期思路。
- `cancel.py / joblog.py / netproxy.py` 复制精简自 article_to_video（自包含，不跨项目 import）。

## 已知限制 / 待验证事项
- **未做独立的自底向上聚类**：首版按章程骨架 + 模型逐批挂载。方案 §5.3 的自底向上聚类在文章库变大后再评估（§21-2 允许 20 篇规模如此）。
- **文章未回填 `tree_node`**：文章与节点的对应关系由概念推导，没有写回文章的 front matter。
- **提案预览是操作清单，不是“应用后的树”图**（方案 §19.3 的预览图未做）。
- **覆盖度的“证据强度”**取有依据定义的概念占比，而不是方案中的“有来源断言占比”；“新鲜度”只看导入时间。
- 删除的方向不会自动删除（保留节点 id），需要人工合并或改名。
- 真实模型下的挂载质量未验证（测试使用按阶段分派的假模型）。
- **P2 的关系与归一质量未在真实模型上验证**：测试使用按阶段分派的假模型。
- **归一的两两比较是 O(n²)**：几百种写法以内没有问题；规模再大需要分桶预筛（P3 前评估）。
- **超长文章不做关系抽取**：正文超过 12000 字的文章跳过关系步骤（依据摘录需要整篇原文做校验）。
- **冲突只读展示**：看板不能直接“采纳 A / 采纳 B / 并存”，也没有概念合并建议与手动加删边界面（方案 §19.3 中这些属 P3/之后的交互）。人工编辑目前只能直接改概念页并设 `human_edited: true`。
- **未使用共现信号**：方案 §5.2 提到的文章共现分在首版归一中未参与打分。
- **真实模型的摘要质量未验证**：所有测试用确定性的假模型。提示词在真实模型上是否稳定、修复轮数是否够用，需要用你的 20 余篇文章实际跑一遍再调整。
- **以下预算项在 P1 只是设置项，尚未强制执行**：`budget.step_timeout_minutes`（单步超时）、`budget.max_llm_calls_per_day` 与 `global_max_llm_calls_per_day`（日调用上限）。它们属于 P7「预算与自动化」，届时统一强制执行。当前只有“每篇修复轮数上限”真正生效。
- **看板里没有“停止”按钮**：摘要以子进程后台运行，而停止信号目前只在同一进程内有效；跨进程停止需要文件信号，留待 P7。
- 导入的文章默认标记为 `status: published`（视为你已发布的既有文章）；生成文章（P6 起）才会有 `draft / reviewed` 流程。
- 看板只做了 Streamlit AppTest 级别的冒烟测试，**没有在真实浏览器里点过**。
- 看板只做了 Streamlit AppTest 级别的冒烟测试（脚本真实执行、控件交互），**没有在真实浏览器里点过**；布局与观感需要你实际打开看一眼。
- 文本模型就绪检查依赖本机 mini_agent 的 provider 配置，未在真实模型环境验证。
- `data/domains.json` 损坏时看板会提示并停止（不会当作“空”继续，避免覆盖你的注册表），需手动修复或恢复备份。
- 自动化开关目前只被记录，不产生任何行为（P7 生效）。
