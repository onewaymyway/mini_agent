# 配置

配置分为四类：全局配置（`config/settings.yaml`）、分类（`config/categories.yaml`）、站点（`config/sites.yaml`）、信息源（`sources/<id>/source.yaml`）。另外，看板上保存的偏好写入 `data/` 目录，优先级高于 `settings.yaml`。

## config/settings.yaml

| 段 | 作用 | 主要键 |
|---|---|---|
| `paths` | 目录，相对项目根目录 | `sources_dir`、`data_dir`、`reports_dir`、`browser_profile_dir` |
| `streamlit` | 看板 | `port`（默认 8506）、`background_tick`（看板进程内是否后台调度） |
| `health` | 健康状态 | `enabled`（是否降频与自动暂停，默认关）、`degrade_after`、`pause_after`、`backoff_factor`、`max_interval` |
| `defaults` | 通用默认值 | `schedule_every`（60m）、`retain_days`（14）、`max_items`（50）、`tick_interval_minutes`（5） |
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
