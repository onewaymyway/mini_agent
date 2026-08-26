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
│   └── health.py               # project.yaml health_check 对应的探测脚本
├── stock_watch/                 # 项目私有库代码
│   ├── config.py                # 观察列表 / 抓取参数配置加载
│   ├── data_sources.py          # akshare 封装 + 网页抓取公共层（UA/重试/限速）
│   ├── candidate_pool.py        # 候选池账本（去重/合并/评分）
│   ├── kline.py                 # K 线数据获取 + 绘图
│   ├── screener.py              # 问财等网站选股结果抓取
│   ├── analysis.py              # 个股综合分析（公告/帖子/新闻 → 报告）
│   └── report.py                # 报告渲染（Markdown）公共函数
├── config/
│   └── watchlist.yaml            # 候选池种子标的 + 抓取源配置
├── data/                          # 候选池账本、K 线缓存（不进 git 的运行期数据）
├── reports/                       # 面向人的产出物：候选池报告/K 线图/选股结果/个股分析
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
```

每个 entrypoint 都会往 `.agent/run_status.jsonl` 写执行记录
（`trigger="external_cron"` 或未显式指定时的默认值，见各脚本内
`track_run()` 调用），供大管家按阶段 4 的机制被动读取。

## 如何接入 daemon（可选）

```bash
mini-agent projects register external_projects/stock_watch --name stock_watch
mini-agent projects status stock_watch
mini-agent projects run stock_watch hotlist_scan
```

`project.yaml` 里已声明各 entrypoint 的建议 cron 时间（盘前/盘中/盘后），
daemon 主循环接入 `run_due_entrypoints()` 后会按声明自动触发；即使 daemon
从未启动，用户也可以直接用 OS 级 cron 指向上面"独立运行"一节的命令。
