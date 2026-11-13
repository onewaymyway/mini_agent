# 信息源

## 基本概念
一个信息源是一段抓取配置：从哪里取、怎么解析、多久取一次、属于哪个分类。每个信息源的配置位于 `sources/<id>/source.yaml`，并在 `sources/_index.json` 中登记 id 与启用状态。

- **id**：唯一标识，同时是目录名（必须一致）。
- **kind**：`hot_list`（热榜，有排名）、`feed`（资讯流）、`recommend`、`custom`、`subscription`（订阅，跟踪更新进度）。kind 影响新鲜度的半衰期，也影响看板筛选。
- **category**：分类 id，必须在 `config/categories.yaml` 中定义（当前有 hot_community、video、novel、tech、news、other）。

## 适配器类型

| 适配器 | 适用 | 必填项 | 说明 |
|---|---|---|---|
| `rss` | RSS 2.0 与 Atom | `request.url` | 标准库解析。遇到非法 XML 字符会尝试修复（去控制字符、转义裸 `&`、`<`）；修复过的结果带 `xml_sanitized` 标记。 |
| `json_api` | 返回 JSON 的接口 | `request.url`、`parse.items_path`、`parse.fields` | `items_path` 支持 `a.b.c`，`*` 展开列表；`url_template` 在接口不给链接时拼接；`time_format: unix` 转换时间戳。 |
| `html_list` | 服务端渲染的页面，或需要浏览器渲染的页面 | `request.url`、`parse.list_selector`、`parse.fields.title` | 字段值格式为 `选择器@属性`；`self` 取条目本身；相对链接按页面地址补全。`browser.required: true` 时通过共享浏览器渲染，并用 `browser.site` 检测登录失效。 |
| `script` | 需要多次请求或复杂处理的源 | 同目录下的 `script.py` | `script.py` 必须定义 `fetch(ctx)`，返回字典列表（至少包含 `title`）。禁止导入 `os`、`sys`、`subprocess` 等模块，禁止调用 `exec`、`eval`、`open`。 |
| `subscription` | 订阅类（章节、视频更新） | 见 `checkers/` | 只在进度变化时提醒。 |

## source.yaml 示例

```yaml
id: hacker_news
name: Hacker News 热门
category: tech
kind: hot_list
adapter: script
enabled: true
schedule: {every: 1h}
request:
  url: https://hacker-news.firebaseio.com/v0/topstories.json
policy: {max_items: 30}
tags: [Hacker News, 技术, 英文]
```

常用字段：

| 字段 | 说明 |
|---|---|
| `enabled` | 是否启用；也可在看板“信息源”页签切换 |
| `schedule.every` | 抓取间隔，如 `30m`、`1h`、`6h`、`1d`；缺省用 `defaults.schedule_every` |
| `request.headers` | 请求头（如 User-Agent、Referer） |
| `policy.max_items` | 最多保留的条目数 |
| `browser.required`、`browser.site`、`browser.wait_selector`、`browser.headless` | 浏览器渲染相关（仅 html_list） |
| `tags` | 标签，用于看板展示与筛选 |

## 现有信息源

| id | 名称 | 分类 | 类型 | 适配器 | 调度 | 备注 |
|---|---|---|---|---|---|---|
| `zhihu_hot` | 知乎热榜 | hot_community | hot_list | html_list（浏览器） | 1h | 需要先登录一次：`python cli.py login zhihu` |
| `zhihu_home` | 知乎首页推荐 | hot_community | feed | html_list（浏览器） | 1h | 需要登录（与 zhihu_hot 共用登录态）；选择器未在线验证 |
| `weibo_hot` | 微博热搜 | hot_community | hot_list | json_api | 30m | |
| `baidu_hot` | 百度热搜 | hot_community | hot_list | json_api | 30m | |
| `bilibili_popular` | B站热门 | video | hot_list | json_api | 1h | |
| `v2ex_hot` | V2EX 热议 | tech | hot_list | json_api | 1h | |
| `netease_news` | 网易新闻首页 | news | feed | html_list | 30m | 移动端首页 |
| `xiaohongshu_hot` | 小红书发现页 | hot_community | feed | html_list（浏览器） | 10m | 需要登录 |
| `eastmoney_news` | 东方财富要闻 | news | feed | html_list | 30m | |
| `netease_money` | 网易财经 | news | feed | html_list（浏览器） | 1h | 无需登录 |
| `bangumi_calendar` | Bangumi 今日放送 | video | feed | script | 6h | 只取当天播出的条目 |
| `hacker_news` | Hacker News 热门 | tech | hot_list | script | 1h | 前 30 条，每次 31 次请求 |
| `github_trending` | GitHub Trending | tech | hot_list | html_list | 6h | 页面改版时调整 `parse` |
| `sspai_feed` | 少数派 | tech | feed | rss | 1h | |
| `kr36_feed` | 36氪 | news | feed | html_list（浏览器） | 1h | 通过专用浏览器打开首页，抓取 `/p/<ID>` 链接；需要浏览器已启动 |
| `ruanyifeng_blog` | 阮一峰的网络日志 | tech | feed | rss | 12h | |
| `hf_daily_papers` | Hugging Face 每日论文 | tech | feed | script | 6h | 官方接口，未在线验证 |
| `hf_trending_models` | Hugging Face 热门模型 | tech | hot_list | script | 2h | 官方接口，未在线验证 |

> 以上“未在线验证”指沙箱环境无法访问站点，接口与选择器依据公开资料确定。上线后请以本机抓取结果为准，并在 `next_doc/info_hub_sources_expansion_plan.md` 中更新状态。

## 添加信息源
三种途径：

1. **看板表单**：“添加信息源”页签，填写 id、名称、分类、类型、适配器与地址，保存后会校验配置。
2. **对话生成**：“添加信息源”页签中用自然语言描述，LLM 生成草稿，预览、修改后确认。LLM 只生成草稿，不会自动运行生成的脚本。
3. **手动创建**：在 `sources/` 下新建目录与 `source.yaml`，并在 `_index.json` 中登记，然后执行 `python cli.py validate`。

添加后建议先执行一次“抓取一次”，确认返回条数与字段是否正确。

## 调试一个失败的信息源
1. 在“信息源”页签选择该源，点击“抓取一次”。
2. 失败时结果区域会显示错误信息、解析错误位置附近的原文（标出 `[此处]`）、抓取到的原文（可下载）与解析统计。
3. 完整调试信息保存在 `data/debug/<id>/`，保留最近 30 份。
4. 常见原因见 [运行与运维](operations.md#常见问题)。

## 健康状态
连续失败会标红；开启 `health.enabled` 后会降频，超过阈值会自动暂停。暂停的源需在看板或用 `python cli.py resume <id>` 手动恢复。
