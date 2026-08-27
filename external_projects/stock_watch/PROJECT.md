# stock_watch — A 股监控分析系统

> 本项目是 `next_doc/external_projects_workspace_plan.md` 阶段 6 的落地
> 案例：一个完全自包含、可独立于 mini_agent daemon 运行的"外部项目"。
> 它可以整体移动到任意路径、放进独立 git 仓库，只要在 daemon 侧重新
> `mini-agent projects register <新路径>` 一下即可继续被"大管家"看见。

## 目标

对 A 股（含 ETF 等泛化"股票"标的）做低成本、可持续运行的监控与分析：

1. **热点候选池抓取**：从股票网站/论坛抓取热点、有前景的标的，进入候选
   池后再抓取该标的的进一步信息，生成候选池报告。
2. **K 线批量生成**：每天定时为候选池内所有标的（股票/ETF）生成最新
   K 线图。
3. **选股（条件筛选）**：复用同花顺问财（iwencai）等网站已有的自然语言
   选股能力，直接抓取其筛选结果，而不是自己重新实现技术指标引擎。
4. **个股综合分析**：抓取单个标的的历史公告、论坛帖子、相关新闻，综合
   给出一份结构化分析报告。
5. **候选池状态跟踪**（见 `next_doc/stock_watch_pool_state_tracking_and_
   kanban_plan.md` 阶段2）：候选池内每只标的可以被标记为
   `watching`（观察，默认）/ `focused`（重点关注）/ `buy_suggested`
   （建议买入）/ `holding`（已建仓）/ `sell_suggested`（建议卖出）/
   `dropped`（已淘汰）六种状态之一，每次状态变更都记一条历史事件
   （进入时间 + 进入时价格），每天跑一次跟踪任务算出"历史每一段状态
   各自的区间涨跌幅"，而不只是"自纳入观察以来"这一个粗粒度数字。

## 数据源与依赖策略

优先使用免费数据源，两层兜底：

1. **`akshare`**（首选）：开源免费的金融数据接口库，覆盖行情、K 线、
   公告、新闻、问财选股结果等大部分需求，背后是对各数据网站/接口的
   封装，不需要自己维护爬虫。见 `stock_watch/data_sources.py`。
2. **直接网页抓取**（`akshare` 覆盖不到时的兜底）：东方财富股吧
   （guba.eastmoney.com）、雪球（xueqiu.com）热帖、问财
   （iwencai.com）网页版结果等，用 `requests` + `BeautifulSoup` 做
   轻量抓取，统一走 `stock_watch/data_sources.py` 的 `fetch_html()`，
   带 UA、超时、重试、限速的公共封装，避免每个抓取函数各写一套。

`akshare` 与 `requests`/`beautifulsoup4` 都需要真实网络访问才能验证；
本项目在无网络的开发环境中通过后（语法检查 + 单元测试用 mock 数据）
交付，**首次连网运行前请先看"已知限制"一节**。

## 已知限制 / 待验证事项

- 本项目在构建时所处的沙箱环境**没有到财经网站/`akshare` 数据源的出网
  权限**，所有抓取逻辑只做了语法正确性检查、纯逻辑单元测试（用固定
  mock 数据跑通候选池合并/去重/K 线绘图/报告拼装等纯函数），**没有**
  用真实网络连通性验证过。首次在有网络的机器上运行时，大概率需要：
  - 核对 `akshare` 各接口的当前函数签名/返回列名是否与代码假设一致
    （`akshare` 更新较频繁，函数名/字段名偶有变化）；
  - 核对东方财富股吧 / 雪球 / 问财网页版的 HTML 结构是否与
    `data_sources.py` 里的选择器假设一致（这类网站改版是本机制阶段 5
    "维护类交互标准化"要覆盖的典型场景——抓取失效后，可以直接让大管家
    走 `propose_fix` 流程尝试修复）。
- 问财（iwencai）网页版有基础反爬（UA/频率限制），`screener.py` 里默认
  加了限速与重试退避，但没有做完整的人机验证绕过；如果被拦截，建议
  改用 `akshare` 的 `ak.stock_zh_a_st_em` 等结构化接口做条件筛选降级
  方案，或降低调用频率。
- 论坛帖子抓取（股吧/雪球）默认只取标题 + 摘要 + 阅读/评论数，不抓取
  完整回帖树，避免抓取量过大、请求过于频繁。
- 候选池目前是本地 JSON 账本（`data/candidate_pool.json`），未来如果
  候选标的量级明显增长，可以考虑换 sqlite，但当前量级（数十到数百只）
  JSON 全量读写足够。

## 目录结构

```
stock_watch/
├── project.yaml              # daemon 契约（entrypoints/schedule/health_check/resources）
├── PROJECT.md                 # 本文件
├── requirements.txt
├── entrypoints/                # headless 单次执行入口，可被 daemon / OS cron / 手动调用
│   ├── run_hotlist_scan.py     # 功能 1：热点候选池抓取
│   ├── run_kline_batch.py      # 功能 2：K 线批量生成
│   ├── run_screener.py         # 功能 3：条件选股
│   ├── run_stock_analysis.py   # 功能 4：个股综合分析
│   ├── reconcile_outcomes.py   # 结果回溯：候选池打分 vs 实际涨跌，见持续优化机制
│   ├── run_pool_tracking.py    # 候选池状态区间每日跟踪（阶段2，新增）
│   ├── change_pool_state.py    # 手动变更某标的的候选池状态（阶段2，新增）
│   ├── health.py               # project.yaml health_check 对应的探测脚本
│   └── _common.py              # entrypoint 公共引导（sys.path、账本/积压账本降级写入）
├── stock_watch/                 # 项目私有库代码
│   ├── config.py                # 观察列表 / 抓取参数配置加载
│   ├── data_sources.py          # akshare 封装 + 网页抓取公共层（UA/重试/限速）
│   ├── candidate_pool.py        # 候选池账本（去重/合并/评分/状态机）+ 归档快照
│   ├── kline.py                 # K 线数据获取 + 绘图
│   ├── screener.py              # 问财等网站选股结果抓取
│   ├── analysis.py              # 个股综合分析（公告/帖子/新闻 → 报告）
│   ├── outcomes.py              # 结果回溯纯逻辑：预测 vs 实际涨跌幅
│   ├── source_health.py         # 数据源级别成败记录（细粒度信号）
│   └── report.py                # 报告渲染（Markdown）公共函数 + 状态跟踪 JSON 导出
├── config/
│   └── watchlist.yaml            # 候选池种子标的 + 抓取源配置
├── data/                          # 候选池账本、K 线缓存（不进 git 的运行期数据）
│   └── pool_tracking_latest.json  # 状态区间跟踪最新快照（阶段2新增，供未来看板读取）
├── reports/                       # 面向人的产出物：候选池报告/K 线图/选股结果/个股分析
│   └── pool_tracking/             # 阶段2新增：每日状态区间跟踪 Markdown 报告
└── tests/                         # 纯逻辑单元测试（mock 数据，不需要网络）
```

## 如何独立运行（不依赖 daemon）

```bash
cd external_projects/stock_watch
pip install -r requirements.txt

python entrypoints/run_hotlist_scan.py          # 功能 1
python entrypoints/run_kline_batch.py            # 功能 2
python entrypoints/run_screener.py "今日涨停"    # 功能 3（参数为问财自然语言查询）
python entrypoints/run_stock_analysis.py 600519  # 功能 4（参数为标的代码）

python entrypoints/run_pool_tracking.py                        # 候选池状态区间每日跟踪
python entrypoints/change_pool_state.py 600519 focused "关注中" # 手动变更某标的状态
```

## 持续优化迭代（新增，见 `next_doc/stock_watch_continuous_improvement_plan.md`）

除了四项核心功能，项目还落地了该文档设计的"结果回溯"能力：
`entrypoints/reconcile_outcomes.py` 定期把 N 天前候选池的归档快照
（`data/pool_snapshots/`）与实际涨跌幅对照，写入 `data/
outcome_ledger.jsonl` 并渲染报告；涨跌幅超出阈值的案例会自动记入
改进积压账本（`.agent/improvement_backlog.jsonl`，`mini-agent projects
backlog stock_watch list` 可查看），供后续人工或"周期性 review"判断
评分逻辑是否需要调整。数据源级别的成败也会记进 `data/
source_health.jsonl`（`stock_watch/source_health.py`），供判断"哪个
数据源经常挂"。这些都是纯附加能力，不影响四项核心功能的既有行为。

每个 entrypoint 都会往 `.agent/run_status.jsonl` 写执行记录
（`trigger="external_cron"` 或未显式指定时的默认值，见各脚本内
`track_run()` 调用），供大管家按阶段 4 的机制被动读取。

## 结果文件存放位置

每个 entrypoint 的产出物都是 `reports/` 下的 Markdown（`kline_batch`
另外还有图片），路径由 `stock_watch/config.py::REPORTS_DIR` 统一定义
（`REPORTS_DIR = <本项目根目录>/reports`，与运行时的当前工作目录无关，
不管从哪个目录触发都写到这里）：

| entrypoint | 输出路径 | 备注 |
|---|---|---|
| `hotlist_scan` | `reports/candidate_pool/<日期 YYYYMMDD>.md` | 一天一份，同日重复触发会覆盖 |
| `kline_batch` | `reports/kline/<日期 YYYYMMDD>/` | 每只标的一张图，目录下多个文件 |
| `screener` | `reports/screener/<时间戳 YYYYMMDD_HHMMSS>.md` | 每次触发一份新文件 |
| `stock_analysis` | `reports/analysis/<代码>_<时间戳>.md` | **需要传入标的代码作为参数**，见下方说明 |
| `reconcile_outcomes` | `reports/outcomes/<快照日期>_reconciled_<截止日期>.md` | 结果回溯报告 |
| `pool_tracking` | `reports/pool_tracking/<日期 YYYYMMDD>.md` | 状态区间跟踪报告；同时落一份结构化 `data/pool_tracking_latest.json` |
| `change_pool_state` | 无报告文件，直接改 `data/candidate_pool.json` | 状态变更是否成功看执行账本退出码 |

`stock_analysis` 依赖位置参数（`sys.argv[1]` 是代码、`sys.argv[2]`
可选是名称），命令行直接不带参数运行会在生成任何报告之前就以退出码 2
提前返回（`entrypoints/run_stock_analysis.py::main()` 里的用法检查），
**不会产出报告文件**——这不是 bug，是缺参数的正常表现：

```bash
python entrypoints/run_stock_analysis.py 600519 贵州茅台
```

通过 mini_agent 看板「🗂️ 外部项目」卡片「▶️ 手动触发」触发时，`analyze`
所在行会按 `project.yaml` 里 `stock_analysis.params` 的声明渲染出
`code`（必填）/`name`（可选）两个输入框，填好再点触发即可，不需要记
命令行参数顺序（见 `next_doc/external_projects_kanban_integration_
plan.md` 阶段6）。

每次触发是否成功、退出码多少，记在执行账本
`.agent/run_status.jsonl`（不是 `reports/` 目录，是两回事：账本记录
"跑没跑成功"，`reports/` 存"跑出来的实际内容"）——看板卡片「最近5条
执行记录」/「执行账本」区块就是读这份文件，命令行也可以
`mini-agent projects ledger stock_watch` 直接查看。

## 候选池状态跟踪（新增，见 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md`）

除了"打分排序"这一维度，候选池内每个标的还有一个独立的状态字段
（`CandidateEntry.state`），六选一：`watching`（默认）/ `focused`/
`buy_suggested`/`holding`/`sell_suggested`/`dropped`。每次状态变更都
追加一条 `state_history` 事件（进入时间 + 进入时价格 + 备注），供
`entrypoints/run_pool_tracking.py` 每天算出"历史每一段状态各自的区间
涨跌幅"，而不只是笼统的"自纳入观察以来涨了多少"。

- 手动变更状态：`python entrypoints/change_pool_state.py <代码> <状态> [备注]`，
  也是看板未来「变更状态」按钮的落地对象（`project.yaml` 已声明
  `params`，看板"手动触发"会自动渲染成输入框）。
- 已经进入 `watching` 之外状态的标的不会被 `hotlist_scan` 的热度衰减
  自动淘汰（见 `candidate_pool.py::enforce_max_size`），需要人工显式
  操作降级到 `dropped`。
- 状态区间跟踪的结构化产出物：`data/pool_tracking_latest.json`
  （未来看板直接读，不需要解析 Markdown 表格）。

本阶段（阶段2）尚未做的：自主挖掘信号层（不依赖外部网站结论的历史
行情/公告/新闻分析）与真正的可视化看板，规划见上述文档阶段3/4。

## 如何接入 daemon（可选）

```bash
mini-agent projects register external_projects/stock_watch --name stock_watch
mini-agent projects status stock_watch
mini-agent projects run stock_watch hotlist_scan
```

`project.yaml` 里已声明各 entrypoint 的建议 cron 时间（盘前/盘中/盘后），
daemon 主循环接入 `run_due_entrypoints()` 后会按声明自动触发；即使 daemon
从未启动，用户也可以直接用 OS 级 cron 指向上面"独立运行"一节的命令。
