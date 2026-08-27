# stock_watch 三阶段升级规划：状态化跟踪 / 自主挖掘 / 看板

> **这篇文档管什么**：`external_projects/stock_watch` 从"抓取外部网站
> 现成结果的转发器"升级为"有自己判断力的持续跟踪系统"的整体路线图，
> 覆盖三件事：(1) 候选池状态机 + 按状态区间的涨跌跟踪，(2) 不依赖外部
> 网站结论、自己分析历史行情/公告/新闻的挖掘能力，(3) 一个可视化、可
> 操作的看板。
>
> **不管什么**：不重复 `external_projects_workspace_plan.md`（daemon
> 侧通用机制：`project.yaml`/账本/health_check）和
> `stock_watch_continuous_improvement_plan.md`（结果回溯/改进积压账本
> 这条已经落地的"评估优化效果"的机制）已经确立的内容，本文档在这两篇
> 的基础上继续往前走，且会复用其中的基础设施（`data/pool_snapshots/`、
> `outcome_ledger.jsonl`、`improvement_backlog.jsonl`、
> `_common.append_backlog()`/`tracked_run()`）而不是另起一套。
>
> **约定**：每完成一个阶段，回来把对应复选框打勾，在文末"变更记录"补
> 一行，并同步更新 `external_projects/stock_watch/PROJECT.md` 里受影响
> 的章节（目录结构、功能列表、独立运行说明）。

## 0. 背景与现状

当前 `stock_watch` 的四项核心功能（热点候选池、K 线批量、选股、个股
分析）本质上都是"抓取外部网站/接口已经算好的结果"：候选池的"热度"来自
东方财富/雪球的热榜排名，选股复用同花顺问财的自然语言选股引擎，"分析"
只是把公告/新闻/帖子罗列出来，没有自己的判断。`candidate_pool.py` 里
的 `CandidateEntry` 只有一个累加的 `score` 和 `first_seen`/`last_seen`
两个时间戳，没有"状态"概念，也没有"进入某个状态后表现如何"的跟踪。

用户希望的理想形态有三层，对应本文档的三个阶段：

1. **不再单纯依赖外部网站的挖掘结论**，自己基于历史行情、公告、新闻
   算出候选理由和分数。
2. **候选池状态化**：标的进入池子后要能标记"观察/重点关注/建议买入"
   等状态，且要能看到"自进入某个状态以来"的涨跌幅，而不只是"自纳入
   观察以来"这一个粗粒度数字。
3. **看板**：可视化以上信息，并能直接在看板上操作（改状态、触发任务）。

## 1. 三个阶段的依赖关系

阶段 2（状态机跟踪）不依赖阶段 3（自主挖掘），可以先做——现在的
`score`/`reasons` 字段不管来自外部网站还是未来的自有信号，状态机的
数据结构和跟踪逻辑是一样的。阶段 4（看板）依赖阶段 2 的数据结构先
稳定下来，否则看板刚做完数据结构又要改，返工成本最高。因此推荐顺序：

```
阶段 1（本文档，设计确认）
   └─ 阶段 2：候选池状态机 + 区间涨跌跟踪   ← 优先，成本低、见效快
        └─ 阶段 4：看板（状态列视图 + 操作入口 + 信号溯源面板）
   └─ 阶段 3：自主挖掘信号层（历史行情/公告/新闻）  ← 可与阶段2并行，
                                                     互不阻塞，但建议
                                                     阶段2先行，因为
                                                     信号最终也是写进
                                                     同一个 CandidateEntry
```

阶段 3 单独按"信号来源"再拆三个子阶段（3a 历史行情技术指标 / 3b 公告
结构化解析 / 3c 新闻舆情统计），互相独立，可以分批交付，不要求一次做完。

## 2. 阶段 2：候选池状态机 + 按状态区间的涨跌跟踪

### 2.1 数据结构改造

`CandidateEntry` 新增两个字段，`from_dict`/`to_dict` 需要兼容旧数据
（缺字段时给默认值，不能让老的 `candidate_pool.json` 读取报错）：

```python
state: str = "watching"          # 见下方状态枚举
state_history: List[StateEvent] = field(default_factory=list)
```

`StateEvent` 是新的 dataclass：

```python
@dataclass
class StateEvent:
    state: str
    entered_at: str        # ISO8601
    price_at_entry: Optional[float]   # 进入该状态时的收盘价，取不到价格时为 None，不阻塞状态变更
    note: str = ""          # 变更原因：人工填写或"信号自动触发：xxx"
```

状态枚举（`stock_watch/candidate_pool.py` 定义为模块级常量，非
`Enum`——与仓库既有的"轻量 dataclass 优先"风格保持一致）：

```
watching        观察池（默认，进池即此状态）
focused         重点关注
buy_suggested   建议买入
holding         已建仓
sell_suggested  建议卖出
dropped         已淘汰（终态，不参与每日跟踪，但保留历史）
```

允许的迁移不做强校验（不阻止用户手工把 `holding` 直接改回
`watching`），只记录一条 warning 日志——理由：这是个人使用的分析辅助
工具，不是需要强流程约束的多人协作系统，强校验的维护成本大于收益。

### 2.2 状态变更函数

`candidate_pool.py` 新增：

```python
def change_state(
    pool: Dict[str, CandidateEntry], code: str, new_state: str, *,
    price_at_entry: Optional[float] = None, note: str = "",
) -> CandidateEntry
```

- 标的不在池中时抛 `KeyError`（调用方即 entrypoint 负责转成合适的
  退出码 + 提示信息，不在本函数内吞异常）。
- 状态不变时（比如已经是 `focused` 又设一次 `focused`）不追加新的
  `StateEvent`，只更新 `note`（避免刷历史噪音）。
- `price_at_entry` 由调用方传入（entrypoint 负责调用
  `fetch_latest_close` 拿实时价格），本函数不做网络调用——保持
  `candidate_pool.py` 是纯逻辑模块的既有约定（呼应
  `stock_watch_continuous_improvement_plan.md` 里"纯函数方便离线测试"
  的设计原则）。

### 2.3 区间收益计算

新增纯函数（不依赖网络，输入当前价格，输出计算结果，方便离线测试）：

```python
@dataclass
class StateReturn:
    state: str
    entered_at: str
    price_at_entry: Optional[float]
    days_in_state: int
    change_pct: Optional[float]   # price_at_entry 缺失时为 None

def compute_state_returns(entry: CandidateEntry, current_price: Optional[float]) -> List[StateReturn]
```

对 `entry.state_history` 里的每一段区间都算一遍涨跌幅（不只是当前
状态），这样报告里能同时看到"这只票在'重点关注'阶段涨了 8%，进入
'建议买入'后又涨了 3%"这种分段收益，直接支撑用户提出的"更细化状态，
更新进入不同状态到当前股价变化"的诉求。

### 2.4 每日跟踪任务（新 entrypoint）

`entrypoints/run_pool_tracking.py`：
- 加载候选池，过滤掉 `state == "dropped"` 的标的。
- 对每只标的调用新增的 `data_sources.fetch_latest_close(code, type)`
  拿最新收盘价（单只失败不影响其它标的，失败的记 warning 并跳过，
  与 `reconcile_outcomes.py` 现有的容错风格一致）。
- 用 `compute_state_returns` 算出每段状态的区间收益。
- 渲染 `reports/pool_tracking/<日期>.md`（复用 `report.py` 的
  `_write` 私有写文件函数模式，新增
  `render_pool_state_report()`）。
- `project.yaml` 里挂 `schedule: "cron: 0 15 * * 1-5"`（盘后跑，价格
  取当天收盘价，与 `kline_batch` 的盘后时间点一致，避免和 `kline_batch`
  抢锁——`resources.max_concurrency: 1` 已经保证同一时刻只跑一个
  entrypoint，时间错开是为了让两者产出物的"日期"含义一致）。

### 2.5 手动状态变更（新 entrypoint，供看板触发）

`entrypoints/change_pool_state.py`，参数走 `project.yaml` 的 `params`
声明（与 `stock_analysis` 现有的 `code`/`name` 参数模式一致）：

```yaml
change_pool_state:
  cmd: "python entrypoints/change_pool_state.py"
  timeout_sec: 60
  params:
    - name: code
      required: true
      help: "标的代码"
    - name: state
      required: true
      help: "新状态：watching/focused/buy_suggested/holding/sell_suggested/dropped"
    - name: note
      required: false
      help: "变更原因（可选）"
```

执行时实时拉一次最新价（`fetch_latest_close`，失败则 `price_at_entry`
记 `None`，不阻塞状态变更本身——"记录不到当前价格"不该导致"用户点了
按钮却操作失败"），调用 `change_state()`，保存池子，打印确认信息。
这是本阶段唯一"允许网络失败也要保证核心操作成功"的 entrypoint，其它
entrypoint 都遵循"部分失败不影响整体，但网络是必需前提"的既有风格。

### 2.6 与现有机制的衔接

- `merge_hot_items()` 新增标的时默认 `state="watching"`，并立即写入
  一条 `StateEvent`（`price_at_entry` 在纯逻辑函数里仍为 `None`，由
  调用方 `run_hotlist_scan.py` 在拿到抓取结果后统一去查一次价格再
  回填——避免 `candidate_pool.py` 本身发起网络请求）。
- `apply_decay()`/`enforce_max_size()` 的打分淘汰逻辑不变，但淘汰时
  （从 `pool` 字典里移除）如果标的当前状态不是 `watching`，先跳过
  淘汰并打印告知——已经进入"重点关注"及以上状态的标的不应该被单纯的
  热度衰减自动清出池子，需要用户显式操作降级到 `dropped`。
- `reconcile_outcomes.py` 的既有结果回溯（评估"打分逻辑准不准"）与
  本阶段的状态区间跟踪（评估"状态判断准不准"）是两件不同的事，不合并
  ——前者是"分数-未来涨跌"的相关性，后者是"人工状态决策点-此后涨跌"
  的相关性，指标含义不同，混在一起会让 `outcome_ledger.jsonl` 的
  schema 变复杂。

### 2.7 验收标准

- [x] `CandidateEntry`/`StateEvent` 新字段 + 新旧数据兼容读取
- [x] `change_state()`/`compute_state_returns()` 纯逻辑单测通过
- [x] `run_pool_tracking.py`/`change_pool_state.py` 语法检查 + 离线
      单测（mock 价格数据）通过
- [x] `project.yaml` 新增两个 entrypoint 声明
- [x] `PROJECT.md` 目录结构/功能列表/结果文件存放位置表同步更新

## 3. 阶段 3：自主挖掘信号层（不依赖外部网站结论）

> 本阶段已实现（见第 6 节变更记录）。落地时选择了与设计稿一处不同的
> 实现方式：三类信号没有接入 `run_hotlist_scan.py`，而是新增独立的
> `entrypoints/run_signal_scan.py`——职责更清晰（"抓别人发现的热点"
> 和"自己分析候选池内已有标的"是两件不同的事），且只分析候选池内已有
> 标的（受 `signals.scan_max_targets` 限制），不做全市场扫描，避免
> 行情+公告+新闻三类抓取对全市场的请求量过大。

### 3.1 信号的统一接口

新增 `stock_watch/signals.py`，定义：

```python
@dataclass
class Signal:
    name: str              # 如 "ma_golden_cross" / "notice_buyback" / "news_sentiment_spike"
    category: str          # "price" | "announcement" | "news"
    score: float            # 该信号贡献的分数（可正可负）
    reason: str              # 人类可读的解释，写入 CandidateEntry.reasons
    evidence_ref: str = ""   # 可选：指向具体数据点（如公告链接）
```

`merge_hot_items()` 之外新增 `merge_signals(pool, code, signals: List[Signal])`
——不替换现有的"外部网站热度合并"，而是并行的第二条打分通路，两者的
分数分开累计到 `entry.score` 里，但 `entry.reasons` 会同时包含"来自
XX 网站热榜"和"自算：MA5/MA20 金叉"这类不同来源的说明，保证可解释性
（呼应"每个信号可解释、可回溯"的既有设计要求）。

### 3.2 子阶段 3a：历史行情技术指标

`stock_watch/indicators.py`，输入 `fetch_kline()` 已经在用的 DataFrame，
输出若干 `Signal`：均线金叉/死叉、放量突破（成交量相对 N 日均量的
倍数）、波动率压缩后突破（布林带宽度分位数 + 突破方向）。用 `pandas`
计算，不新增强依赖（`talib` 安装门槛高，优先用 pandas 手写指标）。

### 3.3 子阶段 3b：公告结构化信号

复用 `data_sources.fetch_announcements()` 已经在抓的公告列表，新增
`stock_watch/announcement_signals.py` 做关键词分类（业绩预增/预减、
回购、股权激励、并购重组等），每类给一个基础分值，具体权重放
`config/watchlist.yaml` 新增的 `signals.announcement_weights` 段，
可调不用改代码。

### 3.4 子阶段 3c：新闻舆情统计

复用 `data_sources.fetch_news()`，用简单的关键词库统计正负面词频、
近 N 日新闻数量环比变化（"新闻数量突增"本身就是信号，不需要精确的
情感分析模型才能起步）。后续如果发现效果不够，再考虑接入更复杂的
情感分析，本阶段先用规则方法验证"这层信号有没有用"这个更基础的问题。

### 3.5 验收标准（每个子阶段独立验收）
- [x] `signals.py` 统一接口 + 纯逻辑单测
- [x] 3a 历史行情指标计算 + 单测（mock K 线 DataFrame）
- [x] 3b 公告分类规则 + 单测（mock 公告列表）
- [x] 3c 新闻统计规则 + 单测（mock 新闻列表）
- [x] 新增 `entrypoints/run_signal_scan.py`：独立于 `run_hotlist_scan.py`
      的自算信号扫描入口，只分析候选池内已有标的（受
      `signals.scan_max_targets` 限制），三类信号各自受
      `signals.*_enabled` 开关控制，默认全部关闭（灰度开启，不强制
      一次性替换外部网站热度）

## 4. 阶段 4：看板

> 本阶段已实现（见第 6 节变更记录）。实际落地方式与设计稿基本一致，
> 唯一的调整：`4.4 回溯统计面板` 没有另外接 `outcome_ledger.jsonl`
> （那是"打分-实际涨跌"相关性的账本，语义与"状态区间收益"不同，见
> 第 2.6 节的既有说明），而是直接对同一次响应里已经拿到的
> `state_returns` 做前端聚合（按状态汇总平均涨跌幅/胜率）——不需要
> 额外的后端接口，数据本来就已经在页面上了。
>
> **⚠️ 后续变更（已完成）**：本阶段的实现是 stock_watch 专属的（路由/
> 字段名/entrypoint 名硬编码），后来发现这个诉求应该做成通用机制——
> 任何外部项目都能通过 `project.yaml` 声明去接入状态看板，而不是每接
> 一个新项目就得改一遍看板代码。这部分已被
> `next_doc/external_projects_generic_kanban_view_refactor_plan.md`
> 重构取代（阶段 A-D 已全部完成）：本节描述的 `pool_tracking` 专属
> 路由/`_render_pool_tracking_panel()` 已被通用的 `kanban_data`/
> `_render_kanban_view_panel()` 替换，stock_watch 通过
> `project.yaml` 里的 `dashboard.kanban_view` 声明接入，不再有任何
> stock_watch 专属的看板代码。下面 4.1-4.4 节的描述仅作历史记录，
> **`4.4 回溯统计面板`未被纳入通用 schema，已随专属实现一起下线**
> （原因见重构计划第7/9节：这是"字段语义是价格/收益率"的项目才有意义
> 的功能，不是通用 `kanban_view` 接入方都需要，暂不做，如需要请单独
> 立项）。

依赖阶段 2 的数据结构（状态机 + 区间收益）先落地稳定。mini_agent
已有"外部项目卡片"和"手动触发"的看板机制（`🗂️ 外部项目` tab，见
`next_doc/external_projects_kanban_integration_plan.md`），本阶段是在
其基础上给 stock_watch 专属扩展一个"📊 候选池状态跟踪"折叠面板，不是
另起一套 Web 后端，也不是新增一个顶层 tab。

### 4.1 状态列视图
候选池按状态分栏展示（`watching`/`focused`/`buy_suggested`/`holding`/
`sell_suggested`，`dropped` 默认折叠），每张卡片显示代码/名称/当前
状态区间涨跌幅/进入天数，数据源直接读 `run_pool_tracking.py` 产出的
最新 `reports/pool_tracking/<日期>.md` 或其背后的结构化数据（建议
`run_pool_tracking.py` 除了渲染 Markdown，额外落一份
`data/pool_tracking_latest.json` 给看板直接读，不强迫看板解析
Markdown 表格）。（**实现**：看板列用 emoji + 中文标签区分状态，而非
颜色底色——Streamlit 原生组件不方便精细控制卡片背景色，用 caption
文本里的 `+`/`-` 符号和数值本身传达涨跌方向，信息完整度不受影响。）

### 4.2 操作入口
每张卡片提供"变更状态"按钮，直接复用阶段 2 的
`change_pool_state` entrypoint（看板"手动触发"机制已经能按
`project.yaml` 的 `params` 渲染表单，不需要新增框架能力）。（**实现**：
除了通用的「▶️ 手动触发」区块本身就能触发 `change_pool_state`，状态
跟踪面板内每个标的也提供了一个更顺手的下拉选择+备注输入表单，底层
调的是同一个 `trigger_external_project_run(name, "change_pool_state",
params=...)`，不是另一套后端逻辑。）

### 4.3 信号溯源面板
点开标的展示 `entry.reasons` 全量列表（阶段 3 落地后会同时包含外部
网站来源和自算信号来源），以及 `state_history` 的完整时间线。（**实现**：
`pool_tracking_latest.json` 补充了 `reasons`/`sources` 字段（原先阶段2
只有 `state`/`score`/`current_price`/`state_returns`），看板逐条展示
`reasons` 文案；`state_history` 本身通过 `state_returns` 间接展示——
每一段区间的 `entered_at`/`days_in_state`/`change_pct` 已经覆盖了
时间线信息，未单独暴露 `price_at_entry`/`note` 字段到看板，避免信息
密度过高，如后续需要可以再补。）

### 4.4 回溯统计面板
读 `outcome_ledger.jsonl`（既有）+ 阶段 2 新增的状态区间收益数据，
做胜率/平均涨跌幅汇总，判断"重点关注"这个动作本身相对"观察"阶段是否
真的提升了后续表现。（**实现**：只用了 `state_returns` 在前端现算
按状态汇总的平均涨跌幅/胜率，未接 `outcome_ledger.jsonl`——那是
"打分-实际涨跌"相关性的账本，语义与"状态区间收益"不同，见 2.6 节
的既有说明，且数据本来就已经在同一次响应里，不需要额外的后端接口。）

### 4.5 验收标准
- [x] `pool_tracking_latest.json` 结构化产出物（阶段2已完成，本阶段
      补充 `reasons`/`sources` 字段以支持信号溯源面板）
- [x] 看板状态列视图接入
- [x] 状态变更操作入口接入
- [x] 信号溯源 + 回溯统计面板接入

## 5. 变更记录

- 2026-08-27：文档创建，阶段 2/3/4 设计确认（阶段 0）。
- 2026-08-27：阶段 2（候选池状态机 + 按状态区间跟踪）实现完成，见
  `stock_watch/candidate_pool.py`（`StateEvent`/`change_state`/
  `compute_state_returns`）、新增 entrypoints
  `run_pool_tracking.py`/`change_pool_state.py`、
  `data_sources.fetch_latest_close()`、
  `report.render_pool_tracking_report()`、`project.yaml` 新增两个
  entrypoint 声明、`PROJECT.md` 同步更新、`tests/test_pool_state.py`
  新增单测。阶段 3/4 尚未开始实现。
- 2026-08-27：阶段 3（自主挖掘信号层）实现完成，见
  `stock_watch/signals.py`（`Signal`/`SignalBundle` 统一接口）、
  `stock_watch/indicators.py`（3a 历史行情技术指标：MA金叉死叉/放量
  突破/布林带压缩后突破）、`stock_watch/announcement_signals.py`
  （3b 公告关键词分类）、`stock_watch/news_signals.py`（3c 新闻词频
  统计）、`candidate_pool.merge_signals()`（自算信号与外部网站热度
  并行的第二条打分通路）、新增 entrypoint `run_signal_scan.py`、
  `config.py` 新增 `signal_categories_enabled`/`announcement_weights`/
  `signal_scan_max_targets`、`config/watchlist.yaml` 新增 `signals`
  段（默认全部关闭）、`project.yaml` 新增 `signal_scan` entrypoint
  声明、`tests/test_signals.py` 新增单测（共 43 个测试通过）。阶段 4
  （看板）尚未开始实现。
- 2026-08-27：阶段 4（看板）实现完成。后端：新增路由 `GET /v1/
  external_projects/{name}/pool_tracking`（`src/mini_agent/api/
  routes.py`，读该项目 `data/pool_tracking_latest.json`，文件不存在
  时返回 `available: false`，不强迫所有外部项目都实现这个约定）；
  `AgentClient.external_project_pool_tracking()`
  （`apps/mini_agent_kanban/client.py`）。前端：`🗂️ 外部项目` tab 内
  新增 `📊 候选池状态跟踪` 折叠面板（`_render_pool_tracking_panel()`，
  `apps/mini_agent_kanban/app.py`），含状态列视图（4.1）、变更状态
  表单（4.2，复用 `change_pool_state` entrypoint）、逐标的信号溯源
  （4.3，展示 `reasons` 列表 + 状态区间收益）、按状态汇总的胜率/
  平均涨跌幅统计（4.4）。`stock_watch/report.py::write_pool_tracking_
  json()` 补充 `reasons`/`sources` 字段以支持信号溯源面板。新增 4 个
  路由测试（`tests/test_api_external_projects_routes.py`），外部项目
  相关测试文件共 114 个用例全部通过，无回归。手动过一遍真实 Streamlit
  UI（需要拉起 daemon）仍留给使用者自行验证，与
  `external_projects_kanban_integration_plan.md` 阶段4的既有约定一致。
- 2026-08-27：阶段4的实现已被
  `next_doc/external_projects_generic_kanban_view_refactor_plan.md`
  重构取代（阶段 A-D 全部完成）。`GET /v1/external_projects/{name}/
  pool_tracking` 路由、`AgentClient.external_project_pool_tracking()`、
  `_render_pool_tracking_panel()` 均已删除，替换为通用的
  `GET /v1/external_projects/{name}/kanban_data`、
  `AgentClient.external_project_kanban_data()`、
  `_render_kanban_view_panel()`。stock_watch 通过
  `project.yaml` 的 `dashboard.kanban_view` 声明接入，字段名对应
  `report.py::write_pool_tracking_json()` 已有输出，未改动
  `candidate_pool.py`/`run_pool_tracking.py` 的业务逻辑本身。
  `4.4 回溯统计面板`未被纳入通用 schema，本次一并下线（详见重构计划
  第7/9节的取舍说明）。回归：`external_projects/stock_watch/tests/`
  43 项 + 外部项目相关测试全部通过，无新增回归。
