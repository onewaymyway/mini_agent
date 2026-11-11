# info_hub 信息聚合与播报

独立外部项目，负责定时抓取信息源、整理为纯文本数据、生成报告，并在看板上听报。
设计见 `next_doc/info_hub_external_project_plan.md`，实施记录见 `next_doc/info_hub_implementation_record.md`。

## 目录约定
- `sources/_index.json`：信息源注册表（id 与启用状态）
- `sources/<id>/source.yaml`：单个信息源声明
- `config/settings.yaml`：全局配置、报告计划（every / at）、默认抓取间隔
- `config/categories.yaml`：分类
- `config/sites.yaml`：需要登录的站点（登录地址、登录失效判定）
- `config/llm.yaml`：对话生成信息源使用的 provider 配置来源（可选）
- `adapters/`：适配器类型，自动发现；新增适配器只需新增模块
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
python cli.py serve                启动看板（http://127.0.0.1:8765）并后台定时调度
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
- **运行记录**：按日期查看。
- **登录**：查看站点登录状态与登录命令。

侧边栏：显示对话生成使用的模型配置来源、后台调度状态、立即调度、生成报告。

后台调度：看板进程启动时，会在后台每隔 `defaults.tick_interval_minutes` 分钟执行一次到期任务。关闭看板即停止。如果不需要，设置 `streamlit.background_tick: false`，或环境变量 `INFO_HUB_BACKGROUND_TICK=0`。也可以改用系统 cron 调用 `python cli.py tick`。

听报使用浏览器语音合成（speechSynthesis）：播放器运行在页面内嵌的组件中，不生成音频文件。

看板业务逻辑复用 `server/api.py`，与下面的 HTML 看板行为一致。

## HTML 看板（可选，旧版）
`serve` 启动后，浏览器打开 http://127.0.0.1:8765 。四个页面：
- **报告**：查看各计划生成的报告；可立即生成；可整份听报，也可逐条朗读。
- **信息源**：按分类筛选，启用/停用，立即抓取单个源，查看最近运行时间与条目数，配置错误单独列出。
- **运行记录**：按日期查看每次抓取的成功/失败与详情。
- **登录**：查看各站点登录状态。登录本身需要在终端执行 `python cli.py login <站点>`，看板只显示状态与命令。

听报使用浏览器自带的语音合成，不生成音频文件。语速、语音选择保存在浏览器本地。Chrome 与 Edge 在 Windows / macOS 上语音选择最丰富；Linux 上取决于是否安装了中文语音包。

安全约定：看板只监听 127.0.0.1；所有修改类请求都需要自定义请求头，外部网页无法直接触发抓取。

`tick` 设计为由系统 cron 每 5 分钟调用一次即可。它只执行到期任务，重复调用安全。

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

看板的"信息源"页面有"＋ 添加信息源"面板，两种方式：

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
