# info_hub 实施记录

- **关联方案**：`next_doc/info_hub_external_project_plan.md`（v2.0）
- **项目位置**：`external_projects/info_hub/`
- **约定**：每完成一个阶段，更新本文件的阶段状态与实施要点，并在 `/mnt/user-data/outputs/` 生成该阶段的打包文件（路径相对 mini_agent 根目录，直接覆盖即可）。

## 阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 项目骨架、纯文本存储工具、配置加载 | 已完成 |
| P1 | 适配器框架、rss / json_api 适配器、信息源注册与 run 命令 | 已完成 |
| P2 | 归一化、去重、增量对比（hot_list 排名变化） | 已完成 |
| P3 | 共享浏览器会话、html_list 适配器、login 命令 | 已完成（有头登录未在沙箱验证） |
| P4 | 定时报告、调度判定、tick 命令 | 已完成 |
| P5 | 看板基础版（含 serve 常驻服务） | 已完成 |
| P6 | 听报（Web Speech） | 已完成（语音输出需在本机确认） |
| P7 | 添加信息源（表单 / 对话生成 / 查重 / provider 选择） | 已完成（真实模型调用未在沙箱验证） |
| 补充 | Streamlit 主看板 | 已完成 |
| 清理 | 移除旧 HTML 看板，只保留 Streamlit | 已完成 |
| 补充 | 补齐外部项目契约（project.yaml / entrypoints），使其可注册 | 已完成 |
| P8 | 订阅源 | 已完成（具体站点待定，通用检查器已就绪） |
| P9 | 健壮性与收尾 | 已完成 |

## 待确认事项的默认取值

用户确认"开始实施"时未逐条回复第 17 节的待确认事项，本实施按以下默认值推进，确认后可再调整：

1. 项目位置：`external_projects/info_hub/`
2. 订阅站点：P8 再决定，先做 Checker 接口
3. 登录站点：按"有头浏览器手动登录一次"实现（P3）
4. 报告计划：`rolling` 每 3 小时、`daily` 每天 08:00（已写入 `config/settings.yaml`）
5. LLM 摘要：不实现，保留配置位置但默认关闭
6. 生成脚本的 LLM：P7 前再决定

---

## P0：项目骨架（已完成）

### 交付内容
- `project.yaml`：对外契约，声明 serve / run_source / report 入口，默认不启用调度（opt-in）。
- `config/settings.yaml`：路径、服务端口、默认抓取间隔、报告计划。
- `config/categories.yaml`：分类定义（热点社区、视频、小说、技术、资讯、其他）。
- `sources/_index.json`：信息源注册表，当前为空。
- `core/store.py`：原子写入（临时文件 + os.replace）、JSON / JSONL 读写、基于 fcntl 的文件锁。
- `core/settings.py`：加载配置，解析路径，提供 `Paths` 与 `Settings`。
- `tests/test_store.py`、`tests/test_settings.py`：共 7 个用例，全部通过。
- `.gitignore`：排除 `data/`、`reports/`、`browser/profile/` 等运行产物。
- `PROJECT.md`：项目说明与命令约定。

### 关键决策
- 测试框架选用标准库 `unittest`，不引入 pytest，依赖只有 PyYAML。
- 存储全部为文本，JSON 使用 `ensure_ascii=False`，中文可直接阅读。
- 文件锁使用独立的 `.lock` 文件，避免锁文件与数据文件互相干扰。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 7 tests ... OK
```

### 已知限制
- 文件锁依赖 fcntl，Windows 下退化为无锁，当前按 Linux / Termux 环境设计。

---

## P1：适配器框架与首批适配器（已完成）

### 交付内容
- `adapters/base.py`：`Adapter` 基类、`SourceSpec`、`RawItem`、`FetchResult`、`FetchContext`。HTTP 通过 `FetchContext.http_get` 注入，测试可替换。
- `adapters/registry.py`：扫描 `adapters/` 包自动注册，`kind_name` 重复时报错。
- `adapters/rss.py`：RSS 2.0 与 Atom，仅用标准库 `xml.etree`。
- `adapters/json_api.py`：通过点号路径（如 `data.list`）取列表与字段，支持 heat、summary、published_at 可选字段。
- `core/sources.py`：读取 `_index.json` 与各源 `source.yaml`，做必填字段、id 一致性、分类存在性、kind 合法性、适配器自校验。单个源出错不影响其他源。
- `core/normalize.py`：生成统一 Item 结构，稳定 id 为 `源id:url哈希`（无 url 时用标题哈希）。
- `core/runner.py`：执行单源，写 `data/raw/<id>/<日期>/<时分秒>.json` 快照、追加 `data/items/<id>/<日期>.jsonl`、追加 `data/runs/<日期>.log.md` 运行记录。失败时不写条目，但记录错误。
- `cli.py`：`list`、`validate`、`run <id>`。
- `tests/helpers.py`：在临时目录构造最小项目，供集成测试使用。
- `tests/test_adapters.py`、`tests/test_sources_and_runner.py`。

### 扩展机制的落地情况
- 新增适配器：在 `adapters/` 下新建模块并继承 `Adapter`，设置 `kind_name`，无需改注册表。
- 新增信息源：在 `sources/` 下建目录与 `source.yaml`，并在 `_index.json` 中登记。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 21 tests ... OK
python3 cli.py validate   -> 已加载 0 个信息源，0 个有错误；可用适配器: json_api, rss
```

### 与方案的偏差
- 方案中 `info_hub run` 写作命令名，实际实现为 `python cli.py run`，`info_hub` 入口待 P9 再包装。
- 当前 `items` 直接追加，未做去重。去重与增量对比按计划放在 P2。
- 真实外网抓取未在沙箱中验证（沙箱无外网），测试全部使用本地假数据。上线前需要在用户环境中用真实 RSS 地址验证一次。

---

## P2：归一化、去重与增量（已完成）

### 交付内容
- `core/pipeline.py`：
  - 状态文件 `data/state/<源id>.json`：记录已见条目（id → 首次见到时间）与上一次快照的排名。
  - 去重：已见条目不再写入 `items/*.jsonl`。
  - 过期清理：超过 `policy.retain_days` 的已见记录自动移除，防止状态文件无限增长。
  - hot_list 变化计算：新进（new）、上升（rising）、下降（falling）、掉出（dropped），并标记首次快照（first_snapshot）。
  - feed / recommend / custom：只输出 new。
- `core/runner.py` 集成流水线：状态读取、整理与写回在同一把文件锁内完成；原始快照新增 `new_count` 与 `changes` 字段；运行记录同时显示"抓取 X 条，新增 Y 条"。
- `tests/test_pipeline.py`：单元测试 5 个，集成测试 1 个（验证第二次抓取只写入新条目，且无重复）。

### 数据文件示例（人可直接阅读）
- `data/state/news_a.json`：`seen` 字典 + `last_snapshot.ranks`。
- `data/raw/news_a/2026-11-11/120000.json`：完整快照，含 `changes` 块。
- `data/items/news_a/2026-11-11.jsonl`：仅新增条目，每行一条 json。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 26 tests ... OK
```

### 备注
- 去重与清理阈值目前只有 `retain_days` 一个参数，`max_items` 仍由适配器截断。
- 增量对比目前基于内存中的单次快照比较，尚未做跨天的热榜趋势分析，该部分留给 P5 看板的"热榜"页面。

---

## P3：共享浏览器会话与 html_list（已完成，有头登录未在沙箱验证）

### 交付内容
- `core/browser_session.py`：`BrowserSession`
  - 所有站点共用同一个持久化 profile（`browser/profile/`），登录一次即可复用。
  - 所有 profile 操作在 `profile.lock` 内串行，避免两个浏览器进程同时占用同一 profile。
  - `fetch_html`：无头抓取；若最终 URL 包含站点的 `expired_pattern`，抛出 `LoginRequired`，并把状态记为 expired。
  - `interactive_login`：有头浏览器打开登录页，用户完成后回车，系统再用无头模式验证 `check_url`，成功则记为 logged_in。
  - `browser/login_status.json`：记录各站点状态与时间，不含任何凭据。
  - playwright 在方法内延迟导入，不使用浏览器的命令不会因此报错。
- `adapters/html_list.py`：基于 BeautifulSoup 的 CSS 选择器解析，字段支持 `选择器@属性`。`browser.required` 为 true 时经共享会话渲染；登录失效转为可读的错误结果，而不是静默失败。
- `config/sites.yaml`：站点登录配置（示例站点，需要替换为真实站点）。
- `core/runner.py`：新增 `default_context`，运行时自动注入浏览器会话与站点配置。
- `cli.py`：新增 `login <site>` 与 `login-status`。
- `adapters/base.py`：`FetchContext` 增加 `browser` 与 `sites` 字段。
- `core/settings.py`：新增 `load_sites`。

### 验证方式
- `tests/test_browser_session.py` 使用**真实无头 Chromium** 与本地 HTTP 服务器：
  - 第一个会话写入持久 cookie，第二个全新会话在同一 profile 下能访问登录后页面。
  - 没有登录态时抛出 LoginRequired，状态记为 expired。
  - `interactive_login` 的流程（用无头模式替代有头模式）能记录 logged_in。
  - html_list 通过真实浏览器完成端到端抓取。
- Chromium 无法启动时这些用例自动跳过。
- `tests/test_html_list.py` 使用假浏览器测试解析与错误转换。

### 验证中发现并修正的问题
- 测试服务器最初发的是会话 cookie（无过期时间），Chromium 关闭时会丢弃它，导致登录态无法跨次保留。这是真实风险：如果目标站点也只发会话 cookie，就需要额外的处理方式。已在 PROJECT.md 中注明，测试改为发持久 cookie。
- 测试中的 html 字段选择器写成了非法的 `"."`，已修正。

### 未在沙箱验证的部分
- **有头登录**：沙箱没有显示器，无法打开有头窗口。登录流程的代码路径已用无头模式覆盖，但真正的有头窗口需要在你的机器上验证一次：`python cli.py login <site>`。
- **真实站点**：知乎、微博、小红书等站点的选择器与登录判定都需要在真实环境中调整，示例配置只是占位。

---

## P4：定时报告与调度（已完成）

### 交付内容
- `core/scheduler.py`：
  - `parse_interval`（支持 Nm / Nh / Nd）、`parse_at`（HH:MM）。
  - `is_due`：`every` 类计划按间隔判定；`at` 类计划每天在指定时刻之后触发一次。
  - `source_is_due`：信息源抓取间隔判定。
- `core/digest.py`：
  - 按时间窗口收集条目（窗口外的过滤掉）。
  - 每个源取窗口内最新快照的热榜变化（新进、上升、下降、掉出）。
  - 收集窗口内的运行失败记录作为"异常"。
  - 按分类分节输出 markdown 与 json；写入 `reports/latest.json` 指针。
- `core/tick.py`：一次调度。运行所有到期的启用信息源，生成所有到期的报告；调度状态写入 `data/state/_scheduler.json`，重复调用安全。
- `core/runner.py`：运行记录改为固定的 `|` 分隔格式，便于解析。
- `cli.py`：新增 `report <plan_id>` 与 `tick`。

### 与方案的偏差
- 报告文件名为 `reports/<日期>/<HHMM>-<plan_id>.md`，方案中写的是 `<HHMM>.md`。加上 plan_id 后，同一时刻生成的多份不同计划不会互相覆盖。
- 调度采用"外部周期调用 tick"的方式（推荐交给系统 cron），而不是常驻 serve 循环。常驻循环与看板一起在 P5 的 serve 中实现。
- 定时表达式只支持 `Nm / Nh / Nd` 与 `HH:MM`，不支持 cron 表达式。
- 失败的源同样更新调度时间，避免失败时每次 tick 都重试造成风控风险。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 53 tests ... OK
```

端到端冒烟测试（在项目副本中、用本地 HTTP 服务器作为真实信息源）：
- `validate` 通过 2 个源。
- `tick` 同时运行两个到期的源，并生成到期的报告。
- 紧接着再次 `tick`，没有任何任务到期，什么都没执行。
- `report rolling` 生成的 markdown 中，RSS 源与 HTML 热榜源的条目都正确出现。
- 运行记录与状态文件均为纯文本，可直接阅读。

冒烟测试中发现并修正的问题：运行记录的错误信息与统计信息混在一起，导致异常小节把"抓取 0 条"也当成了错误内容。已改为固定的分隔格式并补充测试。

### 打包说明
P3 与 P4 的改动中，`cli.py` 与 `core/runner.py` 两个文件同时包含两个阶段的修改，无法干净地拆分。因此 P3 与 P4 合并为一个压缩包 `info_hub_P3-P4.zip` 交付，而不是分别打包。

---

## P5：看板与常驻服务（已完成；其中 HTML 前端与 HTTP 服务已在“清理”阶段移除）

### 交付内容
- `server/api.py`：看板业务逻辑，与 HTTP 解耦。
  - 信息源列表（含上次运行时间、最近条目数、配置错误）、启用/停用（原子写入 `_index.json`）。
  - 报告列表、读取报告（markdown 与 json，路径限定在 reports/ 内）。
  - 运行记录（解析固定格式的运行日志）、登录状态。
  - 立即抓取、立即生成报告、立即调度。
- `server/app.py`：标准库 `ThreadingHTTPServer`，无额外依赖。
  - 只绑定 127.0.0.1，Host 头不是本机则拒绝。
  - 所有 POST 必须带 `X-Info-Hub: 1`，外部网页的跨站请求无法附加自定义头，因此无法直接触发动作。
  - 静态文件只从 ui/ 读取。
  - 后台 tick 线程按 `defaults.tick_interval_minutes`（默认 5 分钟）调度，单次失败不会终止循环。
- `ui/index.html`、`ui/app.css`、`ui/app.js`：原生 JS 单页应用，无构建步骤。
  - Markdown 渲染前先做 HTML 转义，链接只允许 http(s)，防止报告内容注入脚本。
- `cli.py serve`：启动看板与后台调度。
- `adapters/__init__.py`、`server/__init__.py`：显式声明包。

### 验证方式
- `tests/test_api_and_server.py`：API 业务逻辑（启用停用、立即抓取、报告生成与读取、路径穿越拒绝、运行记录解析、登录状态）；HTTP 层（自定义头、本机限制、404、静态文件、目录穿越）。
- `tests/test_ui_smoke.py`：真实无头 Chromium 打开看板，切换各标签，检查没有 JS 报错。
- 端到端：在项目副本中启动 `serve`，用 curl 验证 POST 鉴权，再用真实浏览器检查报告视图、单条朗读按钮、播放器、信息源与运行记录页面。全部通过，没有 JS 错误。

### 验证中发现并修正的问题
- 报告列表的计划选项最初写死为 rolling、daily，改为由服务端从配置返回。
- 报告视图最初没有渲染单条朗读按钮（代码里留了一个空函数）。报告 json 中的条目原本不带 id，无法定位，补上 id 后按钮正常渲染。
- 测试中的 bytes 字面量包含中文，改为 str 编码。

### 与方案的偏差
- 方案中"登录"按钮在看板上触发。实际实现中看板只显示状态与命令，登录仍需在终端执行，因为有头浏览器需要终端中的回车确认，而看板请求是非交互的。
- 看板使用标准库而不是 FastAPI，减少依赖，代价是 API 的参数校验写得更手工。

---

## P6：听报（已完成，语音输出需在本机确认）

### 交付内容
- `core/speech_text.py`：把报告 json 转为朗读段落。
  - 去除 url 与 markdown 符号。
  - 按标点切分超长段落（上限 160 字），避免语音合成中途截断。
  - 段落带 kind 标记（概览、异常、分节、信息源、条目），便于前端展示进度。
- `server/api.py`：`/api/report/speech`、`/api/item/speech`。
- 前端播放器（底部固定栏）：
  - 播放、暂停、继续、停止、上一段、下一段、进度显示。
  - 语速（0.5–2.0）与语音选择，中文语音排在最前，设置保存在 localStorage。
  - 不支持 speechSynthesis 的浏览器会显示提示，而不是报错。
  - 整份报告听报与单条朗读共用同一个播放器。

### 验证方式
- `tests/test_speech_text.py`：URL 与符号清理、超长切分、报告段落覆盖概览/异常/分节/热榜变化/条目、单条朗读。
- 冒烟测试中播放器显示"1 / 5"进度，标题与报告一致，没有 JS 错误。

### 未在沙箱验证的部分
- **语音输出本身**：沙箱中的无头 Chromium 没有语音引擎，语音列表为空，无法真正发声。请在你的浏览器里点击"听报"确认声音与语速。
- **连续播报**：逐段 onend 推进的逻辑已写，但需要真实语音引擎才能验证段落间的衔接。
- **移动端**：手机浏览器对后台播放的限制因系统而异，看板只保证页面打开时可以播放。

### 已知限制
- 语音列表因浏览器与系统而异。Linux 环境下中文语音通常需要单独安装。
- speechSynthesis 在部分浏览器中需要用户先有一次点击才能发声，播放按钮满足这个前提。

### 打包说明
P5 与 P6 的前端在同一组文件中（app.js 同时包含看板与播放器），且 digest 的 id 字段改动同时影响两者，因此合并为 `info_hub_P5-P6.zip` 交付。


---

## P7：添加信息源（已完成，真实模型调用未在沙箱验证）

### provider 选择（按用户要求）
- 默认使用主项目的 `providers.json`，与主项目 agent 的 LLM 配置保持一致。
- 本项目可在 `config/llm.yaml` 中通过 `providers_file` 指定单独的配置文件。
- 本项目根目录若存在 `providers.json`，优先于主项目。
- 选择顺序：`config/llm.yaml` 指定 > 本项目 `providers.json` > 主项目 `providers.json`。看板显示实际生效的来源。
- 通过主项目的 `load_config(providers_config_file=...)` 与 `LLMHelper.from_config` 构造模型，不复制 provider 的格式与实现。

### 交付内容
- `core/llm.py`：provider 选择、`build_llm`、`provider_status`。
- `core/explore.py`：页面结构摘要（候选列表容器、样例、内嵌数据提示、JSON 形状）。不向模型发送整页 HTML。
- `core/fingerprint.py`：查重指纹（域名去掉 www 后比较；同域名且同列表选择器或同路径视为相似）。
- `adapters/script.py`：脚本适配器，`fetch(ctx)` 约定，静态检查禁止高风险导入与调用。
- `core/source_store.py`：校验（id 格式、分类、kind、适配器自校验、查重）、写入 `sources/`、草稿存储、预览。
- `core/source_generator.py`：LLM 对话生成（生成、修改、预览、确认、放弃），输出 JSON 解析（容忍代码块与前后文字）。
- `server/api.py`：表单预览/保存、草稿接口。`server/app.py` 改为正则路由表（21 条路由）。
- `ui/`：信息源页的添加面板（表单 / 对话生成）、草稿列表与草稿详情。
- `config/llm.yaml`：provider 来源配置，默认留空。

### 验证方式
- 单元与流程测试（`tests/test_p7_units.py`、`tests/test_p7_flow.py`、`tests/test_p7_http.py`）：使用假模型，覆盖：
  - provider 三级选择与缺失时的错误；
  - 查重（www 与裸域名、同选择器、不同路径）；
  - 页面摘要（候选选择器、内嵌数据提示）；
  - 脚本静态检查（禁止导入、禁止 exec/eval/open、缺少 fetch）；
  - 生成草稿时模型收到的是摘要而非整页 HTML；
  - **脚本草稿在生成时不执行，预览时才执行**；
  - 对话修改保持同一草稿 id，并携带用户反馈；
  - 相似源需要强制确认；分类错误阻止确认；
  - 表单预览与保存、409 冲突、404 与 405 路由行为。
- UI 冒烟测试：添加面板展开、显示 provider 状态、表单字段随适配器切换、切换到对话生成。
- 与主项目的集成（真实位置、临时 providers.json，不发起网络请求）：
  - 本项目 providers.json 存在时，选择为 project，并通过主项目 load_config 与 LLMHelper 成功构造；
  - 本项目无配置时，回退到主项目 providers.json，选择为 main，成功构造。
  - 临时文件均已删除。

### 未在沙箱验证的部分
- **真实模型生成草稿**：沙箱没有可用的 API key，只验证了配置链路与构造，没有实际调用模型。请在你的环境里用一个真实站点试一次"对话生成"，重点看生成的选择器是否正确。
- **真实站点的选择器质量**：摘要与模型输出的质量取决于具体页面，需要实际试用。

### 与主项目的依赖问题（观察到，未修改主项目）
- 主项目的 `src/mini_agent/utils/blocking_guard.py` 直接导入 fastapi，但主项目 `requirements.txt` 中未列出 fastapi。在干净环境中 `from mini_agent.config import load_config` 会失败。外部项目调用主项目 LLM 时会遇到这个问题。建议主项目补充依赖声明。

### 与方案的偏差
- 对话生成的"script"类型在 P7 实现，方案中 script 适配器原定放在 P7，已按计划完成。
- 草稿目录按源 id 命名，LLM 建议的 id 合法且不冲突时直接采用，否则加后缀。
- 修改次数上限为 5 次，方案未规定，属于为控制成本而加的限制。


---

## 补充：Streamlit 主看板（已完成）

按要求，与其他外部项目（stock_watch、novel_writer）保持一致，主看板改为 Streamlit 应用。

### 交付内容
- `app.py`：Streamlit 看板，启动命令 `streamlit run app.py --server.port 8506`（端口与 stock_watch 的 8503 错开）。
  - 复用 `server/api.py`，业务逻辑与 HTML 看板一致，不重复实现。
  - 报告页：摘要指标、Markdown 报告、整份听报与单条朗读。
  - 信息源页：表格编辑启用状态、立即抓取。
  - 添加信息源页：表单（预览、保存）与对话生成（草稿、预览、修改、确认、放弃）。
  - 运行记录页、登录页。
  - 侧边栏：provider 来源、后台调度状态、立即调度、生成报告。
  - 后台调度：`st.cache_resource` 保证每个进程只启动一次；可用 `streamlit.background_tick` 或环境变量关闭。
  - `INFO_HUB_ROOT` 环境变量：指定项目目录（测试与多实例用）。
- `dashboard/speech.py`：听报播放器 HTML。段落 JSON 嵌入脚本前转义 `</`，防止报告内容闭合 `<script>`。
- `requirements.txt` 增加 streamlit；`project.yaml` 增加 dashboard 入口；`config/settings.yaml` 增加 streamlit 配置块。

### 验证方式
- `tests/test_streamlit_app.py`：使用 `streamlit.testing.v1.AppTest` 无头运行 app.py。覆盖：空项目渲染、有数据时报告与信息源显示、provider 缺失时显示错误而不崩溃、立即抓取按钮、听报组件转义、以及"禁止裸表达式语句"的回归测试。
- 真实服务验证：用临时项目（带 RSS 源与报告）启动 `streamlit run`，用无头 Chromium 打开页面并切换各页签，截图检查。没有 Streamlit 异常。

### 真实验证中发现并修正的问题
- 侧边栏把一行三元表达式 `st.info(...) if ... else st.error(...)` 当成"魔法输出"，把源码和 DeltaGenerator 对象打印到了页面上。Streamlit 会渲染裸表达式语句，已改为 if/else，并增加回归测试。

### 未在沙箱验证的部分
- **听报声音**：无头浏览器没有语音引擎，语音列表为空。请在本机打开看板确认朗读。
- **iframe 内的语音权限**：听报播放器运行在 components.html 的 iframe 中。用户点击"播放"按钮后应能正常发声，但需要在 Chrome/Edge 上实际确认。

### 与方案的偏差与说明
- ~~旧的 HTML 看板（`cli.py serve`）保留，作为可选的旧版~~ ——**已在“清理”阶段移除**，见下文。
- 为兼容 requirements 中声明的 `streamlit>=1.37`，没有改用较新的 `width="stretch"` 参数，因此 streamlit 1.67 会打印弃用提示。功能不受影响。


---

## 清理：移除旧 HTML 看板，只保留 Streamlit（已完成）

### 动机
P5/P6/P7 先做了原生 HTML 看板，随后补了 Streamlit 看板，两者并存会让每次功能改动都要维护两套界面。按要求删除旧 HTML 看板，只保留 Streamlit。

### 删除
- `ui/`（`index.html`、`app.js`、`app.css`）
- `server/app.py`（标准库 HTTP 服务、路由表、自定义头与本机 Host 校验）
- `cli.py serve` 子命令及 `cmd_serve`
- `config/settings.yaml` 的 `server:` 块（host/port 8765）；`core/settings.py` 的 `server_host` / `server_port`
- `project.yaml` 的 `serve` 入口（保留 `dashboard` 入口）
- 测试：`tests/test_ui_smoke.py`（HTML 页面冒烟）、`tests/test_p7_http.py`、`tests/test_api_and_server.py` 中的 `ServerTests`（HTTP 层）

### 保留与迁移
- `server/api.py` **保留**：它是 Streamlit 看板调用的业务层，不含 HTTP。`server/__init__.py` 说明改为“业务 API”。
- `start_tick_loop`（后台调度线程）原在 `server/app.py`，Streamlit 看板也在用，**迁到 `core/tick.py`**；`app.py` 改为 `from core.tick import start_tick_loop`。行为不变：单次 tick 失败只打印、不终止循环。
- 测试：`tests/test_api_and_server.py` 的 `ApiTests` 保留并改名 `tests/test_api.py`；`test_p7_http.py` 的业务断言（无 provider 返回 503、草稿 404、表单创建后可列出）改为直接调用 api 层，落在新文件 `tests/test_p7_api.py`；新增 `tests/test_html_dashboard_removed.py`（旧文件不存在、`serve` 不可用、`project.yaml` 仅有 streamlit 看板、settings 无 server 块、`start_tick_loop` 能运行/停止/容忍失败）。
- 随 HTTP 层一起删除的测试（自定义请求头 403、非本机 Host 拒绝、路径路由 404/405、静态文件目录穿越）只针对已移除的 HTTP 服务，不再适用。报告路径穿越的防护在 api 层（`_safe_report_path`），其测试 `test_report_path_traversal_rejected` 保留。

### 行为变化（需知悉）
- 不再有 `python cli.py serve`。需要“无界面常驻调度”时，用系统 cron / 计划任务每 5 分钟执行 `python cli.py tick`；有看板时调度随 Streamlit 进程运行。
- 端口 8765 不再被本项目占用（主项目 HTTP API 默认端口也是 8765，此前两者配置易混淆）。

### 验证
```
python -m unittest discover -s tests -t .
Ran 108 tests ... OK
```
（含 `tests/test_streamlit_app.py` 的 AppTest 无头运行，确认 `app.py` 在移除 `server/app.py` 后仍可渲染。）

### 覆盖解压后需手动删除的旧文件
zip 覆盖不会删除文件。在 mini_agent 根目录执行（PowerShell）：
```
Remove-Item -Recurse -Force external_projects\info_hub\ui
Remove-Item -Force external_projects\info_hub\server\app.py
Remove-Item -Force external_projects\info_hub\tests\test_ui_smoke.py
Remove-Item -Force external_projects\info_hub\tests\test_p7_http.py
Remove-Item -Force external_projects\info_hub\tests\test_api_and_server.py
```
包内同时附带 `external_projects/info_hub/DELETED_FILES.txt` 记录这份清单。

### 未验证
- 沙箱未做浏览器级的 Streamlit 页面截图复查，只有 AppTest 无头测试；看板 UI 本身本阶段未改动。


---

## P8：订阅源（已完成）

### 站点未定的处理
第 17 节“订阅站点范围”一直未回复，按本记录开头的默认值“先做 Checker 接口”：实现**通用**检查器，不绑定具体站点；之后确定站点时，多数只需写一份 `source.yaml`。

### 交付内容
- `checkers/`（新包，自动发现，与 adapters 同构）：`base.py`（`Checker`/`Cursor`/`CheckResult`）、`registry.py`、`wrapped.py`（包装现有列表适配器）、`feed_latest.py`（包装 rss）、`json_latest.py`（包装 json_api）、`html_latest.py`（包装 html_list，可走共享登录态）。包装时把 `max_items` 放宽到 1000，保证“从旧到新”排列的目录页不会因截断丢掉最新项。
- `adapters/subscription.py`：调用 Checker → `order` 处理 → 关键词过滤 → 输出当前最新一条（`extra.cursor`）。校验：kind 必须为 subscription、checker 存在、order/关键词格式、并校验被包装适配器的配置。
- `core/pipeline.py`：subscription 走游标比较而不是“已见去重”；首次只记基线（`notify_on_first` 可选开启）；变化才产生条目并带 `previous_title`；无候选不动游标。状态文件新增 `cursor` 字段。
- `core/normalize.py`：订阅条目 id 以游标值为准。
- `core/digest.py`：新增 `subscriptions` 与 `summary.subscription_updates`；markdown 新增置顶的“## 订阅更新”（形如“《某小说》更新至 第2章（上次：第1章）”）；订阅条目不再进入分类小节；只有订阅更新时不再显示“没有可展示的内容”。
- `core/speech_text.py`：听报先读订阅更新（kind=subscription）。
- `server/api.py::list_subscriptions`、`app.py` 新增“订阅”页签（当前进度、最近检查/更新、最近更新列表）。

### 验证
```
python -m unittest discover -s tests -t .
Ran 129 tests ... OK
```
新增 `tests/test_p8_subscription.py`（19 用例：注册与校验、基线→更新→无变化、游标回退、notify_on_first、关键词过滤、无候选保持游标、抓取失败保持状态、json oldest_first 且 max_items=3 不丢最新项、html_latest、报告/听报/api）与 `tests/test_streamlit_app.py` 两个订阅页用例（AppTest 无头）。测试全部使用本地假数据。

### 测试中发现的问题
- 自己写的测试数据里 RSS 链接用的是序号，导致不同章节 url 相同、游标不变——说明 `cursor_from: auto` 优先取 url：**如果站点的 url 不随章节变化，请改用 `cursor_from: title`**，已写入 PROJECT.md。
- html 的 `title: ""` 不会取行自身文本（html_list 的既有行为），文档示例已改为 `list_selector: ".chapter li"` + `title: "a"`。

### 未做 / 未验证
- 表单与对话生成不支持创建订阅源（需手写 yaml）。
- 真实站点（小说站、视频频道）与真实网络未验证；站点定下后再配置。
- 看板订阅页只有无头测试，没有浏览器截图复查。
- 订阅源与普通源共用调度间隔与失败处理；P9 的“连续失败降频/暂停”会一并覆盖订阅源。


---

## P9：健壮性与收尾（已完成）

### 默认值决定（请确认）
方案要求“连续失败自动降频、自动暂停”。这会改变调度行为，按“新功能保守、opt-in”的原则拆成两层：
- **记录与标红始终开启**：连续失败计数、看板标红、报告“异常”、运行记录“告警”，不改变抓取频率。
- **降频与自动暂停默认关闭**：`config/settings.yaml` 的 `health.enabled: false`，改为 true 才生效。若你希望默认开启，改一个值即可，测试已覆盖两种状态。

### 交付内容
- `core/health.py`：`_health.json` 读写、`record_result`（成功清零/失败累加，返回 degraded/paused/recovered 事件）、`effective_interval`（降频后的间隔，不会把源本身更长的间隔缩短）、`resume`、`status_of`、配置加载（写错退回默认）。
- `core/runner.py`：三个返回路径都记录健康；记录失败（如磁盘问题）只打印，不影响抓取结果。
- `core/tick.py`：已暂停的源跳过并计入 `paused_sources`；到期判断改用 `effective_interval`；`cli.py tick` 输出暂停提示。
- `core/digest.py`：新增 `health` 列表与 `summary.problem_count`；“异常”小节列出已暂停/连续失败/配置错误（与窗口无关，因为这些源不再产生失败记录，不列出就会悄悄消失）。`core/speech_text.py` 听报同步朗读。
- 运行记录新增事件行：降频/告警/暂停/恢复（`server/api.py::list_runs` 解析放宽，运行记录页可见）。
- `server/api.py`：`list_sources` 增加 health/failures/last_error；新增 `resume_source`。
- `app.py`：信息源表新增“健康”列；“异常信息源”区带“恢复”按钮。
- `cli.py`：新增 `health`、`resume <id>`。
- `config/settings.yaml`：新增 `health` 块（默认关闭）。
- `PROJECT.md`：新增健康状态、扩展指南、运维速查三节。

### 验证
```
python -m unittest discover -s tests -t .
Ran 141 tests ... OK
```
新增 `tests/test_p9_health.py`（12 用例）。验收场景“人为制造连续失败”：假网络持续失败，用 `tick(now=...)` 推进 40 个 10 分钟周期——前几次间隔 10 分钟，随后间隔变长，第 6 次失败后自动暂停并不再请求（共 6 次调用），运行记录有降频与暂停，报告“异常”显示“已自动暂停，需手动恢复（连续失败 6 次）”，听报读出；`resume` 后重新运行；手动抓取成功同样恢复。另覆盖默认关闭时只标红不降频不暂停、配置错误源进报告、API/CLI/Streamlit（含点击“恢复”按钮）。变异检查：移除暂停跳过、移除降频放大、移除 enabled 门控，测试均转红。

### 与方案的偏差
- 方案写“连续失败 N 次降低频率并标红”，实现为标红始终开启、降频需 `health.enabled`（见上）。
- 登录失效类错误与其他失败同样计数；若需要对它单独处理（例如不降频），可后续加。
- 方案 P9 的“原始样本保留（失败时保存响应前若干字节到 samples/）”**未实现**：适配器错误以异常字符串形式返回，拿不到原始响应，需要改适配器接口，且与已有的运行日志/错误信息重叠，暂不做。

### 整体收尾状态
P0–P9 均已完成。仍未在真实环境验证：有头登录、语音输出、真实模型生成信息源、真实站点选择器与订阅站点。


---

## 补充：补齐外部项目契约（已完成）

### 问题
看板“注册新项目”扫描 `external_projects/` 时提示 `info_hub` 暂不能注册：`entrypoints.run_source: 缺少必填字段 'cmd'`。原因是 P0 起 `project.yaml` 用了不被框架识别的 `command` 字段，另外还缺 `health_check`、`entrypoints/` 脚本等规范内容。

### 对照规范补齐（参考 stock_watch / novel_writer 与 external-project-manager 模板）
- `project.yaml` 重写：`cmd`（非 `command`）、`timeout_sec`、`params`；去掉框架不认识的顶层字段（`version`/`description`/`enabled_by_default`）与把看板当 entrypoint 的 `dashboard` 项（看板改由 `web_app` 声明）；新增 `health_check`、`resources`、`review`（enabled: false）、`web_app`（entry app.py，端口 8506）。
- 新增 `entrypoints/`：`_common.py`（引导 sys.path、`run_entrypoint` 记账本，无 mini_agent 时降级只执行、`INFO_HUB_ROOT` 指定根目录）、`tick.py`、`run_source.py`、`report.py`、`resume_source.py`、`health.py`。入口复用 `cli.main`，与 `python cli.py ...` 同一代码路径。
- 四个业务入口标 `concurrency: unmanaged`（不调 LLM）并写 `timeout_sec`。
- `.gitignore` 增加 `.agent/run_status.jsonl`、`.agent/improvement_backlog.jsonl`、`.run/`；`PROJECT.md` 补齐目标/依赖策略/已知限制/entrypoints 接入/变更记录。
- `.claude/skills/external-project-manager/reference/project_yaml_schema.md` 端口约定补 info_hub 8506。

### 默认值决定（请确认）
**`tick` 没有声明 `schedule`**。原因：看板进程已自带后台调度；Windows 下文件锁退化为无锁，daemon 再定时跑 tick 可能与看板后台调度重叠、重复抓取。注册默认也不开自动调度。需要 daemon 定时时，取消 `project.yaml` 里 `# schedule: "cron: */5 * * * *"` 的注释，并把 `streamlit.background_tick` 设为 false。

### 验证
- 框架 `load_manifest` 校验通过；扫描接口不再把 info_hub 标为不可注册，可正常注册（默认 enabled=False）。
- 新增 `tests/test_external_projects_manifests_valid.py`（主仓库，8 用例）：**所有** external_projects 的 project.yaml 都必须通过校验、entrypoint 脚本存在、web_app 端口不冲突；info_hub 契约断言。
- 新增 `external_projects/info_hub/tests/test_entrypoints.py`（8 用例，真实子进程运行）：health 正常/源配置有误只告警/配置损坏失败；缺参数退出码 2；run_source 失败返回非 0 并写运行记录；resume 无状态返回 1；tick 正常、report 未知计划失败；有框架时写 `.agent/run_status.jsonl`（成功与失败都记）。
- info_hub 全部测试通过（含新增）。
未验证：真实 daemon 注册后由 daemon 触发 entrypoint、看板启动 info_hub 的 Streamlit（沙箱只有子进程与 AppTest）。


---

## 补充：专用浏览器工具条丢失标记的二次修复（已完成）

### 问题
小红书笔记从看板“在专用浏览器中打开”后，地址栏里没有 `#ihk=...`，反馈工具条不出现。上一次修复（`document_start` 读取并写入 sessionStorage）只处理“脚本读到之后被改写”，不覆盖“脚本运行前片段已丢失”。

### 改动
- 新增 `core/pending_open.py`：看板打开时登记条目上下文到 `data/state/pending_open.json`；按“主机 + 路径”匹配，路径变化时按最后一段 ID 兜底；24 小时过期、最多 200 条。
- `server/api.py`：`open_in_dedicated_browser` 打开前登记；新增 `resolve_context_api`。
- `server/feedback_receiver.py`：新增 `POST /v1/context`（需令牌、限扩展来源）。
- 扩展 0.2.0：内容脚本在片段与 sessionStorage 都没有上下文时，经 background 查询 `/v1/context`；新增选项“在所有页面显示反馈工具条”（默认关闭），开启后无匹配页面以手动反馈模式显示（`source_id=manual`，不统计打开与停留）。
- 测试：新增 `tests/test_pending_open.py`；`tests/test_extension_e2e.py` 重构并新增 3 个有头 Chromium 用例。

### 默认值决定（请确认）
**“在所有页面显示”默认关闭**。理由：保守默认，避免普通浏览被打扰、避免产生与看板条目无关的反馈数据；正常从看板打开的页面已由登记机制覆盖。需要时在扩展选项勾选即可。

### 验证与未验证
- 全量 info_hub 测试：新增与受影响用例全部通过；仍有 7 个与本次无关的既有失败（沙箱缺少 streamlit 与 mini_agent 框架，原始代码同样失败）。
- 未验证：真实小红书页面与真实 Windows Chrome；手动模式下点击工具条按钮的完整路径。


---

## 补充：插件配对与看板打开方式简化（已完成）

### 需求
1. 插件令牌能否在看板直接设置，不必去插件里填。
2. 去掉内嵌页面等打开方式，改用专用浏览器。
3. 看板上所有显示抓取内容地址的地方增加“打开”按钮，不再需要先选条目。

### 改动
- 配对：新增 `core/pairing.py`、`/v1/pair`、`api.pairing_start/status/cancel`；看板“服务管理”新增“插件配对”；插件 `background.js` 在无令牌/令牌失效时自动配对，工具条出现时发送 `ensure`。
- 打开：`api.open_in_dedicated_browser` 支持无条目的纯打开（候选、配置地址），打开成功后服务端记录一次“打开”；插件不再上报“打开”（修复此前看板与插件重复计数）。
- 看板 `app.py`：新增 `open_button`；抓取结果改为逐行显示并带按钮（默认 30 条，可显示更多）；订阅、报告单条、候选、编辑源地址同样加按钮；阅读与反馈面板去掉三种打开方式与“设为默认”，保留反馈表单。
- `core/feedback.py`：反馈事件的 `open_mode` 接受 `dedicated`（此前被丢弃为空）。
- `config/settings.yaml`：`view.default_open_mode` 标注已弃用。

### 默认值与取舍（请确认）
- 配对窗口 5 分钟、一次性；窗口外拒绝。窗口内同一浏览器的其他扩展理论上可抢先取走令牌（来源无法钉死具体扩展 ID）。
- 抓取结果由可排序表格改为逐行列表，以便每行放按钮；放弃了表格的列排序与整表导出能力，默认只渲染前 30 条。
- **未删除**已不用的后端：`core/reader.py`、`read_article`、`view_prefs`、`set_default_open_mode` 及对应测试，待确认后清理。

### 验证与未验证
- 全量 info_hub 测试（装了 streamlit 后）377 项，仅剩 2 个与本次无关的既有失败（缺 mini_agent 框架，原始代码同样失败）；有头 Chromium 的 6 个插件用例全部通过。
- 未验证：真实 Windows 下按钮拉起专用浏览器；真实小红书页面。


---

## 补充：看板“反馈记录”页签（已完成）

- `server/api.py` 新增 `list_feedback_history`（动作/信息源/关键词/最近天数筛选，新的在前，返回筛选后计数、总数与出现过的信息源）。
- `app.py` 新增“反馈记录”页签：筛选条、计数汇总、逐条记录（原因、备注、信息源名称）、“打开”按钮、显示更多。
- 新增 `tests/test_feedback_history.py`。
- 取舍：只读，不提供删除/撤销；默认每页 30 条。
- 验证：全量测试见下方汇总；真实 Streamlit 渲染截图确认布局。


---

## 补充：抓取结果行内的感兴趣 / 不感兴趣（已完成）

- `app.py` 结果列表每行新增 👍 / 👎 按钮：直接调用 `api.record_feedback`；最近一次反馈高亮；同动作重复点击只提示不重复写入；改标新增一条记录。
- 取舍：只放“感兴趣 / 不感兴趣”两个（“稍后看”仍在下方面板与页面工具条）；不提供撤销，只能改标；原因与备注留空。
- 测试：`tests/test_feedback_dashboard.py` 新增行内反馈用例。全量见交付说明。


---

## 补充：新增财经信息源（已完成，未经真实页面验证）

- 新增 `eastmoney_news`（东方财富首页，普通抓取）与 `netease_money`（网易财经，浏览器渲染）；`sources/_index.json` 登记，`config/sites.yaml` 新增 `netease_money` 站点；`html_list` 适配器非 UTF-8 页面改为 gb18030 回退解码。
- 新增 `tests/test_finance_sources.py`（6 用例：配置加载、分类存在、选择器只取文章链接、`//` 协议头补全、GBK 页面不乱码、浏览器路径与等待选择器）。
- 取舍：分类沿用“资讯”，未新增“财经”分类（避免改变现有报告范围）；网易财经用浏览器渲染是基于“网页抓取只看到文字无链接”的判断，可能并不必要，也可能选择器与真实链接形态不符。
- **未验证**：沙箱访问不了这两个站点，真实页面是否抓得到、选择器是否命中、浏览器渲染是否需要 CDP 模式或会被风控，都需在你的环境里“抓取一次”确认。
- 全量测试 393 项，仅剩 2 个与本次无关的既有失败（缺 mini_agent 框架）。


---

## 补充：抓取结果页按信息源分类 / 类型查看（已完成）

- `app.py` 结果页新增“信息源分类”“信息源类型”两个筛选，缩小信息源下拉；默认全部；选项带数量；空组合有提示。
- 测试：`tests/test_feedback_dashboard.py` 新增 `ResultsFilterTests`；过程中发现 `format_func` 闭包引用了后来被重新赋值的列表导致计数为 0，已改为固定使用完整列表。
- 取舍：只做筛选，不做跨源合并的聚合视图。


---

## 补充：抓取结果页“合并显示”与时间范围（已完成）

- 需求：同一分类下的信息源合并成一张列表，并可按“最近几小时 / 几天”查看。上一轮只做了分类/类型筛选下拉，与需求不符，本次补上合并视图。
- 新增 `core/merged.py`（按时间窗口合并快照：同源同条目去重，保留首次出现时间与最近一次排名/热度，标记窗口内新增）、`api.merged_results`；`app.py` 抽出共用行渲染 `_item_rows`，新增“查看方式”与 `_merged_view`（时间范围、排序、只看新增、标题过滤、空结果与缺数据提示）。
- 测试：新增 `tests/test_merged_results.py`（14 用例）。
- 默认值与取舍（请确认）：默认仍为“单个信息源”，合并需手动切换；合并视图缓存 60 秒；不记录曝光；窗口内“出现过”包含早已在榜的老条目，需“只看新增”才能只看新条目。
- 未验证：大量快照（数百个）时的真实读取耗时；真实抓取数据下的体验。


---

## 推荐信息流 P1：骨架（已完成，见设计文档第 11 节）

- 新增“推荐”页签：今日精选 → 多源热点 → 关注主题 → 稍后看 → 其余；每个事件只出现在一个分区。
- 新增 `core/feed.py`：候选池过滤（已 👍/👎/打开的隐藏，⏰ 进稍后看，屏蔽词剔除）→ 标题二元组 Jaccard 聚合（阈值 0.45，URL 相同直接合并）→ 五个分量加权打分 → 分区 → 一句话理由 → 曝光写入 `data/feed/shown.jsonl`。
- 新增 `tests/test_feed.py`（31 用例，含看板 AppTest 冒烟与点击反馈）。
- 配套改动：`core/merged.py` 增加 `first_rank`（排名上升用）；`core/feedback.py` / `core/evaluation.py` 接受视图名 `feed_v1`；`config/settings.yaml` 新增 `feed` 段。
- 测试适配（非功能修改）：推荐页按钮键加 `feed_` 前缀，避免与抓取结果页的 `fb_*` 键冲突；`tests/test_merged_results.py` 的标题提取限定到“抓取结果”页签内，因为推荐页也会展示同样的条目。
- 验证：全量 433 通过；剩余 2 个失败为改动前即存在的 `test_entrypoints` 账本测试（依赖外部框架）。2000 条合成标题聚合约 0.66 秒，200 组重复全部找回且无误并。
- 未验证：真实抓取数据下的首屏耗时与体验（沙箱没有真实快照）。
- 未实现（属 P2 及以后）：`data/feed/state.json`、曝光疲劳、多样性与来源上限、探索位、快速分诊、“不再显示”、离线回放评估、评估面板中的 `feed_v1` 视图。

---

## 推荐信息流 P2：行为闭环（已完成，见设计文档第 12 节）

- 新增 `core/feed_eval.py`：离线回放（推荐顺序 vs 按首次出现时间倒序，前 10 条的正例率 / 👎 率 / NDCG@10；样本不足时明确提示）。
- `core/feed.py`：曝光疲劳（从 `shown.jsonl` 统计，窗口 7 天）、精选多样性（同源 ≤3、同主题递减）、探索位（默认 15%，单独成分区）、不再显示（`data/feed/prefs.json`）、`first_seen` 写入曝光记录供回放使用。
- `server/api.py`：`feed_set_source_hidden_api`、`feed_block_topic_api`、`feed_replay_api`；推荐接口自动应用不再显示的来源。
- `app.py`：卡片 🚫、主题“屏蔽此主题”、探索分区、快速分诊模式、已隐藏来源的恢复列表、离线回放面板；推荐页按钮键统一用 `feed_` 前缀。
- 新增 `tests/test_feed_p2.py`（17 用例）。全量 450 通过，剩余 2 个为改动前就存在的账本测试。
- 未实现：`state.json`（改由 `shown.jsonl` 统计）、线上对比视图 `feed_v1`（评估面板）、精选小批量重排与理由润色（P4 中未实施的两项）。

---

## 推荐信息流 P3：学习权重（已完成，默认关闭，见设计文档第 13 节）

- 新增 `core/feed_learn.py`：纯 Python 逻辑回归（6 个分量），按展示批次时间切分留出集，NDCG@10 对比手工权重；结果写入 `data/feed/learned_weights.json`。
- 生效门槛：样本 ≥200、留出集提升 ≥0.02、用户在页面开启；缺一即回落手工权重。
- `core/feed.py`：`build_feed(learned_weights=...)`，返回 `scoring`；`learned_score` 移入 `feed.py` 以避免循环导入。
- `core/feed.py` 偏好新增 `learning_enabled`（默认 false）；`server/api.py` 新增 `feed_learning_status_api`、`feed_set_learning_api`、`feed_train_api`。
- `app.py`：“学习权重（P3，默认关闭）”面板。
- 新增 `tests/test_feed_p3.py`（10 用例）。全量 460 通过，剩余 2 个为改动前即存在的账本测试。
- 未验证：真实反馈数据下的效果（样本未达门槛）。
- 未实现：P4 LLM 增强、线上对比视图 `feed_v1`。

---

## 推荐信息流 P4：LLM 增强（已完成，默认关闭，见设计文档第 14 节）

- 新增 `core/feed_llm.py`：前 12 张卡片的主题标签与摘要（缓存 `data/feed/llm_cache.json`）；近似对的“同一事件”判断（缓存结论，聚合时只读使用）；每日上限（`data/feed/llm_usage.json`）；分批调用；模型输出不合法时只记录错误。
- `core/feed.py`：`build_feed(merge_judge=...)` 在聚合后应用缓存的合并结论；偏好新增 `llm_enabled`（默认 false），保存偏好时保留其它字段。
- `server/api.py`：`feed_llm_status_api`、`feed_set_llm_api`、`feed_llm_plan_api`（不调用模型）、`feed_llm_run_api`（未开启 400，模型不可用 503）；`feed_view` 仅在开启时挂载缓存标签并应用缓存合并。
- `app.py`：“LLM 增强（P4，默认关闭）”面板（开关、模型来源、今日用量、预计调用、运行按钮与结果）；卡片显示主题与摘要；缓存版本纳入偏好与 LLM 缓存文件。
- `config/settings.yaml`：`feed.llm` 段。
- 新增 `tests/test_feed_p4.py`（14 用例，假模型）。全量 474 通过，剩余 2 个为改动前即存在的账本测试。
- 未实现：精选小批量重排、理由润色（设计文档 P4 中的两项，见设计文档 14.2）。
- 未验证：真实模型的输出质量与补聚判断的准确率。

---

## 推荐页交互修正：反馈不刷新列表（已完成，见设计文档第 15 节）

- 问题：点击 👍 / 👎 / ⏰ 后整页重新读取数据，已处理条目被过滤，列表整体跳变。
- 修正：推荐数据改为会话快照（`app.py::_feed_snapshot`），只在首次进入、切换筛选、点击“🔄 刷新推荐”或重新加载页面时重建；反馈、🚫、屏蔽主题、开关等只读快照，在原地显示标记。
- 去掉基于文件修改时间的 60 秒缓存（`_feed_version` / `_feed_cached`）。
- 新增 `tests/test_feed_snapshot.py`（3 用例）。全量 477 通过，剩余 2 个为改动前即存在的账本测试。

## 看板重启后自动恢复接收服务与专用浏览器（已完成，默认关闭，见专用浏览器设计第 21 节）

- 服务管理面板新增“看板启动后自动恢复”设置；开启后看板每次启动时，拉起当前未运行的接收服务与专用浏览器，已运行的不动。
- 新增 `core/autostart.py`、两个 API 接口、`app.py` 启动钩子与面板区块；`config/settings.yaml` 新增 `service_autostart.enabled: false`。
- 验证：`tests/test_autostart.py` 16 项；info_hub 全量 491 通过，剩余 2 个为改动前即存在的账本测试。
