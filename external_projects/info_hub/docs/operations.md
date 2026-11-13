# 运行与运维

## 命令行

在项目根目录执行：

| 命令 | 作用 |
|---|---|
| `python cli.py list` | 列出信息源 |
| `python cli.py validate` | 校验全部信息源配置 |
| `python cli.py run <id>` | 立即抓取一个信息源 |
| `python cli.py login <site>` | 打开有头浏览器登录站点，完成后回到终端按回车 |
| `python cli.py login-status` | 查看各站点登录状态 |
| `python cli.py report <plan_id>` | 立即生成一份报告 |
| `python cli.py tick` | 执行一次调度：运行到期的信息源，生成到期的报告 |
| `python cli.py health` | 查看各信息源的健康状态 |
| `python cli.py resume <id>` | 恢复被暂停或连续失败的信息源 |

`INFO_HUB_ROOT` 环境变量可指定项目根目录（测试与多实例时使用）。

## 看板
见 [看板](dashboard.md)。启动：`streamlit run app.py --server.port 8506`。

## 定时调度

默认方式：看板进程启动后，每隔 `defaults.tick_interval_minutes`（5 分钟）执行一次调度。需要看板保持运行。

由 mini_agent daemon 调度：按 `project.yaml` 的注释取消 `schedule` 的声明，并在看板中关闭 `streamlit.background_tick`，避免两处同时运行。注意：文件锁依赖 fcntl，Windows 下退化为无锁，两处同时调度可能重复抓取。

入口脚本（供外部调用）：`entrypoints/tick.py`、`run_source.py <id>`、`report.py <plan>`、`resume_source.py <id>`、`health.py`。

## 专用浏览器

部分信息源（知乎、小红书、网易财经、36 氪首页）通过专用浏览器渲染页面。

- **启动**：看板“设置与管理”→“服务管理”中点击“启动专用浏览器”。它使用独立的数据目录（`browser/dedicated_profile/`），与系统浏览器隔离。
- **调试端口**：`dedicated_browser.debug_port`（默认 9223，仅本机）。抓取模式 `cdp` 时附着到该端口；`launch` 时由 Playwright 独立启动。
- **登录**：`python cli.py login <site>` 或看板“登录”面板。登录态保存在数据目录，长期复用；只发会话 cookie 的站点关闭浏览器后可能失效。
- **自动恢复**：“看板启动后自动恢复”开关（`service_autostart.enabled`）。

## 服务
- **反馈接收服务**（`python -m server.feedback_receiver`，默认端口 18765）：接收浏览器插件的反馈与打开记录。插件的令牌通过配对窗口获取。
- **看板**：默认端口 8506。

## 健康与降频
`health.enabled` 为 false 时只记录连续失败并标红。开启后连续失败超过 `degrade_after` 会降频，超过 `pause_after` 会自动暂停，需手动恢复（看板或 `cli.py resume`）。

## 调试文件与日志
- **运行记录**：`data/runs/<日期>.log.md`，每行一次运行的结果。
- **抓取快照**：`data/raw/<id>/<日期>/<时间>.json`，包含条目、变化与元信息。
- **失败调试**：`data/debug/<id>/<时间>.json`，包含错误、原文与解析错误位置；每个源保留最近 30 份。

## 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 错误为 `被站点安全验证拦截` | 站点返回了验证页（WAF / Cloudflare），而不是内容 | 对 RSS 源：改用浏览器抓取或停用；对浏览器源：在专用浏览器中手动访问并通过验证 |
| 错误为 `ParseError` | RSS 或 XML 不合法，且修复后仍失败 | 看调试信息中的出错位置原文；多为非法字符或被替换的页面 |
| 错误为 `未配置浏览器会话` | 需要浏览器的源，但专用浏览器未启动 | 在“服务管理”中启动专用浏览器 |
| 错误为 `需要重新登录` | 站点登录态失效 | `python cli.py login <site>` 重新登录 |
| 错误为 `风控拦截` | 站点检测到自动访问 | 降低抓取频率，或在浏览器中手动验证 |
| 0 条，但没有错误 | 选择器未命中（页面改版） | 查看调试信息的“解析统计”，调整 `parse` 中的选择器 |
| 看板提示“需要 playwright” | 未安装浏览器依赖 | `pip install playwright` 并执行 `playwright install chromium` |
| 推荐为空或太少 | 时间窗内没有新内容，或条目都已处理 | 放宽时间范围；点“🔄 刷新推荐”；在“显示数量设置”中调大 |
| 推荐里同一事件仍重复 | 标题差异大，未被聚合 | 属于设计取舍（宁可漏聚）；可提高 `cluster_threshold` 之外的 LLM 补聚（需开启） |

## 数据保留与清理
数据会持续增长，系统每天（随调度）自动清理一次过程数据，规则见 [配置](configuration.md#数据保留retention-段)：

- **会删除**：超过保留天数的抓取快照、条目日志、运行记录、推荐展示记录、信息源展示记录（impression）。每个信息源始终保留最新一份快照。
- **不会删除**：反馈（👍 / 👎 / ⏰）、打开与停留记录、偏好、学习结果、LLM 缓存、报告。

手动操作：看板“设置与管理”→“数据保留与清理”，先看预览，再点“立即清理”。也可以在配置中关闭自动清理（`retention.enabled: false`）。

备份建议：
- 反馈与偏好：`data/feedback/`、`data/feed/prefs.json`（清理不会删除，但建议定期备份）。
- `browser/dedicated_profile/` 含登录态，备份时注意保密。
- 其余 `data/` 下的内容都可以重新生成，但删除后历史趋势与回放会变少。
