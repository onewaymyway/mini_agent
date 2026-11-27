# 配置

配置分为四类：全局配置（`config/settings.yaml`）、分类（`config/categories.yaml`）、站点（`config/sites.yaml`）、信息源（`sources/<id>/source.yaml`）。另外，看板上保存的偏好写入 `data/` 目录，优先级高于 `settings.yaml`。

## config/settings.yaml

| 段 | 作用 | 主要键 |
|---|---|---|
| `paths` | 目录，相对项目根目录 | `sources_dir`、`data_dir`、`reports_dir`、`browser_profile_dir` |
| `streamlit` | 看板 | `port`（默认 8506）、`background_tick`（看板进程内是否后台调度） |
| `health` | 健康状态 | `enabled`（是否降频与自动暂停，默认关）、`degrade_after`、`pause_after`、`backoff_factor`、`max_interval` |
| `defaults` | 通用默认值 | `schedule_every`（60m）、`max_items`（50）、`tick_interval_minutes`（5）。`retain_days` 已不再用于全局清理，见下方 `retention` |
| `retention` | 数据保留与清理 | `enabled`、`raw_days`、`items_days`、`runs_days`、`shown_days`、`impressions_days`、`run_every_hours`，见下 |
| `reports` | 报告计划 | 每个计划有 `id`，并用 `every`（间隔）或 `at`（每天固定时刻）指定时间，`window` 为统计窗口，`categories` 为分类 |
| `profile` | 兴趣画像 | `llm_enabled`、`rank_reports`、`days` |
| `dedicated_browser` | 专用浏览器 | `executable`（留空自动查找 Chrome/Edge）、`profile_dir`、`debug_port`（默认 9223）、`scrape_mode`（`launch` 或 `cdp`） |
| `feedback_receiver` | 反馈插件接收服务 | `port`（默认 18765） |
| `service_autostart` | 看板启动后是否自动恢复接收服务与专用浏览器 | `enabled`（默认关） |
| `feed` | 推荐信息流 | 见下 |

### feed 段（推荐）

| 键 | 默认值 | 含义 |
|---|---|---|
| `featured_size` | 15 | 今日精选条数 |
| `explore_size` | 5 | 探索条数；0 即关闭 |
| `hot_min_sources` | 3 | 多源热点：至少被几个不同信息源报道 |
| `hot_size` | 12 | 多源热点最多条数 |
| `topic_groups` | 5 | 关注主题分组数 |
| `topic_group_size` | 8 | 每个主题组最多条数 |
| `topic_min_group` | 2 | 主题组至少包含的事件数 |
| `later_size` | 50 | 稍后看展示条数 |
| `others_limit` | 50 | 其余分区展示并记录曝光的条数 |
| `candidate_limit` | 2000 | 候选池上限 |
| `cluster_threshold` | 0.45 | 标题聚合阈值，越大越保守 |
| `cluster_df_ratio` | 0.1 | 高频二元组忽略比例（控制计算量） |
| `cold_start_factor` | 0.3 | 画像为空时 interest 权重的系数 |
| `persistence_snapshots` | 12 | 一直挂在榜上超过该次数的条目降权 |
| `weights` | 见文件 | 各分量权重：interest、source_affinity、freshness、importance、persistence_penalty、fatigue_penalty |
| `diversity.source_cap` | 3 | 同一信息源在精选中的上限 |
| `diversity.topic_repeat_penalty` | 0.1 | 同一主题重复时的扣分 |
| `fatigue.shows`、`fatigue.window_days` | 3、7 | 曝光疲劳的次数与统计窗口 |
| `half_life_hours` | 见文件 | 新鲜度半衰期，按信息源类型 |
| `learning.*` | 见文件 | 学习权重的样本下限、增益门槛、留出比例、统计天数（默认关闭） |

## 数据保留（retention 段）

| 键 | 默认 | 下限 | 删除的内容 |
|---|---|---|---|
| `enabled` | true | — | 自动清理总开关（看板手动清理不受影响） |
| `raw_days` | 31 | 31 | `data/raw/` 下的抓取快照；每个信息源始终保留最新一份 |
| `items_days` | 31 | 2 | `data/items/` 下的新增条目日志 |
| `runs_days` | 90 | 7 | `data/runs/` 下的运行记录 |
| `shown_days` | 90 | 60 | `data/feed/shown.jsonl` 中的推荐展示记录（按行删除） |
| `impressions_days` | 90 | 7 | `data/feedback/exposures.jsonl` 中的 impression 行（只删展示，不删用户行为） |
| `run_every_hours` | 24 | — | 自动清理的最小间隔；随调度（tick）执行 |

下限的原因：快照需覆盖 30 天的合并与推荐窗口；展示记录需覆盖 60 天的学习与回放。低于下限的配置会被提高到下限。

**永久保留（不会被任何清理删除）**：反馈事件 `feedback/events.jsonl`；打开与停留记录（exposures 中的 `open`、`dwell`）；偏好 `feed/prefs.json`；学习权重、LLM 缓存与计数；`state/` 下的信息源状态；`reports/` 下生成的报告。

看板“设置与管理”→“数据保留与清理”中可以：
- 修改以上全部参数（自动清理开关、五类保留天数、自动清理的最小间隔）。超出范围的值会被限制，低于下限的会被提高到下限。
- 保存后立即生效；“恢复默认”清除看板上的设置，回到 `settings.yaml`（或内置默认）。
- 先预览各类数据的数量与大小，再手动“立即清理”。

看板设置保存在 `data/state/retention_prefs.json`，优先级高于 `settings.yaml`。可调范围：天数 1–3650（各类有下限），间隔 1–720 小时。

## 看板可调的展示数量
推荐页的“⚙ 显示数量设置”面板可直接调整以下参数，保存到 `data/feed/prefs.json`，优先级高于 `settings.yaml`，“恢复默认”会清除：

| 参数 | 范围 |
|---|---|
| 今日精选条数 `featured_size` | 1–40 |
| 探索条数 `explore_size` | 0–40 |
| 多源热点最多条数 `hot_size` | 0–40 |
| 关注主题分组数 `topic_groups` | 0–12 |
| 每个主题组条数 `topic_group_size` | 1–30 |
| 稍后看展示条数 `later_size` | 1–200 |
| 其余分区展示条数 `others_limit` | 1–300 |

## config/categories.yaml
分类由用户编辑。`source.yaml` 中的 `category` 必须引用这里的 id。

```yaml
categories:
  - id: tech
    name: 技术
```

## config/sites.yaml
需要登录或浏览器渲染的站点。信息源通过 `browser.site` 引用这里的 id。

| 键 | 说明 |
|---|---|
| `login_url` | 登录页，`cli.py login <site>` 打开的地址 |
| `check_url` | 检测登录状态的地址 |
| `expired_pattern` | 访问后最终地址包含该片段即视为登录失效（如跳转到登录页） |

无需登录的站点（如 `netease_money`、`kr36`）也需要配置一条，因为 `browser.site` 是必填项；此时 `expired_pattern` 一般不会命中。

## 偏好与运行时文件（data/ 下）
- `data/feed/prefs.json`：隐藏的信息源、学习权重与 LLM 开关、展示数量。
- `data/state/runtime_config.json`：看板上修改的报告计划、后台调度设置等运行时覆盖项。
- `data/feed/learned_weights.json`：学习权重（训练后生成，需在页面开启才生效）。

## 修改后如何生效
- `settings.yaml` 与 `sources/` 的修改在下次读取时生效；看板中的修改立即保存。
- 推荐页的参数修改在“刷新推荐”或重新加载后生效。
- 信息源配置修改后建议执行 `python cli.py validate`。

## 自动探索（config/discovery.yaml 的 autoexplore 段，P12）

| 参数 | 默认 | 含义 |
|---|---|---|
| daily | 20 | 每天最多探索的站点数（0 即关闭） |
| min_interval_hours | 1 | 同一站点两次探索的最小间隔 |
| min_items | 3 | 预览至少要有的条目数 |
| min_completeness | 0.8 | 预览条目中标题与链接都有的比例 |
| harvest_limit | 30 | 每轮从抓取条目中提取的外链站点上限 |
| max_listing_pages | 4 | P14：每站点最多探索的栏目页（sitemap 栏目 + 站内分类） |
| max_feeds_per_site | 3 | P14：每站点最多登记的订阅入口（RSS/Atom，不需要模型） |
| render | off | P14：`off` 或 `headed`。`headed` 时，页面没有列表结构则用有头浏览器渲染后再判断 |
| render_daily | 40 | P14：每天最多渲染次数（同一页面在一次探索中只计一次） |
| max_apis_per_site | 3 | P15：每站点最多登记的列表型接口（渲染时观察到的 JSON，不需要模型） |
| frontier_batch | 10 | P16：每轮从队列中取出处理的页面数 |
| host_daily_pages | 30 | P16：每站点每天最多访问的页面数（含渲染前的普通请求） |
| frontier_max_queued | 500 | P16：队列中待访问条目的全局上限 |
| page_links_cap | 20 | P16：每个页面最多入队的链接数 |
| cross_site | false | P16：是否允许跨站入队（只来自种子或已接受来源的页面） |

禁止名单 `config/denylist.yaml`（项目维护）：列出的站点（含子域名）不访问、不入队。

### 后台自动探索（P17）

| 参数 | 默认 | 含义 |
|---|---|---|
| background | false | 是否由调度（tick）定时运行自动探索。默认关闭 |
| background_every_hours | 6 | 两次后台运行的最小间隔 |
| background_window | 00:00-24:00 | 运行窗口，如 `22:00-06:00`（支持跨午夜） |
| low_yield_checks | 5 | 探索达到该次数仍无产出，才视为低产出 |
| low_yield_threshold | 0.1 | 产出分低于该值视为低产出；产出分 = (新增 + 2×采纳 + 1) /（探索次数 + 2） |
| recheck_days | 30 | 低产出站点的复查间隔 |

暂停：看板“推荐”页的候选区可随时“暂停自动探索”，暂停状态保存在 `data/discovery/control.json`。暂停后，正在进行的一轮会在下一个站点或页面前停止。

说明：有头渲染使用一次性 profile，结束即删除，不携带任何登录态。渲染得到的候选会标注 `browser.required`；它们在正式运行时仍使用现有的共享浏览器会话（只用于公开页面），这一点与探索过程不同。

### 复查与退役（P18，config/discovery.yaml 的 review 段）

默认关闭。只复查「自动探索发现、且已确认启用」的信息源；手工配置的源、已停用的源、需要浏览器的源不复查。

| 参数 | 默认 | 含义 |
|---|---|---|
| enabled | false | 是否由调度（tick）每天最多运行一轮复查 |
| every_days | 30 | 正常源的复查间隔 |
| failing_recheck_days | 7 | 上次复查失败的源的复查间隔 |
| retire_after | 3 | 连续复查失败多少次后建议退役 |
| auto_retire | false | 是否自动停用；默认只建议，不自动改动 |
| max_alternatives | 3 | 每个失效源每次最多生成的替代候选 |
| daily | 10 | 每轮最多复查的源数 |
| redraft | true | 失效源是 html_list 且有可用模型时，让模型重建同页面草稿 |

状态文件：`data/discovery/review_state.json`（每源复查状态）、`retired.jsonl`（退役记录）、`review.jsonl`（每轮摘要）。退役只停用并记录，不删除文件；暂停自动探索后复查也会停止。

种子目录 `config/seed_sites.yaml` 由项目维护，用户不需要编辑。目录内容是草案，尚未在真实环境中逐个核对。

## 探索参数的看板覆盖（P19）

`config/discovery.yaml` 中 `autoexplore` 与 `review` 两段的所有参数，都可以在看板设置并保存。优先级从高到低：

1. **看板保存的值**：写入 `data/state/runtime_config.json` 的 `explore_params.<分组>` 下，不改动 yaml 文件；
2. **config/discovery.yaml** 中的同名字段；
3. **默认值**：登记在 `core/explore_params.py` 的 `SCHEMA` 中（每个参数的类型、取值范围与中文说明只在这里维护）。

规则：

- 保存时逐项校验（类型、范围、可选值、时间格式）；任何一项不合法则整体拒绝，已保存的内容不变。
- 看板保存过的参数，之后修改 yaml 不会生效；需要回到 yaml 或默认值时，在看板对该参数“恢复默认”。
- 运行时覆盖文件损坏或某一项不合法时，该项回退到 yaml / 默认值，不影响其他参数。
- 覆盖文件与 tick 间隔、报告计划、服务端口等其他运行时设置共存，互不覆盖。
- 看板“自动探索”页签的“探索参数设置”即为此入口（P19-2）；也可通过 `server/api.py` 的 `explore_params_overview / explore_params_save / explore_params_reset` 调用。

### P19-3 新增可在看板修改的参数

| 分组 | 参数 | 默认 | 说明 |
|---|---|---|---|
| 自动探索 · 队列 | `max_depth` | 2 | 站内最大探索深度（种子页为 0） |
| 自动探索 · 接口 | `json_min_items` | 3 | 渲染时 JSON 列表至少含的条目数，低于则不识别为候选接口 |
| 自动探索 · 接口 | `json_max_depth` | 3 | 识别接口时搜索 JSON 的最大嵌套深度 |
| 自动探索 · 渲染 | `render_max_responses` | 40 | 单次渲染最多分析的 JSON 响应数 |
| 自动探索 · 渲染 | `render_timeout_s` | 30 | 单页渲染超时（秒） |
| 发现额度（搜索模板） | `daily` / `pending` | 3 / 10 | 对应 yaml 的 `limits.daily` / `limits.pending` |
| 采纳率自动暂停 | `min_decisions` / `min_rate` | 5 / 0.2 | 对应 yaml 的 `adoption.*`；`min_rate` 设为 0 即关闭自动暂停 |

看板中这两组分别显示为“发现额度（搜索模板）”“采纳率自动暂停”，与自动探索、复查两组并列。
