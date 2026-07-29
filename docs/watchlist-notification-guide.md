# 关注对象 · 分级汇报 · 通知系统 使用指南

- **设计文档**：`next_doc/watchlist_notification_goal_design.md`
- **前置依赖**：`docs/external-input-gateway-guide.md`（External Input
  Gateway 本体，本功能是它的扩展，不重复实现事件采集/路由）
- **当前实施进度**：P1（通知系统骨架）、P2（关注对象匹配）、P3（分级汇报）
  已完成；P4-P8（Goal 关联执行、看板展示、测试补齐）待实施，见设计文档
  §6 状态表。

---

## 1. 这套机制解决什么问题

External Input Gateway 已经能把外部世界的事件（RSS/网页 diff/天气……）
归一化并按 `policies.yaml` 路由。本功能在此基础上新增两件事（当前只
落地了其中"关注对象 + 通知"这一半，"Goal 关联执行"那一半见设计文档
§4.2/§4.4，还未实施）：

1. **关注对象识别**：用户配置一批关键词（竞品名、关键词、话题……），
   命中后按**用户自己设定的频率**（1分钟/30分钟/1小时/1天……而不是
   简单的"紧急/不紧急"）打包汇报，而不是一有动静就打扰。
2. **可扩展的通知渠道**：目前实现了 kanban（恒真兜底）+ 邮件两个渠道，
   接口留好，以后加企业微信/Telegram 等只需要新增一个
   `NotificationChannel` 子类。

---

## 2. 配置关注对象：`.agent/external_input/watchlist.yaml`

```yaml
watchlist:
  - id: competitor_launch
    keywords: ["某竞品发布", "CompetitorA release"]
    match_type: keyword          # 目前只支持 keyword，regex 占位未实现
    report_tier: minute_1        # 引用下面 report_tiers.yaml 里的 tier id
    notify_channels: []          # 留空则用该 tier 的默认渠道
    scope:
      source_channels: []        # 限定只在这些 source channel 里生效；空=不限
    dedup_window_seconds: 86400  # 可选，覆盖默认的 24 小时去重窗口
    enabled: true
```

- 关键词匹配是**大小写不敏感的子串匹配**，标题+详情一起比对，不做分词/
  语义匹配（成本可控优先）。
- **去重**：同一话题（按归一化标题判断）在 `dedup_window_seconds`
  （默认 24 小时）内只计入一次，避免多个 RSS 源转载同一条新闻反复触发。
- 该配置由 `WatchlistMatcher` 消费（独立 consumer，跟 `IngestionPolicy`
  一样挂在 `AutonomousLoop.tick()` 的 maintenance 档位，各自独立游标，
  互不影响），命中后写入 `.agent/external_input/pending_hits.jsonl`。

## 3. 配置分级汇报：`.agent/notification/report_tiers.yaml`

复制 `.agent/notification/report_tiers.yaml.example` 为
`report_tiers.yaml` 并按需增删：

```yaml
tiers:
  - id: minute_1
    schedule: "interval:60"        # 复用 CronScheduler 已支持的 interval:<秒> / cron:<表达式>
    notify_channels: [kanban]
  - id: daily
    schedule: "cron:0 22 * * *"
    notify_channels: [kanban, email]
```

- daemon 启动时会按这份配置**自动补注册**对应的
  `sys:watchlist_report_<tier_id>` cron job（缺失才补，已存在不重复、
  也不会覆盖你用 `/cron` 命令手动改过的 schedule/enabled）。
- 这些 job 触发时**直接在本进程内**读 `pending_hits.jsonl`、生成摘要、
  调用 `NotificationDispatcher` 发送——**不产生任何 LLM 调用**，可以
  放心把某个 tier 设成很高频（比如 1 分钟）而不用担心成本。
- 没有新命中就直接跳过，不发送空消息。**高频 tier**（interval ≤ 5
  分钟）如果连续 5 次都没有新命中，会自动退化到 5 分钟才真正读一次
  文件（纯粹省点文件 IO，一旦有新命中立即恢复原频率）。
- 单条摘要里，每个 `watchlist_id` 最多列 20 条命中，超出部分显示
  "及其余 N 条"，避免消息本身过长。
- 用 `/cron status` 可以看到这些 job 跟其它内置 job 一起排队等待触发；
  `sys:` 前缀的 job 不可删除，只能 `/cron disable <job_id>`。

## 4. 配置通知渠道：`.agent/notification/config.yaml`

```yaml
default_channels: [kanban]
channels:
  kanban:
    enabled: true            # 恒真，写不写都一样，不可关闭
  email:
    enabled: false
    smtp_host: "smtp.example.com"
    smtp_port: 465
    use_ssl: true
    username: "${ENV:MINI_AGENT_SMTP_USER}"
    password: "${ENV:MINI_AGENT_SMTP_PASSWORD}"
    from_addr: "mini-agent@example.com"
    to_addrs: ["you@example.com"]
```

- 密钥字段支持 `${ENV:VAR_NAME}` 占位符，运行时从环境变量读取；
  `config.yaml` 本身已经加进 `.gitignore`，不会被误提交。
- **kanban 是恒真兜底渠道**：不管某个 tier/watchlist 项配置了什么
  `notify_channels`，实际发送时都会隐式带上 kanban——即便邮件发送失败
  （SMTP 连不上之类），至少能在看板的"待处理告警"面板看到这条通知本身，
  不会因为唯一渠道失败而彻底消失。
- 邮件发送失败不重试、不阻塞其它渠道，失败会记 `log_exception`。

## 5. 通知落地位置

- **kanban 渠道**：复用现有 `alerts.jsonl` + `/v1/inbox` 机制，跟
  External Input Gateway 原有的 `notify_only` 告警落在同一个文件，但
  记录里带 `source="watchlist_report"` 字段，看板侧可以按这个字段区分
  "关注命中/分级汇报"和网关路由规则触发的告警。
- **email 渠道**：标准 SMTP 发送，标题=汇报标题，正文=Markdown 摘要
  （按 watchlist_id 分组列出命中标题+链接）。

## 6. 尚未实现的部分

以下能力在设计文档里已经设计好，但代码还未实施（见设计文档 §6 状态表）：

- **Goal 关联执行**（`GoalRelevanceEngine`、`context_only`/`advance_goal`、
  Prompt 精确注入）——P4/P5/P6。
- **看板可视化**（关注对象列表、tier 配置只读展示、Goal 详情页"相关
  外部信息"面板、通知发送记录）——P7。
- **补齐测试**（`test_goal_relevance_engine.py` 等）——P8。

---

## 7. 相关文件一览

| 文件 | 作用 |
|---|---|
| `src/mini_agent/external_input/watchlist.py` | 关注对象配置加载 + 匹配 + 去重 + 写 pending_hits |
| `src/mini_agent/external_input/report_tiers.py` | tier 配置加载 + 消费 pending_hits + 生成摘要 + dispatch |
| `src/mini_agent/external_input/filelock.py` | 跨平台文件独占锁（pending_hits 并发读写保护） |
| `src/mini_agent/notification/dispatcher.py` | `NotificationDispatcher`/`NotificationChannel` 骨架 |
| `src/mini_agent/notification/config.py` | 通知渠道配置加载（`${ENV:...}` 占位符解析） |
| `src/mini_agent/notification/channels/kanban.py` | kanban 渠道实现 |
| `src/mini_agent/notification/channels/email.py` | 邮件渠道实现 |
| `.agent/external_input/watchlist.yaml` | 用户关注对象配置（需自行创建） |
| `.agent/notification/report_tiers.yaml` | 分级汇报 tier 配置（复制 `.example` 后使用） |
| `.agent/notification/config.yaml` | 通知渠道配置（含密钥，已 gitignore） |
