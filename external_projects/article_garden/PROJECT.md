# article_garden 领域文章库 → 知识 wiki → 自动扩展成文

独立外部项目：给定一个领域和一批已有文章，先整理成有结构、有关系、可追溯的知识树（wiki），
再持续发现缺口、规划选题、自动收集资料、写成新文章并回灌知识树，不断完善该领域的内容。
完整设计见 `next_doc/article_garden_external_project_plan.md`（含界面交互 §19、多文章库 §20、AI 领域冷启动 §21）。

> 遵循外部项目标准结构：完全自包含、可独立于 mini_agent daemon 运行，可整体移动到任意路径，
> 在 daemon 侧 `mini-agent projects register external_projects/article_garden`（或看板「外部项目 → 注册新项目」扫描选择）即可被看见。

## 当前进度：P0 骨架已完成

| 期 | 内容 | 状态 |
|---|---|---|
| **P0** | 项目骨架、多文章库（新建/切换/归档/删除）、章程、两层设置、看板框架、health | ✅ |
| P1 | 文章导入与单篇摘要 | 待做 |
| P2 | 概念归一与关系 | 待做 |
| P3 | 知识树与覆盖度 | 待做 |
| P4 | 缺口检测与选题 | 待做 |
| P5 | 资料收集 | 待做 |
| P6 | 成文 | 待做 |
| P7 | 闭环、挡位、预算、定时、导出 | 待做 |

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
├── entrypoints/     health.py  list_domains.py  create_domain.py   # 每个看板动作都有对应入口（后续各期补齐）
├── article_garden/  paths  store  charter  charter_form  templates  domains  settings  overview
│                    cancel  joblog  netproxy  providers/text.py
├── ui/              state  topbar  home/library/runs/settings/wizard 页面  placeholder_pages
├── tests/           64 项（单元 + 入口 + Streamlit AppTest 冒烟）
└── data/            运行产物，已加入 .gitignore（可用环境变量 ARTICLE_GARDEN_HOME 改位置）
    ├── domains.json         文章库注册表
    ├── settings.yaml        全局默认设置（稀疏覆盖）
    ├── backups/             删除/导出文章库时的 zip 备份
    └── domains/<id>/        一个文章库：charter.yaml settings.yaml library/ wiki/ plan/ research/ drafts/ runs/ logs/
```

## 命令
```
python entrypoints/health.py                              环境自检（JSON，不访问网络、不调用模型）
python entrypoints/create_domain.py ai 人工智能 ai         新建文章库（模板 blank/general_tech/ai；--copy-charter-from <id>）
python entrypoints/list_domains.py [--all]                列出文章库（--all 含已归档）
streamlit run app.py --server.port 8508                   看板（端口约定：world_simulator 8502 … article_to_video 8507，本项目 8508）
python -m pytest                                          测试
```
看板由 `project.yaml` 的 `web_app` 块声明，可在 mini_agent 看板「外部项目」里启动；默认只监听本机。
注册：`mini-agent projects register external_projects/article_garden`。**默认不登记 cron**：摘要、调研、成文会调用模型与网络，属于高成本操作，由你手动触发；定时 cycle 在 P7 提供且需在文章库设置里显式打开。

## 依赖与 mini_agent 的关系
- 依赖见 `requirements.txt`：streamlit、PyYAML、pytest。
- 模型：复用 mini_agent 的 `LLMHelper`（与 article_to_video / info_hub 同一套 provider 配置），P1 起使用；P0 只在设置页做“就绪检查”。搜索：P5 起复用 `mini_agent.web_search`。
- 不 import `src/mini_agent/wiki`（那套服务于 agent 自身记忆，语义与领域文章库不同），只借鉴其判重与生命周期思路。
- `cancel.py / joblog.py / netproxy.py` 复制精简自 article_to_video（自包含，不跨项目 import）。

## 已知限制 / 待验证事项
- 看板只做了 Streamlit AppTest 级别的冒烟测试（脚本真实执行、控件交互），**没有在真实浏览器里点过**；布局与观感需要你实际打开看一眼。
- 文本模型就绪检查依赖本机 mini_agent 的 provider 配置，未在真实模型环境验证。
- `data/domains.json` 损坏时看板会提示并停止（不会当作“空”继续，避免覆盖你的注册表），需手动修复或恢复备份。
- 自动化开关在 P0 只被记录，不产生任何行为。
