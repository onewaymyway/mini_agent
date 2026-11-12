# info_hub 信息聚合与播报

独立外部项目，负责定时抓取信息源、整理为纯文本数据、生成报告，并在看板上听报。
设计见 `next_doc/info_hub_external_project_plan.md`，实施记录见 `next_doc/info_hub_implementation_record.md`。

> 本项目遵循 `next_doc/external_projects_workspace_plan.md` 确立的外部项目标准结构：完全自包含、可独立于
> mini_agent daemon 运行，可整体移动到任意路径，只要在 daemon 侧重新 `mini-agent projects register <路径>`
> （或在看板「外部项目 → 注册新项目」里选择）即可被看见。

## 目标
1. 定时抓取多个信息源（RSS / JSON 接口 / HTML 页面 / 脚本），热榜排名变化、增量去重。
2. 订阅小说章节、视频更新，只在进度变化时提醒。
3. 按计划生成报告（markdown + json），在看板上听报（浏览器语音，不生成音频文件）。
4. 全部产出为纯文本，人可直接阅读；抓取、整理、报告不调用 LLM（LLM 仅用于看板“对话生成信息源”）。

## 数据源与依赖策略
- 信息源由用户在 `sources/` 中配置，优先级 rss > json_api > html_list > script；需要登录的站点用共享浏览器 profile，登录一次长期复用。
- 依赖见 `requirements.txt`：PyYAML、beautifulsoup4（解析）、streamlit（看板）、playwright（仅 login 与需浏览器渲染的源使用）。
- 不访问任何固定域名，`project.yaml` 的 `resources.allowed_domains` 因此为空。

## 已知限制 / 待验证事项
- 有头浏览器登录、听报的声音、真实模型生成信息源、真实站点选择器均未在真实环境验证（详见 `next_doc/info_hub_implementation_record.md`）。
- 文件锁依赖 fcntl，Windows 下退化为无锁：看板后台调度与另一路 tick 同时运行可能重复抓取，所以 daemon 默认不定时调度本项目（见下）。
- 登录态依赖浏览器持久化 profile，只发会话 cookie 的站点关闭浏览器后会失效。

## 作为外部项目接入（entrypoints）
`project.yaml` 声明了以下 headless 入口，均可脱离 daemon 直接运行（成功/失败记进 `.agent/run_status.jsonl`，检测不到 mini_agent 时只执行不记账）：

| entrypoint | 命令 | 说明 |
|---|---|---|
| `tick` | `python entrypoints/tick.py` | 执行一次调度（等价 `cli.py tick`）。**默认不声明 schedule**：看板进程自带后台调度；需要 daemon 定时请按 `project.yaml` 注释取消 `schedule` 并关闭看板 `background_tick` |
| `run_source <source_id>` | `python entrypoints/run_source.py <id>` | 立即抓取一个信息源 |
| `report <plan_id>` | `python entrypoints/report.py <plan>` | 立即生成一份报告 |
| `resume_source <source_id>` | `python entrypoints/resume_source.py <id>` | 手动恢复被暂停/连续失败的源 |
| `health_check` | `python entrypoints/health.py` | 只检查依赖、配置、状态文件，不访问网络；单个源的问题只告警不影响退出码 |

四个入口都标 `concurrency: unmanaged`（不调 LLM，不占 cron 并发槽位）。`INFO_HUB_ROOT` 环境变量可指定项目根目录（测试/多实例用）。
看板（Streamlit，端口 8506）由 `web_app` 块声明，可在 mini_agent 看板「外部项目」里启动/打开。
注册：`mini-agent projects register external_projects/info_hub`，或在看板「注册新项目」里扫描选择；默认不开启自动调度。

## 目录约定
- `sources/_index.json`：信息源注册表（id 与启用状态）
- `sources/<id>/source.yaml`：单个信息源声明
- `config/settings.yaml`：全局配置、报告计划（every / at）、默认抓取间隔
- `config/categories.yaml`：分类
- `config/sites.yaml`：需要登录的站点（登录地址、登录失效判定）
- `config/llm.yaml`：对话生成信息源使用的 provider 配置来源（可选）
- `adapters/`：适配器类型，自动发现；新增适配器只需新增模块
- `checkers/`：订阅检查器（feed_latest / json_latest / html_latest），自动发现；新增订阅站点类型只需新增模块
- `core/`：存储、流水线、浏览器会话、调度、报告
- `data/`、`reports/`、`browser/profile/`：运行产物，已加入 .gitignore。`browser/profile/` 含登录凭据，勿分享

## 命令
```
python cli.py list                 列出信息源
python cli.py validate             校验全部信息源配置
python cli.py run <source_id>      立即抓取一个信息源
python cli.py login <site>         打开有头浏览器登录站点，完成后回终端按回车
python cli.py login-status         查看各站点登录状态
python cli.py report <plan_id>     立即生成一份报告
python cli.py tick                 执行一次调度：运行到期的信息源，生成到期的报告
python cli.py health               查看各信息源健康状态（连续失败、降频、暂停）
python cli.py resume <source_id>   手动恢复被暂停/连续失败的信息源
```

## Streamlit 看板（主看板）

与其他外部项目一致，本项目的主看板是 Streamlit 应用 `app.py`：

```
cd external_projects/info_hub
pip install -r requirements.txt
streamlit run app.py --server.port 8506
```

浏览器打开 http://localhost:8506 。端口与 stock_watch（8503）错开。

页面：
- **报告**：选择报告，查看摘要与 Markdown 内容；整份听报，或选择单条条目朗读。
- **信息源**：表格形式查看，勾选启用后点"保存启用状态"；选择信息源立即抓取。
- **添加信息源**：表单添加（预览、保存），或对话生成（生成草稿、预览、按意见修改、确认、放弃）。
- **订阅**：每个订阅源的当前进度、最近检查/更新时间、最近几次更新。
- **运行记录**：按日期查看（含降频/告警/暂停/恢复事件）。
- **登录**：查看站点登录状态与登录命令。

侧边栏：显示对话生成使用的模型配置来源、后台调度状态、立即调度、生成报告。

后台调度：看板进程启动时，会在后台每隔 `defaults.tick_interval_minutes` 分钟执行一次到期任务。关闭看板即停止。如果不需要，设置 `streamlit.background_tick: false`，或环境变量 `INFO_HUB_BACKGROUND_TICK=0`。也可以改用系统 cron 调用 `python cli.py tick`。

听报使用浏览器语音合成（speechSynthesis）：播放器运行在页面内嵌的组件中，不生成音频文件。

看板业务逻辑在 `server/api.py`（仅业务层，不含 HTTP）。旧的 HTML 看板与 `cli.py serve` 已移除，Streamlit 是唯一看板。

## 听报说明
听报使用浏览器自带的语音合成，不生成音频文件。语速、语音选择仅在当前页面有效。Chrome 与 Edge 在 Windows / macOS 上语音选择最丰富；Linux 上取决于是否安装了中文语音包。

调度：看板进程内置后台调度；也可以不开看板，用系统 cron / 计划任务每 5 分钟执行一次 `python cli.py tick`（只执行到期任务，重复调用安全）。

## 典型使用流程
1. 在 `config/sites.yaml` 中登记需要登录的站点。
2. `python cli.py login <site>`，在弹出的浏览器中登录，回到终端按回车。之后抓取会复用这份登录态，无需重复登录。
3. 在 `sources/` 中添加信息源，`python cli.py validate` 检查配置。
4. `python cli.py tick` 手动验证一次，结果在 `reports/` 与 `data/runs/` 中可直接查看。

## 登录态的限制
登录态通过持久化 profile 保存。只发会话 cookie（无过期时间）的站点，浏览器关闭后 cookie 会丢失，需要重新登录。遇到这种情况，报告与运行记录会提示"需要重新登录"。

## 运行环境
- Python 3.10+，依赖见 requirements.txt
- 安装浏览器：`python -m playwright install chromium`
- 测试：`python -m unittest discover -s tests -t .`


## 添加信息源（P7）

Streamlit 看板的"添加信息源"页面，两种方式：

**表单添加**：填写名称、ID、分类、类型、适配器和选择器。点"预览"会抓取前 10 条，点"保存"写入 `sources/`。保存前会检查与已有源的相似度（同域名、同列表选择器或同路径视为相似），相似时需要勾选"仍然保存"。

**对话生成**：填写地址与需求描述，系统会：
1. 抓取页面，生成结构摘要（候选列表选择器、样例文本、内嵌数据提示），不把整页 HTML 发给模型；
2. 调用 LLM 生成源配置（优先 rss > json_api > html_list > script）；
3. 生成草稿保存在 `data/drafts/`，**此时不会运行任何脚本**；
4. 你可以预览、按意见修改（最多 5 次，同一个草稿）、确认添加或放弃。

### LLM 的 provider 配置
对话生成使用的模型配置按以下顺序选择：
1. `config/llm.yaml` 中 `providers_file` 指定的文件；
2. 本项目根目录的 `providers.json`；
3. 主项目的 `providers.json`（本项目未单独配置时的默认行为）。

格式与主项目的 `providers.json` 相同。看板顶部会显示当前使用的是哪一份配置。

### 脚本信息源的安全边界
LLM 生成的 `script.py` 会在你点击"预览"或"确认添加"时运行，之后随调度运行。
- 静态检查会拒绝导入 `os`、`sys`、`subprocess`、`socket` 等模块，拒绝调用 `exec`、`eval`、`open`；
- 这只是护栏，**不是沙箱**。请像审查代码一样阅读草稿中的脚本（位于 `data/drafts/<id>/script.py`）再确认；
- 确认前可以放弃草稿，草稿目录中的脚本不会被调度器执行。


## 订阅源（P8）

订阅源用来跟踪“某个对象的最新进度”（小说最新章节、UP 主最新视频），只在**进度变化**时产生一条更新，并在报告顶部的“订阅更新”小节置顶显示（不再重复出现在分类小节里），听报时也会先读订阅更新。

### 配置（`sources/<id>/source.yaml`）
```yaml
id: novel_a
name: 某小说
category: news
kind: subscription          # 必须
adapter: subscription       # 必须
schedule: {every: 30m}
request: {url: https://example.com/novel/123/chapters}
parse:                      # 与 html_list 的写法相同
  list_selector: ".chapter li"
  fields: {title: "a", url: "a@href"}
subscription:
  checker: html_latest      # feed_latest(RSS/Atom) | json_latest(JSON 接口) | html_latest(HTML 页面，可走共享登录态)
  order: newest_first       # 目录页从旧到新排列时写 oldest_first
  cursor_from: auto         # 游标取值：auto(优先 url，其次标题) | url | title
  include_keywords: []      # 非空时：标题（或 extra.author）命中任一才算候选
  exclude_keywords: ["番外"] # 命中任一则排除
  notify_on_first: false    # 首次检查是否也算更新（默认 false，只记录基线）
```
`feed_latest` 用 RSS 的 `request.url`；`json_latest` 用 `parse.items_path` 与 `parse.fields`（同 json_api）；`html_latest` 的 `browser.*` 写法同 html_list，需要登录时与其他源共用同一份登录态。

### 行为
- 每次检查取候选列表 → 按 `order` 与关键词过滤 → 取最新一条作为“当前游标”。
- 游标保存在 `data/state/<id>.json` 的 `cursor` 字段（人可直接阅读）。**首次检查只记录基线，不算更新**（保守默认，避免第一次就刷出一条“更新”）。
- 游标与上次不同才写入一条更新条目（`extra.previous_title` 记录上次进度）；游标回退再前进同样算变化，不会被“已见去重”吞掉。
- 过滤后无候选、或抓取失败：不改动游标，失败照常进“异常”小节。

### 新增一种订阅站点
在 `checkers/` 新建模块，继承 `checkers.base.Checker`，设置 `name`，实现 `validate` 与 `check`（返回最新在前的 `Cursor` 列表）。无需改调度、报告或看板。多数“列表页”类站点直接用上述三种内置检查器即可。

### 限制
- 看板“添加信息源”表单暂不支持订阅源，需手写 `source.yaml` 并在 `sources/_index.json` 登记（对话生成同样未覆盖）。
- 第一批具体站点未定，内置检查器是通用的；真实站点的选择器/接口需要在你的环境里调试。


## 健康状态：降频与自动暂停（P9）

每次运行（调度或手动）都会更新 `data/state/_health.json`（纯文本）里的连续失败次数，成功即清零。

| 层 | 默认 | 行为 |
|---|---|---|
| 记录与标红 | **始终开启** | 连续失败达到 `degrade_after` 次：看板信息源表“健康”列标红、出现“异常信息源”区，报告“异常”小节列出，运行记录写一条“告警”。**不改变抓取频率** |
| 降频与自动暂停 | **关闭**（`health.enabled: false`） | 开启后：达到 `degrade_after` 次起抓取间隔按 `backoff_factor` 逐次放大（上限 `max_interval`）；达到 `pause_after` 次自动暂停，tick 不再运行，直到手动恢复 |

```yaml
# config/settings.yaml
health:
  enabled: false        # 改为 true 才会降频/自动暂停
  degrade_after: 3
  pause_after: 8
  backoff_factor: 2
  max_interval: 1d
```
示例（`every: 10m`，degrade_after=3，factor=2）：第 3 次失败后间隔 20m，第 4 次 40m，之后不超过 `max_interval`；到第 `pause_after` 次失败即暂停。配置写错（如 `max_interval` 无法解析）时退回默认（即关闭降频/暂停），不会中断调度。

**恢复**：看板“异常信息源”里点“恢复”；或 `python cli.py resume <id>`；或手动抓取一次成功（清零并解除暂停）。`python cli.py health` 随时查看状态。

**不静默**：已暂停与配置错误的源不会再产生运行失败记录，所以报告“异常”小节会直接列出它们（与时间窗口无关），听报也会读到；降频/暂停/恢复事件都写进当天运行记录。

## 扩展指南

- **新增信息源实例**：在 `sources/<id>/source.yaml` 写配置，并在 `sources/_index.json` 登记；`python cli.py validate` 校验。看板“添加信息源”（表单/对话生成）覆盖 rss / json_api / html_list / script；订阅源需手写。
- **新增适配器类型**：在 `adapters/` 新建模块，继承 `Adapter`，设置 `kind_name`，实现 `validate` 与 `fetch`；自动发现，无需改调度、报告、看板。
- **新增订阅检查器**：在 `checkers/` 新建模块，继承 `Checker`，设置 `name`，实现 `validate` 与 `check`。
- **新增看板页面**：业务逻辑写进 `server/api.py`（纯函数，便于测试），页面写在 `app.py`。
- **测试**：`python -m unittest discover -s tests -t .`；`tests/helpers.py::make_project` 在临时目录构造最小项目，网络通过 `FetchContext.http_get` 注入。

## 运维速查

| 现象 | 看哪里 |
|---|---|
| 某个源没数据 | 看板“信息源”健康列 / `python cli.py health` / 运行记录 |
| 提示需要重新登录 | “登录”页，`python cli.py login <site>` |
| 报告“异常”里出现已暂停 | 先查最近错误，修好后“恢复” |
| 想让失败源自动降频/暂停 | 把 `health.enabled` 改为 true |
| 不开看板也要调度 | 系统计划任务每 5 分钟 `python cli.py tick` |

## 变更记录
- 2026-11-12：抓取结果页新增“合并显示”：同一分类/类型下所有信息源在所选时间范围（1 小时到 30 天）内的结果合成一张列表，支持排序、只看新增、标题过滤；新增 `core/merged.py`、`api.merged_results`。详见 `next_doc/info_hub_dedicated_browser_design.md` §20。
- 2026-11-12：抓取结果页增加“信息源分类 / 信息源类型”筛选（默认全部），用于缩小信息源下拉。详见 `next_doc/info_hub_dedicated_browser_design.md` §19。
- 2026-11-12：新增信息源 `eastmoney_news`（东方财富要闻）与 `netease_money`（网易财经，浏览器渲染，`config/sites.yaml` 新增同名站点）；`html_list` 适配器对非 UTF-8（GBK/GB2312）页面改为 gb18030 回退解码。
- 2026-11-12：抓取结果每行增加 👍 / 👎 按钮，直接记录“感兴趣 / 不感兴趣”，最近一次反馈高亮，重复点击不重复记录。详见 `next_doc/info_hub_dedicated_browser_design.md` §18。
- 2026-11-12：看板新增“反馈记录”页签，显示历史反馈，可按动作/信息源/时间/关键词筛选，每条带“打开”按钮（只读）。详见 `next_doc/info_hub_dedicated_browser_design.md` §17。
- 2026-11-12：看板只保留“在专用浏览器中打开”，移除阅读模式/内嵌页面/新标签页；抓取结果、订阅、报告单条、候选与编辑源地址处都有“打开”按钮；新增“插件配对”（看板开启 5 分钟窗口，插件自动取得令牌，免手填）；“打开”记录改由服务端统一写入；插件 0.4.0 需在 chrome://extensions 重新加载。详见 `next_doc/info_hub_dedicated_browser_design.md` §16。
- 2026-11-12：修复看板“在专用浏览器中打开”后工具条不出现（地址片段 `#ihk` 在脚本运行前丢失）：看板打开时登记待匹配上下文（`core/pending_open.py`），插件凭当前地址经 `/v1/context` 恢复；扩展新增选项“在所有页面显示反馈工具条”（默认关闭，手动反馈记为 `manual`）；插件版本 0.2.0，需在 chrome://extensions 重新加载。详见 `next_doc/info_hub_dedicated_browser_design.md` §14。
- 2026-11-11：补齐外部项目契约——`project.yaml` 按规范改用 `cmd`、新增 `entrypoints/`（tick / run_source / report / resume_source / health）、`health_check`、`resources`、`review`、`web_app`；`.gitignore` 增加账本与 `.run/`。此前 `project.yaml` 使用了不被框架识别的 `command` 字段，无法注册。

## 内置信息源：知乎热榜（zhihu_hot）

- 配置：`sources/zhihu_hot/source.yaml`（html_list + 共享浏览器登录态，每 1 小时抓取，分类“热点社区”，kind=hot_list，因此报告里有排名上升/下降/新上榜）。
- 首次使用先登录一次：`python cli.py login zhihu`（站点定义在 `config/sites.yaml` 的 `zhihu`，被跳转到 `/signin` 视为登录失效，看板“登录”页可见状态）。未登录时该源会报“需要重新登录”，不影响其他源。
- 选择器基于知乎热榜页 `.HotItem` 系列 DOM；若知乎改版导致抓到 0 条，只需修改 `source.yaml` 的 `parse`。
- 验证情况：用仿真页面测试了选择器与登录失效处理（`tests/test_zhihu_hot_source.py`）；沙箱无法访问知乎，真实页面抓取需在你的环境里验证。
- 不想抓取时：把 `enabled` 改为 false（同时改 `sources/_index.json`），或在看板信息源表里停用。

## 看板：抓取结果页签

看板新增“抓取结果”页签（在“信息源”之后）：选择信息源与抓取时间（最近 30 次），查看当次完整条目表（排名/标题/热度/摘要/可点击链接），热榜源额外列出新上榜、上升、下降、掉出；支持按标题/摘要过滤。数据来自 `data/raw/<源>/<日期>/<时分秒>.json`，对应业务函数 `server/api.py::list_snapshots/read_snapshot`（快照 id 做了格式校验，防路径穿越）。

## 内置信息源清单

| id | 名称 | 分类/类型 | 方式 | 备注 |
|---|---|---|---|---|
| zhihu_hot | 知乎热榜 | 热点社区 / hot_list | html_list + 登录态 | 需先 `python cli.py login zhihu` |
| weibo_hot | 微博热搜 | 热点社区 / hot_list | json_api（公开接口） | 链接由 `url_template` 拼出搜索页 |
| baidu_hot | 百度热搜 | 热点社区 / hot_list | json_api | |
| bilibili_popular | B站热门 | 视频 / hot_list | json_api | 链接由 bvid 拼出 |
| v2ex_hot | V2EX 热议 | 技术 / hot_list | json_api | |
| netease_news | 网易新闻头条 | 资讯 / feed | json_api（移动端列表接口） | 头条流而非热度榜，只报新增 |
| eastmoney_news | 东方财富要闻 | 资讯 / feed | html_list（普通抓取首页） | 取 `*.eastmoney.com/a/<ID>.html` 文章链接，只报新增 |
| netease_money | 网易财经 | 资讯 / feed | html_list + 浏览器渲染 | 页面疑似脚本渲染，需共享浏览器；未经真实页面验证，见下 |

- `json_api` 新增两个可选配置：`request.headers`（额外请求头，如 User-Agent/Referer）与 `parse.url_template`（接口不给链接时用 `{字段路径}` 拼接，值会做 URL 编码，任一字段为空则链接留空）。
- 验证情况：只用仿真响应测了字段映射（`tests/test_builtin_sources.py`）；这些是第三方非官方接口，沙箱无法联网，真实可用性与字段名需在你的环境里验证，接口改版时只需改对应 `source.yaml`。
- 不想抓某个源：看板“信息源”页签里停用，或把 `enabled` 改 false。

## 财经信息源：东方财富要闻与网易财经

- `eastmoney_news`（`sources/eastmoney_news/source.yaml`）：抓取 https://www.eastmoney.com/ 首页，选择器 `a[href*="eastmoney.com/a/"]`，只取文章链接，忽略频道导航、行情与广告跳转；每 30 分钟，分类“资讯”，kind=feed（只报新增）。依据：用网页抓取工具看到首页服务端渲染且文章链接形如 `finance.eastmoney.com/a/<时间戳ID>.html`。
- `netease_money`（`sources/netease_money/source.yaml`）：https://money.163.com/ 首页。网页抓取工具只看到标题文字而没有任何链接地址、页面含 `{{navList}}` 模板占位，判断文章列表由脚本渲染，所以用共享浏览器渲染后再解析（`browser.required: true`，每 1 小时）。无需登录，`browser.site: netease_money` 只是适配器必填项（`config/sites.yaml` 新增同名站点，看板“登录”页会列出它，可忽略）。
- 选择器 `a[href*="163.com/money/article/"], a[href*="money.163.com/2"]` 基于网易文章链接的常见形态（新式 `www.163.com/money/article/<ID>.html`、旧式 `money.163.com/<YY>/<MMDD>/<HH>/<ID>.html`），**没有在真实页面上验证**：沙箱无法访问这两个站点，只用仿真页面测了选择器、链接补全（含 `//` 协议头）与编码（`tests/test_finance_sources.py`）。
- 首次使用：在看板“信息源”页签对 `netease_money` 点“抓取一次”。若报等待元素超时或 0 条，到“抓取结果”的调试信息里看页面实际链接，再改 `wait_selector` 与 `parse.list_selector`。连续失败会被健康检查自动暂停，不会反复打扰。
- 两个源都归入现有分类“资讯”；需要单独的“财经”分类时，在 `config/categories.yaml` 增加后修改两个源的 `category` 即可。
- `html_list` 普通抓取的解码：先按 UTF-8，失败退回 gb18030，再失败才替换非法字符（老站点常见 GBK）。
