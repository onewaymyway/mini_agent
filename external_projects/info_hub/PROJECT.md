# info_hub 信息聚合与播报

独立外部项目，负责定时抓取信息源、整理为纯文本数据、生成报告，并在看板上听报。
设计见 `next_doc/info_hub_external_project_plan.md`，实施记录见 `next_doc/info_hub_implementation_record.md`。

## 目录约定
- `sources/_index.json`：信息源注册表（id 与启用状态）
- `sources/<id>/source.yaml`：单个信息源声明
- `config/settings.yaml`：全局配置、报告计划（every / at）、默认抓取间隔
- `config/categories.yaml`：分类
- `config/sites.yaml`：需要登录的站点（登录地址、登录失效判定）
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

## 看板
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
