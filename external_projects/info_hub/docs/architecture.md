# 架构

## 总体结构

```
信息源配置 (sources/) ──► 适配器 (adapters/) ──► 原始条目
                                                    │
                                                    ▼
                              流水线 (core/pipeline.py)：标准化、去重、状态比较
                                                    │
                    ┌───────────────────────────────┼──────────────────────────────┐
                    ▼                               ▼                              ▼
          快照与条目存储 (data/raw, data/items)   报告 (core/digest.py)     推荐信息流 (core/feed.py)
                    │                                                              │
                    └──────────────── 服务层 (server/api.py) ───────────────────────┘
                                                    │
                                                    ▼
                                     Streamlit 看板 (app.py) ＋ 命令行 (cli.py)
```

## 主要模块

| 目录 / 文件 | 职责 |
|---|---|
| `adapters/` | 把各种来源转换为统一的 `RawItem`。类型：`rss`、`json_api`、`html_list`、`script`、`subscription`。新增类型只需新增模块，注册表自动发现。 |
| `checkers/` | 订阅检查器（小说章节、视频更新等），按站点类型自动发现。 |
| `core/runner.py` | 运行一个信息源：调用适配器、写快照、写条目、记录运行日志与健康状态；失败时保存调试文件。 |
| `core/pipeline.py` | 与上次状态比较，得出新增、上升、下降、掉出，并更新状态文件。 |
| `core/tick.py`、`core/scheduler.py` | 调度：找出到期的信息源与报告并执行。 |
| `core/digest.py` | 按计划生成报告（Markdown 与 JSON）。 |
| `core/merged.py` | 跨信息源的时间窗合并视图，供“抓取结果”的合并显示使用。 |
| `core/feed.py` 及 `feed_*.py` | 推荐信息流：候选池、聚合去重、打分、分区、曝光与回放；学习权重与 LLM 增强分别在 `feed_learn.py`、`feed_llm.py`。 |
| `core/profile.py` | 兴趣画像：手写主题、屏蔽词、从反馈中学习的关键词与来源权重。 |
| `core/feedback.py`、`core/evaluation.py` | 反馈事件（👍 / 👎 / ⏰）、打开与曝光记录、评估指标。 |
| `core/dedicated_browser.py`、`core/browser_session.py` | 专用浏览器：独立数据目录的有头浏览器，抓取时可附着或独立启动。 |
| `core/health.py` | 连续失败计数、标红、降频与暂停。 |
| `core/store.py` | 文件锁、原子写入、JSONL 读写。 |
| `core/retention.py` | 数据保留：按天数删除过程数据，永久保留反馈与行为记录；每天随调度执行一次。 |
| `server/api.py` | 服务层：看板与命令行调用的全部业务接口。看板不直接读写数据文件。 |
| `server/feedback_receiver.py` | 浏览器插件的本地接收服务（反馈与打开记录）。 |
| `app.py` | Streamlit 看板，只做界面，业务通过 `server/api.py` 完成。 |
| `entrypoints/` | 脱离看板直接运行的入口（tick、run_source、report、resume_source、health），供 mini_agent 调用。 |

## 数据目录

运行产物全部在 `data/` 下（已加入 `.gitignore`）：

| 路径 | 内容 |
|---|---|
| `data/raw/<id>/<日期>/<时间>.json` | 每次抓取的快照（条目、变化、元信息） |
| `data/items/<id>/<日期>.jsonl` | 新增条目 |
| `data/state/<id>.json` | 信息源的上次状态，用于比较 |
| `data/runs/<日期>.log.md` | 运行记录 |
| `data/debug/<id>/<时间>.json` | 失败时的调试信息（原文、错误位置） |
| `data/feedback/events.jsonl` | 反馈事件 |
| `data/feed/prefs.json` | 推荐页的偏好：隐藏的信息源、学习权重开关、LLM 开关、展示数量 |
| `data/feed/shown.jsonl` | 推荐的每次展示记录（位置、分区、分量） |
| `data/feed/learned_weights.json`、`llm_cache.json`、`llm_usage.json` | 学习权重、LLM 结论缓存、每日调用计数 |
| `reports/` | 生成的报告 |
| `browser/dedicated_profile/`、`browser/profile/` | 浏览器登录态（含凭据，勿分享） |

## 设计约定
- **数据分两类**：过程数据（快照、条目、运行记录、展示）可以按天数清理；用户数据（反馈、打开、停留、偏好）永久保留。`core/retention.py` 只按规则删除前者。
- **只读与写入分离**：推荐构建只读，曝光由调用方显式记录；看板的反馈不改变当前列表（会话快照）。
- **失败隔离**：单个信息源失败只影响该源，不中断调度与其他源。
- **保守默认**：LLM、学习权重、健康降频与自动暂停默认关闭，需要在看板或配置中显式开启。
- **配置校验**：`source.yaml` 在加载时校验（id 一致、分类存在、适配器自校验），错误在“信息源”页签与 `cli.py validate` 中显示。
