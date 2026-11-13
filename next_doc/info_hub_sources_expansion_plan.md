# info_hub 信息源扩展计划

> 目的：在现有热榜、新闻、财经之外，逐步增加稳定、合规、低维护成本的信息源。
> 每批次独立实施、独立回退；新源默认启用，若某源抓取失败只影响该源，不影响其他源。

## 1. 第一批（已实施）

| 信息源 | id | 适配器 | 分类 | 类型 | 调度 | 说明 |
|---|---|---|---|---|---|---|
| Bangumi 今日放送 | `bangumi_calendar` | script | video | feed | 6h | 官方公开接口 `api.bgm.tv/calendar`，免 key；只取今天播出的条目，按评分降序 |
| Hacker News 热门 | `hacker_news` | script | tech | hot_list | 1h | 官方 Firebase 接口；前 30 条，每次 31 次请求；单条失败跳过 |
| GitHub Trending | `github_trending` | html_list | tech | hot_list | 6h | 公开页面，按今日新增 star 排序；改版时只需改 `parse` |
| 少数派 | `sspai_feed` | rss | tech | feed | 1h | RSS |
| 36 氪 | `kr36_feed` | rss | news | feed | 1h | RSS |
| 阮一峰的网络日志 | `ruanyifeng_blog` | rss | tech | feed | 12h | Atom，更新较慢 |

### 1.1 代码位置
- `sources/<id>/source.yaml` 与（script 类）`sources/<id>/script.py`，共 6 个目录。
- `sources/_index.json` 追加 6 个启用记录。
- 分类沿用 `config/categories.yaml` 中已有的 `video`、`tech`、`news`，未新增分类。

### 1.2 验证
- `tests/test_new_sources.py` 5 项：配置加载无错误；Bangumi 只取当天并按评分排序，无中文名时使用原名；Hacker News 跳过非故事与失败条目；GitHub Trending 的 HTML 解析；RSS 与 Atom 解析。
- 测试使用仿真响应，**未联网验证**。沙箱无法访问这些站点，需要在本机看板的“信息源”页手动抓取一次确认。
- info_hub 全量 517 项，剩余 2 个为改动前即存在的账本测试（`test_entrypoints`）。

### 1.3 需要你确认的地方
- 少数派与 36 氪的 RSS 地址（`https://sspai.com/feed`、`https://36kr.com/feed`）是常见地址，若抓取为 0 条，请告知，我改为实际可用的地址。
- GitHub Trending 的选择器基于页面当前结构，如果返回 0 条需要调整。

## 2. 后续候选（未实施）

| 信息源 | 状态 | 备注 |
|---|---|---|
| 豆瓣（电影、剧集热门） | 暂缓 | 无官方开放接口，非官方接口可能变化或限制，使用条款不允许抓取；如需要，作为实验性源单独评估 |
| 今日头条热榜、澎湃等 | 待验证 | 需确认接口或页面的稳定性 |
| 雪球、华尔街见闻 | 暂缓 | 需要登录态，维护成本高 |

## 3. 不变的约定
- 新源必须有明确的公开来源（官方接口、RSS 或公开页面），不绕过登录与访问限制。
- 每个源的解析规则写在它自己的 `source.yaml` 或 `script.py` 中，便于单独修改。

## 4. 36 氪抓取失败（ParseError）的修复与排查方式

### 4.1 现象
36 氪 RSS 解析失败：`ParseError: not well-formed (invalid token): line 10, column 168`。即源站 RSS 含有非法 XML 字符（最常见是标题或摘要中未转义的 `&`、`<`，或控制字符）。

### 4.2 修复
- `adapters/rss.py`：原文解析失败后，先去掉 XML 不允许的控制字符，再把裸 `&`、裸 `<` 转义后重试一次。修复成功时结果的 `meta` 带 `xml_sanitized: true` 与修复前的错误，便于确认。
- 修复后仍失败：返回错误，并在 `meta.debug` 中附带原文与出错位置。
- 注意：本环境无法访问 36 氪，**没有用真实的失败响应验证**。修复覆盖的是常见的非法字符；若仍失败，看板的调试信息会给出出错位置附近的原文，据此再调整。

### 4.3 看板上的调试信息（立即抓取）
“信息源”页点“抓取一次”后，结果区域会显示：
- 成功：抓取条数、新增条数、耗时，以及解析统计（如是否做过 XML 修复）。
- 失败：错误信息、错误类型、原文长度（超过 10 万字符时截断并标记）、出错位置附近的原文（标出 `[此处]`）、原文前 5000 字符，以及“下载抓取原文”按钮。
- 失败时的完整调试信息写入 `data/debug/<信息源 id>/<时间>.json`，每个信息源保留最近 30 份，结果区域会显示文件路径。

### 4.4 代码位置
- `adapters/base.py::failure_debug`：生成调试信息（原文、错误位置、上下文）。
- `adapters/rss.py`：`sanitize_xml`，fetch 的修复与失败调试。
- `core/runner.py::_failure_extra`：写入调试文件并返回摘要；`DEBUG_KEEP` 控制保留数量。
- `server/api.py::run_source_now`：返回 `ok`、`meta`、`debug`、`debug_path` 等。
- `app.py::_run_once_result_panel`：结果与调试信息的展示。

### 4.5 验证
- `tests/test_fetch_debug.py` 11 项：修复裸 `&` 与裸 `<`；去掉控制字符；无法修复时返回错误位置与上下文；网络错误的调试信息；原文截断；失败时写入调试文件并按数量清理；API 对失败与成功的返回；看板结果区域显示错误、上下文与原文。
- info_hub 全量 538 项，剩余 2 个为改动前即存在的账本测试（`test_entrypoints`）。
